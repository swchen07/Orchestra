import json
from pathlib import Path
import tempfile
import unittest
import uuid

from open_webui.execution.dsh_usage import matches_reference, read_turn_usage, recover_usage, read_native_tree
from open_webui.execution.claude import conversation_prompt

try:
    from compression import zstd
except ImportError:
    zstd = None


@unittest.skipIf(zstd is None, 'zstd is unavailable')
class DshUsageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = self.temporary.name
        self.session = str(uuid.uuid4())
        self.path = Path(self.home) / '.dsh/sessions/project' / self.session / 'session.v4.jsonl.zstd'
        self.path.parent.mkdir(parents=True)

    def write(self, rows, **header):
        with zstd.open(self.path, 'wt') as stream:
            stream.write(json.dumps({'type': 'session', 'version': 4, 'id': self.session,
                                     'cwd': '/project', **header}) + '\n')
            for row in rows:
                stream.write(json.dumps(row) + '\n')

    @staticmethod
    def turn(turn, reference, offset=0, usage=None):
        return [
            {'seq': offset + 1, 'time': 100, 'type': 'turn/start', 'data': {'turn': turn}},
            {'seq': offset + 2, 'time': 101, 'type': 'step/start', 'data': {'turn': turn, 'step': 1}},
            {'seq': offset + 3, 'time': 102, 'type': 'user/message', 'data': {
                'source': {'kind': 'user'}, 'content': [{'type': 'text', 'text':
                    'Task\n\nReference ID for this response: ' + reference}]}},
            {'seq': offset + 4, 'time': 151, 'type': 'assistant/message', 'data': {
                'turn': turn, 'step': 1, 'usage': usage or {'inputTokens': 10, 'outputTokens': 3,
                                                         'cacheReadTokens': 20, 'cacheWriteTokens': 2}}},
            {'seq': offset + 5, 'time': 155, 'type': 'turn/end', 'data': {'turn': turn}},
        ]

    def test_resumed_turns_are_independent_and_cache_is_added_exactly_once(self):
        self.write(self.turn(1, 'first') + self.turn(2, 'second', 10, {
            'inputTokens': 1141, 'outputTokens': 15068, 'cacheReadTokens': 11648, 'cacheWriteTokens': 0,
        }))
        result = read_turn_usage(self.session, 'second', '/project', self.home)
        self.assertEqual(result['usage']['prompt_tokens'], 12789)
        self.assertEqual(result['usage']['completion_tokens'], 15068)
        self.assertEqual(result['usage']['fresh_input_tokens'], 1141)
        self.assertEqual(result['usage']['source_event_ids'], [f'dsh:{self.session}:14'])
        self.assertEqual(result['native_metrics']['llm_ms'], 50)
        self.assertEqual(read_turn_usage(self.session, 'second', '/project', self.home), result)
        self.assertEqual(read_turn_usage(self.session, 'first', home=self.home)['usage']['prompt_tokens'], 32)
        self.assertIsNone(read_turn_usage(self.session, 'second', '/another-project', self.home))

    def test_quoted_old_reports_do_not_own_new_turns_and_serialized_first_prompt_matches(self):
        rows = self.turn(1, 'old')
        prompt = conversation_prompt([
            {'role': 'user', 'content': 'Reference ID for this response: old'},
            {'role': 'assistant', 'content': 'old report'},
            {'role': 'user', 'content': 'Reference ID for this response: new'},
        ])
        self.assertFalse(matches_reference(prompt, 'old'))
        self.assertTrue(matches_reference(prompt, 'new'))
        more = self.turn(2, 'new', 10)
        more[2]['data']['content'][0]['text'] = prompt
        self.write(rows + more)
        self.assertEqual(read_turn_usage(self.session, 'new', home=self.home)['native_metrics']['native_turn_id'], 2)

    def test_multiple_steps_deduplicate_events_and_keep_native_tool_time_separate(self):
        rows = self.turn(1, 'current')[:-1]
        rows.append(rows[-1].copy())  # Replay of the same committed source event.
        rows += [
            {'seq': 5, 'time': 152, 'type': 'tool/call', 'data': {'callId': 'tool'}},
            {'seq': 6, 'time': 172, 'type': 'tool/result', 'data': {'message': {'source': {'callId': 'tool'}}}},
            {'seq': 7, 'time': 173, 'type': 'step/start', 'data': {'turn': 1, 'step': 2}},
            {'seq': 8, 'time': 183, 'type': 'assistant/message', 'data': {
                'turn': 1, 'step': 2, 'usage': {'inputTokens': 0, 'outputTokens': 0}}},
            {'seq': 9, 'time': 184, 'type': 'turn/end', 'data': {'turn': 1}},
        ]
        self.write(rows)
        result = read_turn_usage(self.session, 'current', home=self.home)
        self.assertEqual(result['usage']['prompt_tokens'], 32)
        self.assertEqual(result['usage']['completion_tokens'], 3)
        self.assertEqual(result['native_metrics']['llm_ms'], 60)
        self.assertEqual(result['native_metrics']['tool_ms'], 20)

    def test_incomplete_invalid_child_and_unattributed_usage_remain_unknown(self):
        cases = [
            (self.turn(1, 'target')[:-1], {}),
            (self.turn(1, 'other'), {}),
            (self.turn(1, 'target', usage={'inputTokens': -1, 'outputTokens': 3}), {}),
            (self.turn(1, 'target'), {'parentSession': 'parent'}),
        ]
        for rows, header in cases:
            self.write(rows, **header)
            self.assertIsNone(read_turn_usage(self.session, 'target', home=self.home))
        self.assertIsNone(read_turn_usage('../outside', 'target', home=self.home))

    def test_retained_recovery_does_not_mutate_native_history_or_add_session_totals(self):
        self.write(self.turn(1, 'run/call'))
        before = self.path.read_bytes()
        record = {'id': 'run', 'calls': [{
            'id': 'call', 'status': 'completed', 'execution': {
                'framework_id': 'dsh', 'native_session_id': self.session,
            },
        }]}
        recover_usage(record, self.home)
        recover_usage(record, self.home)
        self.assertEqual(record['calls'][0]['usage']['prompt_tokens'], 32)
        self.assertEqual(self.path.read_bytes(), before)

    def test_native_tree_excludes_fork_seed_and_old_child_turns_when_parent_resumes(self):
        child = str(uuid.uuid4())
        first, second = self.turn(1, 'first'), self.turn(2, 'second', 20)
        second = [{**r, 'time': r['time'] + 200} for r in second]
        first.insert(-1, {'seq': 10, 'time': 153, 'type': 'subagent/catalog', 'data': {'childId': child}})
        self.write(first + second)
        child_path = self.path.parent.parent / child / self.path.name
        child_path.parent.mkdir()
        rows = [
            {'seq': 1, 'time': 1, 'type': 'assistant/message', 'data': {'turn': 99,
                'usage': {'inputTokens': 9999, 'outputTokens': 9999}}},
            {'seq': 2, 'time': 102, 'type': 'subagent/descriptor', 'data': {'version': 3}},
        ]
        for turn, offset, start in ((1, 10, 105), (2, 30, 305)):
            own = self.turn(turn, 'child', offset)
            rows.extend({**r, 'time': start + i} for i, r in enumerate(own))
        with zstd.open(child_path, 'wt') as f:
            f.write(json.dumps({'type': 'session', 'version': 4, 'id': child,
                'parentSession': self.session, 'delegationDepth': 1, 'isSeeded': True}) + '\n')
            for r in rows:
                f.write(json.dumps(r) + '\n')
        for reference in ('first', 'second'):
            value = read_native_tree(self.session, reference, home=self.home)
            self.assertTrue(value['native_usage']['complete'])
            self.assertEqual(value['native_usage']['child_count'], 1)
            self.assertEqual(value['native_usage']['self']['total_tokens'], 35)
            self.assertEqual(value['native_usage']['inclusive']['total_tokens'], 70)
        self.assertNotEqual(read_native_tree(self.session, 'first', home=self.home)['usage']['source_event_ids'],
                            read_native_tree(self.session, 'second', home=self.home)['usage']['source_event_ids'])

    def test_new_missing_or_running_child_never_turns_into_complete_zero_cost(self):
        child = str(uuid.uuid4())
        rows = self.turn(1, 'parent')
        rows.insert(-1, {'seq': 10, 'time': 110, 'type': 'subagent/catalog', 'data': {'childId': child}})
        self.write(rows)
        missing = read_native_tree(self.session, 'parent', home=self.home)
        self.assertFalse(missing['native_usage']['complete'])
        self.assertEqual(missing['native_usage']['child_count'], 1)
        self.assertFalse(missing['usage']['native_tree_total_complete'])
        path = self.path.parent.parent / child / self.path.name
        path.parent.mkdir()
        with zstd.open(path, 'wt') as f:
            f.write(json.dumps({'type': 'session', 'version': 4, 'id': child, 'parentSession': self.session}) + '\n')
            child_rows = [{'seq': 0, 'time': 111, 'type': 'subagent/descriptor', 'data': {'version': 3}}]
            child_rows += [{**r, 'time': 112 + i} for i, r in enumerate(self.turn(1, 'child')[:-1])]
            for row in child_rows:
                f.write(json.dumps(row) + '\n')
        running = read_native_tree(self.session, 'parent', home=self.home)
        self.assertFalse(running['native_usage']['complete'])
        self.assertEqual(running['native_usage']['agents'][1]['status'], 'running')
        self.assertEqual(running['native_usage']['inclusive']['total_tokens'], 70)
