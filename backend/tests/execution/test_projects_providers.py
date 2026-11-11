import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from open_webui.execution.providers import (
    claude_profiles,
    public_claude_providers,
    direct_profiles,
    public_direct_providers,
)
from open_webui.execution.catalog import build_models, filter_models
from open_webui.execution.projects import validate_project, resolve_project


class ProviderTests(unittest.TestCase):
    def test_multiple_profiles_common_merge_and_no_credentials_in_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = home / '.cc-switch'
            root.mkdir()
            (root / 'settings.json').write_text(json.dumps({'currentProviderClaude': 'b'}))
            with sqlite3.connect(root / 'cc-switch.db') as db:
                db.execute('PRAGMA user_version=23')
                db.execute('CREATE TABLE providers(id,name,settings_config,is_current,meta,app_type,sort_index)')
                db.execute('CREATE TABLE settings(key,value)')
                db.execute(
                    'INSERT INTO settings VALUES(?,?)',
                    (
                        'common_config_claude',
                        json.dumps({'includeCoAuthoredBy': False, 'env': {'SHARED_SETTING': 'common'}}),
                    ),
                )
                for index, identity in enumerate(['a', 'b']):
                    config = {
                        'env': {
                            'ANTHROPIC_API_KEY': 'private-test-value',
                            'ANTHROPIC_MODEL': 'same-model',
                            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'fast',
                            'SHARED_SETTING': identity,
                        }
                    }
                    db.execute(
                        'INSERT INTO providers VALUES(?,?,?,?,?,?,?)',
                        (
                            identity,
                            'Provider ' + identity,
                            json.dumps(config),
                            identity == 'a',
                            json.dumps({'commonConfigEnabled': True}),
                            'claude',
                            index,
                        ),
                    )
            profiles = claude_profiles(home)
            self.assertEqual([p['current'] for p in profiles], [False, True])
            self.assertEqual(profiles[0]['config']['env']['SHARED_SETTING'], 'a')
            self.assertFalse(profiles[0]['config']['includeCoAuthoredBy'])
            public = public_claude_providers(home)
            self.assertNotIn('private-test-value', json.dumps(public))
            self.assertNotIn('ANTHROPIC_API_KEY', json.dumps(public))
            direct = public_direct_providers(home)
            self.assertEqual([p['current'] for p in direct], [False, True])
            self.assertEqual(direct[0]['models'], ['same-model', 'fast'])
            self.assertNotIn('private-test-value', json.dumps(direct))
            self.assertNotIn('key', direct[0])
            direct_models = build_models([], {}, {'direct': {'providers': direct}}, [])
            self.assertEqual(len(direct_models), 4)
            self.assertTrue(all(m['execution']['api_adapter'] == 'anthropic' for m in direct_models))
            self.assertNotIn('private-test-value', json.dumps(direct_models))
            local = {'claude-code': {'installed': True, 'providers': public}}
            models = build_models([], {}, local, [])
            self.assertEqual(len({m['id'] for m in models}), 4)
            scoped = filter_models(
                models, {'frameworks': {'claude-code': {'disabled_models': ['claude-code:ccswitch:a::same-model']}}}
            )
            self.assertEqual(len(scoped), 3)
            self.assertTrue(
                any(
                    m['execution']['provider_id'].endswith(':b') and m['execution']['model_id'] == 'same-model'
                    for m in scoped
                )
            )

    def test_codex_api_snapshot_keeps_wire_protocol_and_does_not_reuse_oauth(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = home / '.cc-switch'
            root.mkdir()
            with sqlite3.connect(root / 'cc-switch.db') as db:
                db.execute('PRAGMA user_version=23')
                db.execute('CREATE TABLE providers(id,name,settings_config,app_type,sort_index)')
                for index, protocol in enumerate(['responses', 'chat', 'oauth']):
                    cfg = {
                        'config': 'model="test-model"\nmodel_provider="proxy"\n'
                        '[model_providers.proxy]\nbase_url="https://api.example.test/v1"\n'
                        f'wire_api="{protocol if protocol != "oauth" else "responses"}"',
                        'auth': {'OPENAI_API_KEY': 'private-api-key'}
                        if protocol != 'oauth'
                        else {'tokens': {'access_token': 'private-oauth-token'}},
                    }
                    db.execute(
                        'INSERT INTO providers VALUES(?,?,?,?,?)',
                        (protocol, protocol, json.dumps(cfg), 'codex', index),
                    )
            snapshots = direct_profiles(home)
            self.assertEqual([p['protocol'] for p in snapshots], ['responses', 'chat'])
            self.assertTrue(all(p['endpoint'] == 'https://api.example.test/v1' for p in snapshots))
            public = json.dumps(public_direct_providers(home))
            self.assertNotIn('private-api-key', public)
            self.assertNotIn('private-oauth-token', public)

    def test_provider_disable_excludes_its_models(self):
        local = {
            'claude-code': {
                'installed': True,
                'providers': [
                    {'id': 'one', 'name': 'One', 'models': ['m'], 'available': True},
                    {'id': 'two', 'name': 'Two', 'models': ['m'], 'available': True},
                ],
            }
        }
        models = build_models([], {}, local, [])
        filtered = filter_models(models, {'frameworks': {'claude-code': {'disabled_providers': ['one']}}})
        self.assertEqual([m['execution']['provider_id'] for m in filtered], ['two'])


class ProjectTests(unittest.IsolatedAsyncioTestCase):
    def model(self, provider='allowed'):
        return {'id': provider + '-model', 'execution': {'provider_id': provider, 'framework_id': 'claude-code'}}

    def test_default_model_must_belong_to_bound_provider_and_directory_is_separate(self):
        user = SimpleNamespace(role='admin')
        form = {'name': 'Writing', 'provider_ids': ['allowed'], 'default_model_id': 'other-model'}
        with self.assertRaises(ValueError):
            validate_project(form, [self.model(), self.model('other')], user)
        form['default_model_id'] = 'allowed-model'
        with tempfile.TemporaryDirectory() as directory:
            value = validate_project({**form, 'directory': directory}, [self.model()], user)
            self.assertEqual(value['data']['execution_project']['directory'], str(Path(directory).resolve()))
            self.assertEqual(value['data']['model_ids'], ['allowed-model'])
            with self.assertRaises(ValueError):
                validate_project({**form, 'directory': '/'}, [self.model()], user)
            with self.assertRaises(ValueError):
                validate_project({**form, 'directory': directory}, [self.model()], SimpleNamespace(role='user'))

    async def test_persisted_chat_project_wins_and_unbound_provider_is_rejected(self):
        folder = SimpleNamespace(
            id='project', name='Writing', data={'execution_project': {'provider_ids': ['allowed'], 'directory': None}}
        )
        folders = SimpleNamespace(Folders=SimpleNamespace(get_folder_by_id=AsyncMock(return_value=folder)))
        access = SimpleNamespace(has_folder_write_access=AsyncMock(return_value=True))
        with patch.dict(
            sys.modules, {'open_webui.models.folders': folders, 'open_webui.utils.access_control.folders': access}
        ):
            chat = SimpleNamespace(folder_id='project')
            context = await resolve_project(SimpleNamespace(id='owner'), self.model(), chat=chat)
            self.assertEqual(context['id'], 'project')
            with self.assertRaises(HTTPException):
                await resolve_project(SimpleNamespace(id='owner'), self.model('other'), chat=chat)
            with self.assertRaises(HTTPException):
                await resolve_project(SimpleNamespace(id='owner'), self.model(), folder_id='other-project', chat=chat)
            access.has_folder_write_access.return_value = False
            with self.assertRaises(HTTPException):
                await resolve_project(SimpleNamespace(id='stranger'), self.model(), chat=chat)
