import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from open_webui.execution.hermes_usage import snapshot, measure


class HermesTreeTests(unittest.TestCase):
    def test_multiple_turns_use_child_counter_deltas_and_native_receipts_not_parent_session_totals(self):
        with tempfile.TemporaryDirectory() as temp:
            db = sqlite3.connect(Path(temp) / 'state.db')
            self.addCleanup(db.close)
            db.executescript('''CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,
                started_at REAL,ended_at REAL,input_tokens INTEGER,output_tokens INTEGER,
                cache_read_tokens INTEGER,cache_write_tokens INTEGER,api_call_count INTEGER);
                CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,tool_name TEXT,
                timestamp REAL,content TEXT);''')
            db.executemany('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?)', [
                ('root', None, 1, None, 10, 2, 4, 0, 1),
                ('child', 'root', 2, None, 20, 3, 5, 0, 1),
                ('unrelated', None, 2, None, 9999, 9999, 0, 0, 9)])
            receipt = json.dumps({'results': [{'status': 'completed'}]})
            db.executemany('INSERT INTO messages(session_id,role,tool_name,timestamp,content) VALUES(?,?,?,?,?)', [
                ('root', 'tool', 'delegate_task', 4, receipt), ('child', 'assistant', None, 3, 'answer')])
            db.commit()
            first = measure('root', {'prompt_tokens': 14, 'completion_tokens': 2}, 1000, 5000, [temp], invocation_id='first')
            self.assertTrue(first['complete'])
            self.assertEqual(first['self']['total_tokens'], 16)
            self.assertEqual(first['children']['total_tokens'], 28)
            self.assertEqual(first['inclusive']['total_tokens'], 44)
            before = snapshot('root', [temp])
            db.execute("UPDATE sessions SET input_tokens=27,output_tokens=5,api_call_count=2 WHERE id='child'")
            db.executemany('INSERT INTO messages(session_id,role,tool_name,timestamp,content) VALUES(?,?,?,?,?)', [
                ('root', 'tool', 'delegate_task', 9, receipt), ('child', 'assistant', None, 8, 'continued')])
            db.commit()
            second = measure('root', {'prompt_tokens': 30, 'completion_tokens': 4}, 6000, 10000,
                             [temp], before, 'second')
            self.assertTrue(second['complete'])
            self.assertEqual(second['children']['total_tokens'], 9)  # +7 input, +2 output; no old cache
            self.assertEqual(second['inclusive']['total_tokens'], 43)
            self.assertNotEqual(first['agents'][1]['source_event_ids'], second['agents'][1]['source_event_ids'])
            missing = measure('remote', {'prompt_tokens': 30, 'completion_tokens': 4}, 0, 1, [temp])
            self.assertFalse(missing['complete'])
            self.assertEqual(missing['children'], {})
