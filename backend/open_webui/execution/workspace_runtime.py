"""Workspace lifecycle adapter for the existing module and graph executor."""

import asyncio
import copy
import threading
import time
import os
import shutil
from contextlib import AsyncExitStack
from open_webui.execution.claude import ExecutionError
from open_webui.execution.workspaces import WorkspaceService, receipts, digest, safe_file
from open_webui.execution.workspace_process import execute_command


_SERVICE_CREATION_LOCK = threading.RLock()


def service(engine):
    # One construction per root even when first independent leaves enter via
    # worker threads concurrently. Baseline initialization is not a lease writer.
    with _SERVICE_CREATION_LOCK:
        return _service(engine)


def _service(engine):
    root = engine.root
    existing = getattr(root, 'workspace_service', None)
    if existing is not None:
        return existing
    def descendants(bindings, seen=None):
        seen = set() if seen is None else seen
        for binding in bindings:
            if id(binding) in seen:
                continue
            seen.add(id(binding))
            yield binding
            yield from descendants(binding.get('_workflow_bindings') or [], seen)
    binding = next((b for b in descendants([*engine.bindings.values(), *engine.registry])
                    if (b.get('_environment_base') or {}).get('workspace', {}).get('enabled')), None)
    if binding is None:
        raise ExecutionError('This Project has not enabled coding workspaces')
    base = binding['_environment_base']
    project = {'directory': base.get('directory'), 'workspace': base['workspace']}
    root.workspace_service = WorkspaceService(project, root.record, root=base.get('_workspace_root'))
    restored = {}
    for record in root.previous:
        restored.update(copy.deepcopy(record.get('workspace_states') or {}))
    root.record.setdefault('workspace_states', restored)
    root.record.setdefault('host_operations', [])
    return root.workspace_service



def _host_requirements(graph, registry, ancestors=()):
    """Only currently declared, enabled execution closures create requirements."""
    found = []
    for node in graph.get('nodes', []):
        if node.get('enabled', True) is False:
            continue
        if node.get('op') in {'command', 'evaluate'}:
            found.append((node['op'], node.get('workspace') or {}))
        selected = registry.get(node.get('binding'), {})
        if selected.get('module_type') != 'workflow':
            continue
        template = selected['_workflow_template']
        if template['id'] in ancestors:
            raise ExecutionError('Recursive Workflow host requirements are unavailable')
        children = {**registry, **{binding['id']: binding for binding in selected['_workflow_bindings']}}
        found.extend(_host_requirements(template['graph'], children, (*ancestors, template['id'])))
    return found


def _verify_host_requirement(manager, operation, config):
    identities = config.get('command_ids') or []
    if not identities:
        raise ExecutionError('Choose administrator-approved command IDs before native admission')
    commands = [manager.command(identity) for identity in identities]
    backend = manager.policy['command_backend']
    if operation == 'command' and backend != 'local-worktree':
        raise ExecutionError('Use artifact.evaluate for enforced Docker commands on immutable revisions')
    for command in commands:
        if operation == 'evaluate':
            if not command['authoritative_paths']:
                raise ExecutionError('Evaluation commands require pinned authoritative check paths')
            for name in command['authoritative_paths']:
                safe_file(manager.baseline, name, exists=True)
        if backend == 'local-worktree':
            executable = command['argv'][0]
            if os.path.isabs(executable):
                available = os.path.isfile(executable) and os.access(executable, os.X_OK)
            elif '/' in executable:
                path = safe_file(manager.baseline, executable.removeprefix('./'), exists=True)
                available = path.is_file() and os.access(path, os.X_OK)
            else:
                available = shutil.which(executable) is not None
            if not available:
                raise ExecutionError('The approved host command executable is unavailable; no native call was admitted')
    return commands


async def preflight_host_actions(runner, graph=None):
    """Single-flight required host capability admission before any native work.

    Project reads do not call this. Unused configured evaluators do not create
    requirements. Saved child graphs are traversed; revised/generated graphs
    are checked before their newly declared work can be admitted.
    """
    graph = graph if graph is not None else runner.graph
    marker = (id(graph), graph.get('revision', 1))
    if getattr(runner, '_workspace_host_preflight_marker', None) == marker:
        return
    registry = getattr(runner, 'registry', {})
    required = _host_requirements(graph, registry)
    if not required:
        runner._workspace_host_preflight_marker = marker
        return
    root = runner.engine.root
    if not hasattr(root, '_workspace_host_preflight_lock'):
        root._workspace_host_preflight_lock = asyncio.Lock()
    async with root._workspace_host_preflight_lock:
        manager = service(runner.engine)
        verified = getattr(manager, 'verified_host_capabilities', None)
        if verified is None:
            verified = manager.verified_host_capabilities = {}
        failures = getattr(manager, 'failed_host_capabilities', None)
        if failures is None:
            failures = manager.failed_host_capabilities = {}
        for operation, config in required:
            commands = _verify_host_requirement(manager, operation, config)
            signature = digest({'operation': operation, 'commands': commands,
                                'backend': manager.policy['command_backend'], 'image': manager.policy['docker_image']})
            if signature in verified:
                continue
            if manager.policy['command_backend'] == 'docker':
                capability_key = 'docker:' + manager.policy['docker_image']
                if capability_key in failures:
                    raise ExecutionError(failures[capability_key])
                capability = verified.get(capability_key)
                if capability is None:
                    from open_webui.execution.workspace_process import docker_capability
                    try:
                        capability = await asyncio.to_thread(docker_capability, manager.policy['docker_image'])
                    except ExecutionError as error:
                        failures[capability_key] = str(error)
                        raise
                    verified[capability_key] = capability
            else:
                capability = {'backend': 'local-worktree', 'os_sandbox': False}
            verified[signature] = {'operation': operation, 'command_ids': [c['id'] for c in commands],
                                   'capability': copy.deepcopy(capability)}
        runner._workspace_host_preflight_marker = marker


def qualified(engine, instance):
    return (engine.instance_path + '/' if engine.instance_path else '') + instance


def checkpoint(engine, instance, public):
    engine.root.record.setdefault('workspace_states', {})[instance] = copy.deepcopy(public)
    if engine.parent:
        engine.record.setdefault('workspace_states', {})[instance] = copy.deepcopy(public)


def acquire(engine, instance):
    manager = service(engine)
    previous = engine.root.record['workspace_states'].get(instance)
    lease, forked = manager.acquire(instance, previous)
    checkpoint(engine, instance, manager.public(lease))
    return manager, lease, forked


def output_receipts(call):
    execution = call.get('execution') or {}
    return {key: copy.deepcopy(execution[key]) for key in ('__workspace', '__artifacts') if key in execution}


async def host_action(runner, node, inputs):
    if node.get('op') in {'command', 'evaluate'}:
        await preflight_host_actions(runner)
    # A separate resource pool bounds host processes without occupying model slots.
    engine = runner.engine
    deadlines = []
    ancestor = engine
    while ancestor:
        if ancestor.runner:
            deadlines.append(ancestor.runner.graph['limits']['max_seconds'] - (time.monotonic() - ancestor.runner.run_started))
        ancestor = ancestor.parent
    remaining = min(deadlines, default=300)
    if remaining <= 0:
        raise ExecutionError('The shared workflow deadline was reached before host admission')
    try:
        async with asyncio.timeout(remaining), AsyncExitStack() as slots:
            ancestor = engine
            while ancestor.parent:
                await slots.enter_async_context(ancestor.local_host_slots)
                ancestor = ancestor.parent
            await slots.enter_async_context(engine.host_slots)
            return await _host_action(runner, node, inputs)
    except TimeoutError:
        raise ExecutionError('The shared workflow deadline stopped the host operation') from None


async def _host_action(runner, node, inputs):
    engine = runner.engine
    manager = service(engine)
    config = node.get('workspace') or {}
    op = node['op']
    selected = receipts(inputs)
    granted = {r['id'] for r in selected}
    instance = qualified(engine, config.get('instance', node['id']))
    operations = engine.root.record['host_operations']
    before = len(operations)
    if op == 'evaluate':
        if not selected:
            raise ExecutionError('Select immutable candidate artifacts through declared inputs')
        evaluated = []
        for receipt in selected:
            evaluated.append(await manager.evaluate(receipt, config.get('command_ids', []), granted=granted,
                                                     record=operations))
        output = {'evaluations': evaluated, 'status': 'passed' if all(v['status'] == 'passed' for v in evaluated) else 'failed',
                  'report': 'Host evaluation ' + ('passed' if all(v['status'] == 'passed' for v in evaluated) else 'failed')}
    elif op == 'integrate':
        # Selection is explicit dataflow. Alternatives must provide exactly one selected input.
        previous = engine.root.record['workspace_states'].get(instance)
        output = await asyncio.to_thread(manager.integrate, instance, selected, config.get('strategy', 'select'),
                                          previous=previous, granted=granted)
        checkpoint(engine, instance, output['__workspace'])
        operations.append({'kind': 'workspace-integration', 'node_id': node['id'], 'instance_id': instance,
                           **copy.deepcopy(output)})
    else:
        manager, lease, forked = await asyncio.to_thread(acquire, engine, instance)
        with manager.lock(lease['id']):
            if op == 'command':
                commands = config.get('command_ids') or []
                if not commands:
                    raise ExecutionError('Choose administrator-approved command IDs')
                results = []
                if manager.policy['command_backend'] != 'local-worktree':
                    # Immutable evaluation is the enforced backend. Never mount a private
                    # native checkout together with its credentials/runtime into Docker.
                    raise ExecutionError('Use artifact.evaluate for enforced Docker commands on immutable revisions')
                try:
                    for identity in commands:
                        results.append(await execute_command(manager.checkout(lease), manager.command(identity), runtime=operations))
                finally:
                    public, artifact = await asyncio.to_thread(manager.snapshot, lease,
                        provenance={'action': 'workspace.command', 'node_id': node['id']})
                    checkpoint(engine, instance, public)
                output = {'commands': results, 'status': 'passed' if all(r['status'] == 'passed' for r in results) else 'failed',
                          '__workspace': public, '__artifacts': [artifact], 'report': 'Host commands completed; inspect exit receipts.'}
            elif op == 'publish':
                public, artifact, retained = await asyncio.to_thread(manager.publish, lease,
                    provenance={'action': 'workspace.publish', 'node_id': node['id']})
                checkpoint(engine, instance, public)
                output = {'status': 'published', 'retained': retained, '__workspace': public, '__artifacts': [artifact],
                          'report': 'Unchanged canonical artifact retained.' if retained else 'Immutable workspace revision published.'}
                operations.append({'kind': 'workspace-publication', 'node_id': node['id'], **copy.deepcopy(output)})
            else:
                raise ExecutionError('Unknown workspace host Action')
    for operation in operations[before:]:
        operation.setdefault('node_id', node['id'])
    return output


def delivery_guard(runner):
    """An opted-in coding delivery needs resolved integration, exact tests and review."""
    record = runner.engine.root.record
    if not record.get('workspace_states'):
        return
    # Only states reached by this execution participate; superseded source candidates
    # may have failed evaluations and remain preserved without blocking a selected result.
    operations = record.get('host_operations') or []
    integrated = [o for o in operations if o.get('kind') == 'workspace-integration'
                  and (not runner.engine.instance_path or o.get('instance_id', '').startswith(runner.engine.instance_path + '/'))]
    if not integrated:
        return  # A reusable coding subgraph may intentionally return source candidates.
    latest = {}
    for operation in integrated:
        latest[operation['instance_id']] = operation
    for instance in latest:
        state = record['workspace_states'].get(instance, {})
        if state.get('pending_artifacts'):
            raise ExecutionError('Delivery blocked until every explicitly selected integration artifact is applied')
        if state.get('conflicts') or state.get('status') == 'conflict':
            raise ExecutionError('Delivery blocked by an unresolved integration conflict')
        revision = state.get('current_revision')
        if not any(o.get('kind') == 'workspace-evaluation' and o.get('candidate_revision') == revision
                   and o.get('status') == 'passed' for o in operations):
            raise ExecutionError('Delivery requires authoritative host tests of the final integration revision')
    review = runner.engine.record.get('review') or {}
    if review.get('status') != 'approved' or review.get('revision') != runner.graph['revision']:
        raise ExecutionError('Integrated code delivery requires a completed independent review of this graph revision')
    review_node = next((n for n in runner.graph['nodes'] if n['id'] == review.get('node_id') and n['op'] == 'review'), None)
    if review_node is None:
        raise ExecutionError('Integrated code delivery requires an independent review node')
    reviewed = []
    def collect(value):
        if isinstance(value, dict):
            if value.get('kind') == 'workspace-evaluation':
                reviewed.append(value)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    collect({name: runner.resolve(ref) for name, ref in review_node['inputs'].items()})
    required_revisions = {record['workspace_states'][instance]['current_revision'] for instance in latest}
    if not required_revisions <= {r.get('candidate_revision') for r in reviewed if r.get('status') == 'passed'}:
        raise ExecutionError('Independent review must receive host test receipts for every final integration revision')


def accounting(record):
    """Observable host work only; native filesystem syscall totals remain unknown."""
    commands, evaluations, artifacts = {}, {}, {}
    for operation in record.get('host_operations') or []:
        if operation.get('kind') == 'workspace-evaluation':
            evaluations[operation['id']] = operation
        for command in operation.get('commands', []) + ([operation] if operation.get('command_id') else []):
            commands[command['id']] = command
        for artifact in operation.get('__artifacts') or []:
            artifacts[artifact['id']] = artifact
    for call in record.get('calls') or []:
        for artifact in (call.get('execution') or {}).get('__artifacts') or []:
            artifacts[artifact['id']] = artifact
    intervals = [(c['started_at'], c['finished_at']) for c in commands.values()
                 if 'started_at' in c and 'finished_at' in c]
    from open_webui.execution.native_accounting import union_ms
    elapsed = union_ms(intervals, min((a for a,b in intervals), default=0), max((b for a,b in intervals), default=0))
    return {'host_commands': len(commands), 'evaluations': len(evaluations),
            'passed_evaluations': sum(e['status'] == 'passed' for e in evaluations.values()),
            'command_environment_ms': elapsed, 'command_output_bytes': sum(c.get('output_bytes',0) for c in commands.values()),
            'published_artifacts': len(artifacts), 'published_files': sum(len(a['files']) for a in artifacts.values()),
            'published_snapshot_bytes': sum(a['size'] for a in artifacts.values()),
            'native_filesystem_io_bytes': None, 'native_filesystem_io_complete': False}
