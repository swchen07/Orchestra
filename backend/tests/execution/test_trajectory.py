import asyncio
import copy
import json
import unittest

from open_webui.execution.trajectory import add_event, clean, public_run, tool_state
from open_webui.execution.workflows import WorkflowEngine
from open_webui.execution.claude import ExecutionError


class TrajectoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.template = {
            'id': 'flow',
            'name': 'Flow',
            'mode': 'summarize',
            'lead_id': 'lead',
            'participant_ids': ['lead', 'a', 'b'],
            'max_rounds': 2,
        }
        self.bindings = [
            {'id': i, 'name': i, 'framework_id': f} for i, f in [('lead', 'claude-code'), ('a', 'codex'), ('b', 'dsh')]
        ]
        self.snapshots = []

    async def publish(self, value):
        self.snapshots.append(copy.deepcopy(value))

    @staticmethod
    def response(content):
        return {'choices': [{'message': {'content': content}}]}

    async def test_mixed_framework_calls_share_ledger_and_summary_dependencies(self):
        async def invoke(binding, messages, native):
            role = binding['id']
            await engine.observe(role, 'metadata', {'actual_model': 'observed-' + role, 'api_key': 'do-not-save'})
            await engine.observe(
                role,
                'tool',
                {
                    'id': 'tool-' + role,
                    'name': 'Read',
                    'done': False,
                    'input': {'path': 'fixture.txt', 'password': 'private-password'},
                },
            )
            await engine.observe(
                role, 'tool', {'id': 'tool-' + role, 'name': 'Read', 'done': True, 'output': 'fixture data'}
            )
            await engine.observe(role, 'text', {'text': 'part '})
            await engine.observe(role, 'text', {'text': 'two'})
            return self.response(role + ' report')

        engine = WorkflowEngine(self.template, self.bindings, 'task', invoke, self.publish)
        await engine.execute()
        calls = engine.record['calls']
        self.assertEqual(set(calls[-1]['depends_on']), {c['id'] for c in calls[:-1]})
        events = engine.record['events']
        self.assertEqual([e['seq'] for e in events], list(range(1, len(events) + 1)))
        self.assertEqual(len([e for e in events if e['type'] == 'model.output']), 3)
        self.assertTrue(all(e['text'] == 'part two' for e in events if e['type'] == 'model.output'))
        self.assertEqual(len([e for e in events if e['type'] == 'report.completed']), 3)
        self.assertNotIn('private-password', json.dumps(events))
        self.assertNotIn('do-not-save', json.dumps(events))
        self.assertTrue(any(e['data'].get('output') == 'fixture data' for e in events if e['type'] == 'tool.completed'))

    async def test_queued_calls_are_visible_before_a_slot_and_cancel_cleanly(self):
        started = set()

        async def invoke(binding, messages, native):
            started.add(binding['id'])
            await asyncio.Event().wait()

        template = {**self.template, 'mode': 'parallel'}
        engine = WorkflowEngine(template, self.bindings, 'task', invoke, self.publish)
        task = asyncio.create_task(engine.execute())
        while len(engine.record['calls']) < 3:
            await asyncio.sleep(0)
        self.assertEqual(len(started), 2)
        self.assertEqual(engine.record['calls'][-1]['status'], 'queued')
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(all(c['status'] == 'cancelled' for c in engine.record['calls']))
        self.assertEqual(engine.record['events'][-1]['type'], 'run.cancelled')

    async def test_delegate_handoffs_follow_actual_decisions_and_returned_reports(self):
        decisions = iter(
            [
                {'action': 'dispatch', 'tasks': [{'participant_id': 'b', 'input': 'verify'}]},
                {'action': 'final', 'content': 'verified'},
            ]
        )

        async def invoke(binding, messages, native):
            return self.response(json.dumps(next(decisions)) if binding['id'] == 'lead' else 'evidence')

        engine = WorkflowEngine({**self.template, 'mode': 'delegate'}, self.bindings, 'task', invoke, self.publish)
        await engine.execute()
        first, worker, final = engine.record['calls']
        self.assertEqual(worker['depends_on'], [first['id']])
        self.assertEqual(final['depends_on'], [worker['id']])
        self.assertNotIn('a', [c['participant_id'] for c in engine.record['calls']])
        self.assertEqual([c['kind'] for c in engine.record['calls']], ['decision', 'work', 'decision'])
        self.assertTrue(
            any(e['type'] == 'agent.waiting' and e.get('role_id') == 'lead' for e in engine.record['events'])
        )
        self.assertEqual(len([e for e in engine.record['events'] if e['type'] == 'report.completed']), 1)
        self.assertTrue(
            any(s.get('role_activity', {}).get('lead', {}).get('status') == 'waiting' for s in self.snapshots)
        )

    def test_public_view_excludes_private_checkpoints_and_normalizes_interrupted_nodes(self):
        record = {
            'id': 'old',
            'status': 'interrupted',
            'role_contexts': {'a': {'messages': ['private memory']}},
            'calls': [{'id': 'c', 'participant_id': 'a', 'status': 'running', 'created_at': 1}],
        }
        value = public_run(record)
        self.assertNotIn('role_contexts', value)
        self.assertTrue(value['legacy_trace'])
        self.assertEqual(value['calls'][0]['status'], 'interrupted')
        self.assertEqual(record['calls'][0]['status'], 'running')

    def test_event_limits_and_secret_redaction_preserve_usage_numbers(self):
        self.assertEqual(tool_state({'status': 'declined', 'done': True}), ('tool.blocked', 'blocked'))
        self.assertEqual(
            clean({'api_key': 'key', 'nested': {'authorization': 'Bearer token'}, 'prompt_tokens': 42}),
            {'api_key': '[redacted]', 'nested': {'authorization': '[redacted]'}, 'prompt_tokens': 42},
        )
        self.assertNotIn('abc123', clean('Authorization: Bearer abc123'))
        value = {'events': [{'seq': i} for i in range(2048)]}
        self.assertIsNone(add_event(value, 'tool.started'))
        self.assertEqual(value['events_omitted'], 1)

    async def test_final_only_direct_response_has_output_and_usage_without_fake_tool_events(self):
        async def invoke(binding, messages, native):
            return {**self.response('direct result'), 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}

        engine = WorkflowEngine(
            {**self.template, 'mode': 'single', 'participant_ids': ['lead']},
            [{'id': 'lead', 'name': 'Direct', 'framework_id': 'direct'}],
            'task',
            invoke,
            self.publish,
        )
        await engine.execute()
        events = engine.record['events']
        self.assertEqual(next(e['text'] for e in events if e['type'] == 'model.output'), 'direct result')
        self.assertEqual(next(e['data']['prompt_tokens'] for e in events if e['type'] == 'model.usage'), 10)
        self.assertFalse(any(e['type'].startswith('tool.') for e in events))

    async def test_batch_limit_is_checked_before_starting_any_sibling(self):
        async def invoke(binding, messages, native):
            self.fail('An over-budget batch must not start native execution')

        engine = WorkflowEngine(self.template, self.bindings, 'task', invoke, self.publish)
        engine.record['calls'] = [{} for _ in range(23)]
        with self.assertRaises(ExecutionError):
            await engine.collect([('a', 'first', []), ('b', 'second', [])])
        self.assertEqual(len(engine.record['calls']), 23)


if __name__ == '__main__':
    unittest.main()
