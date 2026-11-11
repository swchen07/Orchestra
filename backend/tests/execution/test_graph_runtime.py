import asyncio
import copy
import json
import unittest

from open_webui.execution.graph_spec import validate_graph, apply_patch
from open_webui.execution.workflows import WorkflowEngine


def call(identity, binding, inputs=None):
    return {
        'id': identity,
        'label': identity,
        'op': 'call',
        'binding': binding,
        'instruction': identity,
        'inputs': inputs or {},
    }


def graph():
    return {
        'schema_version': '1.0',
        'nodes': [
            call('fast', 'a'),
            call('slow', 'b'),
            call('next', 'a', {'report': {'source': 'node', 'node': 'fast', 'pointer': '/report'}}),
            {
                'id': 'answer',
                'label': 'Collect',
                'op': 'join',
                'policy': 'all_terminal',
                'inputs': {
                    'fast': {'source': 'node', 'node': 'next', 'pointer': '/report'},
                    'slow': {'source': 'node', 'node': 'slow', 'pointer': '/report'},
                },
            },
        ],
        'edges': [{'from': 'fast', 'to': 'next'}, {'from': 'next', 'to': 'answer'}, {'from': 'slow', 'to': 'answer'}],
        'result_node': 'answer',
    }


class GraphTests(unittest.IsolatedAsyncioTestCase):
    bindings = [{'id': p, 'name': p, 'framework_id': 'direct', 'model_id': p} for p in ('lead', 'a', 'b')]

    def engine(self, plan, invoke, **kwargs):
        return WorkflowEngine(
            {'id': 'test', 'name': 'Any topology', 'lead_id': 'lead', 'graph': plan},
            self.bindings,
            'task',
            invoke,
            self.publish,
            **kwargs,
        )

    async def publish(self, value):
        pass

    @staticmethod
    def result(text):
        return {'choices': [{'message': {'content': text}}]}

    async def test_fast_successor_starts_before_unrelated_slow_branch_finishes(self):
        successor_started, slow_finished = asyncio.Event(), asyncio.Event()
        seen = []

        async def invoke(binding, context, native):
            prompt = context[-1]['content']
            seen.append((binding['id'], prompt))
            if binding['id'] == 'b':
                await successor_started.wait()
                slow_finished.set()
            elif prompt.startswith('next'):
                self.assertFalse(slow_finished.is_set(), 'A global batch barrier delayed the fast successor')
                self.assertIn('fast result', prompt)
                successor_started.set()
            return self.result(('fast' if prompt.startswith('fast') else binding['id']) + ' result')

        engine = self.engine(graph(), invoke)
        await asyncio.wait_for(engine.execute(), 2)
        fast, slow, successor = engine.record['calls'][:3]
        self.assertLessEqual(successor['started_at'], slow['finished_at'] * 1000 + 999)
        self.assertEqual(successor['depends_on'], [fast['id']])
        self.assertEqual(engine.record['node_states']['answer']['status'], 'completed')

    async def test_review_can_edit_pending_node_and_approve_without_changing_project_binding(self):
        seen = []

        async def invoke(binding, context, native):
            seen.append(context[-1]['content'])
            return self.result('done')

        engine = self.engine(
            {'nodes': [call('work', 'a')], 'edges': [], 'result_node': 'work'}, invoke, policy='review'
        )
        task = asyncio.create_task(engine.execute())
        while not engine.runner:
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(seen, [])
        node = call('work', 'b') | {'instruction': 'edited by user'}
        await engine.runner.control(
            {'action': 'patch', 'revision': 1, 'changes': [{'op': 'update_node', 'node': node}]}
        )
        self.assertEqual(engine.runner.graph['revision'], 2)
        await engine.runner.control({'action': 'approve', 'revision': 2})
        self.assertEqual(await asyncio.wait_for(task, 2), 'done')
        self.assertTrue(seen[0].startswith('edited by user'))
        self.assertEqual(self.bindings[1]['model_id'], 'a')
        self.assertEqual(engine.record['calls'][0]['participant_id'], 'b')

    async def test_ai_generates_then_revises_subgraph_while_slow_call_runs(self):
        slow_started, child_started = asyncio.Event(), asyncio.Event()
        decisions = 0
        plan = graph()
        plan['nodes'] = [n for n in plan['nodes'] if n['id'] != 'next']
        plan['nodes'][-1]['inputs']['fast']['node'] = 'fast'
        plan['edges'] = [{'from': 'fast', 'to': 'answer'}, {'from': 'slow', 'to': 'answer'}]

        async def invoke(binding, context, native):
            nonlocal decisions
            if binding['id'] == 'lead':
                decisions += 1
                if decisions == 1:
                    return self.result(json.dumps({'action': 'propose_plan', 'graph': plan}))
                if decisions == 2:
                    await slow_started.wait()
                    return self.result(
                        json.dumps(
                            {
                                'action': 'patch_plan',
                                'base_revision': 2,
                                'reason': 'Verify fast evidence now',
                                'changes': [
                                    {
                                        'op': 'add_node',
                                        'node': call(
                                            'next',
                                            'a',
                                            {'report': {'source': 'node', 'node': 'fast', 'pointer': '/report'}},
                                        ),
                                    },
                                    {'op': 'add_edge', 'edge': {'from': 'fast', 'to': 'next'}},
                                    {'op': 'add_edge', 'edge': {'from': 'next', 'to': 'answer'}},
                                ],
                            }
                        )
                    )
                return self.result('{"action":"wait"}')
            if binding['id'] == 'b':
                slow_started.set()
                await child_started.wait()
            if context[-1]['content'].startswith('next'):
                child_started.set()
            return self.result('evidence')

        placeholder = {
            'nodes': [{'id': 'answer', 'op': 'finish', 'inputs': {'answer': {'source': 'literal', 'value': ''}}}],
            'edges': [],
            'result_node': 'answer',
            'controller': {'binding': 'lead', 'initial': True, 'on_events': True},
        }
        engine = self.engine(placeholder, invoke)
        await asyncio.wait_for(engine.execute(), 3)
        self.assertTrue(child_started.is_set())
        self.assertEqual(engine.record['plan']['revision'], 3)
        self.assertTrue(any(e['type'] == 'plan.revised' for e in engine.record['events']))

    def test_rejects_cycles_undeclared_bindings_and_started_node_edits(self):
        plan = validate_graph(graph(), {'a', 'b'})
        bad = copy.deepcopy(plan)
        bad['edges'].append({'from': 'next', 'to': 'fast'})
        with self.assertRaises(ValueError):
            validate_graph(bad)
        with self.assertRaises(ValueError):
            validate_graph(graph(), {'a'})
        with self.assertRaises(ValueError):
            apply_patch(
                plan, [{'op': 'update_node', 'node': call('fast', 'b')}], {'fast': {'status': 'running'}}, {'a', 'b'}
            )
        self.assertEqual(plan['revision'], 1)

    async def test_same_stateful_role_is_serialized_without_consuming_all_leaf_slots(self):
        active = set()
        count = 0

        async def invoke(binding, context, native):
            nonlocal count
            self.assertNotIn(binding['id'], active)
            active.add(binding['id'])
            await asyncio.sleep(0)
            active.remove(binding['id'])
            count += 1
            return self.result('done')

        plan = {'nodes': [call('one', 'a'), call('two', 'a'), call('three', 'b')], 'edges': [], 'result_node': 'three'}
        await asyncio.wait_for(self.engine(plan, invoke).execute(), 2)
        self.assertEqual(count, 3)

    async def test_first_success_join_unblocks_downstream_while_other_call_is_running(self):
        consumer = asyncio.Event()
        plan = graph()
        plan['nodes'] = [
            call('fast', 'a'),
            call('slow', 'b'),
            {
                'id': 'first',
                'op': 'join',
                'policy': 'first_success',
                'inputs': {
                    'a': {'source': 'node', 'node': 'fast', 'pointer': '/report'},
                    'b': {'source': 'node', 'node': 'slow', 'pointer': '/report'},
                },
            },
            call('consumer', 'a', {'evidence': {'source': 'node', 'node': 'first'}}),
        ]
        plan['edges'] = [{'from': 'first', 'to': 'consumer'}]
        plan['result_node'] = 'consumer'

        async def invoke(binding, context, native):
            if binding['id'] == 'b':
                await consumer.wait()
            elif context[-1]['content'].startswith('consumer'):
                self.assertIn('Execution failed: No committed output', context[-1]['content'])
                consumer.set()
            return self.result('evidence')

        engine = self.engine(plan, invoke)
        await asyncio.wait_for(engine.execute(), 2)
        self.assertEqual(engine.record['node_states']['first']['status'], 'completed')
        self.assertTrue(consumer.is_set())

    async def test_cancellation_drains_owned_map_children_and_retains_completed_reports(self):
        started = asyncio.Event()
        stopped = set()
        plan = {
            'nodes': [
                {
                    'id': 'batch',
                    'op': 'map',
                    'binding': 'a',
                    'inputs': {},
                    'items': {'source': 'literal', 'value': [1, 2]},
                }
            ],
            'edges': [],
            'result_node': 'batch',
        }

        async def invoke(binding, context, native):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.add(binding['id'])

        engine = self.engine(plan, invoke)
        task = asyncio.create_task(engine.execute())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped)
        self.assertFalse(any(c['status'] in {'running', 'queued'} for c in engine.record['calls']))

    async def test_review_rejects_stale_version_and_cannot_change_started_calls(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def invoke(binding, context, native):
            entered.set()
            await release.wait()
            return self.result('done')

        engine = self.engine({'nodes': [call('work', 'a')], 'edges': [], 'result_node': 'work'}, invoke)
        task = asyncio.create_task(engine.execute())
        await entered.wait()
        try:
            with self.assertRaises(ValueError):
                await engine.runner.control(
                    {'action': 'patch', 'revision': 0, 'changes': [{'op': 'update_node', 'node': call('work', 'b')}]}
                )
            with self.assertRaises(ValueError):
                await engine.runner.control(
                    {'action': 'patch', 'revision': 1, 'changes': [{'op': 'update_node', 'node': call('work', 'b')}]}
                )
            self.assertEqual(engine.runner.graph['revision'], 1)
        finally:
            release.set()
        await task

    def test_data_references_compile_dependencies_without_a_redundant_edge(self):
        plan = {
            'nodes': [
                call('first', 'a'),
                call('second', 'b', {'evidence': {'source': 'node', 'node': 'first', 'pointer': '/report'}}),
            ],
            'edges': [],
            'result_node': 'second',
        }
        compiled = validate_graph(plan, {'a', 'b'})
        self.assertEqual(compiled['edges'], [{'from': 'first', 'to': 'second', 'on': 'success'}])
        self.assertEqual(plan['edges'], [])
        plan['nodes'][0]['inputs'] = {'bad': {'source': 'node', 'node': 'second'}}
        with self.assertRaises(ValueError):
            validate_graph(plan)

    async def test_binding_switch_retains_logical_dialogue_but_never_resumes_wrong_native_session(self):
        seen = []
        plan = {
            'nodes': [call('one', 'a'), call('two', 'b') | {'context_key': 'a'}],
            'edges': [{'from': 'one', 'to': 'two'}],
            'result_node': 'two',
        }

        async def invoke(binding, context, native):
            seen.append((binding['model_id'], copy.deepcopy(context), native))
            return {
                'choices': [{'message': {'content': 'private evidence'}}],
                'execution': {'native_session_id': 'native-' + binding['model_id']},
            }

        bindings = [{**p, 'framework_id': 'codex'} for p in self.bindings]
        engine = WorkflowEngine(
            {'id': 'test', 'name': 'Any topology', 'lead_id': 'lead', 'graph': plan},
            bindings,
            'task',
            invoke,
            self.publish,
        )
        await engine.execute()
        self.assertEqual([x[0] for x in seen], ['a', 'b'])
        self.assertIsNone(seen[1][2])
        self.assertIn('private evidence', str(seen[1][1]))
        self.assertTrue(any(e['type'] == 'context.rebound' for e in engine.record['events']))

    async def test_shared_call_budget_includes_all_map_items(self):
        plan = {
            'nodes': [
                {
                    'id': 'batch',
                    'op': 'map',
                    'binding': 'a',
                    'inputs': {},
                    'items': {'source': 'literal', 'value': [1, 2, 3]},
                }
            ],
            'edges': [],
            'result_node': 'batch',
            'limits': {'max_calls': 2},
        }

        async def invoke(binding, context, native):
            return self.result('done')

        engine = self.engine(plan, invoke)
        await engine.execute()
        self.assertEqual(len(engine.record['calls']), 2)
        self.assertEqual(engine.record['node_states']['batch']['output']['items'][2]['status'], 'failed')
        self.assertEqual(engine.record['status'], 'completed_with_errors')

    def test_public_schema_rejects_malformed_or_unimplemented_fields(self):
        from jsonschema import Draft202012Validator
        from open_webui.execution.graph_schema import SCHEMA

        Draft202012Validator.check_schema(SCHEMA)
        for patch in (
            {'limits': []},
            {'controller': {'binding': 'lead', 'initial': 'yes'}},
            {'message_routes': [{'from': 'fast', 'to': 'slow'}]},
            {'revision': True},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_graph(graph() | patch)
        with self.assertRaises(ValueError):
            validate_graph(
                {
                    'nodes': [call('x', 'a') | {'output_schema': {'$ref': 'https://example.com/schema'}}],
                    'result_node': 'x',
                }
            )

    async def test_route_selects_one_branch_and_loop_stops_on_structured_result(self):
        attempts = 0

        async def invoke(binding, context, native):
            nonlocal attempts
            prompt = context[-1]['content']
            if prompt.startswith('choose'):
                return self.result('{"case":"accept","report":"selected"}')
            if prompt.startswith('refine'):
                attempts += 1
                if attempts > 1:
                    self.assertIn('draft', prompt)
                return self.result(
                    json.dumps({'accepted': attempts == 2, 'report': 'draft' if attempts == 1 else 'approved'})
                )
            self.fail('The rejected route branch must not call a model')

        plan = {
            'nodes': [
                call('choose', 'a') | {'op': 'route', 'cases': ['accept', 'reject']},
                call('reject', 'b'),
                call('refine', 'b')
                | {
                    'op': 'loop',
                    'max_iterations': 3,
                    'stop_when': {'pointer': '/accepted', 'equals': True},
                    'output_schema': {
                        'type': 'object',
                        'properties': {'accepted': {'type': 'boolean'}, 'report': {'type': 'string'}},
                        'required': ['accepted', 'report'],
                    },
                },
                {
                    'id': 'answer',
                    'op': 'finish',
                    'inputs': {'answer': {'source': 'node', 'node': 'refine', 'pointer': '/report'}},
                },
            ],
            'edges': [
                {'from': 'choose', 'to': 'refine', 'on': 'case', 'case': 'accept'},
                {'from': 'choose', 'to': 'reject', 'on': 'case', 'case': 'reject'},
            ],
            'result_node': 'answer',
        }
        engine = self.engine(plan, invoke)
        self.assertEqual(await asyncio.wait_for(engine.execute(), 2), 'approved')
        self.assertEqual(attempts, 2)
        self.assertEqual(engine.record['node_states']['reject']['status'], 'skipped')
        self.assertEqual(engine.record['node_states']['refine']['output']['stop_reason'], 'condition')

    async def test_checkpoint_answer_is_a_reusable_result_and_step_launches_one_task(self):
        async def invoke(*args):
            self.fail('A human checkpoint must not invoke a model')

        plan = {
            'nodes': [
                {'id': 'question', 'op': 'checkpoint', 'question': 'Choose the answer', 'inputs': {}},
                {
                    'id': 'answer',
                    'op': 'finish',
                    'inputs': {'answer': {'source': 'node', 'node': 'question', 'pointer': '/answer'}},
                },
            ],
            'edges': [],
            'result_node': 'answer',
        }
        engine = self.engine(plan, invoke, policy='step')
        task = asyncio.create_task(engine.execute())
        while not engine.runner:
            await asyncio.sleep(0)
        await engine.runner.control({'action': 'approve', 'revision': 1})
        while engine.runner.states['question']['status'] != 'waiting':
            await asyncio.sleep(0)
        await engine.runner.control({'action': 'answer', 'node_id': 'question', 'value': 'confirmed'})
        while engine.runner.states['question']['status'] != 'completed':
            await asyncio.sleep(0)
        self.assertNotEqual(engine.runner.states['answer']['status'], 'completed')
        await engine.runner.control({'action': 'step'})
        self.assertEqual(await asyncio.wait_for(task, 2), 'confirmed')

    async def test_all_terminal_join_retains_success_and_explicit_failure(self):
        async def invoke(binding, context, native):
            if binding['id'] == 'b':
                from open_webui.execution.claude import ExecutionError

                raise ExecutionError('provider unavailable')
            return self.result('successful report')

        plan = {
            'nodes': [
                call('good', 'a'),
                call('bad', 'b'),
                {
                    'id': 'answer',
                    'op': 'join',
                    'policy': 'all_terminal',
                    'inputs': {
                        'good': {'source': 'node', 'node': 'good', 'pointer': '/report'},
                        'bad': {'source': 'node', 'node': 'bad', 'pointer': '/report'},
                    },
                },
            ],
            'edges': [],
            'result_node': 'answer',
        }
        engine = self.engine(plan, invoke)
        answer = await asyncio.wait_for(engine.execute(), 2)
        self.assertIn('successful report', answer)
        self.assertIn('provider unavailable', answer)
        self.assertEqual(engine.record['status'], 'completed_with_errors')

    async def test_human_review_does_not_consume_active_execution_deadline(self):
        async def invoke(*args):
            return self.result('review accepted')

        plan = {'nodes': [call('work', 'a')], 'edges': [], 'result_node': 'work', 'limits': {'max_seconds': 1}}
        engine = self.engine(plan, invoke, policy='review')
        task = asyncio.create_task(engine.execute())
        while not engine.runner:
            await asyncio.sleep(0)
        await asyncio.sleep(1.05)
        await engine.runner.control({'action': 'approve', 'revision': 1})
        self.assertEqual(await asyncio.wait_for(task, 2), 'review accepted')
