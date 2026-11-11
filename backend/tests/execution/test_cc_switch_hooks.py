import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from open_webui.integrations.cc_switch.adapter import CcSwitchAdapter, contract, open_database, integration_status
from open_webui.execution.project_data import usage_summary
from open_webui.execution.hooks import validate_bindings
from open_webui.execution.profiles import discover_profiles, public_profiles
from open_webui.execution.topology import validate_topology, visible_runs


class DataHooksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / '.cc-switch').mkdir()
        self.path = self.home / '.cc-switch/cc-switch.db'
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""PRAGMA user_version=23;
            CREATE TABLE skills(id,name,description,directory,enabled_claude,enabled_codex,enabled_hermes);
            CREATE TABLE prompts(id,name,description,app_type,enabled,content);
            CREATE TABLE mcp_servers(id,name,description,server_config,enabled_claude,enabled_codex,enabled_hermes);
            CREATE TABLE profiles(id,name,sort_order);
            CREATE TABLE session_log_sync(file_path,last_synced_at);
            CREATE TABLE proxy_request_logs(request_id,app_type,model,input_tokens,output_tokens,cache_read_tokens,cache_creation_tokens,total_cost_usd,status_code,session_id,created_at,data_source,input_token_semantics);
            """)
            db.execute(
                'INSERT INTO skills VALUES(?,?,?,?,?,?,?)',
                ('skill', 'Test skill', 'Description', 'test-skill', 1, 0, 1),
            )
            db.execute(
                'INSERT INTO prompts VALUES(?,?,?,?,?,?)', ('prompt', 'Prompt', 'Prompt', 'claude', 1, 'Use facts.')
            )
            db.execute(
                'INSERT INTO mcp_servers VALUES(?,?,?,?,?,?,?)',
                (
                    'mcp',
                    'Tool',
                    'Tool',
                    json.dumps({'command': 'tool', 'args': ['private-argument'], 'env': {'TOKEN': 'private-value'}}),
                    1,
                    0,
                    0,
                ),
            )
        root = self.home / '.cc-switch/skills/test-skill'
        root.mkdir(parents=True)
        (root / 'SKILL.md').write_text('Use facts from the project.')
        self.adapter = CcSwitchAdapter(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def session(self, identity, directory):
        root = self.home / '.codex/sessions'
        root.mkdir(parents=True, exist_ok=True)
        path = root / (identity + '.jsonl')
        path.write_text(
            '\n'.join(
                json.dumps(v)
                for v in [
                    {'type': 'session_meta', 'payload': {'id': identity, 'cwd': str(directory)}},
                    {
                        'type': 'response_item',
                        'payload': {
                            'type': 'message',
                            'role': 'assistant',
                            'channel': 'analysis',
                            'content': [{'type': 'output_text', 'text': 'Internal trace excluded'}],
                        },
                    },
                    {
                        'type': 'response_item',
                        'payload': {
                            'type': 'message',
                            'role': 'user',
                            'content': [{'type': 'input_text', 'text': 'A task'}],
                        },
                    },
                    {
                        'type': 'response_item',
                        'payload': {
                            'type': 'message',
                            'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': 'A result'}],
                        },
                    },
                ]
            )
        )
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO session_log_sync VALUES(?,?)', (str(path), 1))
        return path

    def test_contract_is_pinned_and_connection_cannot_write(self):
        self.assertEqual(contract()['upstream']['commit'], 'c4db63c60d2a1ddcb4bd9f81152b9c6126413659')
        self.assertTrue(integration_status(self.home)['compatible'])
        with open_database(self.home) as db, self.assertRaises(sqlite3.OperationalError):
            db.execute('DELETE FROM skills')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA user_version=24')
        self.assertFalse(integration_status(self.home)['compatible'])
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 24)

    def test_resource_catalog_redacts_mcp_credentials_and_enforces_skill_paths(self):
        resources = self.adapter.resources()
        self.assertNotIn('private-value', str(resources))
        self.assertNotIn('private-argument', str(resources))
        self.assertFalse(resources['mcp'][0]['executable'])
        self.assertEqual(self.adapter.resource_text('cc-prompt:prompt', 'claude-code'), 'Use facts.')
        self.assertEqual(self.adapter.resource_text('cc-prompt:prompt', 'hermes'), '')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE skills SET directory='../../.claude'")
        with self.assertRaises(ValueError):
            self.adapter.resource_text('cc-skill:skill')
        with self.assertRaises(ValueError):
            validate_bindings({'skill_ids': ['missing']}, resources, [])

    def test_native_history_is_scoped_to_exact_project_directory(self):
        project = self.home / 'project'
        project.mkdir()
        other = self.home / 'other'
        other.mkdir()
        self.session('own', project)
        self.session('foreign', other)
        rows = self.adapter.sessions(project)
        self.assertEqual([r['session_id'] for r in rows], ['own'])
        result = self.adapter.session(rows[0]['id'], project)
        self.assertEqual([m['content'] for m in result['messages']], ['A task', 'A result'])
        with self.assertRaises(ValueError):
            self.adapter.session(rows[0]['id'], other)
        self.assertNotIn('_path', result['session'])

    def test_upstream_cross_source_usage_dedup_and_unknown_cost(self):
        project = self.home / 'project'
        project.mkdir()
        self.session('own', project)
        with closing(sqlite3.connect(self.path)) as db, db:
            for identity, source, cost in [('proxy', 'proxy', '0.12'), ('session', 'codex_session', '0.12')]:
                db.execute(
                    'INSERT INTO proxy_request_logs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (identity, 'codex', 'model', 10, 3, 4, 2, cost, 200, 'own', 100, source, 2),
                )
        records = self.adapter.usage(project)['records']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['id'], 'proxy')
        self.assertEqual(self.adapter.usage(project, ['own'])['records'], [])
        summary = usage_summary([*records, {'id': 'missing', 'input_tokens': 0, 'cost_usd': None}])
        self.assertEqual(summary['total_tokens'], 19)
        self.assertEqual(summary['known_cost_usd'], '0.12')
        self.assertEqual(summary['unknown_cost_records'], 1)

    def test_native_profiles_do_not_expose_credentials_or_conflate_roles(self):
        root = self.home / '.codex'
        root.mkdir()
        (root / 'config.toml').write_text('[profiles.research]\nmodel="model"\napi_key="secret-profile-value"\n')
        profiles = public_profiles(discover_profiles(home=self.home))
        self.assertNotIn('secret-profile-value', str(profiles))
        profile = next(p for p in profiles if p['id'] == 'codex-profile:research')
        self.assertTrue(profile['available'])
        self.assertEqual(profile['execution_mode'], 'config-overlay')
        self.assertEqual(profile['kind'], 'native-profile')


class TopologyValidationTests(unittest.TestCase):
    def setUp(self):
        self.models = [
            {'id': 'one', 'execution': {'framework_id': 'claude-code', 'provider_id': 'allowed'}},
            {'id': 'two', 'execution': {'framework_id': 'hermes', 'provider_id': 'allowed2'}},
        ]
        self.nodes = [
            {'id': 'a', 'name': 'A', 'model_id': 'one', 'depends_on': []},
            {'id': 'b', 'name': 'B', 'model_id': 'two', 'depends_on': ['a']},
        ]

    def test_dag_order_and_cross_framework_bindings(self):
        value = validate_topology({'name': 'Chain', 'nodes': self.nodes}, self.models, ['allowed', 'allowed2'], [])
        self.assertEqual(value['layers'], [['a'], ['b']])
        with self.assertRaises(ValueError):
            validate_topology({'name': 'Chain', 'nodes': self.nodes}, self.models, ['allowed'], [])

    def test_cycles_unknown_dependencies_and_profiles_fail_before_execution(self):
        with self.assertRaises(ValueError):
            validate_topology(
                {'id': [], 'name': 'Broken', 'nodes': self.nodes}, self.models, ['allowed', 'allowed2'], []
            )
        for deps in [['b'], ['missing']]:
            nodes = [{**self.nodes[0], 'depends_on': deps}, self.nodes[1]]
            with self.assertRaises(ValueError):
                validate_topology({'name': 'Broken', 'nodes': nodes}, self.models, ['allowed', 'allowed2'], [])
        nodes = [{**self.nodes[0], 'profile_id': 'role:hermes:planner'}]
        with self.assertRaises(ValueError):
            validate_topology(
                {'name': 'Broken', 'nodes': nodes},
                self.models,
                ['allowed'],
                [{'id': 'role:hermes:planner', 'framework_id': 'hermes', 'available': True}],
            )

    def test_restart_is_reported_as_interrupted_without_replay(self):
        rows = [{'id': 'not-active', 'status': 'running', 'nodes': {'a': {'status': 'running'}}}]
        result = visible_runs(rows)
        self.assertEqual(result[0]['status'], 'interrupted')
        self.assertEqual(rows[0]['status'], 'running')


class TopologyExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from starlette.requests import Request
        from open_webui.execution import topology

        self.topology = topology
        self.folder = SimpleNamespace(
            id='test-project-runner', name='Project', data={'execution_project': {'directory': None}}
        )
        self.user = SimpleNamespace(id='owner', role='admin')
        app = SimpleNamespace(state=SimpleNamespace(redis=None, MODELS={'one': {'id': 'one'}, 'two': {'id': 'two'}}))
        self.request = Request(
            {'type': 'http', 'method': 'POST', 'path': '/', 'headers': [(b'authorization', b'Bearer test')], 'app': app}
        )
        self.created = []
        self.created_profiles = []
        self.saved = []
        self.states = []

        async def insert(identity, owner, form_data, **kwargs):
            self.created.append((identity, form_data.folder_id))
            self.created_profiles.append(form_data.chat.get('params', {}).get('execution_profile_id'))
            return SimpleNamespace(id=identity)

        async def update(identity, payload):
            self.saved.append((identity, payload))
            return SimpleNamespace(id=identity)

        async def create(redis, coroutine, id=None):
            return 'task', asyncio.create_task(coroutine)

        async def persist(folder_id, user, run):
            import copy

            self.states.append(copy.deepcopy(run))

        chats = SimpleNamespace(
            Chats=SimpleNamespace(insert_new_chat=insert, update_chat_by_id=update),
            ChatForm=lambda **kwargs: SimpleNamespace(**kwargs),
        )
        self.patches = [
            patch.dict(
                'sys.modules',
                {'open_webui.models.chats': chats, 'open_webui.tasks': SimpleNamespace(create_task=create)},
            ),
            patch.object(topology, 'persist_run', persist),
            patch('open_webui.execution.project_data.set_stage', AsyncMock()),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])

    async def test_nodes_use_separate_chats_and_pass_only_dependency_outputs(self):
        from unittest.mock import patch
        from open_webui.execution import bridge

        observed = []

        async def generate(request, form, user, model):
            observed.append((model['id'], form['messages'][-1]['content']))
            return {
                'choices': [{'message': {'content': 'A-result' if model['id'] == 'one' else 'B-result'}}],
                'usage': {'prompt_tokens': 1},
                'execution': {'framework_name': 'fake'},
            }

        definition = {
            'id': 'flow',
            'name': 'Flow',
            'layers': [['a'], ['b']],
            'nodes': [
                {
                    'id': 'a',
                    'name': 'A',
                    'model_id': 'one',
                    'profile_id': 'role:test',
                    'depends_on': [],
                    'prompt': 'Analyze',
                },
                {'id': 'b', 'name': 'B', 'model_id': 'two', 'depends_on': ['a'], 'prompt': 'Review'},
            ],
        }
        with patch.object(bridge, 'generate_chat', generate):
            run = await self.topology.start_run(self.request, self.user, self.folder, definition, 'Synthetic task')
            await self.topology.active_runs[run['id']]['task']
        self.assertEqual([model for model, _ in observed], ['one', 'two'])
        self.assertIn('A-result', observed[1][1])
        self.assertEqual(len(set(identity for identity, _ in self.created)), 2)
        self.assertEqual(self.created_profiles, ['role:test', None])
        self.assertTrue(all(folder == 'test-project-runner' for _, folder in self.created))
        self.assertEqual(run['status'], 'done')
        self.assertTrue(
            all(payload['history']['messages'][payload['history']['currentId']]['done'] for _, payload in self.saved)
        )

    async def test_stop_cancels_running_node_and_never_claims_completion(self):
        import asyncio
        from unittest.mock import patch
        from open_webui.execution import bridge

        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def generate(*args):
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        definition = {
            'id': 'flow',
            'name': 'Flow',
            'layers': [['a']],
            'nodes': [{'id': 'a', 'name': 'A', 'model_id': 'one', 'depends_on': [], 'prompt': 'Analyze'}],
        }
        with patch.object(bridge, 'generate_chat', generate):
            run = await self.topology.start_run(self.request, self.user, self.folder, definition, 'Synthetic task')
            await asyncio.wait_for(started.wait(), 2)
            await self.topology.stop_run(self.folder.id, run['id'])
        self.assertTrue(cancelled.is_set())
        self.assertEqual(run['status'], 'cancelled')
        self.assertEqual(run['nodes']['a']['status'], 'cancelled')
        self.assertNotIn(run['id'], self.topology.active_runs)
