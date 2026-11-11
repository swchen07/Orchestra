"""The callable boundary shared by a model, a native agent, or an agent team.

Graph roles and private conversation storage belong to the caller. A module
receives one frozen invocation snapshot and returns one attributed result. No
workflow operation or framework name is interpreted here.
"""

from abc import ABC, abstractmethod
import asyncio
import copy
from dataclasses import asdict, dataclass
import time

from open_webui.execution.claude import ExecutionError
from open_webui.execution.module_environment import ModuleEnvironment


@dataclass(frozen=True)
class ModuleScope:
    run_id: str
    invocation_id: str
    instance_id: str
    context_id: str
    definition_id: str


@dataclass(frozen=True)
class ModuleInput:
    scope: ModuleScope
    binding: dict
    messages: list
    native_session_id: str | None = None
    environment: ModuleEnvironment | None = None
    inputs: dict | None = None
    input_schema: dict | bool | None = None
    output_schema: dict | bool | None = None
    invocation: dict | None = None
    workspace: dict | None = None
    artifacts: tuple = ()


@dataclass(frozen=True)
class ModuleResult:
    scope: ModuleScope
    output: str
    usage: dict
    execution: dict
    timings: dict
    value: object = None
    workspace: dict | None = None
    artifacts: tuple = ()

    def completion(self):
        """Compatibility boundary for the existing chat response protocol."""
        return {
            'choices': [{'message': {'content': self.output}}],
            'usage': copy.deepcopy(self.usage),
            'execution': copy.deepcopy(self.execution),
            'module': {'schema_version': '1.0', **asdict(self.scope), 'timings': self.timings},
        }


class AgentModule(ABC):
    async def run(self, request: ModuleInput, emit):
        # A backend may mutate its working copy, never a peer's or the caller's
        # retained dialogue/configuration. Scope remains stable for all events.
        request = copy.deepcopy(request)
        began = time.monotonic()
        started_at = int(time.time() * 1000)
        status = 'failed'
        usage = {}
        accepting_events = True
        native_tree = None

        async def observe(kind, payload):
            nonlocal native_tree
            if not accepting_events:
                return
            if kind == 'usage':
                usage.update(copy.deepcopy(payload))
            if kind == 'metadata' and isinstance(payload.get('native_usage'), dict):
                native_tree = copy.deepcopy(payload['native_usage'])
            await emit(kind, payload)

        try:
            from open_webui.execution.port_types import validate_value
            if request.input_schema is not None:
                validate_value(request.inputs, request.input_schema, 'Module input')
            response = await self.execute(request, observe)
            usage.update(copy.deepcopy(response.get('usage') or {}))
            native_tree = (response.get('execution') or {}).get('native_usage') or native_tree
            try:
                content = response['choices'][0]['message']['content']
            except (KeyError, IndexError, TypeError):
                raise ExecutionError('Module returned an incompatible result') from None
            if not isinstance(content, str) or not content.strip() or len(content.encode()) > 128000:
                raise ExecutionError('Participant output is empty or exceeds 128 KiB')
            value = {'report': content}
            if request.output_schema is not None:
                import json
                try:
                    raw = content.strip()
                    if raw.startswith('```'):
                        raw = '\n'.join(raw.splitlines()[1:-1])
                    value = json.loads(raw)
                    validate_value(value, request.output_schema, 'Module output')
                except ValueError as error:
                    await emit('metadata', {'contract_error': str(error), 'invalid_output': content})
                    raise ExecutionError('Module output violates its declared contract; original response is retained') from None
            status = 'completed'
            execution = copy.deepcopy(response.get('execution') or {})
            if request.environment:
                execution.setdefault('environment', request.environment.public())
            result = ModuleResult(
                request.scope, content, copy.deepcopy(usage),
                execution, {}, copy.deepcopy(value), execution.get('__workspace'), tuple(execution.get('__artifacts') or ()),
            )
        except asyncio.CancelledError:
            status = 'cancelled'
            raise
        finally:
            accepting_events = False
            timings = {
                'started_at': started_at,
                'finished_at': int(time.time() * 1000),
                # Includes provider admission, hooks and transport; never label
                # this as model time or add overlapping calls as run wall time.
                'invocation_ms': max(0, round((time.monotonic() - began) * 1000)),
            }
            from open_webui.execution.native_accounting import module_timings
            timings = module_timings(timings, native_tree)
            await emit('module.finished', {
                'schema_version': '1.0', **asdict(request.scope),
                'status': status, 'timings': timings, 'usage': copy.deepcopy(usage),
                **({'environment': request.environment.public()} if request.environment else {}),
            })
        return ModuleResult(result.scope, result.output, result.usage, result.execution, timings, result.value, result.workspace, result.artifacts)

    @abstractmethod
    async def execute(self, request: ModuleInput, emit):
        """Return a native-team or API result through the normalized chat bridge."""


class ChatAgentModule(AgentModule):
    """Wrap any registered chat backend without inheriting its SDK's Agent type."""

    def __init__(self, invoke, workspace_engine=None):
        self.invoke = invoke
        self.workspace_engine = workspace_engine

    async def execute(self, request, emit):
        binding = {**request.binding, '_module_scope': asdict(request.scope), '_module_emit': emit}
        if request.environment:
            binding['_module_environment'] = request.environment.public()
        policy = (binding.get('_environment_base') or {}).get('workspace') or {}
        if not policy.get('enabled') or not request.environment or request.environment.filesystem not in {'workspace-write', 'read-only'}:
            return await self.invoke(binding, request.messages, request.native_session_id)
        if self.workspace_engine is None:
            raise ExecutionError('Coding modules require a host-owned workspace lifecycle')
        from open_webui.execution.workspace_runtime import service, acquire, checkpoint
        from open_webui.execution.workspaces import receipts
        manager = service(self.workspace_engine)
        prepared = binding.pop('_prepared_workspace_lease', None)
        if prepared is not None:
            lease, forked = prepared, False  # The engine already rotated a fork's native identity.
        else:
            manager, lease, forked = await asyncio.to_thread(acquire, self.workspace_engine, request.scope.instance_id)
        with manager.lock(lease['id']):
            if forked:
                import uuid
                context_id = str(uuid.uuid4())
                binding['_module_scope']['context_id'] = context_id
                local_id = binding['id']
                state = self.workspace_engine.role_states[local_id]
                state['scope_id'] = context_id
                state['native_session_id'] = None
                state.pop('usage_native_session_id', None)
                self.workspace_engine.native_sessions.pop(local_id, None)
                await emit('metadata', {'context_mode': 'workspace-branch-fork', 'context_id': context_id})
            selected = receipts(request.inputs or {})
            shared = await asyncio.to_thread(manager.materialize_inputs, lease, selected)
            binding['_workspace_service'] = manager
            binding['_workspace_lease'] = manager.public(lease)
            binding['_workspace_inputs'] = shared
            await emit('metadata', {'__workspace': manager.public(lease), 'workspace_forked': forked})
            messages = copy.deepcopy(request.messages)
            messages[-1]['content'] += '\nHost coding workspace: ' + str(manager.checkout(lease)) + \
                ('\nWrite the real requested files here. Private input copies: ' if request.environment.filesystem == 'workspace-write' else '\nRead only: inspect this private workspace without changing files. Input copies: ') + str(shared) + \
                '\nThis is trusted local execution with separate Git trees; it is not an OS security sandbox.'
            response = None
            try:
                response = await self.invoke(binding, messages, None if forked else request.native_session_id)
                return response
            finally:
                # Preserve attempted file state even when transport/model execution failed.
                try:
                    public, artifact = await asyncio.to_thread(manager.snapshot, lease, provenance={
                        'run_id': request.scope.run_id, 'invocation_id': request.scope.invocation_id,
                        'status': 'completed' if response else 'failed'})
                    checkpoint(self.workspace_engine, request.scope.instance_id, public)
                    metadata = {'__workspace': public, '__artifacts': [artifact]}
                    if response is not None:
                        response.setdefault('execution', {}).update(metadata)
                    await emit('metadata', metadata)
                except ExecutionError as error:
                    await emit('metadata', {'workspace_error': str(error), '__workspace': manager.public(lease)})
                    if response is not None:
                        raise
