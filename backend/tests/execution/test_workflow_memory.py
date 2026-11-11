import copy
import asyncio
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from open_webui.execution.claude import ExecutionError
from open_webui.execution.workflow_memory import branch_records, claim_native_context, restore_roles, report_archive
from open_webui.execution.workflows import WorkflowEngine


class WorkflowMemoryTests(unittest.IsolatedAsyncioTestCase):
    def test_an_unused_instance_keeps_its_private_checkpoint_across_later_runs(self):
        old = {'messages': [{'role': 'assistant', 'content': 'own private retained fact'}],
               'source_run_id': 'original', 'scope_id': 'independent-context', 'native_session_id': 'native-tip'}
        first = {'role_contexts': {'extra_instance': copy.deepcopy(old)}}
        second = {'role_contexts': {'a': {'messages': [{'role': 'assistant', 'content': 'another role'}]}}}
        restored = restore_roles([first, second])
        self.assertEqual(restored['extra_instance'], old)
        self.assertNotIn('another role', str(restored['extra_instance']))
        restored['extra_instance']['messages'].append({'role': 'user', 'content': 'new task'})
        self.assertEqual(first['role_contexts']['extra_instance'], old)

    def setUp(self):
        self.template = {
            'id': 'workflow',
            'mode': 'parallel',
            'name': 'Compare',
            'participant_ids': ['a', 'b'],
            'lead_id': 'a',
            'max_rounds': 1,
        }
        self.bindings = [{'id': i, 'name': i, 'framework_id': 'codex', 'model_id': i} for i in ('a', 'b')]

    async def publish(self, record):
        pass

    @staticmethod
    def response(content, native=None):
        return {'choices': [{'message': {'content': content}}], 'execution': {'native_session_id': native}}

    async def first(self):
        async def invoke(binding, messages, native):
            return self.response(binding['id'] + '-private-report', binding['id'] + '-native')

        engine = WorkflowEngine(self.template, self.bindings, 'first task', invoke, self.publish)
        await engine.execute()
        return copy.deepcopy(engine.record)

    async def test_multiturn_restarts_restore_each_role_and_native_handle(self):
        first = await self.first()
        seen = {}

        async def invoke(binding, messages, native):
            seen[binding['id']] = (messages, native)
            return self.response('continued', native)

        with patch('open_webui.execution.workflows.claim_native_context', return_value=True):
            second = WorkflowEngine(self.template, self.bindings, 'next task', invoke, self.publish, previous=[first])
            await second.execute()
        self.assertEqual(seen['b'][1], 'b-native')
        self.assertIn('b-private-report', str(seen['b'][0]))
        self.assertNotIn('a-private-report', str(seen['b'][0]))
        self.assertEqual(len(second.contexts['b']), 4)
        self.assertNotIn('b-private-report', str(seen['a'][0]))
        self.assertEqual(second.record['continuation']['roles']['b']['method'], 'native')
        self.assertEqual(first['role_contexts']['b']['messages'][-1]['content'], 'b-private-report')

    async def test_summary_dependency_does_not_implicitly_grant_all_historical_worker_reports(self):
        first = await self.first()

        async def invoke(binding, messages, native):
            if binding['id'] == 'a':
                self.assertNotIn(first['calls'][1]['report_id'], messages[-1]['content'])
                self.assertNotIn('b-private-report', str(messages))
                self.assertIn('summary result', messages[-1]['content'])
            else:
                self.assertNotIn('a-private-report', str(messages))
            return self.response('summary result')

        template = {**self.template, 'mode': 'summarize'}
        await WorkflowEngine(template, self.bindings, 'next task', invoke, self.publish, previous=[first]).execute()

    async def test_changed_binding_keeps_dialogue_but_starts_native_session(self):
        first = await self.first()
        bindings = copy.deepcopy(self.bindings)
        bindings[1]['model_id'] = 'different-model'

        async def invoke(binding, messages, native):
            if binding['id'] == 'b':
                self.assertIsNone(native)
                self.assertIn('b-private-report', str(messages))
            return self.response('new result', 'new-native')

        with patch('open_webui.execution.workflows.claim_native_context', return_value=True):
            engine = WorkflowEngine(self.template, bindings, 'next task', invoke, self.publish, previous=[first])
            await engine.execute()
        self.assertEqual(engine.record['continuation']['roles']['b']['method'], 'replay')

    async def test_transport_rotation_preserves_first_history_and_marks_failed_attempts(self):
        first = await self.first()
        first['status'] = 'failed'  # Role work succeeded; the overall attempt did not.
        original = copy.deepcopy(first['role_contexts']['b']['messages'])
        binding = copy.deepcopy(self.bindings)
        binding[1]['model_id'] = 'changed-backend'

        async def invoke(selected, messages, native):
            if selected['id'] == 'b':
                self.assertIsNone(native)
                self.assertEqual(messages[:len(original)], original)
                self.assertNotIn('a-private-report', str(messages))
                self.assertIn('"run_status": "failed"', messages[-1]['content'])
            return self.response('second report')

        engine = WorkflowEngine(self.template, binding, 'continue', invoke, self.publish, previous=[first])
        with patch('open_webui.execution.workflows.claim_native_context', return_value=True):
            await engine.execute()
        own = next(c for c in first['calls'] if c['participant_id'] == 'b')
        restored = next(c for c in engine.record['calls'] if c['participant_id'] == 'b')
        self.assertNotEqual(restored['module']['context_id'], own['module']['context_id'])
        self.assertEqual(restored['history']['first_retained_report_ref'], own['report_id'])
        self.assertEqual(restored['history']['history_messages'], len(original))
        self.assertEqual(restored['history']['attempts'][0]['run_status'], 'failed')

    async def test_failed_native_turn_cannot_poison_saved_successful_context(self):
        first = await self.first()

        async def invoke(binding, messages, native):
            raise ExecutionError('native failure')

        with patch('open_webui.execution.workflows.claim_native_context', return_value=True):
            engine = WorkflowEngine(self.template, self.bindings, 'failed task', invoke, self.publish, previous=[first])
            with self.assertRaises(ExecutionError):
                await engine.execute()
        self.assertEqual(engine.role_states['b']['messages'], first['role_contexts']['b']['messages'])
        self.assertIsNone(engine.role_states['b']['native_session_id'])
        self.assertNotIn('failed task', str(engine.role_states['b']['messages']))

    async def test_sibling_branch_replays_private_history_without_mutating_native_tip(self):
        first = await self.first()

        async def invoke(binding, messages, native):
            self.assertIsNone(native)
            self.assertIn(binding['id'] + '-private-report', str(messages))
            return self.response('branch result')

        with patch('open_webui.execution.workflows.claim_native_context', return_value=False):
            await WorkflowEngine(
                self.template, self.bindings, 'branch', invoke, self.publish, previous=[first]
            ).execute()

    async def test_cancelled_turn_keeps_finished_reports_and_previous_other_role_context(self):
        first = await self.first()
        ready = asyncio.Event()

        async def invoke(binding, messages, native):
            if binding['id'] == 'a':
                return self.response('completed intermediate report', 'a-next-native')
            ready.set()
            await asyncio.Event().wait()

        with patch('open_webui.execution.workflows.claim_native_context', return_value=True):
            engine = WorkflowEngine(self.template, self.bindings, 'second task', invoke, self.publish, previous=[first])
            task = asyncio.create_task(engine.execute())
            await ready.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        restored = restore_roles([first, engine.record])
        self.assertEqual(restored['a']['messages'][-1]['content'], 'completed intermediate report')
        self.assertEqual(restored['b']['messages'], first['role_contexts']['b']['messages'])
        self.assertIsNone(restored['b']['native_session_id'])
        self.assertTrue(any(r['output'] == 'completed intermediate report' for r in report_archive([engine.record])))

    def test_native_claim_is_durable_and_atomic_for_siblings(self):
        state = {'source_run_id': str(uuid.uuid4())}
        with (
            tempfile.TemporaryDirectory() as root,
            patch.dict('sys.modules', {'open_webui.env': SimpleNamespace(DATA_DIR=root)}),
        ):
            self.assertTrue(claim_native_context(state, 'a', 'child-1'))
            self.assertFalse(claim_native_context(state, 'a', 'child-2'))
            self.assertTrue(claim_native_context(state, 'b', 'child-2'))

    def test_branch_ancestry_filters_other_users_chats_projects_and_workflows(self):
        scope = {'user_id': 'u', 'chat_id': 'c', 'project_id': 'p', 'workflow_id': 'w'}
        messages = {'current': {'role': 'user', 'parentId': 'reply'}}
        messages['reply'] = {'role': 'assistant', 'meta': {'workflow_run_id': 'r'}}
        record = {'id': 'r', 'user_id': 'u', 'chat_id': 'c', 'project_id': 'p', 'template': {'id': 'w'}}
        self.assertEqual(branch_records(messages, 'current', scope, lambda _: record), [record])
        for key in ('user_id', 'chat_id', 'project_id'):
            self.assertEqual(branch_records(messages, 'current', scope, lambda _: {**record, key: 'other'}), [])
        self.assertEqual(
            branch_records(messages, 'current', scope, lambda _: {**record, 'template': {'id': 'other'}}), []
        )
        messages['sibling'] = {'role': 'assistant', 'meta': {'workflow_run_id': 'unrelated'}}
        self.assertEqual(
            branch_records(messages, 'current', scope, lambda r: record if r == 'r' else self.fail()), [record]
        )

    def test_missing_checkpoint_or_broken_branch_is_not_silently_reset(self):
        messages = {
            'u': {'role': 'user', 'parentId': 'a'},
            'a': {'role': 'assistant', 'statusHistory': [{'workflow': {'run_id': 'missing'}}]},
        }
        with self.assertRaises(ExecutionError):
            branch_records(messages, 'u', {}, lambda _: (_ for _ in ()).throw(OSError()))
        with self.assertRaises(ExecutionError):
            branch_records({'u': {'role': 'user', 'parentId': 'missing'}}, 'u', {}, lambda _: None)

    def test_legacy_reports_are_migrated_without_trusting_old_native_sessions(self):
        record = {
            'id': 'old',
            'calls': [
                {
                    'participant_id': 'a',
                    'status': 'completed',
                    'input': 'task',
                    'output': 'private',
                    'execution': {'native_session_id': 'unsafe'},
                },
                {'participant_id': 'b', 'status': 'failed', 'input': 'failed'},
            ],
        }
        states = restore_roles([record])
        self.assertEqual(states['a']['messages'][-1]['content'], 'private')
        self.assertIsNone(states['a']['native_session_id'])
        self.assertNotIn('b', states)


if __name__ == '__main__':
    unittest.main()
