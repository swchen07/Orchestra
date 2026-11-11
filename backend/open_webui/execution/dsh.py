"""DeepSeek Harness ACP execution with exact provider/model selection."""

import asyncio
import json
from pathlib import Path
from open_webui.execution.runtimes import runtime_path
import tempfile
import uuid

import yaml

from open_webui.execution.claude import ExecutionError, conversation_prompt
from open_webui.execution.native_rpc import NativeRpc, native_environment
from open_webui.execution.dsh_config import DshDumper, preset_patch
from open_webui.execution.dsh_usage import read_native_tree, read_interrupted_usage


async def configure_effort(rpc, session_id, options, requested):
    option = next((o for o in options if o.get('id') == 'reasoning_effort'), {})
    if requested is not None:
        if requested not in [o.get('value') for o in option.get('options', [])]:
            raise ExecutionError('DSH does not advertise the selected reasoning effort for this model')
        if option.get('currentValue') != requested:
            result = await rpc.request('session/set_config_option',
                {'sessionId': session_id, 'configId': 'reasoning_effort', 'value': requested})
            option = next((o for o in result.get('configOptions', []) if o.get('id') == 'reasoning_effort'), {})
        if option.get('currentValue') != requested:
            raise ExecutionError('DSH did not confirm the selected reasoning effort')
    return option.get('currentValue') or None


class DshAdapter:
    def __init__(self, workspace, model, provider, profile=None, timeout=120, native_session_id=None):
        self.workspace, self.model, self.provider = workspace, model, provider
        self.profile, self.timeout, self.native_session_id = profile, timeout, native_session_id
        self.effort = None

    async def stream(self, messages):
        scoped_replay = False
        if getattr(self, 'runtime_directory', None) and self.native_session_id:
            root = Path(self.runtime_directory) / '.dsh/sessions'
            try:
                identity = str(uuid.UUID(self.native_session_id))
                checkpoints = list(root.glob('*/' + identity + '/session.v4.jsonl.zstd'))
                available = len(checkpoints) == 1 and checkpoints[0].is_file() and checkpoints[0].resolve().is_relative_to(root.resolve())
            except (OSError, ValueError, TypeError):
                available = False
            if not available:
                # Never resume a scoped role through a global native history.
                self.native_session_id = None
                scoped_replay = True
        usage_reference = str(uuid.uuid4())
        async def client_request(event):
            if event['method'] == 'session/request_permission':
                if getattr(self, 'coding', False):
                    options = (event.get('params') or {}).get('options', [])
                    chosen = next((o for o in options if o.get('kind') == 'allow_once'), None)
                    if chosen:
                        return {'outcome': {'outcome': 'selected', 'optionId': chosen['optionId']}}
                return {'outcome': {'outcome': 'cancelled'}}
            # No terminal/filesystem capability is advertised. DSH retains its native sandbox.
            return None

        with tempfile.TemporaryDirectory(prefix='owui-dsh-') as temporary:
            patch = Path(temporary) / 'selection.yml'
            rows = [{'id': 'acp', 'config': {'provider': self.provider, 'model': self.model}}]
            if self.profile and self.profile.get('native_name'):
                rows.extend(
                    preset_patch(self.profile['native_name'], Path(__file__).with_name('dsh_preset_bridge.mjs'))
                )
            if getattr(self, 'runtime_directory', None):
                rows.append({'id': 'session-persistence-jsonl', 'config': {'root': str(Path(self.runtime_directory) / '.dsh/sessions')}})
            patch.write_text(yaml.dump(rows, Dumper=DshDumper, sort_keys=False))
            executable = runtime_path('dsh')
            if not executable:
                raise ExecutionError('DSH runtime is unavailable; check Connections')
            argv = [executable, '--profile', 'acp', '--patch', str(patch)]
            env = {**native_environment(), **({'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'} if getattr(self, 'runtime_directory', None) else {}), 'DSH_PERMISSION_MODE': 'workspace-write' if getattr(self, 'coding', False) else 'read-only'}
            session_id = None
            try:
                async with (
                    asyncio.timeout(self.timeout),
                    NativeRpc(argv, self.workspace, env, client_request, (b'[llm-deepseek-account]',)) as rpc,
                ):
                    yield 'phase', {'title': 'Native DSH process started', 'native_entry_calls': 1}
                    await rpc.request(
                        'initialize',
                        {
                            'protocolVersion': 1,
                            'clientInfo': {'name': 'open-webui', 'version': '0.1.0'},
                            'clientCapabilities': {},
                        },
                    )
                    params = {'cwd': self.workspace, 'mcpServers': []}
                    if self.native_session_id:
                        params['sessionId'] = self.native_session_id
                    response = await rpc.request('session/resume' if self.native_session_id else 'session/new', params)
                    session_id = self.native_session_id or response['sessionId']
                    model_option = next((o for o in response.get('configOptions', []) if o.get('id') == 'model'), {})
                    selected = model_option.get('currentValue')
                    expected = json.dumps([self.provider, self.model], separators=(',', ':'))
                    if selected != expected:
                        response = await rpc.request(
                            'session/set_config_option',
                            {'sessionId': session_id, 'configId': 'model', 'value': expected},
                        )
                        selected = next(
                            (
                                o.get('currentValue')
                                for o in response.get('configOptions', [])
                                if o.get('id') == 'model'
                            ),
                            None,
                        )
                    if selected != expected:
                        raise ExecutionError('DSH did not confirm the selected provider and model')
                    actual_effort = await configure_effort(rpc, session_id, response.get('configOptions', []), self.effort)
                    yield (
                        'metadata',
                        {
                            'actual_model': self.model,
                            'native_session_id': session_id,
                            'context_mode': 'workspace-history-replay' if scoped_replay else 'native' if self.native_session_id else 'new-native',
                            **({'native_context_rotation': 'private-checkpoint-unavailable'} if scoped_replay else {}),
                            'observed_provider': self.provider,
                            'usage_reference': usage_reference,
                            'effort': actual_effort,
                        },
                    )
                    prompt = (
                        conversation_prompt(messages)
                        if not self.native_session_id
                        else str(messages[-1].get('content', ''))
                    )
                    prompt += '\n\nExecution usage reference: ' + usage_reference
                    result = asyncio.create_task(
                        rpc.request(
                            'session/prompt', {'sessionId': session_id, 'prompt': [{'type': 'text', 'text': prompt}]}
                        )
                    )
                    event_task = None
                    interrupted = True
                    try:
                        size = 0
                        while not result.done() or not rpc.events.empty():
                            event_task = asyncio.create_task(rpc.next_event())
                            done, _ = await asyncio.wait([event_task, result], return_when=asyncio.FIRST_COMPLETED)
                            if event_task not in done:
                                event_task.cancel()
                                await asyncio.gather(event_task, return_exceptions=True)
                                break
                            event = event_task.result()
                            value = event.get('params') or {}
                            if event.get('method') != 'session/update' or value.get('sessionId') != session_id:
                                continue
                            update = value.get('update') or {}
                            kind = update.get('sessionUpdate')
                            if kind == 'agent_message_chunk' and update.get('content', {}).get('type') == 'text':
                                text = update['content'].get('text', '')
                                size += len(text.encode())
                                if size > 1024 * 1024:
                                    raise ExecutionError('DSH output exceeds 1 MiB')
                                yield 'text', {'text': text}
                            elif kind in {'tool_call', 'tool_call_update'}:
                                yield (
                                    'tool',
                                    {
                                        'id': update.get('toolCallId'),
                                        'name': str(update.get('title') or update.get('kind') or 'Tool')[:80],
                                        'done': update.get('status') in {'completed', 'failed'},
                                        'status': update.get('status'),
                                        'input': update.get('rawInput'),
                                        'output': update.get('rawOutput') or update.get('content'),
                                    },
                                )
                        final = await result
                        if final.get('stopReason') != 'end_turn':
                            raise ExecutionError(
                                'DSH turn stopped before completion (permission, cancellation or execution failure)'
                            )
                        interrupted = False
                    finally:
                        if event_task:
                            event_task.cancel()
                        result.cancel()
                        await asyncio.gather(result, *([event_task] if event_task else []), return_exceptions=True)
                        if interrupted:
                            # Give the owned runtime a bounded chance to stop its tools
                            # and flush committed accounting before process-group cleanup.
                            try:
                                async with asyncio.timeout(1):
                                    await rpc.notify('session/cancel', {'sessionId': session_id})
                                    await rpc.request('session/close', {'sessionId': session_id})
                            except (Exception, asyncio.CancelledError):
                                pass  # The original interruption still propagates below.
                    await rpc.request('session/close', {'sessionId': session_id})
                    measured = await asyncio.to_thread(
                        read_native_tree, session_id, usage_reference, self.workspace,
                        self.runtime_directory if getattr(self, 'runtime_directory', None) else None,
                    )
                    if measured:
                        yield 'usage', measured['usage']
                        yield 'metadata', {'native_metrics': measured['native_metrics'],
                                           'native_usage': measured['native_usage']}
            except (asyncio.CancelledError, TimeoutError, ExecutionError, OSError, KeyError, ValueError) as error:
                if session_id:
                    measured = await asyncio.shield(asyncio.to_thread(
                        read_interrupted_usage, session_id, usage_reference, self.workspace,
                        self.runtime_directory if getattr(self, 'runtime_directory', None) else None,
                        'cancelled' if isinstance(error, asyncio.CancelledError) else 'failed'))
                    if measured:
                        yield 'usage', measured['usage']
                        yield 'metadata', {'native_metrics': measured['native_metrics'],
                                           'native_usage': measured['native_usage']}
                if isinstance(error, asyncio.CancelledError):
                    raise
                if isinstance(error, TimeoutError):
                    raise ExecutionError('DSH timed out; its process has been stopped') from None
                if isinstance(error, ExecutionError):
                    raise
                raise ExecutionError('DSH could not start or returned an incompatible ACP response') from None
