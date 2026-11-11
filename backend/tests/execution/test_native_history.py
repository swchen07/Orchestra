import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from open_webui.execution.native_history import NativeHistory, imported_chat_payload


class NativeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        root = self.home / '.hermes'
        root.mkdir()
        self.db = root / 'state.db'
        with closing(sqlite3.connect(self.db)) as db, db:
            db.executescript("""
                CREATE TABLE sessions(id TEXT PRIMARY KEY, source TEXT, model TEXT, title TEXT,
                parent_session_id TEXT, started_at REAL, ended_at REAL, end_reason TEXT,
                model_config TEXT, system_prompt TEXT);
                CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,
                timestamp REAL,reasoning TEXT);
            """)
            db.executemany(
                'INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?)',
                [
                    (
                        'root',
                        'cli',
                        'model',
                        'Older conversation',
                        None,
                        1,
                        3,
                        'compression',
                        'credential-secret',
                        'system-secret',
                    ),
                    ('child', 'cli', 'model', 'Continued conversation', 'root', 4, 8, None, '', ''),
                    ('agent', 'cli', 'model', 'Hidden worker', 'root', 2, 3, None, '', ''),
                    ('cron', 'cron', 'model', 'Scheduled', None, 10, 11, None, '', ''),
                ],
            )
            db.executemany(
                'INSERT INTO messages VALUES(?,?,?,?,?,?)',
                [
                    (1, 'root', 'user', 'Original question', 1, None),
                    (2, 'root', 'assistant', 'Original answer', 2, 'thinking-secret'),
                    (
                        3,
                        'child',
                        'assistant',
                        json.dumps(
                            [
                                {'type': 'text', 'text': 'Continued answer'},
                                {'type': 'thinking', 'thinking': 'block-secret'},
                            ]
                        ),
                        5,
                        'thinking-secret',
                    ),
                    (4, 'child', 'tool', 'tool-secret', 6, None),
                ],
            )
        self.history = NativeHistory(self.home)

    def claude_file(self):
        root = self.home / '.claude/projects/work'
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'native.jsonl'
        path.write_text(
            '\n'.join(
                json.dumps(event)
                for event in [
                    {
                        'sessionId': 'native',
                        'timestamp': '2026-10-27T10:30:00Z',
                        'cwd': str(self.home / 'project'),
                        'message': {'role': 'user', 'content': 'A Claude question'},
                    },
                    {
                        'sessionId': 'native',
                        'message': {
                            'role': 'assistant',
                            'model': 'native-model',
                            'content': [
                                {'type': 'thinking', 'thinking': 'private-thinking'},
                                {'type': 'text', 'text': 'A Claude answer'},
                            ],
                        },
                    },
                ]
            )
        )
        return path

    def test_unindexed_claude_and_hermes_are_visible_without_cc_switch(self):
        self.claude_file()
        rows, warnings = self.history.sessions()
        self.assertEqual({r['framework_id'] for r in rows}, {'claude-code', 'hermes'})
        self.assertEqual(sum(r['framework_id'] == 'hermes' for r in rows), 1)
        self.assertTrue(warnings)
        all_rows, _ = self.history.sessions(include_automation=True)
        self.assertEqual(sum(r['framework_id'] == 'hermes' for r in all_rows), 2)

    def test_hermes_compression_chain_preserves_public_text_and_redacts_internals(self):
        row = self.history.hermes_sessions()[0]
        preview = self.history.session(row['id'])
        self.assertEqual(
            [m['content'] for m in preview['messages']], ['Original question', 'Original answer', 'Continued answer']
        )
        for secret in [
            'credential-secret',
            'system-secret',
            'thinking-secret',
            'block-secret',
            'tool-secret',
            '_chain',
        ]:
            self.assertNotIn(secret, str(preview))
        self.assertEqual(preview['session']['title'], 'Continued conversation')

    def test_project_scope_is_not_guessed_for_hermes_and_claude_scope_is_enforced(self):
        self.claude_file()
        rows, _ = self.history.sessions(self.home / 'project')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['framework_id'], 'claude-code')
        with self.assertRaises(ValueError):
            self.history.session(rows[0]['id'], self.home / 'other-project')
        with self.assertRaises(ValueError):
            self.history.session('/etc/passwd')
        with self.history.hermes_db() as db, self.assertRaises(sqlite3.OperationalError):
            db.execute('DELETE FROM sessions')

    def test_native_sidechain_is_hidden_and_thinking_not_rendered(self):
        path = self.claude_file()
        rows, _ = self.history.sessions(self.home / 'project')
        preview = self.history.session(rows[0]['id'])
        self.assertEqual([m['content'] for m in preview['messages']], ['A Claude question', 'A Claude answer'])
        self.assertEqual(preview['messages'][1]['model'], 'native-model')
        self.assertEqual(preview['messages'][0]['timestamp'], 1793097000)
        sidechain = path.with_name('agent-hidden.jsonl')
        sidechain.write_text(path.read_text())
        rows, _ = self.history.sessions(self.home / 'project')
        self.assertEqual(len(rows), 1)

    def test_cut_structured_content_is_flagged_without_exposing_thinking(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                'INSERT INTO messages VALUES(?,?,?,?,?,?)',
                (
                    5,
                    'child',
                    'assistant',
                    json.dumps([{'type': 'thinking', 'thinking': 'private-block-' * 4000}]),
                    7,
                    None,
                ),
            )
        row = self.history.hermes_sessions()[0]
        preview = self.history.session(row['id'])
        self.assertTrue(preview['truncated'])
        self.assertNotIn('private-block-', str(preview))

    def test_imported_chat_is_linked_complete_and_has_no_duplicated_usage(self):
        row = self.history.hermes_sessions()[0]
        chat = imported_chat_payload(self.history.session(row['id']), 'current-provider-model')
        messages = chat['history']['messages']
        self.assertEqual(chat['models'], ['current-provider-model'])
        last = messages[chat['history']['currentId']]
        self.assertTrue(last['done'])
        self.assertNotIn('usage', last)
        self.assertIsNone(last['statusHistory'][0]['execution']['actual_model'])
        self.assertEqual(last['statusHistory'][0]['execution']['requested_model'], 'model')
        self.assertIn(last['id'], messages[last['parentId']]['childrenIds'])
