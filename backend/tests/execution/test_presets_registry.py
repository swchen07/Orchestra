import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from open_webui.execution import native_config, runtimes
from open_webui.execution.claude import ExecutionError
from open_webui.execution.codex import CodexAdapter
from open_webui.execution.dsh import DshAdapter
from open_webui.execution.api_connections import edit_connection, public_connections
from open_webui.execution.catalog import build_models
from open_webui.execution.native_config import parse_dsh_catalog
from open_webui.execution.providers import provider_catalog
from open_webui.execution.workflow_presets import complete_presets, reconcile_catalog, match_participants
from open_webui.execution.workflow_templates import (
    compile_template,
    effective_bindings,
    template_view,
    validate_template,
)


class WorkflowPresetTests(unittest.TestCase):
    def test_two_template_migration_archives_legacy_identity_without_changing_source(self):
        old = {'id': '1d6d9ffb-0e7a-47f1-bca5-5fd3af3c8668', 'name': 'Summary (example)',
               'mode': 'summarize', 'participant_ids': ['a', 'b'], 'lead_id': 'a',
               'instructions': 'Keep my shared prompt', 'version': 2}
        before = copy.deepcopy(old)
        rows, archive = reconcile_catalog([old])
        self.assertEqual([r['name'] for r in rows], ['Adaptive Modules', 'Compare'])
        self.assertEqual(old, before)
        self.assertEqual(archive[0]['id'], old['id'])
        self.assertEqual(archive[0]['instructions'], old['instructions'])
        self.assertEqual(effective_bindings(archive[0], {}), {'role_1': 'a', 'role_2': 'b'})
        self.assertEqual((rows, archive), reconcile_catalog(rows, archive))
        for row in rows:
            validate_template(template_view(row))
            self.assertTrue(all(role['instructions'] for role in row['roles']))

    def test_dynamic_adaptive_migration_archives_old_contexts_and_projects_core_defaults(self):
        old = {'id': '1d6d9ffb-0e7a-47f1-bca5-5fd3af3c8668', 'version': 7,
               'name': 'Adaptive modules · Independent review',
               'roles': [{'id': r, 'name': r} for r in ('manager', 'worker_b', 'worker_c', 'reviewer')],
               'graph': {'controller': {'binding': 'manager'}, 'reviewer': {'binding': 'reviewer'}}}
        before = copy.deepcopy(old)
        rows, archive = reconcile_catalog([old])
        self.assertNotEqual(rows[0]['id'], old['id'])
        self.assertEqual(archive[0]['roles'], old['roles'])
        self.assertEqual(archive[0]['version'], 7)
        self.assertEqual(old, before)
        bindings = effective_bindings(rows[0], {old['id']: {'manager': 'main', 'reviewer': 'check', 'worker_b': 'old-worker'}})
        self.assertEqual(bindings, {'controller': 'main', 'reviewer': 'check'})
        self.assertEqual((rows, archive), reconcile_catalog(rows, archive))

    def test_matching_is_project_scoped_deterministic_keeps_valid_defaults_and_reuses_when_needed(self):
        definition = next(r for r in complete_presets([]) if r['name'] == 'Compare')
        choices = [{'id': 'b', 'name': 'Reviewer', 'model_id': 'm2', 'framework_id': 'codex'},
                   {'id': 'a', 'name': 'Default', 'model_id': 'm1', 'framework_id': 'direct'}]
        bindings = match_participants(definition, choices, {'executor_01': 'a', 'executor_02': 'outside'}, 'm1')
        self.assertEqual(bindings['executor_01'], 'a')
        self.assertEqual(bindings['executor_02'], 'b')
        self.assertEqual(bindings, match_participants(definition, list(reversed(choices)), {'executor_01': 'a'}, 'm1'))
        reused = match_participants(definition, choices[:1], {})
        _, resolved = compile_template(definition, reused, choices[:1])
        self.assertEqual(len(resolved), 3)
        self.assertEqual(len({p['id'] for p in resolved}), 3)
        with self.assertRaises(ValueError):
            match_participants(definition, [], {})


class ApiProviderTests(unittest.TestCase):
    def test_ollama_keeps_legacy_first_provider_id_and_disabled_models_remain_manageable(self):
        full = build_models([{'id': 'local', 'owned_by': 'ollama', 'ollama': {'urls': [0]}}], {}, {}, [])
        self.assertEqual(full[0]['execution']['provider_id'], 'direct:ollama:default')
        rows = public_connections('ollama', {'ollama.base_urls': ['http://localhost:11434']})
        self.assertEqual(rows[0]['id'], 'direct:ollama:default')
        provider = provider_catalog([], {}, full)[0]
        self.assertEqual(provider['model_ids'], [])
        self.assertEqual(provider['models'], ['local'])

    def values(self):
        return {
            'openai.api_base_urls': ['https://old.example/v1', 'http://localhost:8642/v1'],
            'openai.api_keys': ['private-old', 'private-other'],
            'openai.enable': True,
            'openai.api_configs': {
                '0': {'headers': {'X-Custom': 'private-header'}, 'access_control': {'read': {'user_ids': ['owner']}}}
            },
        }

    def test_blank_key_preserves_secret_other_providers_advanced_options_and_ids(self):
        values = self.values()
        before = copy.deepcopy(values)
        form = {
            'kind': 'openai',
            'index': 0,
            'name': 'Remote',
            'url': 'https://new.example/v1',
            'key': '',
            'enabled': True,
        }
        update, index = edit_connection('openai', values, form)
        self.assertEqual(values, before)
        self.assertEqual(index, 0)
        self.assertEqual(update['openai.api_keys'], before['openai.api_keys'])
        self.assertEqual(update['openai.api_base_urls'][1], before['openai.api_base_urls'][1])
        self.assertEqual(update['openai.api_configs']['0']['headers'], before['openai.api_configs']['0']['headers'])
        self.assertEqual(
            update['openai.api_configs']['0']['access_control'], before['openai.api_configs']['0']['access_control']
        )
        rows = public_connections('openai', before | update)
        self.assertEqual([r['id'] for r in rows], ['direct:openai:0', 'hermes:openai:1'])
        self.assertTrue(rows[0]['has_key'])
        self.assertNotIn('private-', str(rows))

    def test_add_is_append_only_and_key_clear_is_explicit(self):
        form = {'kind': 'openai', 'name': 'New', 'url': 'https://new.example/v1', 'enabled': False}
        update, index = edit_connection('openai', self.values(), form)
        self.assertEqual(index, 2)
        self.assertEqual(len(update['openai.api_base_urls']), 3)
        update, _ = edit_connection('openai', self.values(), form | {'index': 0, 'clear_key': True})
        self.assertEqual(update['openai.api_keys'][0], '')
        for url in ('file:///tmp/x', 'https://secret@example.com/v1', 'https://example.com?key=secret'):
            with self.assertRaises(ValueError):
                edit_connection('openai', self.values(), form | {'url': url})


class RuntimeDiscoveryTests(unittest.TestCase):
    def test_discovery_selects_newer_candidate_but_manual_path_wins(self):
        def inspect(family, path):
            return {'path': path, 'version': '0.165.0' if path == '/new' else '0.153.4'}

        with (
            patch.object(runtimes, 'candidates', return_value=['/old', '/new']),
            patch.object(runtimes, 'inspect_path', side_effect=inspect),
        ):
            rows = runtimes.scan({})
            self.assertTrue(all(row['path'] == '/new' for row in rows))
            self.assertEqual(next(r for r in runtimes.scan({'codex': '/old'}) if r['id'] == 'codex')['path'], '/old')

    def test_missing_and_non_executable_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'not-executable'
            file.write_text('plain text')
            for path in (str(file), temp, temp + '/missing'):
                with self.assertRaises(ValueError):
                    runtimes.inspect_path('codex', path)

    def test_dsh_catalog_uses_all_groups_and_returns_only_public_model_fields(self):
        rows = parse_dsh_catalog(
            [
                {
                    'id': 'model',
                    'currentValue': '["account","flash"]',
                    'options': [
                        {
                            'group': 'account',
                            'name': 'Account',
                            'secret': 'never-public',
                            'options': [
                                {'value': '["account","flash"]', 'name': 'Flash'},
                                {'value': '["account","pro"]', 'name': 'Pro'},
                                {'value': 'malformed'},
                            ],
                        },
                        {'group': 'api', 'name': 'API', 'options': [{'value': '["api","v4-flash"]'}]},
                    ],
                },
                {'id': 'reasoning_effort', 'currentValue': 'high', 'options': [
                    {'value': 'off'}, {'value': 'low'}, {'value': 'high'}, {'value': 'max'}]},
            ]
        )
        self.assertEqual(rows[0]['models'], ['flash', 'pro'])
        self.assertEqual(rows[0]['model_details']['flash']['effort_options'], ['off', 'low', 'high', 'max'])
        self.assertEqual(rows[0]['model_details']['flash']['default_effort'], 'high')
        self.assertNotIn('effort_options', rows[0]['model_details']['pro'])
        self.assertEqual(rows[1]['models'], ['v4-flash'])
        self.assertTrue(rows[0]['current'])
        self.assertNotIn('never-public', str(rows))


class RuntimeExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_forced_refresh_waits_for_old_probe_then_reads_the_new_runtime(self):
        for family, data, lock in (
            ('codex', '_codex_models', '_codex_catalog_lock'),
            ('dsh', '_dsh_catalog', '_dsh_lock'),
        ):
            selected = '/old'
            started, release = asyncio.Event(), asyncio.Event()
            probed = []

            def transport(argv, *args, **kwargs):
                path = argv[0]
                probed.append(path)
                rpc = AsyncMock()
                rpc.__aenter__.return_value = rpc

                async def request(method, params):
                    if method == 'initialize' and path == '/old':
                        started.set()
                        await release.wait()
                    if method == 'model/list':
                        return {'data': [{'model': path}]}
                    if method == 'session/new':
                        return {
                            'sessionId': 'probe',
                            'configOptions': [
                                {
                                    'id': 'model',
                                    'options': [
                                        {'name': 'Account', 'options': [{'value': '["account","' + path + '"]'}]},
                                    ],
                                }
                            ],
                        }
                    return {}

                rpc.request.side_effect = request
                return rpc

            with (
                patch.object(native_config, 'runtime_path', side_effect=lambda name: selected),
                patch.object(native_config, data, None),
                patch.object(native_config, f'_{family}_path', None),
                patch.object(native_config, f'_{family}_checked_at', 0),
                patch.object(native_config, lock, asyncio.Lock()),
                patch('open_webui.execution.native_rpc.NativeRpc', side_effect=transport),
            ):
                refresh = getattr(native_config, f'refresh_{family}_catalog')
                first = asyncio.create_task(refresh())
                await asyncio.wait_for(started.wait(), timeout=2)
                selected = '/new'
                second = asyncio.create_task(refresh(True))
                await asyncio.sleep(0)
                release.set()
                await asyncio.gather(first, second)
                self.assertEqual(probed, ['/old', '/new'])
                self.assertEqual(getattr(native_config, f'_{family}_path'), '/new')
                self.assertIn('/new', str(getattr(native_config, data)))

    async def test_missing_registered_runtime_cannot_silently_fall_back_to_another_binary(self):
        for module, adapter in (
            ('codex', CodexAdapter('/tmp', 'model')),
            ('dsh', DshAdapter('/tmp', 'model', 'account')),
        ):
            with patch(f'open_webui.execution.{module}.runtime_path', return_value=None):
                with self.assertRaisesRegex(ExecutionError, 'runtime is unavailable'):
                    await anext(adapter.stream([{'role': 'user', 'content': 'test'}]))
