import json
import asyncio
import time
import uuid
import httpx
from open_webui.execution.claude import ExecutionError
from open_webui.execution.history_provenance import dialogue_digest


class HermesAdapter:
    def __init__(self, endpoint, key, wire_model='hermes-agent', client_factory=httpx.AsyncClient):
        self.endpoint = endpoint.rstrip('/')
        self.key = key
        self.wire_model = wire_model
        self.client_factory = client_factory
        self.context_key = None
        self.usage_session_id = None

    async def stream(self, messages):
        from open_webui.execution.hermes_usage import local_roots, snapshot, measure
        from open_webui.execution.native_accounting import accounting_usage
        roots = local_roots(self.endpoint)
        baseline = await asyncio.to_thread(snapshot, self.usage_session_id, roots)
        started_at, invocation = int(time.time() * 1000), uuid.uuid4().hex
        own, native_id = {}, None
        try:
            async with self.client_factory(trust_env=False, timeout=httpx.Timeout(120, connect=10)) as client:
                async with client.stream(
                    'POST',
                    self.endpoint + '/chat/completions',
                    headers={
                        'Authorization': 'Bearer ' + self.key,
                        **({'X-Hermes-Session-Key': self.context_key} if self.context_key else {}),
                    },
                    json={'model': self.wire_model, 'messages': messages, 'stream': True},
                ) as response:
                    if response.status_code in (401, 403):
                        raise ExecutionError('Hermes 认证失败，请检查现有连接')
                    if response.status_code != 200:
                        raise ExecutionError('Hermes 返回异常，请检查网关状态')
                    native_id = getattr(response, 'headers', {}).get('X-Hermes-Session-Id')
                    dialogue = [m for m in messages if m.get('role') in {'user', 'assistant'}]
                    metadata = {'context_mode': 'text-replay',
                                'submitted_history_messages': len(dialogue[:-1]),
                                'submitted_history_sha256': dialogue_digest(dialogue[:-1])}
                    if isinstance(native_id, str) and 0 < len(native_id) <= 512:
                        metadata['native_session_id'] = native_id
                    yield 'metadata', metadata
                    event_type = ''
                    async for line in response.aiter_lines():
                        if len(line) > 262144:
                            raise ExecutionError('Hermes 输出数据超过单帧上限')
                        if line.startswith('event:'):
                            event_type = line[6:].strip()
                            continue
                        if not line:
                            event_type = ''
                            continue
                        if not line.startswith('data:'):
                            continue
                        data = line[5:].strip()
                        if data == '[DONE]':
                            if own:
                                tree = await asyncio.to_thread(measure, native_id, own, started_at,
                                    int(time.time() * 1000), roots, baseline, invocation)
                                yield 'usage', accounting_usage(tree)
                                yield 'metadata', {'native_usage': tree}
                            return
                        event = json.loads(data)
                        if event_type == 'hermes.tool.progress':
                            yield (
                                'tool',
                                {
                                    'id': event.get('tool_call_id'),
                                    'name': str(event.get('tool_name') or event.get('tool') or 'Hermes 工具')[:80],
                                    'done': event.get('status') in {'completed', 'finished', 'success'},
                                    'status': event.get('status'),
                                    'input': event.get('arguments'),
                                    'output': event.get('result'),
                                },
                            )
                            continue
                        if event.get('error'):
                            raise ExecutionError('Hermes 执行失败')
                        for choice in event.get('choices', []):
                            if choice.get('finish_reason') in {'error', 'length', 'content_filter'}:
                                raise ExecutionError('Hermes 未能完整完成本次回答')
                            text = choice.get('delta', {}).get('content')
                            if isinstance(text, str) and text:
                                yield 'text', {'text': text}
                        if isinstance(event.get('usage'), dict):
                            own.update({k: v for k, v in event['usage'].items()
                                        if k in {'prompt_tokens', 'completion_tokens', 'total_tokens'}
                                        and type(v) is int and v >= 0})
                            yield (
                                'usage',
                                {
                                    k: v
                                    for k, v in event['usage'].items()
                                    if k in {'prompt_tokens', 'completion_tokens', 'total_tokens'}
                                    and isinstance(v, int)
                                },
                            )
                    raise ExecutionError('Hermes 输出连接中断，回答未完整结束')
        except httpx.TimeoutException:
            raise ExecutionError('Hermes 响应超时') from None
        except httpx.RequestError:
            raise ExecutionError('无法连接 Hermes 网关') from None
        except (ValueError, AttributeError, TypeError):
            raise ExecutionError('Hermes 输出格式异常') from None
