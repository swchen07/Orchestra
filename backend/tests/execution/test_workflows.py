import asyncio
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from open_webui.execution.claude import ExecutionError
from open_webui.execution.workflow_config import parse_dispatch, validate_workflow
from open_webui.execution.workflow_config import enforce_project_participant
from open_webui.execution.workflows import WorkflowEngine, shared_task, scoped_participant_context


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def test_identical_hermes_tasks_get_distinct_stable_native_fingerprints(self):
        run = '00b66401-f329-4b58-bc1d-fc9c2a554e28'
        messages = [{'role': 'user', 'content': 'same task'}]
        first, first_key = scoped_participant_context(messages, run, {'id': 'a', 'framework_id': 'hermes'})
        repeated, repeated_key = scoped_participant_context(messages, run, {'id': 'a', 'framework_id': 'hermes'})
        second, second_key = scoped_participant_context(messages, run, {'id': 'b', 'framework_id': 'hermes'})
        other_run, other_key = scoped_participant_context(
            messages, '45faea1c-d77f-4ab7-a76e-8934db5f8906', {'id': 'a', 'framework_id': 'hermes'}
        )
        self.assertEqual((first, first_key), (repeated, repeated_key))
        self.assertEqual(len({first_key, second_key, other_key}), 3)
        self.assertNotEqual(first[0], second[0])
        self.assertNotEqual(first[0], other_run[0])
        self.assertEqual(first[1:], messages)
        self.assertEqual(messages, [{'role': 'user', 'content': 'same task'}])
        self.assertEqual(
            scoped_participant_context(messages, run, {'id': 'a', 'framework_id': 'codex'}), (messages, None)
        )

    def setUp(self):
        self.bindings = [{'id': identity, 'name': identity, 'framework_id': 'codex'} for identity in ('lead', 'a', 'b')]
        self.published = []

    def template(self, mode='delegate'):
        return {
            'id': 'test',
            'name': 'Review',
            'mode': mode,
            'participant_ids': ['lead', 'a', 'b'],
            'lead_id': 'lead',
            'max_rounds': 2,
            'instructions': '',
        }

    async def publish(self, record):
        self.published.append(copy.deepcopy(record))

    @staticmethod
    def response(content, native=None):
        return {
            'choices': [{'message': {'content': content}}],
            'execution': {'native_session_id': native},
            'usage': {'prompt_tokens': 3, 'completion_tokens': 2},
        }

    async def test_lead_can_choose_another_round_and_workers_keep_independent_context(self):
        seen = []
        choices = iter(
            [
                {'action': 'dispatch', 'tasks': [{'participant_id': 'a', 'input': 'first task'}]},
                {
                    'action': 'dispatch',
                    'tasks': [
                        {'participant_id': 'a', 'input': 'review first result'},
                        {'participant_id': 'b', 'input': 'independent verification'},
                    ],
                },
                {'action': 'final', 'content': 'integrated result'},
            ]
        )

        async def invoke(binding, messages, native):
            seen.append((binding['id'], copy.deepcopy(messages), native))
            content = json.dumps(next(choices)) if binding['id'] == 'lead' else binding['id'] + ' output'
            return self.response(content, binding['id'] + '-native')

        engine = WorkflowEngine(self.template(), self.bindings, 'original task', invoke, self.publish)
        self.assertEqual(await engine.execute(), 'integrated result')
        a_calls = [c for c in seen if c[0] == 'a']
        b_call = next(c for c in seen if c[0] == 'b')
        self.assertEqual(a_calls[1][2], 'a-native')
        self.assertEqual(len(a_calls[1][1]), 3)
        self.assertEqual(len(b_call[1]), 1)
        self.assertNotIn('first task', str(b_call))
        self.assertEqual(len(engine.record['calls']), 6)
        self.assertEqual(engine.record['status'], 'completed')
        self.assertTrue(engine.record['messages'])

    async def test_parallel_and_summary_preserve_source_and_failures(self):
        async def invoke(binding, messages, native):
            if binding['id'] == 'b':
                raise RuntimeError('private-provider-key')
            if binding['id'] == 'lead':
                self.assertIn('a output', messages[-1]['content'])
                self.assertIn('Execution failed', messages[-1]['content'])
                return self.response('summary')
            return self.response('a output')

        engine = WorkflowEngine(self.template('summarize'), self.bindings, 'task', invoke, self.publish)
        self.assertEqual(await engine.execute(), 'summary')
        self.assertEqual(engine.record['status'], 'completed_with_errors')
        self.assertNotIn('private-provider-key', str(engine.record))

    async def test_invalid_dispatch_fails_before_running_any_worker(self):
        async def invoke(binding, messages, native):
            self.assertEqual(binding['id'], 'lead')
            return self.response('{"action":"dispatch","tasks":[{"participant_id":"outsider","input":"task"}]}')

        engine = WorkflowEngine(self.template(), self.bindings, 'task', invoke, self.publish)
        with self.assertRaises(ExecutionError):
            await engine.execute()
        self.assertEqual(engine.record['status'], 'failed')
        self.assertEqual(len(engine.record['calls']), 1)

    async def test_cancellation_reaches_every_running_participant(self):
        started, stopped = set(), set()

        async def invoke(binding, messages, native):
            identity = binding['id']
            started.add(identity)
            try:
                await asyncio.Event().wait()
            finally:
                stopped.add(identity)

        engine = WorkflowEngine(self.template('parallel'), self.bindings, 'task', invoke, self.publish)
        task = asyncio.create_task(engine.execute())
        while len(started) < 2:
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(started, stopped)
        self.assertEqual(engine.record['status'], 'cancelled')

    async def test_definition_is_frozen_and_scope_validated(self):
        available = self.bindings
        value = self.template('parallel')
        value.pop('id')
        valid = validate_workflow(value, available)

        async def invoke(binding, messages, native):
            return self.response(binding['id'])

        engine = WorkflowEngine(valid, available, 'task', invoke, self.publish)
        valid['participant_ids'].clear()
        self.assertEqual(len(engine.template['participant_ids']), 3)
        await engine.execute()
        with self.assertRaises(ValueError):
            validate_workflow(
                {**self.template('parallel'), 'id': None, 'participant_ids': ['lead', 'outside']}, available
            )
        with self.assertRaises(ValueError):
            parse_dispatch(
                '{"action":"dispatch","tasks":[{"participant_id":"a","input":"first"},{"participant_id":"a","input":"second"}]}',
                ['a'],
            )

    async def test_project_scope_rejects_other_models_and_unselected_presets(self):
        from fastapi import HTTPException

        folder = SimpleNamespace(data={'execution_participants': ['model:allowed'], 'execution_profiles': []})
        modules = {
            'open_webui.models.folders': SimpleNamespace(
                Folders=SimpleNamespace(get_folder_by_id=AsyncMock(return_value=folder))
            ),
            'open_webui.models.config': SimpleNamespace(Config=SimpleNamespace(get=AsyncMock(return_value=[]))),
        }
        model = {'id': 'allowed', 'execution': {'framework_id': 'direct'}}
        with (
            patch.dict('sys.modules', modules),
            patch('open_webui.execution.profiles.discover_profiles', return_value=[]),
        ):
            await enforce_project_participant({'id': 'project'}, model, None)
            for other, profile in [
                ({'id': 'other', 'execution': {'framework_id': 'direct'}}, None),
                (model, 'unselected-role'),
            ]:
                with self.assertRaises(HTTPException) as error:
                    await enforce_project_participant({'id': 'project'}, other, profile)
                self.assertEqual(error.exception.status_code, 403)

    async def test_api_participants_receive_readable_task_and_shared_history(self):
        self.assertEqual(shared_task([{'role': 'user', 'content': '20 + 22?'}]), '20 + 22?')
        context = shared_task(
            [
                {'role': 'user', 'content': 'Remember IVORY72'},
                {'role': 'assistant', 'content': 'Stored'},
                {'role': 'user', 'content': 'Recall the code'},
            ]
        )
        self.assertIn('User:\nRemember IVORY72', context)
        self.assertTrue(context.endswith('Current task:\nRecall the code'))
        self.assertNotIn('"role"', context)
        with self.assertRaises(ExecutionError):
            shared_task([{'role': 'user', 'content': [{'type': 'image_url', 'image_url': 'private'}]}])
