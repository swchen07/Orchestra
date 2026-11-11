"""Independent participant contexts and an iterative lead-controlled dispatch loop."""

import asyncio
from contextlib import AsyncExitStack
import copy
import json
import os
from pathlib import Path
import time
import uuid

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.requests import Request

from open_webui.execution.claude import ExecutionError
from open_webui.execution.modules import ChatAgentModule, ModuleInput, ModuleScope
from open_webui.execution.trajectory import add_event, clean, stamp, tool_state, has_token_usage
from open_webui.execution.prompt_context import compact_controller_snapshots
from open_webui.execution.workflow_config import valid_id
from open_webui.execution.workflow_memory import (
    claim_native_context,
    context_signature,
    load_chat_records,
    report_archive,
    restore_roles,
)

active_runs = set()
active_engines = {}


def shared_task(messages):
    """Pass readable task text to peers; do not make API models imitate a JSON transcript."""
    visible = []
    for message in messages:
        content = message.get('content', '')
        if isinstance(content, list):
            if any(not isinstance(p, dict) or p.get('type') not in {'text', 'input_text'} for p in content):
                raise ExecutionError('Workflows currently support text only')
            content = '\n'.join(p.get('text', '') for p in content)
        if message.get('role') in {'system', 'user', 'assistant'} and isinstance(content, str) and content.strip():
            visible.append({'role': message['role'], 'content': content})
    if not visible or visible[-1]['role'] != 'user':
        raise ExecutionError('A workflow needs a user task')
    task = visible[-1]['content']
    if len(visible) > 1:
        prior = '\n\n'.join(item['role'].title() + ':\n' + item['content'] for item in visible[:-1])
        task = 'Shared conversation context:\n' + prior + '\n\nCurrent task:\n' + task
    if len(task.encode()) > 256000:
        raise ExecutionError('Workflow context exceeds 250 KiB; start a new chat or reduce the input')
    return task


def scoped_participant_context(context, run_id, binding):
    """Keep Hermes's conversation fingerprint and optional long-term-memory scope independent."""
    if binding.get('framework_id') != 'hermes':
        return context, None
    key = str(uuid.uuid5(uuid.UUID(run_id), binding['id']))
    return [
        {
            'role': 'system',
            'content': f'Workflow participant context: {key}. This identifies your independent conversation.',
        },
        *context,
    ], key


def run_path(identity):
    from open_webui.env import DATA_DIR

    if not valid_id(identity):
        raise ValueError('Invalid run ID')
    root = Path(DATA_DIR) / 'execution-runs'
    root.mkdir(parents=True, exist_ok=True)
    return root / (identity + '.json')


def save_run(record):
    path = run_path(record['id'])
    temporary = path.with_suffix('.pending')
    with temporary.open('w') as stream:
        os.chmod(temporary, 0o600)
        json.dump(record, stream, ensure_ascii=False)
    os.replace(temporary, path)


def read_run(identity, user_id):
    try:
        record = json.loads(run_path(identity).read_text())
    except (OSError, ValueError):
        raise HTTPException(404, 'Workflow run not found') from None
    if record.get('user_id') != user_id:
        raise HTTPException(404, 'Workflow run not found')
    if record.get('status') in {'queued', 'running'} and identity not in active_runs:
        record = copy.deepcopy(record)
        record['status'] = 'interrupted'
        record['error'] = 'The server stopped before this run finished. It will not be replayed automatically.'
    from open_webui.execution.dsh_usage import recover_usage

    recover_usage(record)
    from open_webui.execution.claude_usage import recover_call
    for call in record.get('calls', []):
        recover_call(call)
        if call.get('module', {}).get('timings'):
            from open_webui.execution.native_accounting import module_timings
            call['module']['timings'] = module_timings(call['module']['timings'],
                (call.get('execution') or {}).get('native_usage'))
    if not record.get('module_names'):
        from open_webui.execution.module_naming import legacy_record_names
        path = run_path(identity)
        record['module_names'] = dict(legacy_record_names(str(path), path.stat().st_mtime_ns))
    return record


class WorkflowEngine:
    def __init__(
        self,
        template,
        bindings,
        task,
        invoke,
        publish,
        record=None,
        previous=(),
        signatures=None,
        policy='automatic',
        registry=(),
        input_values=None,
        parent=None,
        instance_path='',
        invocation=None,
    ):
        self.template = copy.deepcopy(template)
        self.bindings = {p['id']: copy.deepcopy(p) for p in bindings}
        self.task, self.invoke, self.publish = task, invoke, publish
        self.input_values = copy.deepcopy(input_values if input_values is not None else {'task': task})
        self.invocation = copy.deepcopy(invocation if parent and invocation is not None else {})
        self.previous = previous
        self.parent = parent
        self.root = parent.root if parent else self
        self.instance_path = instance_path
        self.workflow_locks = {}
        self.child_engines = {} if parent is None else self.root.child_engines
        restored = restore_roles(previous)
        self.restored_states = restored
        self.policy, self.registry = policy, registry
        self.runner = None
        self.role_locks = {}
        self.initialized_roles = set()
        self.role_states = {identity: restored.get(identity, {'messages': []}) for identity in self.bindings}
        self.contexts = {identity: state['messages'] for identity, state in self.role_states.items()}
        self.native_sessions = {}
        self.max_calls = 24
        self.signature_resolver = None
        self.signatures = signatures or {i: context_signature(template, b) for i, b in self.bindings.items()}
        self.prior_reports = report_archive(previous)
        self.slots = asyncio.Semaphore(2)
        self.local_slots = asyncio.Semaphore(24)
        self.host_slots = self.root.host_slots if parent else asyncio.Semaphore(2)
        self.local_host_slots = asyncio.Semaphore(24)
        self.write_lock = asyncio.Lock()
        self.record = record or {'id': str(uuid.uuid4()), 'calls': [], 'messages': [], 'status': 'queued'}
        self.record.setdefault('template', copy.deepcopy(template))
        self.record.setdefault('created_at', int(time.time()))
        if parent:
            self.record.update(parent_run_id=parent.record['id'], workflow_path=instance_path)
        self.record['continuation'] = {'previous_run_ids': [r['id'] for r in previous], 'roles': {}}
        self.record.update(trace_version=1, events=[], heartbeat_at=stamp())
        self.record['role_activity'] = {}
        self.record['participants'] = copy.deepcopy(self.record.get('participants', bindings))
        self.record['participants'] = [{k:v for k,v in b.items() if not k.startswith('_')} for b in self.record['participants']]
        from open_webui.execution.module_naming import ModuleNames
        self.module_names = ModuleNames(self.template, bindings, previous)
        self.record['module_names'] = self.module_names.labels
        for identity, binding in self.bindings.items():
            binding['name'] = self.module_name(identity)
        for participant in self.record['participants']:
            participant['name'] = self.module_name(participant['id'])
        self.last_published = 0

    def module_name(self, identity, slot=None):
        return self.module_names.name(identity, slot=slot)

    async def update(self):
        async with self.write_lock:
            self.record['updated_at'] = stamp()
            from open_webui.execution.native_accounting import invocation_counts
            self.record['execution_counts'] = invocation_counts(self.record)
            if self.root.record.get('workspace_states'):
                from open_webui.execution.workspace_runtime import accounting
                self.root.record['workspace_usage'] = accounting(self.root.record)
            self.last_published = time.monotonic()
            self.record['role_contexts'] = copy.deepcopy(self.role_states)
            await self.publish(copy.deepcopy(self.record))

    def history_manifest(self, identity):
        from open_webui.execution.history_provenance import history_manifest
        from open_webui.execution.module_protocol import report_rows

        scoped_identity = self.instance_path + '/' + identity if self.instance_path else identity
        return history_manifest(scoped_identity, self.contexts[identity], report_rows(self.record, self.prior_reports))

    async def heartbeat(self):
        while True:
            await asyncio.sleep(5)
            self.record['heartbeat_at'] = stamp()
            await self.update()

    async def awaiting(self, identity):
        self.record['role_activity'][identity] = {'status': 'waiting', 'phase': 'Waiting for reports'}
        add_event(self.record, 'agent.waiting', role_id=identity, title='Waiting for reports')
        await self.update()

    async def observe(self, identity, kind, payload, call_id=None):
        call = next(
            (c for c in reversed(self.record['calls']) if c.get('local_participant_id', c['participant_id']) == identity
             and c['status'] == 'running' and (call_id is None or c['id'] == call_id)),
            None,
        )
        if not call:
            return
        payload = clean(payload)
        for key in ('native_entry_calls', 'native_model_request_count'):
            if key in payload:
                call.setdefault('execution', {})[key] = copy.deepcopy(payload[key])
        if kind == 'module.finished':
            call['module'] = payload
            if payload.get('usage'):
                call['usage'] = {**call.get('usage', {}), **payload['usage']}
        elif kind == 'text':
            call['phase'] = 'Writing report'
            event = next(
                (
                    e
                    for e in reversed(self.record['events'])
                    if e.get('call_id') == call['id'] and e['type'] == 'model.output'
                ),
                None,
            )
            if not event:
                event = add_event(self.record, 'model.output', call=call, title='Model response', text='')
            if event:
                event['text'] = (event.get('text', '') + payload.get('text', ''))[:128000]
                event['updated_at'] = stamp()
                call['last_event_at'] = self.record['last_event_at'] = event['updated_at']
        elif kind == 'metadata':
            runtime = {k:copy.deepcopy(payload[k]) for k in ('native_session_id', 'usage_reference', 'native_tools', 'context_mode') if k in payload}
            if runtime:
                call.setdefault('execution', {}).update(runtime)
            if payload.get('environment'):
                call.setdefault('execution', {})['adapter_environment'] = copy.deepcopy(payload['environment'])
            if payload.get('contract_error'):
                call['contract_error'] = payload['contract_error']
                call['invalid_output'] = payload.get('invalid_output')
            native = payload.get('native_usage')
            if isinstance(native, dict):
                call.setdefault('execution', {})['native_usage'] = copy.deepcopy(native)
                add_event(self.record, 'native.accounted', call=call, title='Native agent accounting',
                          data={k: native.get(k) for k in ('child_count', 'complete', 'self', 'children', 'inclusive', 'timings')})
            observed = {
                k: payload[k]
                for k in ('actual_model', 'provider_name', 'framework_name', 'context_mode', 'effort',
                          'submitted_history_messages', 'submitted_history_sha256')
                if payload.get(k) is not None
            }
            merged = {**call.get('observed', {}), **observed}
            if merged == call.get('observed') and not isinstance(native, dict):
                return
            call['observed'] = merged
            add_event(
                self.record,
                'model.metadata',
                call=call,
                title='Runtime confirmed' if observed.get('actual_model') else 'Framework selected',
                data=observed,
            )
        elif kind == 'tool':
            tools = call.setdefault('tools', {})
            key = payload.get('id') or payload.get('name', 'Tool')
            prior = tools.get(key, {})
            if payload.get('name') == 'Tool' and prior.get('name'):
                payload['name'] = prior['name']
            tools[key] = {**prior, **{k: v for k, v in payload.items() if v is not None}}
            payload = tools[key]
            call['phase'] = ('Finished ' if payload.get('done') else 'Using ') + payload.get('name', 'tool')
            event_type, suffix = tool_state(payload, bool(prior))
            add_event(
                self.record,
                event_type,
                call=call,
                title=payload.get('name', 'Tool') + ' ' + suffix,
                data=payload,
            )
        elif kind == 'usage':
            payload = {**call.get('usage', {}), **payload}
            call['usage'] = payload
            # Usage notifications replace one live counter row, not thousands of duplicate events.
            event = next(
                (
                    e
                    for e in reversed(self.record['events'])
                    if e.get('call_id') == call['id'] and e['type'] == 'model.usage'
                ),
                None,
            )
            if event:
                event.update(data=payload, updated_at=stamp())
            else:
                add_event(self.record, 'model.usage', call=call, title='Token usage', data=payload)
        elif kind == 'phase':
            call['phase'] = payload.get('title', 'Running')
            add_event(self.record, 'call.phase', call=call, title=call['phase'])
        if kind != 'text' or time.monotonic() - self.last_published >= 0.5:
            await self.update()

    async def turn(self, identity, prompt, depends_on=(), kind='work', node_id=None, plan_revision=None, action_id=None,
                   port_inputs=None, input_schema=None, output_schema=None, invocation=None):
        if len(self.record['calls']) >= self.max_calls:
            raise ExecutionError(f'This workflow reached its {self.max_calls}-call limit')
        ancestor = self.parent
        while ancestor:
            if len(ancestor.record['calls']) >= ancestor.max_calls:
                raise ExecutionError('The shared parent workflow call limit was reached')
            if ancestor.runner and ancestor.runner.budget()['exceeded']:
                raise ExecutionError('The shared parent workflow token budget was reached')
            ancestor = ancestor.parent
        if self.runner and self.runner.budget()['exceeded']:
            raise ExecutionError('The reported token budget was reached; no further module call was admitted')
        record = {
            'id': str(uuid.uuid4()),
            'participant_id': self.instance_path + '/' + identity if self.instance_path else identity,
            'name': (self.instance_path + ' · ' if self.instance_path else '') + self.bindings[identity]['name'],
            **({'local_participant_id': identity, 'workflow_path': self.instance_path,
                'local_node_id': node_id, 'child_run_id': self.record['id'], 'root_run_id': self.root.record['id']} if self.parent else {}),
            'input': prompt,
            'execution': {'native_entry_calls': 0, 'native_entry_count_source': 'host-adapter-events'},
            'status': 'queued',
            'phase': 'Waiting for a call slot',
            'created_at': int(time.time()),
            'queued_at': stamp(),
            'depends_on': list(dict.fromkeys([*depends_on, *getattr(self, 'parent_dependencies', [])])),
            'kind': kind,
            'node_id': node_id,
            'plan_revision': plan_revision,
            **({'action': action_id} if action_id else {}),
            'binding': {
                k: self.bindings[identity].get(k)
                for k in (
                    'binding_id',
                    'framework_id',
                    'provider_name',
                    'model_id',
                    'model_name',
                    'profile_id',
                    'effort',
                )
            },
        }
        self.record['calls'].append(record)
        ancestor = self.parent
        while ancestor:
            ancestor.record['calls'].append(record)
            ancestor = ancestor.parent
        add_event(self.record, 'call.queued', call=record, title='Task assigned', depends_on=list(depends_on))
        await self.update()
        try:
            async with self.role_locks.setdefault(identity, asyncio.Lock()):
                return await self._turn(identity, prompt, record, port_inputs, input_schema, output_schema, invocation)
        except asyncio.CancelledError:
            if record['status'] == 'queued':
                record.update(status='cancelled', phase='Stopped before starting', finished_at=int(time.time()))
                add_event(self.record, 'call.cancelled', call=record, title='Stopped before starting')
                await self.update()
            raise

    async def _turn(self, identity, prompt, record, port_inputs=None, input_schema=None, output_schema=None, invocation=None):
        binding = self.bindings[identity]
        # A locally queued child must not occupy a root slot needed by a peer.
        async with AsyncExitStack() as slots:
            # Only real leaves acquire capacity. Acquire inner limits first so
            # a locally queued descendant cannot occupy a shared root slot.
            ancestor = self
            while ancestor.parent:
                await slots.enter_async_context(ancestor.local_slots)
                ancestor = ancestor.parent
            await slots.enter_async_context(self.slots)
            ancestor = self
            exceeded = False
            while ancestor:
                exceeded = exceeded or bool(ancestor.runner and ancestor.runner.budget()['exceeded'])
                ancestor = ancestor.parent
            if exceeded:
                record.update(status='failed', phase='Token budget reached', finished_at=int(time.time()),
                              error='Token budget reached before this queued call could start')
                add_event(self.record, 'call.failed', call=record, title='Call not admitted: token budget')
                await self.update()
                return record
            record.update(status='running', phase='Starting framework', started_at=stamp())
            self.record['role_activity'].pop(identity, None)
            add_event(self.record, 'call.started', call=record, title='Call started')
            record['report_id'] = self.record['id'] + '/' + record['id']
            state = self.role_states[identity]
            first_call = identity not in self.initialized_roles
            self.initialized_roles.add(identity)
            if not first_call and state.get('signature') != self.signatures[identity]:
                self.native_sessions.pop(identity, None)
                state.pop('usage_native_session_id', None)
                state['native_session_id'] = None
                state['scope_id'] = str(uuid.uuid4())
                add_event(
                    self.record,
                    'context.rebound',
                    call=record,
                    title='Binding changed; role dialogue retained',
                    data={'method': 'replay'},
                )
            if first_call:
                continued = bool(self.contexts[identity])
                compatible = state.get('signature') == self.signatures[identity]
                claimed = (
                    continued
                    and compatible
                    and await asyncio.to_thread(claim_native_context, state, identity, self.record['id'])
                )
                if claimed and state.get('native_session_id'):
                    self.native_sessions[identity] = state['native_session_id']
                if not claimed:
                    state['scope_id'] = str(uuid.uuid4())
                self.record['continuation']['roles'][identity] = {
                    'history_messages': len(self.contexts[identity]),
                    'source_run_id': state.get('source_run_id'),
                    'method': 'native' if identity in self.native_sessions else 'replay' if continued else 'new',
                }
                if continued:
                    add_event(
                        self.record,
                        'context.restored',
                        call=record,
                        title='Role conversation restored',
                        data=self.record['continuation']['roles'][identity],
                    )
                # Ordering dependencies never grant access to peer histories.
                # Graph bindings select data; planners/reviewers explicitly
                # assemble their own evidence. A legacy lead_id may just be
                # the first independent Compare slot, including in a child.
            state.setdefault('scope_id', str(uuid.uuid4()))
            from open_webui.execution.module_environment import resolve_environment
            environment = resolve_environment(binding, (self.instance_path + '/' if self.instance_path else '') + identity)
            record.setdefault('execution', {})['environment'] = environment.public()
            policy = (binding.get('_environment_base') or {}).get('workspace') or {}
            if policy.get('enabled') and environment.filesystem in {'workspace-write', 'read-only'}:
                from open_webui.execution.workspace_runtime import acquire, service
                try:
                    service(self)  # Initialize once before independent threaded lease acquisition.
                    manager, lease, forked = await asyncio.to_thread(acquire, self, record['participant_id'])
                except (ExecutionError, ValueError) as error:
                    record.update(status='failed', phase='Workspace not admitted', error=str(error),
                                  error_type=type(error).__name__, finished_at=int(time.time()))
                    record['execution']['native_admitted'] = False
                    add_event(self.record, 'call.failed', call=record, title='Workspace not admitted')
                    await self.update()
                    return record
                binding = {**binding, '_prepared_workspace_lease': copy.deepcopy(lease), '_prepared_workspace_forked': forked}
                record['execution']['__workspace'] = manager.public(lease)
                if forked:
                    state['scope_id'] = str(uuid.uuid4())
                    state['native_session_id'] = None
                    state.pop('usage_native_session_id', None)
                    self.native_sessions.pop(identity, None)
                    self.record['continuation']['roles'].setdefault(identity, {})['method'] = 'workspace-branch-fork'
                    add_event(self.record, 'workspace.forked', call=record,
                              title='Pinned branch forked into its own workspace', data=manager.public(lease))
            env_signature = environment.signature()
            if state.get('environment_signature') not in (None, env_signature):
                self.native_sessions.pop(identity, None)
                state.pop('usage_native_session_id', None)
                state['scope_id'] = str(uuid.uuid4())
                add_event(self.record, 'context.rebound', call=record, title='Environment changed; dialogue replayed')
            state['environment_signature'] = env_signature
            # If this invocation fails, its native process may already have advanced. Only its
            # successful transcript can be restored; never resume that uncertain native state.
            state['native_session_id'] = None
            from open_webui.execution.history_provenance import HISTORY_NOTE, dialogue_digest
            record['history'] = self.history_manifest(identity)
            if first_call and self.contexts[identity]:
                prompt += '\n\nHost role-history manifest (references/status only):\n' + HISTORY_NOTE
                prompt += '\n' + json.dumps(record['history'], ensure_ascii=False)
            record['input'] = prompt + '\n\nReference ID for this response: ' + record['report_id']
            self.record['messages'].append(
                {
                    'from': 'lead' if identity != self.template['lead_id'] else 'user',
                    'to': identity,
                    'call_id': record['id'],
                }
            )
            await self.update()
            context = [*self.contexts[identity], {'role': 'user', 'content': record['input']}]
            replay, compacted = compact_controller_snapshots(context, reviewer=record['kind'] == 'review')
            context_chars = sum(len(str(m.get('content', ''))) for m in context)
            replay_chars = sum(len(str(m.get('content', ''))) for m in replay)
            native_resume = bool(self.native_sessions.get(identity)) and binding.get('framework_id') in {'codex', 'dsh'}
            rebase_control = bool(native_resume and first_call and compacted and record['kind'] in {'decision', 'review'}
                                  and (self.template.get('graph') or {}).get('schema_version') == '2.0')
            if rebase_control:
                native_resume = False
                self.native_sessions.pop(identity, None)
                state.pop('usage_native_session_id', None)
                if identity in self.record.get('continuation', {}).get('roles', {}):
                    self.record['continuation']['roles'][identity]['method'] = 'history_replay'
            record['request_stats'] = {
                'input_chars': len(record['input']),
                'retained_context_chars': context_chars,
                'adapter_context_chars': replay_chars,
                'submitted_text_chars': len(record['input']) if native_resume else replay_chars,
                'compacted_snapshots': compacted,
                'replay_chars_removed': context_chars - replay_chars,
                'delivery': 'native_continuation' if native_resume else 'dialogue_replay',
                'replay_history_messages': len(replay) - 1,
                'replay_history_sha256': dialogue_digest(replay[:-1]),
                'native_rebased_for_compaction': rebase_control,
            }
            try:
                if len(json.dumps(context, ensure_ascii=False).encode()) > 1048576:
                    raise ExecutionError(
                        'This role has exceeded 1 MiB of retained context; start a new chat. Its history remains saved.'
                    )
                scope = ModuleScope(
                    run_id=self.record['id'], invocation_id=record['id'], instance_id=record['participant_id'],
                    context_id=state['scope_id'],
                    definition_id=binding.get('binding_id') or binding.get('model_id') or binding['id'],
                )

                async def module_event(kind, payload):
                    await self.observe(identity, kind, payload, call_id=record['id'])

                result = (await ChatAgentModule(self.invoke, self).run(
                    ModuleInput(scope, {**binding, '_usage_native_session_id': state.get('usage_native_session_id')},
                                replay, None if rebase_control else self.native_sessions.get(identity), environment,
                                port_inputs, input_schema, output_schema, invocation), module_event,
                )).completion()
                content = result['choices'][0]['message']['content']
                # APIs returning only a completed response use the same ledger rows. The event
                # timestamp is its actual receipt time; this does not invent token streaming.
                if not any(
                    e.get('call_id') == record['id'] and e['type'] == 'model.output' for e in self.record['events']
                ):
                    await self.observe(identity, 'text', {'text': content}, call_id=record['id'])
                if result.get('usage') and not any(
                    e.get('call_id') == record['id'] and e['type'] == 'model.usage' for e in self.record['events']
                ):
                    await self.observe(identity, 'usage', result['usage'], call_id=record['id'])
                execution = result.get('execution') or {}
                if execution.get('context_mode') == 'workspace-history-replay':
                    record['request_stats'].update(delivery='dialogue_replay',
                        submitted_text_chars=record['request_stats']['adapter_context_chars'])
                    self.record['continuation']['roles'].setdefault(identity, {})['method'] = 'workspace-history-replay'
                native = execution.get('native_session_id')
                if native:
                    state['usage_native_session_id'] = native
                if native and binding.get('framework_id') in {'codex', 'dsh'}:
                    self.native_sessions[identity] = native
                self.contexts[identity] = [*context, {'role': 'assistant', 'content': content}]
                state.update(
                    messages=self.contexts[identity],
                    signature=self.signatures[identity],
                    native_session_id=self.native_sessions.get(identity),
                    source_run_id=self.record['id'],
                )
                record.update(status='completed', output=content, usage={**record.get('usage', {}), **(result.get('usage') or {})}, execution={**record.get('execution', {}), **execution})
                from open_webui.execution.module_protocol import report_envelope
                record['report_envelope'] = report_envelope(record)
                record['phase'] = 'Decision ready' if record['kind'] == 'decision' else 'Report ready'
                add_event(
                    self.record,
                    'decision.completed' if record['kind'] == 'decision' else 'report.completed',
                    call=record,
                    title=record['phase'],
                    **({'report_id': record['report_id']} if record['kind'] != 'decision' else {}),
                )
                self.record['messages'].append(
                    {
                        'from': identity,
                        'to': 'lead' if identity != self.template['lead_id'] else 'user',
                        'call_id': record['id'],
                    }
                )
            except asyncio.CancelledError:
                self.native_sessions.pop(identity, None)
                state['scope_id'] = str(uuid.uuid4())
                record.update(status='cancelled')
                record['phase'] = 'Stopped'
                raise
            except Exception as error:
                self.native_sessions.pop(identity, None)
                state['scope_id'] = str(uuid.uuid4())
                # Do not copy provider exceptions (which may contain credentials) into results.
                record.update(
                    status='failed',
                    error_type=type(error).__name__,
                    http_status=getattr(error, 'status_code', None),
                    error=str(error)
                    if isinstance(error, ExecutionError)
                    else 'Participant execution failed. Check its native connection.',
                )
                record['phase'] = 'Failed'
            finally:
                record['finished_at'] = int(time.time())
                record['queue_ms'] = max(0, record['started_at'] - record['queued_at'])
                add_event(self.record, 'call.' + record['status'], call=record, title='Call ' + record['status'])
                await self.update()
            return record

    @staticmethod
    def outputs(records):
        return '\n\n'.join(
            '### '
            + row['name']
            + (' [' + row['report_id'] + ']' if row.get('report_id') else '')
            + '\n'
            + (row.get('output') or ('Execution failed: ' + row.get('error', 'Unknown error')))
            for row in records
        )

    async def collect(self, assignments):
        # Reserve a whole batch before launching it; a limit/storage error must not
        # leave sibling native calls running after the workflow has already ended.
        if len(self.record['calls']) + len(assignments) > 24:
            raise ExecutionError('This workflow reached its 24-call limit')
        tasks = [
            asyncio.create_task(self.turn(identity, prompt, depends_on)) for identity, prompt, depends_on in assignments
        ]
        try:
            return await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def signature_for(self, binding):
        if binding.get('module_type') == 'workflow':
            return context_signature(binding['_workflow_template'], binding)
        return (
            self.signature_resolver(binding) if self.signature_resolver else context_signature(self.template, binding)
        )

    async def execute(self):
        from open_webui.execution.graph_spec import template_graph
        from open_webui.execution.graph_runtime import GraphRunner

        self.record['status'] = 'running'
        add_event(self.record, 'run.started', title='Workflow started')
        heartbeat = None
        try:
            self.runner = GraphRunner(self, template_graph(self.template), self.policy, self.registry)
            self.slots = self.root.slots if self.parent else asyncio.Semaphore(self.runner.graph['limits']['max_concurrency'])
            self.local_slots = asyncio.Semaphore(self.runner.graph['limits']['max_concurrency'])
            self.host_slots = self.root.host_slots if self.parent else asyncio.Semaphore(self.runner.graph['limits']['max_concurrency'])
            self.local_host_slots = asyncio.Semaphore(self.runner.graph['limits']['max_concurrency'])
            self.max_calls = self.runner.graph['limits']['max_calls']
            await self.update()
            heartbeat = asyncio.create_task(self.heartbeat())
            answer = await self.runner.run()
            if not isinstance(answer, str) or not answer.strip():
                raise ExecutionError('The graph did not produce a final answer')
            failed = any(c['status'] == 'failed' for c in self.record['calls']) or any(
                s['status'] == 'failed' or s.get('output', {}).get('partial', False)
                for s in self.runner.states.values()
            )
            self.record.update(status='completed_with_errors' if failed else 'completed', output=answer)
            return answer
        except asyncio.CancelledError:
            self.record.update(status='cancelled', error='Workflow stopped by the user')
            raise
        except (ExecutionError, ValueError, TimeoutError) as error:
            self.record.update(status='failed', error=str(error) or 'Workflow timed out')
            raise ExecutionError(self.record['error']) from None
        except Exception:
            self.record.update(status='failed', error='Workflow execution failed. The retained run was not replayed.')
            raise ExecutionError(self.record['error']) from None
        finally:
            if heartbeat:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            self.record['finished_at'] = int(time.time())
            add_event(
                self.record, 'run.' + self.record['status'], title='Workflow ' + self.record['status'].replace('_', ' ')
            )
            await self.update()


async def workflow_models(request, user):
    """Resolve the authorized catalog even after a cold backend restart."""
    from open_webui.utils.models import get_all_models

    return await get_all_models(request, user=user)


async def generate_workflow(request, form_data, user, *, publish_observer=None):
    from open_webui.models.config import Config
    from open_webui.execution.bridge import authorize, generate_chat
    from open_webui.execution.hooks import prepare_turn
    from open_webui.execution.profiles import discover_profiles
    from open_webui.execution.workflow_config import participants
    from open_webui.execution.workflow_templates import compile_chat_template
    from open_webui.utils.chat import generate_chat_completion

    authorize(request, user)
    meta = form_data.get('metadata') or {}
    identity = meta.get('execution_workflow_id')
    from open_webui.execution.workflow_presets import resolve_workflow

    definition = await resolve_workflow(identity)
    if not definition:
        raise HTTPException(422, 'The workflow no longer exists')
    models = await workflow_models(request, user)
    project = meta.get('execution_project')
    if not project:
        raise HTTPException(422, 'Choose a Project and bind the workflow roles before running it')
    project_instructions = ''
    if project:
        from open_webui.execution.project_data import require_project

        folder = await require_project(project['id'], user, write=True)
        project_instructions = folder.data.get('system_prompt') or ''
        provider_ids = folder.data['execution_project']['provider_ids']
        models = [m for m in models if m.get('execution', {}).get('provider_id') in provider_ids]
    profiles = await asyncio.to_thread(
        discover_profiles, project.get('directory'), None, folder.data.get('execution_profiles', [])
    )
    available = participants(models, profiles, await Config.get('execution.participants') or [])
    if project and isinstance(folder.data.get('execution_participants'), list):
        available = [p for p in available if p['id'] in folder.data['execution_participants']]
    for participant in available:
        directory = project.get('directory')
        if not directory and participant['framework_id'] in {'dsh', 'codex', 'claude-code'}:
            from open_webui.execution.catalog import resolve_workspace
            directory = resolve_workspace({}, 'default')['path']
        app_resources = folder.data.get('execution_bindings') or {}
        resources = [kind + ':' + identity for kind, field in (('prompt', 'prompt_ids'), ('skill', 'skill_ids'),
            ('mcp-metadata-only', 'mcp_ids')) for identity in app_resources.get(field, [])]
        selected_profile = participant.get('profile_id') or app_resources.get('profile_id')
        if selected_profile:
            resources.append('profile:' + selected_profile)
        if project_instructions:
            resources.append('project-instructions:' + project['id'])
        if participant['framework_id'] != 'direct':
            resources.append('native-configuration:' + participant['framework_id'])
        participant['_profile_execution_mode'] = next((p.get('execution_mode') for p in profiles if p['id'] == selected_profile), None)
        participant['_environment_base'] = {'directory': directory, 'resources': resources,
            **({'workspace': project['workspace']} if project.get('workspace') else {})}
    from open_webui.execution.workflow_presets import ensure_presets
    from open_webui.execution.workflow_module import workflow_catalog
    available += workflow_catalog(await ensure_presets(), folder.data.get('execution_workflow_bindings') or {}, available)
    try:
        template, bindings = compile_chat_template(
            definition,
            folder.data.get('execution_workflow_bindings') or {},
            meta.get('execution_workflow_overrides'),
            available,
            models,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    run_id = str(uuid.uuid4())
    record = {
        'id': run_id,
        'user_id': user.id,
        'chat_id': meta.get('chat_id'),
        'message_id': meta.get('message_id'),
        'project_id': project.get('id') if project else None,
        'template': {**template, 'version': definition.get('version', 1)},
        'participants': copy.deepcopy(bindings),
        'status': 'queued',
        'created_at': int(time.time()),
        'calls': [],
        'messages': [],
    }
    queue = asyncio.Queue(maxsize=64)

    async def publish(value):
        await asyncio.to_thread(save_run, value)
        if publish_observer:
            await publish_observer(value)
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(
            {
                'event': {
                    'type': 'status',
                    'data': {
                        'action': 'workflow',
                        'done': value['status'] not in {'queued', 'running'},
                        'description': template['name'] + ' · ' + str(value['status']).replace('_', ' '),
                        'workflow': {
                            'run_id': run_id,
                            'name': template['name'],
                            'revision': (value.get('plan') or {}).get('revision'),
                            'native_session_ids': list(
                                {
                                    c.get('execution', {}).get('native_session_id')
                                    for c in value['calls']
                                    if c.get('execution', {}).get('native_session_id')
                                }
                            ),
                            'calls_usage': [
                                {
                                    'id': c['id'],
                                    'model': c.get('execution', {}).get('actual_model') or c['name'],
                                    'usage': c.get('usage') or {},
                                    'created_at': c['created_at'],
                                }
                                for c in value['calls']
                            ]
                            if value['status'] not in {'queued', 'running'}
                            else [],
                            'calls': [
                                {k: c.get(k) for k in ('id', 'name', 'participant_id', 'status')}
                                for c in value['calls']
                            ],
                        },
                    },
                }
            }
        )

    async def invoke(binding, context, native_id):
        model = request.app.state.MODELS.get(binding['model_id'])
        if not model:
            raise ExecutionError('Participant model is no longer available')
        scoped = Request({**request.scope, 'state': {}})

        async def observer(kind, payload):
            await binding['_module_emit'](kind, payload)

        scoped.state.execution_observer = observer
        if binding.get('_workspace_service') is not None:
            scoped.state.execution_workspace_service = binding['_workspace_service']
        if project_instructions:
            context = [{'role': 'system', 'content': project_instructions}, *context]
        from open_webui.execution.workflow_templates import module_system_prompt

        role_prompt = module_system_prompt(binding.get('_invocation_template') or template, binding)
        context = [{'role': 'system', 'content': role_prompt}, *context]
        context, context_key = scoped_participant_context(
            context, binding['_module_scope']['context_id'], binding
        )
        metadata = {
            'execution_project': project,
            'execution_profile_id': binding.get('profile_id'),
            'execution_native_session_id': native_id,
            'execution_workflow_id': None,
            'execution_context_key': context_key,
            'execution_effort': binding.get('effort'),
            'execution_usage_session_id': binding.get('_usage_native_session_id'),
            'execution_environment_request': binding.get('_environment_request', {}),
            'execution_module_namespace': binding['_module_scope']['instance_id'],
            'execution_workspace_lease': binding.get('_workspace_lease'),
            'execution_workspace_inputs': binding.get('_workspace_inputs', []),
            'chat_id': meta.get('chat_id'),
        }
        body = {'model': model['id'], 'messages': context, 'metadata': metadata, 'stream': False}
        if model.get('execution', {}).get('framework_id', 'direct') != 'direct' or model.get('execution', {}).get(
            'api_adapter'
        ):
            return await generate_chat(scoped, body, user, model)
        body['messages'], _ = await prepare_turn(project, 'direct', context, binding.get('profile_id'))
        await observer('phase', {'title': 'Calling model API'})
        return await generate_chat_completion(scoped, body, user, bypass_filter=True, bypass_system_prompt=True)

    previous = await load_chat_records(meta, user.id, project.get('id'), identity)
    signatures = {}
    signature_environments = {}
    for b in [*available, *bindings]:
        if b.get('module_type') == 'workflow':
            continue
        resource_messages, resource_meta = await prepare_turn(project, b['framework_id'], [], b.get('profile_id'))
        environment = {
            'project': project,
            'instructions': project_instructions,
            'role': next((r for r in template.get('roles', []) if r['id'] == b['id']), None),
            'resources': resource_messages,
            'resource_meta': resource_meta,
            'profile': next((p for p in profiles if p['id'] == b.get('profile_id')), None),
        }
        signature_environments[(b['model_id'], b.get('profile_id'))] = environment
        signatures[b['id']] = context_signature(template, b, environment)
    engine = WorkflowEngine(
        template,
        bindings,
        shared_task(form_data.get('messages', [])),
        invoke,
        publish,
        record,
        previous=previous,
        signatures=signatures,
        policy=meta.get('execution_workflow_policy') or definition.get('execution_policy', 'automatic'),
        registry=[p for p in available if p['id'] in template.get('executor_pool', [v['id'] for v in available])],
    )

    def signature_for_binding(binding):
        environment = copy.deepcopy(signature_environments.get((binding['model_id'], binding.get('profile_id')), {}))
        scoped_template = binding.get('_invocation_template') or template
        environment['role'] = next((r for r in scoped_template.get('roles', []) if r['id'] == binding['id']), None)
        return context_signature(scoped_template, binding, environment)

    engine.signature_resolver = signature_for_binding
    response_id = 'chatcmpl-' + uuid.uuid4().hex

    def chunk(**values):
        return {
            'id': response_id,
            'object': 'chat.completion.chunk',
            'created': int(time.time()),
            'model': form_data['model'],
            **values,
        }

    async def events():
        if meta.get('chat_id'):
            from open_webui.models.chats import Chats

            chat = await Chats.get_chat_by_id(meta['chat_id'])
            if chat and chat.user_id == user.id and meta.get('message_id'):
                await Chats.upsert_message_to_chat_by_id_and_message_id(
                    chat.id,
                    meta['message_id'],
                    {'meta': {'workflow_run_id': run_id}},
                    touch=False,
                )
            if chat and chat.user_id == user.id and chat.title in {'New Chat', '新对话'}:
                first = next((m.get('content') for m in form_data.get('messages', []) if m.get('role') == 'user'), '')
                if isinstance(first, str) and first.strip():
                    title = first.strip().splitlines()[0][:80]
                    await Chats.update_chat_title_by_id(chat.id, title)
                    yield {'event': {'type': 'chat:title', 'data': title}}
        active_runs.add(run_id)
        active_engines[run_id] = engine
        task = asyncio.create_task(engine.execute())
        pending = None
        try:
            while not task.done() or not queue.empty():
                pending = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait([pending, task], return_when=asyncio.FIRST_COMPLETED)
                if pending in done:
                    yield pending.result()
                else:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                    break
            text = await task
            yield chunk(choices=[{'index': 0, 'delta': {'content': text}, 'finish_reason': None}])
            usage = {'prompt_tokens': 0, 'completion_tokens': 0}
            for call in record['calls']:
                for key in usage:
                    usage[key] += (call.get('usage') or {}).get(key, 0) or 0
            usage['unmetered_calls'] = sum(not has_token_usage(c.get('usage') or {}) for c in record['calls'])
            yield chunk(choices=[], usage=usage)
            yield chunk(choices=[{'index': 0, 'delta': {}, 'finish_reason': 'stop'}])
        except ExecutionError as error:
            yield chunk(error={'message': str(error)})
        finally:
            if pending:
                pending.cancel()
            task.cancel()
            await asyncio.gather(task, *([pending] if pending else []), return_exceptions=True)
            active_runs.discard(run_id)
            active_engines.pop(run_id, None)

    if form_data.get('stream'):

        async def wire():
            async for item in events():
                yield 'data: ' + json.dumps(item, ensure_ascii=False) + '\n\n'
            yield 'data: [DONE]\n\n'

        return StreamingResponse(wire(), media_type='text/event-stream')
    text, usage = '', {}
    async for event in events():
        if event.get('error'):
            raise HTTPException(502, event['error']['message'])
        usage.update(event.get('usage', {}))
        for choice in event.get('choices', []):
            text += choice.get('delta', {}).get('content', '')
    return {
        'id': response_id,
        'object': 'chat.completion',
        'model': form_data['model'],
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}],
        'usage': usage,
        'workflow_run_id': run_id,
    }
