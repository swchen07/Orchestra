import unittest
from open_webui.execution.catalog import build_models, validate_settings, filter_models
from open_webui.execution.catalog import is_hermes_url
from open_webui.execution.api_connections import edit_connection, public_connections


class CatalogTests(unittest.TestCase):
    def test_isolated_hermes_gateway_keeps_its_profile_model_and_loopback_boundary(self):
        config = {'framework_id': 'hermes', 'name': 'Hermes Flash',
                  'native_model': 'deepseek-v4-flash', 'native_provider': 'deepseek'}
        self.assertTrue(is_hermes_url('http://127.0.0.1:8643/v1', config))
        self.assertFalse(is_hermes_url('http://127.0.0.1:8643/v1'))
        for url in ('https://remote.example/v1', 'http://user:secret@localhost:8643/v1',
                    'http://localhost:8643/v1?token=secret'):
            self.assertFalse(is_hermes_url(url, config))
        rows = build_models(
            [{'id': 'flash.deepseek-v4-flash', 'urlIdx': 0}], {},
            {'hermes': {'model': 'gpt-default', 'provider': 'other'}},
            ['http://127.0.0.1:8643/v1'], api_configs={'0': config},
        )
        self.assertEqual(rows[0]['name'], 'deepseek-v4-flash')
        self.assertEqual(rows[0]['execution']['framework_id'], 'hermes')
        self.assertEqual(rows[0]['execution']['provider_name'], 'Hermes Flash')
        values, _ = edit_connection('openai', {}, {
            **config, 'kind': 'openai', 'url': 'http://127.0.0.1:8643/v1', 'key': 'private',
        })
        public = public_connections('openai', values)[0]
        self.assertEqual(public['framework_id'], 'hermes')
        self.assertEqual(public['native_model'], 'deepseek-v4-flash')
        self.assertNotIn('private', str(public))
        with self.assertRaises(ValueError):
            edit_connection('openai', {}, {**config, 'url': 'https://remote.example/v1'})

    def test_framework_model_separation_and_native_models(self):
        local = {
            'hermes': {'model': 'gpt-real', 'provider': 'oauth'},
            'claude-code': {'models': ['deepseek-pro', 'deepseek-flash'], 'default': 'deepseek-pro', 'installed': True},
        }
        models = build_models(
            [{'id': 'hermes-agent', 'urlIdx': 0}, {'id': 'gpt-direct', 'urlIdx': 1}],
            {},
            local,
            ['http://localhost:8642/v1', 'https://api.example/v1'],
        )
        by_id = {m['id']: m for m in models}
        self.assertEqual(by_id['hermes-agent']['name'], 'gpt-real')
        self.assertEqual(by_id['hermes-agent']['execution']['framework_id'], 'hermes')
        self.assertEqual(by_id['gpt-direct']['execution']['framework_id'], 'direct')
        claude = [m for m in models if m['execution']['framework_id'] == 'claude-code']
        self.assertEqual({m['execution']['model_id'] for m in claude}, {'deepseek-pro', 'deepseek-flash'})
        self.assertNotIn('sonnet', str(claude))
        self.assertNotIn('api_key', str(models))

    def test_disabled_models_and_frameworks_share_one_catalog(self):
        local = {'hermes': {}, 'claude-code': {'models': ['a', 'b'], 'installed': True}}
        models = build_models([], {'frameworks': {'claude-code': {'disabled_models': ['a']}}}, local, [])
        self.assertEqual([m['execution']['model_id'] for m in models], ['b'])

    def test_workspace_validation_rejects_arbitrary_roots(self):
        with self.assertRaises(ValueError):
            validate_settings({'workspaces': [{'id': 'root', 'name': 'root', 'path': '/'}]})
        with self.assertRaises(ValueError):
            validate_settings({'workspaces': [{'id': 'x', 'name': 'x', 'path': '/nonexistent-test'}]})
        with self.assertRaises(ValueError):
            validate_settings({'frameworks': {'shell': {'command': 'sh'}}})

    def test_preset_cannot_bypass_disabled_framework(self):
        models = [
            {'id': 'hermes-agent', 'execution': {'framework_id': 'hermes', 'model_id': 'gpt-real'}},
            {'id': 'writing-preset', 'execution': {'framework_id': 'hermes', 'model_id': 'gpt-real'}},
        ]
        self.assertEqual(filter_models(models, {'frameworks': {'hermes': {'enabled': False}}}), [])
