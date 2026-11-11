import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from starlette.requests import Request
from fastapi import HTTPException
from open_webui.execution import bridge


class FakeClaude:
    def __init__(self, *args):
        self.args = args

    async def stream(self, messages):
        assert messages[-1]['content'] == 'second'
        yield 'metadata', {'actual_model': 'actual-configured-model'}
        yield 'text', {'text': 'remembered'}


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_chat_stream_contract_and_workspace_ref(self):
        with tempfile.TemporaryDirectory() as folder:
            request = Request(
                {
                    'type': 'http',
                    'method': 'POST',
                    'path': '/api/chat/completions',
                    'headers': [(b'authorization', b'Bearer test')],
                }
            )
            config = {'frameworks': {}, 'workspaces': [{'id': 'project', 'name': 'Project', 'path': folder}]}
            model = {
                'id': 'execution:claude-code:configured-model',
                'execution': {
                    'framework_id': 'claude-code',
                    'framework_name': 'Claude Code',
                    'model_id': 'configured-model',
                },
            }
            form = {
                'stream': True,
                'metadata': {'execution_workspace_id': 'project'},
                'messages': [
                    {'role': 'user', 'content': 'first'},
                    {'role': 'assistant', 'content': 'ok'},
                    {'role': 'user', 'content': 'second'},
                ],
            }
            module = SimpleNamespace(Config=SimpleNamespace())
            with (
                patch.dict(sys.modules, {'open_webui.models.config': module}),
                patch.object(bridge, 'settings', AsyncMock(return_value=config)),
                patch.object(bridge, 'ClaudeAdapter', FakeClaude),
                patch.object(bridge, 'runtime_path', return_value=sys.executable),
            ):
                response = await bridge.generate_chat(request, form, SimpleNamespace(id='owner', role='admin'), model)
                chunks = [chunk async for chunk in response.body_iterator]
                wire = ''.join(chunks)
                self.assertIn('remembered', wire)
                self.assertIn('actual-configured-model', wire)
                self.assertTrue(wire.endswith('data: [DONE]\n\n'))
                form['metadata']['execution_workspace_id'] = '/etc'
                with self.assertRaises(HTTPException) as error:
                    await bridge.generate_chat(request, form, SimpleNamespace(id='owner', role='admin'), model)
                self.assertEqual(error.exception.status_code, 400)

    async def test_missing_optional_runtime_is_rejected_before_adapter_creation(self):
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/chat/completions',
                           'headers': [(b'authorization', b'Bearer test')]})
        model = {'execution': {'framework_id': 'claude-code', 'model_id': 'configured-model'}}
        with (
            patch.dict(sys.modules, {'open_webui.models.config': SimpleNamespace(Config=SimpleNamespace())}),
            patch.object(bridge, 'settings', AsyncMock(return_value={'frameworks': {}})),
            patch.object(bridge, 'runtime_path', return_value=None),
            patch.object(bridge, 'ClaudeAdapter') as adapter,
        ):
            with self.assertRaises(HTTPException) as caught:
                await bridge.generate_chat(request, {'messages': []}, SimpleNamespace(id='owner', role='admin'), model)
            self.assertEqual(caught.exception.status_code, 400)
            self.assertIn('runtime is unavailable', caught.exception.detail)
            adapter.assert_not_called()

    async def test_stale_catalog_cannot_call_disabled_hermes_connection(self):
        values = {
            'openai.api_base_urls': ['http://localhost:8642/v1'],
            'openai.api_keys': ['test-private'],
            'openai.api_configs': {'0': {'enable': False}},
            'openai.enable': True,
        }
        config_module = SimpleNamespace(Config=SimpleNamespace(get=AsyncMock(side_effect=lambda key: values.get(key))))
        request = Request(
            {
                'type': 'http',
                'method': 'POST',
                'path': '/api/chat/completions',
                'headers': [(b'authorization', b'Bearer test')],
            }
        )
        model = {
            'id': 'hermes-agent',
            'execution': {
                'framework_id': 'hermes',
                'framework_name': 'Hermes',
                'model_id': 'configured',
                'connection_id': 'openai:0',
            },
        }
        with (
            patch.dict(sys.modules, {'open_webui.models.config': config_module}),
            patch.object(bridge, 'settings', AsyncMock(return_value={'frameworks': {}})),
        ):
            with self.assertRaises(HTTPException) as caught:
                await bridge.generate_chat(request, {'messages': []}, SimpleNamespace(id='owner', role='admin'), model)
        self.assertEqual(caught.exception.status_code, 503)
