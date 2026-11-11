"""Codex app-server turns; final event and actual native thread identity required."""

import asyncio
import os
from pathlib import Path
import shutil
from open_webui.execution.runtimes import runtime_path

from open_webui.execution.claude import ExecutionError, conversation_prompt
from open_webui.execution.native_rpc import NativeRpc, native_environment


class CodexTurnUsage:
    def __init__(self, turn_id):
        self.turn_id, self.baseline = turn_id, None

    def update(self, event):
        if event.get('turnId') != self.turn_id:
            return None
        value = event.get('tokenUsage') or {}
        total, last = value.get('total') or {}, value.get('last') or {}
        keys = ('inputTokens', 'outputTokens', 'cachedInputTokens')
        if self.baseline is None:
            self.baseline = {key: max(0, total.get(key, 0) - last.get(key, 0)) for key in keys}
        counts = {key: max(0, total.get(key, 0) - self.baseline[key]) for key in keys}
        return {
            'prompt_tokens': counts['inputTokens'],
            'completion_tokens': counts['outputTokens'],
            'cache_read_tokens': counts['cachedInputTokens'],
            'fresh_input_tokens': max(0, counts['inputTokens'] - counts['cachedInputTokens']),
        }


class CodexAdapter:
    def __init__(self, workspace, model, provider='openai', profile=None, timeout=120, native_session_id=None):
        self.workspace, self.model, self.provider = workspace, model, provider
        self.profile, self.timeout, self.native_session_id = profile, timeout, native_session_id
        self.effort = None

    async def stream(self, messages):
        async def server_request(event):
            method = event['method']
            if method in {'item/commandExecution/requestApproval', 'item/fileChange/requestApproval'}:
                return {'decision': 'decline'}
            if method == 'item/permissions/requestApproval':
                return {'permissions': {}, 'scope': 'turn'}
            if method == 'item/tool/requestUserInput':
                return {'answers': {}}
            return None

        try:
            async with asyncio.timeout(self.timeout):
                executable = runtime_path('codex')
                if not executable:
                    raise ExecutionError('Codex runtime is unavailable; check Connections')
                argv = [executable]
                if self.profile and self.profile.get('execution_mode') == 'native-profile':
                    argv.extend(['--profile', self.profile['native_name']])
                argv.append('app-server')
                environment = native_environment()
                if getattr(self, 'runtime_directory', None):
                    private = Path(self.runtime_directory) / '.codex'
                    private.mkdir(mode=0o700, exist_ok=True)
                    for filename in ('config.toml', 'auth.json'):
                        source = Path.home() / '.codex' / filename
                        target = private / filename
                        if not target.exists() and source.is_file():
                            shutil.copyfile(source, target)
                            os.chmod(target, 0o600)
                    environment.update(CODEX_HOME=str(private), PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
                async with NativeRpc(argv, self.workspace, environment, server_request) as rpc:
                    yield 'phase', {'title': 'Native Codex process started', 'native_entry_calls': 1}
                    await rpc.request(
                        'initialize',
                        {
                            'clientInfo': {'name': 'open-webui', 'version': '0.1.0'},
                            'capabilities': {'experimentalApi': True},
                        },
                    )
                    await rpc.notify('initialized')
                    # Preserve native authentication/provider routing while constraining this run.
                    cfg = (await rpc.request('config/read', {'cwd': self.workspace, 'includeLayers': False})).get(
                        'config'
                    ) or {}
                    overrides = {}
                    if self.profile:
                        overrides.update(self.profile.get('config_overrides', {}))
                    overrides.update({'features.apps': False, 'features.multi_agent': False, 'web_search': 'disabled'})
                    if self.effort:
                        overrides['model_reasoning_effort'] = self.effort
                    # Effective config includes project/profile layers. Whole maps preserve literal IDs with dots.
                    overrides['mcp_servers'] = {name: {'enabled': False} for name in (cfg.get('mcp_servers') or {})}
                    overrides['plugins'] = {name: {'enabled': False} for name in (cfg.get('plugins') or {})}
                    params = {
                        'cwd': self.workspace,
                        'model': self.model,
                        'modelProvider': self.provider,
                        'approvalPolicy': 'never' if getattr(self, 'coding', False) else 'untrusted',
                        'sandbox': 'workspace-write' if getattr(self, 'coding', False) else 'read-only',
                        'config': overrides,
                    }
                    if self.native_session_id:
                        response = await rpc.request('thread/resume', {**params, 'threadId': self.native_session_id})
                    else:
                        response = await rpc.request('thread/start', params)
                    thread_id = response['thread']['id']
                    if self.native_session_id and thread_id != self.native_session_id:
                        raise ExecutionError('Codex returned a different thread while resuming')
                    yield (
                        'metadata',
                        {
                            'actual_model': response.get('model'),
                            'native_session_id': thread_id,
                            'context_mode': 'native' if self.native_session_id else 'new-native',
                            'effort': response.get('reasoningEffort'),
                        },
                    )
                    prompt = (
                        conversation_prompt(messages)
                        if not self.native_session_id
                        else str(messages[-1].get('content', ''))
                    )
                    turn = await rpc.request(
                        'turn/start',
                        {
                            'threadId': thread_id,
                            'input': [{'type': 'text', 'text': prompt}],
                            **({'effort': self.effort} if self.effort else {}),
                        },
                    )
                    turn_id = turn['turn']['id']
                    turn_usage = CodexTurnUsage(turn_id)
                    streamed = False
                    size = 0
                    while True:
                        event = await rpc.next_event()
                        method, value = event.get('method'), event.get('params') or {}
                        if value.get('threadId') not in (None, thread_id):
                            continue
                        if method == 'item/agentMessage/delta':
                            text = value.get('delta', '')
                            size += len(text.encode())
                            if size > 1024 * 1024:
                                raise ExecutionError('Codex output exceeds 1 MiB')
                            streamed = True
                            yield 'text', {'text': text}
                        elif method in {'item/started', 'item/completed'}:
                            item = value.get('item', {})
                            if method == 'item/completed' and item.get('type') == 'agentMessage' and not streamed:
                                yield 'text', {'text': item.get('text', '')}
                            elif item.get('type') in {'commandExecution', 'fileChange', 'mcpToolCall'}:
                                yield (
                                    'tool',
                                    {
                                        'id': item.get('id'),
                                        'name': item.get('tool') or item['type'],
                                        'done': method == 'item/completed',
                                        'status': item.get('status'),
                                        'input': item.get('command') or item.get('arguments') or item.get('changes'),
                                        'output': item.get('aggregatedOutput')
                                        or item.get('result')
                                        or item.get('error'),
                                        'exit_code': item.get('exitCode'),
                                    },
                                )
                        elif method == 'thread/tokenUsage/updated':
                            usage = turn_usage.update(value)
                            if usage is not None:
                                yield 'usage', usage
                        elif method == 'turn/completed' and value.get('turn', {}).get('id') == turn_id:
                            if value['turn'].get('status') != 'completed':
                                raise ExecutionError(
                                    'Codex turn did not complete; inspect native authentication or permission settings'
                                )
                            break
        except TimeoutError:
            raise ExecutionError('Codex timed out; its process has been stopped') from None
        except (OSError, KeyError, ValueError):
            raise ExecutionError('Codex could not start or returned an incompatible protocol response') from None
