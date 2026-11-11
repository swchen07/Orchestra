"""Saved Workflows implement the same AgentModule boundary as native teams."""

import asyncio
import copy
import json
import uuid
import hashlib

from open_webui.execution.claude import ExecutionError
from open_webui.execution.modules import AgentModule, ModuleInput, ModuleScope
from open_webui.execution.port_types import compatible, validate_value


def workflow_catalog(definitions, project_bindings, participants):
    """Only active definitions with complete authorized Project role bindings."""
    from open_webui.execution.workflow_templates import compile_template, effective_bindings
    definitions = {d['id']: d for d in definitions if not d.get('archived_at')}
    native = {p['id']: p for p in participants}
    resolved, resolving = {}, set()

    def resolve(identity):
        if identity in native:
            return native[identity]
        if identity in resolved:
            return resolved[identity]
        definition = definitions.get(identity.removeprefix('workflow:'))
        if not definition or not identity.startswith('workflow:') or identity in resolving:
            raise ValueError('Unavailable or recursive Workflow module: ' + identity)
        resolving.add(identity)
        try:
            bindings = effective_bindings(definition, project_bindings)
            if not isinstance(bindings, dict):
                raise ValueError('Workflow role bindings must be an object')
            graph = definition.get('graph') or {}
            dependencies = {str(v) for v in bindings.values()}
            dependencies.update(n.get('binding') for n in graph.get('nodes', [])
                                if str(n.get('binding', '')).startswith('workflow:'))
            choices = dict(native)
            for dependency in dependencies:
                choices[dependency] = resolve(dependency)
            template, roles = compile_template(definition, bindings, list(choices.values()))
            template['graph'] = bind_workflow_ports(template['graph'], {**choices, **{b['id']: b for b in roles}})
            from open_webui.execution.graph_spec import validate_graph
            template['graph'] = validate_graph(template['graph'], set(choices) | {b['id'] for b in roles})
            descriptor = {'id': identity, 'name': definition['name'], 'framework_id': 'workflow',
                          'module_type': 'workflow', 'description': definition.get('description', ''),
                          'input_schema': template['graph']['input_schema'],
                          'output_schema': template['graph']['output_schema'],
                          '_workflow_template': template, '_workflow_bindings': roles}
            resolved[identity] = descriptor
            return descriptor
        finally:
            resolving.remove(identity)

    result = []
    for identity in definitions:
        try:
            result.append(resolve('workflow:' + identity))
        except ValueError:
            continue
    return result


def bind_workflow_ports(graph, registry):
    """Resolve implementations without changing the caller's registered Action."""
    from open_webui.execution.node_actions import resolve_action
    from open_webui.execution.module_environment import narrow_requests, resolve_environment
    value = copy.deepcopy(graph)
    for node in value['nodes']:
        selected = registry.get(node.get('binding'), {})
        operation = resolve_action(node)['operation']
        if selected.get('module_type') != 'workflow':
            if operation == 'workflow':
                raise ValueError('workflow.call requires an authorized project-bound Workflow')
            if selected:
                resolve_environment({**selected, '_environment_request': narrow_requests(
                    selected.get('_environment_request'), node.get('environment'))}, node['id'])
            continue
        if operation not in {'call', 'workflow', 'plan', 'review', 'route', 'dispatch', 'map', 'loop'}:
            raise ValueError('Host Actions cannot use a module binding')
        if '__report' in selected['output_schema'].get('properties', {}):
            raise ValueError('__report is reserved for host-owned provenance')
        from open_webui.execution.node_program import capability_schema
        protocol = capability_schema(operation) if operation in {'plan', 'review', 'dispatch'} else node.get('output_schema') if operation == 'route' else None
        if protocol and not compatible(selected['output_schema'], protocol):
            raise ValueError('Workflow public outputs are incompatible with the registered Action protocol')
        for field in ('input_schema', 'output_schema'):
            declared, actual = node.get(field), copy.deepcopy(selected[field])
            if field == 'input_schema' and operation in {'map', 'loop'} and isinstance(actual, dict):
                injected = 'item' if operation == 'map' else 'previous'
                actual.get('properties', {}).pop(injected, None)
                actual['required'] = [name for name in actual.get('required', []) if name != injected]
            if declared is None or node.get(field + '_inferred'):
                node[field] = copy.deepcopy(actual)
                node.pop(field + '_inferred', None)
            elif not (compatible(declared, actual) if field == 'input_schema' else compatible(actual, declared)):
                raise ValueError('Workflow module public port contract is incompatible with ' + field)
    return value


def preflight_workflows(engine, graph, registry):
    """Validate the reachable closure before admitting even unrelated leaf work."""
    from open_webui.execution.graph_spec import validate_graph, pointer
    from open_webui.execution.module_environment import narrow_requests, resolve_environment
    from open_webui.execution.module_invocation import invocation_schema, boundary_context
    from open_webui.execution.port_types import inferred
    reserved_history = {}
    for record in engine.previous:
        previous = record.get('plan') or record.get('template', {}).get('graph') or {}
        for node in previous.get('nodes', []):
            if node.get('op') in {'plan', 'review'}:
                reserved_history[node.get('context_key') or node.get('binding') or node['id']] = node['op']
        for field, operation in (('controller', 'plan'), ('reviewer', 'review')):
            config = previous.get(field)
            if config:
                reserved_history[config['binding']] = operation
    for node in graph['nodes']:
        key = node.get('context_key') or node['id']
        if key in reserved_history and node['op'] != reserved_history[key]:
            raise ValueError('A work node cannot borrow retained planning or review private history')
    chain, ancestor = [], engine
    while ancestor:
        chain.insert(0, ancestor.template['id'])
        ancestor = ancestor.parent

    def walk(current, choices, ancestors, inherited, invocation):
        boundary_context(current, invocation)
        for node in current['nodes']:
            selected = choices.get(node.get('binding'))
            if not selected:
                continue  # validate_graph enforces authorization of required bindings.
            environment = narrow_requests(inherited, selected.get('_environment_request'))
            environment = narrow_requests(environment, node.get('environment'))
            if selected.get('module_type') != 'workflow':
                resolve_environment({**selected, '_environment_request': environment}, node['id'])
                continue
            template = selected['_workflow_template']
            if template['id'] in ancestors or len(ancestors) >= 4:
                raise ValueError('Workflow recursion or nesting beyond four levels is not allowed')
            children = {**registry, **{b['id']: b for b in selected['_workflow_bindings']}}
            child_graph = validate_graph(bind_workflow_ports(template['graph'], children), children)
            child_context = invocation_schema(node['op'])
            child_context['properties']['inputs'] = copy.deepcopy(node.get('input_schema') or {})
            walk(child_graph, children, [*ancestors, template['id']], environment, child_context)

    walk(graph, registry, chain, {}, inferred(engine.invocation))
    # Resolve actual root/child ingress now, before scheduling another branch.
    for node in graph['nodes']:
        if node['op'] == 'input':
            inputs = {name: pointer(engine.input_values if ref['source'] == 'input' else
                                   engine.invocation if ref['source'] == 'invocation' else ref['value'],
                                   ref.get('pointer', '')) for name, ref in node['inputs'].items()}
            validate_value(inputs, node.get('output_schema'), 'Workflow input boundary')



class WorkflowModule(AgentModule):
    def __init__(self, child):
        self.child = child

    async def execute(self, request, emit):
        await self.child.execute()
        runner = self.child.runner
        # Planner-created aliases are internal. The saved public contract is
        # frozen at admission and cannot grow because the child generated work.
        names = request.output_schema.get('properties', {}) if isinstance(request.output_schema, dict) else {'answer': {}}
        outputs = {k: runner.resolve(runner.graph['outputs'][k]) for k in names}
        validate_value(outputs, request.output_schema, 'Child Workflow output')
        from open_webui.execution.module_protocol import usage_budget
        calls = self.child.record['calls']
        totals = usage_budget(calls)
        from open_webui.execution.native_accounting import add_usage
        usage = add_usage([c.get('usage') or {} for c in calls])
        usage['native_tree_total_complete'] = totals['metered_calls'] == len(calls)
        await emit('usage', usage)
        return {'choices': [{'message': {'content': json.dumps(outputs, ensure_ascii=False)}}],
                'usage': usage, 'execution': {'workflow_id': self.child.template['id'],
                'child_run_id': self.child.record['id'], 'leaf_call_ids': [c['id'] for c in calls],
                'accounting': 'rollup-only; leaf calls charged once in root ledger'}}


async def invoke_workflow(runner, node, inputs, *, suffix='', kind='work', invocation=None, dependencies=None):
    from open_webui.execution.workflows import WorkflowEngine
    selected = runner.registry.get(node['binding'], {})
    if selected.get('module_type') != 'workflow':
        raise ExecutionError('The selected Workflow is not callable in this Project')
    template = copy.deepcopy(selected['_workflow_template'])
    parent = runner.engine
    chain = []
    ancestor = parent
    while ancestor:
        chain.append(ancestor.template['id'])
        ancestor = ancestor.parent
    if template['id'] in chain or len(chain) >= 4:
        raise ExecutionError('Workflow recursion or nesting beyond four levels is not allowed')
    key = node.get('context_key') or node['id']
    if suffix:
        key = key[:35] + '_' + hashlib.sha256(suffix.encode()).hexdigest()[:20]
    path = (parent.instance_path + '/' if parent.instance_path else '') + key
    async with parent.workflow_locks.setdefault(key, asyncio.Lock()):
        previous = [r.get('child_workflows', {}).get(path) for r in parent.previous]
        current = parent.record.get('child_workflows', {}).get(path)
        previous = [r for r in [*previous, current] if r and r.get('template', {}).get('id') == template['id']]
        bindings = copy.deepcopy(selected['_workflow_bindings'])
        registry = copy.deepcopy(list(runner.registry.values()))
        from open_webui.execution.module_environment import narrow_requests, resolve_environment
        environment_request = narrow_requests(selected.get('_environment_request'), node.get('environment'))
        for b in [*bindings, *registry]:
            b['_environment_request'] = narrow_requests(b.get('_environment_request'), environment_request)
            if b.get('module_type') != 'workflow':
                b['_invocation_template'] = template
            if b in bindings and b.get('module_type') != 'workflow':
                resolve_environment(b, path)
        mirrored_events = {}

        async def publish(record):
            parent.record.setdefault('child_workflows', {})[path] = copy.deepcopy(record)
            state['call_ids'] = list(dict.fromkeys([*state.get('call_ids', []), *[c['id'] for c in record['calls']]]))
            state['attempt_call_ids'] = [c['id'] for c in record['calls']]
            from open_webui.execution.trajectory import add_event
            for event in record.get('events', []):
                target = mirrored_events.get(event['seq'])
                if target is None:
                    target = add_event(parent.record, event['type'])
                    if target is None:
                        continue
                    mirrored_events[event['seq']] = target
                # Live usage/output events mutate in place. Mirror updates too,
                # without inventing a second usage row or truncating a report.
                target.update(copy.deepcopy({k:v for k,v in event.items() if k != 'seq'}))
                target.update(title=path + ' · ' + event.get('title', ''), workflow_path=event.get('workflow_path', path))
            await parent.update()

        child = WorkflowEngine(template, bindings, json.dumps(inputs, ensure_ascii=False), parent.invoke, publish,
                               previous=previous, input_values=inputs, parent=parent,
                               instance_path=path, registry=registry, policy=template.get('execution_policy', 'automatic'),
                               invocation=invocation)
        child.parent_dependencies = list(dict.fromkeys([*(runner.dependencies(node['id']) if dependencies is None else dependencies),
            *([runner.last_decision_call] if runner.last_decision_call else [])]))
        child.parent_node_id = node['id']
        child.signature_resolver = parent.signature_resolver
        child.signatures = {identity: child.signature_for(b) for identity, b in child.bindings.items()}
        saved = [r.get('workflow_contexts', {}).get(path) for r in [*parent.previous, parent.record]]
        retained = next((s for s in reversed(saved) if s and s['definition_id'] == template['id']), None)
        context_id = retained['context_id'] if retained else str(uuid.uuid4())
        parent.record.setdefault('workflow_contexts', {})[path] = {'context_id': context_id, 'definition_id': template['id']}
        scope = ModuleScope(parent.record['id'], str(uuid.uuid4()), path, context_id, template['id'])
        state = runner.states[node['id']]
        invocation_record = {'id': scope.invocation_id, 'participant_id': path, 'name': selected['name'],
            'status': 'running', 'kind': kind, 'node_id': node['id'], 'accounting': 'rollup-only'}
        state['last_invocation'] = invocation_record
        state.setdefault('invocations', []).append(invocation_record)
        state['workflow'] = {'path': path, 'workflow_id': template['id'], 'child_run_id': child.record['id']}
        async def observe(kind, payload):
            if kind == 'module.finished':
                state['module'] = copy.deepcopy(payload)
        try:
            parent.root.child_engines[path] = child
            from open_webui.execution.module_environment import ModuleEnvironment
            environment = ModuleEnvironment(path, None, 'delegated-to-children', 'delegated-to-children',
                'delegated-to-children', 'logical', 'host-graph-executor', 'none; child transports own credentials')
            result = await WorkflowModule(child).run(ModuleInput(scope, selected, [],
                environment=environment, inputs=inputs, input_schema=selected['input_schema'],
                output_schema=selected['output_schema'], invocation=invocation), observe)
            output = copy.deepcopy(result.value)
            output.setdefault('report', result.output)
            from open_webui.execution.native_accounting import union_ms
            timing = copy.deepcopy(result.timings)
            intervals = [((c.get('module') or {}).get('timings') or {}) for c in child.record['calls']]
            covered = union_ms([(t.get('started_at'), t.get('finished_at')) for t in intervals],
                               timing['started_at'], timing['finished_at'])
            timing.update(workflow_wall_ms=timing['invocation_ms'], descendant_interval_ms=covered,
                          host_and_wait_ms=max(0, timing['invocation_ms'] - covered))
            # A composite publishes a citable report, never an extra model-call
            # ledger row. Its usage references only the already charged leaves.
            envelope = {'id': parent.record['id'] + '/' + scope.invocation_id,
                'run_id': parent.record['id'], 'root_run_id': parent.root.record['id'],
                'invocation_id': scope.invocation_id, 'instance_id': path, 'context_id': scope.context_id,
                'definition_id': template['id'], 'node_id': node['id'], 'workflow_path': path,
                'action': node.get('action', 'module.call'), 'plan_revision': runner.graph['revision'],
                'sha256': hashlib.sha256(result.output.encode()).hexdigest(),
                'usage_ref': None, 'usage_refs': result.execution['leaf_call_ids'],
                'accounting': 'rollup-only', 'depends_on': child.parent_dependencies,
                'timings': timing}
            output['__report'] = envelope
            parent.record.setdefault('module_reports', []).append({'id': envelope['id'],
                'role_id': path, 'name': selected['name'], 'kind': 'composite' if kind == 'work' else kind,
                'output': result.output, 'envelope': envelope})
            invocation_record.update({'id': scope.invocation_id, 'participant_id': path,
                'name': selected['name'], 'status': 'completed', 'kind': kind,
                'node_id': node['id'], 'output': result.output, 'report_id': envelope['id'],
                'report_envelope': envelope, 'action': envelope['action'], 'accounting': 'rollup-only'})
            state['workflow'] = {'path': path, **result.execution, 'self_usage': {'prompt_tokens': 0, 'completion_tokens': 0},
                                 'usage': result.usage, 'timings': timing, 'report_id': envelope['id']}
            return output
        except BaseException as error:
            invocation_record.update(status='cancelled' if isinstance(error, asyncio.CancelledError) else 'failed',
                                     error=str(error))
            raise
        finally:
            parent.root.child_engines.pop(path, None)
            state['call_ids'] = list(dict.fromkeys([*state.get('call_ids', []), *[c['id'] for c in child.record['calls']]]))
            state['attempt_call_ids'] = [c['id'] for c in child.record['calls']]
