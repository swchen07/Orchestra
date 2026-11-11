import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from open_webui.execution.claude import ExecutionError
from open_webui.execution.codex import CodexTurnUsage
from open_webui.execution.native_config import codex_selection, dsh_selection
from open_webui.execution.native_rpc import NativeRpc
from open_webui.execution import native_config
from open_webui.execution.profiles import discover_profiles, public_profiles


class NativeMetadataTests(unittest.TestCase):
    def test_codex_resumed_turn_usage_excludes_old_turns_and_does_not_double_count_cache(self):
        tracker = CodexTurnUsage('current')

        def event(turn, last, total):
            return {
                'turnId': turn,
                'tokenUsage': {
                    'last': dict(zip(('inputTokens', 'outputTokens', 'cachedInputTokens'), last)),
                    'total': dict(zip(('inputTokens', 'outputTokens', 'cachedInputTokens'), total)),
                },
            }

        self.assertIsNone(tracker.update(event('previous', (100, 10, 50), (100, 10, 50))))
        first = event('current', (7, 2, 3), (107, 12, 53))
        self.assertEqual(tracker.update(first)['prompt_tokens'], 7)
        self.assertEqual(tracker.update(first)['prompt_tokens'], 7)
        second = tracker.update(event('current', (4, 1, 2), (111, 13, 55)))
        self.assertEqual(
            second, {'prompt_tokens': 11, 'completion_tokens': 3, 'cache_read_tokens': 5, 'fresh_input_tokens': 6}
        )

    def test_codex_catalog_hides_ineligible_models_and_keeps_profile_secrets_private(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home) / '.codex'
            root.mkdir()
            (root / 'config.toml').write_text('model="stale-model"\n')
            (root / 'models_cache.json').write_text(
                json.dumps(
                    {'models': [{'slug': 'eligible', 'visibility': 'list'}, {'slug': 'internal', 'visibility': 'hide'}]}
                )
            )
            (root / 'research.config.toml').write_text('model="eligible"\nsecret_key="never-public"\n')
            provider = codex_selection(home)['providers'][0]
            self.assertEqual(provider['models'], ['eligible'])
            self.assertEqual(provider['default_model'], 'eligible')
            profiles = public_profiles(discover_profiles(home=home))
            profile = next(p for p in profiles if p['id'] == 'codex-profile-v2:research')
            self.assertEqual(profile['execution_mode'], 'native-profile')
            self.assertNotIn('never-public', str(profiles))

    def test_dsh_runtime_directory_is_not_an_agent_preset_and_selection_is_metadata_only(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home) / '.dsh/profiles/desktop'
            root.mkdir(parents=True)
            (root / 'cordis.patch.yml').write_text(
                '- id: agent-default-model\n  config:\n    provider: account\n    model: exact\n    token: never-public\n'
            )
            selection = dsh_selection(home)
            self.assertEqual(selection['providers'][0]['models'], ['exact'])
            self.assertNotIn('never-public', str(selection))
            self.assertFalse(any(p['kind'] == 'runtime-profile' for p in discover_profiles(home=home)))


class NativeCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_dsh_effort_is_native_confirmed_and_unsupported_or_unconfirmed_values_fail(self):
        from open_webui.execution.dsh import configure_effort
        choices = [{'id': 'reasoning_effort', 'currentValue': 'high',
                    'options': [{'value': 'high'}, {'value': 'low'}]}]
        rpc = type('Rpc', (), {})()
        rpc.request = AsyncMock(return_value={'configOptions': [{**choices[0], 'currentValue': 'low'}]})
        self.assertEqual(await configure_effort(rpc, 'session', choices, None), 'high')
        rpc.request.assert_not_called()
        self.assertEqual(await configure_effort(rpc, 'session', choices, 'low'), 'low')
        rpc.request.assert_awaited_once_with('session/set_config_option',
            {'sessionId': 'session', 'configId': 'reasoning_effort', 'value': 'low'})
        with self.assertRaises(ExecutionError):
            await configure_effort(rpc, 'session', choices, 'invented')
        rpc.request.return_value = {'configOptions': choices}
        with self.assertRaises(ExecutionError):
            await configure_effort(rpc, 'session', choices, 'low')

    async def test_catalog_follows_pagination_includes_hidden_and_keeps_effort_metadata(self):
        rpc = AsyncMock()
        rpc.__aenter__.return_value = rpc
        rpc.request.side_effect = [
            {},
            {
                'data': [
                    {
                        'model': 'visible',
                        'hidden': False,
                        'supportedReasoningEfforts': [{'reasoningEffort': 'low'}],
                        'defaultReasoningEffort': 'low',
                    }
                ],
                'nextCursor': 'next',
            },
            {'data': [{'model': 'hidden', 'hidden': True}], 'nextCursor': None},
        ]
        with tempfile.TemporaryDirectory() as home:
            root = Path(home) / '.codex'
            root.mkdir()
            (root / 'config.toml').write_text('model="configured-not-listed"\n')
            with (
                patch.object(native_config, '_codex_models', None),
                patch.object(native_config, '_codex_checked_at', 0),
                patch.object(native_config, 'runtime_path', return_value='/codex'),
                patch.object(native_config.time, 'monotonic', return_value=1000),
                patch('open_webui.execution.native_rpc.NativeRpc', return_value=rpc),
                patch.object(native_config.Path, 'home', return_value=Path(home)),
            ):
                await native_config.refresh_codex_catalog()
                provider = native_config.codex_selection()['providers'][0]
                self.assertEqual(provider['models'], ['visible', 'hidden'])
                self.assertEqual(provider['model_details']['visible']['effort_options'], ['low'])
                self.assertTrue(provider['model_details']['hidden']['hidden'])
                self.assertEqual(provider['unlisted_models'], ['configured-not-listed'])
                calls = rpc.request.await_args_list
                self.assertEqual(calls[-1].args[1]['cursor'], 'next')
                self.assertTrue(calls[-1].args[1]['includeHidden'])


class NativeRpcTests(unittest.IsolatedAsyncioTestCase):
    async def test_bidirectional_requests_are_denied_and_native_error_content_is_not_exposed(self):
        script = """import sys,json
for line in sys.stdin:
 e=json.loads(line)
 if e.get('method')=='start':
  print(json.dumps({'jsonrpc':'2.0','id':99,'method':'approve','params':{}}),flush=True)
 elif e.get('id')==99:
  print(json.dumps({'jsonrpc':'2.0','id':1,'result':{'decision':e['result']['decision']}}),flush=True)
 elif e.get('method')=='fail':
  print(json.dumps({'jsonrpc':'2.0','id':e['id'],'error':{'code':-123,'message':'private-key'}}),flush=True)
"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'server.py'
            path.write_text(script)

            async def reject(event):
                return {'decision': 'decline'}

            async with NativeRpc([sys.executable, str(path)], folder, request_handler=reject) as rpc:
                self.assertEqual(await asyncio.wait_for(rpc.request('start'), 3), {'decision': 'decline'})
                with self.assertRaises(ExecutionError) as error:
                    await rpc.request('fail')
                self.assertIn('-123', str(error.exception))
                self.assertNotIn('private-key', str(error.exception))
            self.assertIsNotNone(rpc.process.returncode)

    async def test_only_known_bounded_dsh_diagnostics_are_accepted(self):
        for prefix, accepted in [('[llm-deepseek-account] catalog ready', True), ('unexpected protocol data', False)]:
            script = f"""import sys,json
e=json.loads(sys.stdin.readline())
print({prefix!r},flush=True)
print(json.dumps({{'jsonrpc':'2.0','id':e['id'],'result':{{'ok':True}}}}),flush=True)
sys.stdin.read()
"""
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'server.py'
                path.write_text(script)
                async with NativeRpc(
                    [sys.executable, str(path)], folder, log_prefixes=(b'[llm-deepseek-account]',)
                ) as rpc:
                    if accepted:
                        self.assertEqual(await asyncio.wait_for(rpc.request('start'), 3), {'ok': True})
                    else:
                        with self.assertRaises(ExecutionError):
                            await asyncio.wait_for(rpc.request('start'), 3)

    async def test_cancellation_cleans_up_owned_native_process(self):
        async with NativeRpc([sys.executable, '-c', 'import sys; sys.stdin.read()'], tempfile.gettempdir()) as rpc:
            task = asyncio.create_task(rpc.request('never-replies'))
            await asyncio.sleep(0.03)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(rpc.process.returncode)
