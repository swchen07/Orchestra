"""Direct API calls using private CC Switch snapshots, without launching an agent framework."""

import json
import httpx

from open_webui.execution.claude import ExecutionError


class DirectAdapter:
    def __init__(self, provider, model, client_factory=httpx.AsyncClient):
        self.provider, self.model, self.client_factory = provider, model, client_factory

    async def stream(self, messages):
        clean = []
        for row in messages:
            value = row.get('content', '')
            if not isinstance(value, str):
                raise ExecutionError('This API provider currently supports text only')
            if row.get('role') in {'system', 'user', 'assistant'} and value.strip():
                clean.append({'role': row['role'], 'content': value})
        if len(json.dumps(clean).encode()) > 512000:
            raise ExecutionError('API context exceeds 500 KiB')
        protocol = self.provider['protocol']
        headers = {'Authorization': 'Bearer ' + self.provider['key']}
        body = {'model': self.model, 'stream': True}
        if protocol == 'anthropic':
            headers.update({'x-api-key': self.provider['key'], 'anthropic-version': '2023-06-01'})
            body.update(messages=[m for m in clean if m['role'] != 'system'], max_tokens=4096)
            system = '\n\n'.join(m['content'] for m in clean if m['role'] == 'system')
            if system:
                body['system'] = system
            suffix = '/messages'
        elif protocol == 'responses':
            body['input'] = clean
            suffix = '/responses'
        else:
            body.update(messages=clean, stream_options={'include_usage': True})
            suffix = '/chat/completions'
        endpoint = self.provider['endpoint'].rstrip('/')
        if protocol == 'anthropic' and not endpoint.endswith('/v1'):
            endpoint += '/v1'
        terminal, size, observed = False, 0, None
        usage = {}
        try:
            async with self.client_factory(trust_env=False, timeout=httpx.Timeout(120, connect=10)) as client:
                async with client.stream('POST', endpoint + suffix, headers=headers, json=body) as response:
                    if response.status_code != 200:
                        raise ExecutionError(
                            f'API provider rejected the request (HTTP {response.status_code}); check its connection and model'
                        )
                    async for line in response.aiter_lines():
                        if len(line) > 262144:
                            raise ExecutionError('API event exceeds 256 KiB')
                        if not line.startswith('data:'):
                            continue
                        raw = line[5:].strip()
                        if raw == '[DONE]':
                            if not terminal:
                                raise ExecutionError('API ended without a completion signal')
                            return
                        event = json.loads(raw)
                        if event.get('error') or event.get('type') in {
                            'error',
                            'response.failed',
                            'response.incomplete',
                        }:
                            raise ExecutionError('API execution failed; no provider diagnostics were exposed')
                        text = ''
                        native = event.get('message', {}) if protocol == 'anthropic' else event.get('response', event)
                        if native.get('model') and native['model'] != observed:
                            observed = native['model']
                            yield 'metadata', {'actual_model': observed, 'protocol': protocol}
                        if protocol == 'anthropic':
                            kind = event.get('type')
                            if kind == 'content_block_delta' and event.get('delta', {}).get('type') == 'text_delta':
                                text = event['delta'].get('text', '')
                            reported = native.get('usage') if kind == 'message_start' else event.get('usage')
                            if isinstance(reported, dict):
                                usage.update(reported)
                                yield (
                                    'usage',
                                    {
                                        'prompt_tokens': usage.get('input_tokens', 0)
                                        + usage.get('cache_read_input_tokens', 0)
                                        + usage.get('cache_creation_input_tokens', 0),
                                        'completion_tokens': usage.get('output_tokens', 0),
                                        'fresh_input_tokens': usage.get('input_tokens', 0),
                                        'cache_read_tokens': usage.get('cache_read_input_tokens', 0),
                                        'cache_write_tokens': usage.get('cache_creation_input_tokens', 0),
                                    },
                                )
                            if kind == 'message_delta' and event.get('delta', {}).get('stop_reason') not in (
                                None,
                                'end_turn',
                                'stop_sequence',
                            ):
                                raise ExecutionError('API response stopped before completion')
                            if kind == 'message_delta' and event.get('delta', {}).get('stop_reason') in {
                                'end_turn',
                                'stop_sequence',
                            }:
                                terminal = True
                            if kind == 'message_stop':
                                if not terminal:
                                    raise ExecutionError('API ended without a completion signal')
                                return
                        elif protocol == 'responses':
                            kind = event.get('type')
                            if kind == 'response.output_text.delta':
                                text = event.get('delta', '')
                            if kind == 'response.completed':
                                value = native.get('usage') or {}
                                if value:
                                    cached = (value.get('input_tokens_details') or {}).get('cached_tokens', 0)
                                    yield (
                                        'usage',
                                        {
                                            'prompt_tokens': value.get('input_tokens', 0),
                                            'completion_tokens': value.get('output_tokens', 0),
                                            'cache_read_tokens': cached,
                                            'fresh_input_tokens': max(0, value.get('input_tokens', 0) - cached),
                                        },
                                    )
                                return
                        else:
                            for choice in event.get('choices', []):
                                text += choice.get('delta', {}).get('content') or ''
                                if choice.get('finish_reason'):
                                    if choice['finish_reason'] != 'stop':
                                        raise ExecutionError('API response stopped before completion')
                                    terminal = True
                            if isinstance(event.get('usage'), dict):
                                yield (
                                    'usage',
                                    {
                                        k: v
                                        for k, v in event['usage'].items()
                                        if k in {'prompt_tokens', 'completion_tokens', 'total_tokens'}
                                        and isinstance(v, int)
                                    },
                                )
                        if text:
                            size += len(text.encode())
                            if size > 1024 * 1024:
                                raise ExecutionError('API output exceeds 1 MiB')
                            yield 'text', {'text': text}
                    raise ExecutionError('API stream disconnected before completion')
        except httpx.HTTPError:
            raise ExecutionError('API connection failed or timed out') from None
        except (ValueError, TypeError, AttributeError):
            raise ExecutionError('API returned an incompatible response') from None
