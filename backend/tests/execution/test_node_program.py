import asyncio
import copy
import json
import unittest
from unittest.mock import patch

from open_webui.execution.composed_workflow import distribute_review_definition
from open_webui.execution.adaptive_workflow import adaptive_definition
from open_webui.execution.node_program import upgrade_graph
from open_webui.execution.graph_spec import validate_graph, apply_patch
from open_webui.execution.workflow_templates import normalize_graph_template, compile_template
from open_webui.execution.workflows import WorkflowEngine
from open_webui.execution.module_protocol import usage_budget


BACKEND = {'id': 'catalog_flash', 'name': 'Flash', 'framework_id': 'direct', 'model_id': 'flash'}


class NodeProgramTests(unittest.IsolatedAsyncioTestCase):
    def compile(self, definition):
        definition = normalize_graph_template(definition)
        return compile_template(definition, {r['id']: BACKEND['id'] for r in definition['roles']}, [BACKEND])

    def approval(self, engine, answer):
        return {'verdict': 'approve', 'summary': 'Checked the evidence.', 'issues': [], 'answer': answer,
                'checked_report_refs': [c['report_id'] for c in engine.record['calls'] if c['status'] == 'completed' and c['kind'] == 'work']}

    async def test_saved_assignment_parallel_execution_review_and_private_second_turn(self):
        template, bindings = self.compile(distribute_review_definition())
        self.assertNotIn('controller', template['graph'])
        self.assertNotIn('reviewer', template['graph'])
        previous = []
        for turn in (1, 2):
            started = {key: asyncio.Event() for key in ('executor_01', 'executor_02')}
            finished = set()
            async def invoke(binding, messages, native):
                identity = binding['id']
                body = messages[-1]['content']
                if identity == 'controller':
                    result = {'task_b': 'B_PACKAGE', 'task_c': 'C_PACKAGE', 'selected_nodes':['execute_b','execute_c']}
                elif identity in started:
                    self.assertIn('controller', finished)
                    mine, peer = ('B_PACKAGE', 'C_PACKAGE') if identity == 'executor_01' else ('C_PACKAGE', 'B_PACKAGE')
                    self.assertIn(mine, body)
                    self.assertNotIn(peer, body)
                    history = [m['content'] for m in messages[:-1] if m['role'] == 'assistant']
                    self.assertEqual(history, [] if turn == 1 else [identity + '_PRIVATE'])
                    self.assertNotIn(('executor_02' if identity == 'executor_01' else 'executor_01') + '_PRIVATE', str(messages))
                    started[identity].set()
                    await asyncio.wait_for(asyncio.gather(*(e.wait() for e in started.values())), 1)
                    result = identity + '_PRIVATE'
                else:
                    self.assertTrue(set(started) <= finished)
                    result = self.approval(engine, 'FINAL_' + str(turn))
                finished.add(identity)
                return {'choices': [{'message': {'content': result if isinstance(result, str) else json.dumps(result)}}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
            async def publish(record):
                pass
            engine = WorkflowEngine(template, bindings, 'Current goal', invoke, publish, previous=previous)
            with patch('open_webui.execution.workflows.claim_native_context', return_value=False):
                self.assertEqual(await asyncio.wait_for(engine.execute(), 3), 'FINAL_' + str(turn))
            self.assertEqual(len(engine.record['calls']), 4)
            self.assertEqual(usage_budget(engine.record['calls'])['reported_tokens'], 48)
            self.assertEqual(engine.record['review']['status'], 'approved')
            self.assertTrue(all(s['status'] == 'completed' for s in engine.record['node_states'].values()))
            self.assertEqual(len({s['scope_id'] for s in engine.role_states.values()}), 4)
            previous.append(copy.deepcopy(engine.record))

    async def test_generated_work_uses_same_ports_review_gate_and_fast_branch_readiness(self):
        definition = adaptive_definition()
        definition['graph']['controller']['on_events'] = False
        template, bindings = self.compile(definition)
        next_started = asyncio.Event()
        observed = []
        async def invoke(binding, messages, native):
            identity = binding['id']
            if identity == 'controller':
                result = {'action': 'propose_plan', 'graph': {'nodes': [
                    {'id': key, 'op': 'call', 'binding': BACKEND['id'], 'context_key': key, 'inputs': {}, 'instruction': key}
                    for key in ('fast', 'slow')
                ] + [
                    {'id': 'next', 'op': 'call', 'binding': BACKEND['id'], 'context_key': 'next', 'inputs': {'prior': {'source': 'node', 'node': 'fast', 'pointer': '/report'}}},
                    {'id': 'answer', 'op': 'finish', 'inputs': {'fast': {'source': 'node', 'node': 'next', 'pointer': '/report'}, 'slow': {'source': 'node', 'node': 'slow', 'pointer': '/report'}}}
                ], 'edges': [], 'result_node': 'answer'}}
            elif identity == 'reviewer':
                result = self.approval(engine, 'REVIEWED')
            else:
                if identity == 'slow':
                    await asyncio.wait_for(next_started.wait(), 1)
                elif identity == 'next':
                    self.assertNotIn('slow', observed)
                    next_started.set()
                observed.append(identity)
                result = identity + '_REPORT'
            return {'choices': [{'message': {'content': result if isinstance(result, str) else json.dumps(result)}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
        async def publish(record):
            pass
        engine = WorkflowEngine(template, bindings, 'Goal', invoke, publish, registry=[BACKEND])
        self.assertEqual(await asyncio.wait_for(engine.execute(), 3), 'REVIEWED')
        self.assertLess(observed.index('next'), observed.index('slow'))
        self.assertEqual(len(engine.record['calls']), 5)
        self.assertEqual(engine.record['plan']['outputs']['answer']['node'], 'workflow_output')
        self.assertEqual(engine.record['plan']['outputs']['candidate']['node'], 'answer')
        self.assertEqual([c['node_id'] for c in engine.record['calls'] if c['kind'] == 'decision'], ['plan_graph'])

    async def test_rejected_review_cannot_deliver_and_repairs_keep_original_reports(self):
        template, bindings = self.compile(adaptive_definition())
        planner = next(n for n in template['graph']['nodes'] if n['op'] == 'plan')
        planner['planning']['on_events'] = False
        plans = reviews = 0
        async def invoke(binding, messages, native):
            nonlocal plans, reviews
            if binding['id'] == 'controller':
                plans += 1
                if plans == 1:
                    result = {'action': 'propose_plan', 'graph': {'nodes': [{'id': 'first', 'op': 'call', 'binding': BACKEND['id'], 'context_key': 'worker', 'inputs': {}}], 'edges': [], 'result_node': 'first'}}
                else:
                    result = {'action': 'patch_plan', 'base_revision': engine.runner.graph['revision'], 'changes': [
                        {'op': 'add_node', 'node': {'id': 'corrected', 'op': 'call', 'binding': BACKEND['id'], 'context_key': 'worker', 'inputs': {'prior': {'source': 'node', 'node': 'first', 'pointer': '/report'}}}},
                        {'op': 'set_output', 'port': 'candidate', 'ref': {'source': 'node', 'node': 'corrected', 'pointer': '/report'}}]}
            elif binding['id'] == 'reviewer':
                reviews += 1
                result = self.approval(engine, 'FIXED')
                if reviews == 1:
                    result.update(verdict='revise', issues=['Correct the reported value.'], answer='')
            else:
                result = 'worker report'
            return {'choices': [{'message': {'content': result if isinstance(result, str) else json.dumps(result)}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
        async def publish(record):
            pass
        engine = WorkflowEngine(template, bindings, 'Goal', invoke, publish, registry=[BACKEND])
        self.assertEqual(await asyncio.wait_for(engine.execute(), 3), 'FIXED')
        self.assertEqual((plans, reviews), (2, 2))
        self.assertEqual(len(engine.record['calls']), 6)
        self.assertEqual(usage_budget(engine.record['calls'])['reported_tokens'], 72)
        self.assertEqual(engine.record['node_states']['first']['status'], 'completed')

    def test_import_outputs_and_controls_are_data_not_template_name(self):
        legacy = adaptive_definition()['graph']
        imported = validate_graph(upgrade_graph(legacy))
        self.assertNotIn('controller', imported)
        self.assertNotIn('reviewer', imported)
        self.assertEqual({n['op'] for n in imported['nodes']}, {'input', 'plan', 'review', 'output'})
        self.assertNotIn('candidate', imported['outputs'])
        self.assertEqual(upgrade_graph(imported), imported)
        graph = normalize_graph_template(distribute_review_definition())['graph']
        bad = copy.deepcopy(graph)
        bad['outputs']['answer'] = {'source': 'output', 'port': 'answer'}
        with self.assertRaisesRegex(ValueError, 'cycle'):
            validate_graph(bad)
        states = {n['id']: {'status': 'pending'} for n in graph['nodes']}
        graph['outputs']['b_report'] = {'source': 'node', 'node': 'execute_b', 'pointer': '/report'}
        next(n for n in graph['nodes'] if n['op']=='review')['review']={'max_repairs':0}
        next(n for n in graph['nodes'] if n['id'] == 'review')['inputs']['report_b'] = {'source': 'output', 'port': 'b_report'}
        graph = validate_graph(graph)
        states['review']['status'] = 'running'
        with self.assertRaisesRegex(ValueError, 'started'):
            apply_patch(graph, [{'op': 'set_output', 'port': 'b_report', 'ref': {'source': 'node', 'node': 'assign', 'pointer': '/report'}}], states)

    async def test_two_nodes_sharing_one_private_context_do_not_overlap(self):
        definition = distribute_review_definition()
        definition['graph']['nodes'][2]['binding'] = 'executor_01'
        definition['graph']['nodes'][2]['context_key'] = 'executor_01'
        template, bindings = self.compile(definition)
        busy = False
        async def invoke(binding, messages, native):
            nonlocal busy
            if binding['id'] == 'controller':
                result = {'task_b': 'B', 'task_c': 'C', 'selected_nodes':['execute_b','execute_c']}
            elif binding['id'] == 'reviewer':
                result = self.approval(engine, 'SERIAL_PRIVATE_CONTEXT')
            else:
                self.assertFalse(busy)
                busy = True
                await asyncio.sleep(.01)
                busy = False
                result = 'REPORT'
            return {'choices': [{'message': {'content': result if isinstance(result, str) else json.dumps(result)}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
        async def publish(record):
            pass
        engine = WorkflowEngine(template, bindings, 'Goal', invoke, publish)
        self.assertEqual(await asyncio.wait_for(engine.execute(), 3), 'SERIAL_PRIVATE_CONTEXT')

    async def test_invalid_review_cannot_self_certify_delivery(self):
        template, bindings = self.compile(distribute_review_definition())
        async def invoke(binding, messages, native):
            if binding['id'] == 'controller':
                result = {'task_b': 'B', 'task_c': 'C', 'selected_nodes':['execute_b','execute_c']}
            elif binding['id'] == 'reviewer':
                result = {'verdict': 'approve', 'summary': 'Trust me.', 'issues': [], 'checked_report_refs': [], 'answer': 'UNVERIFIED'}
            else:
                result = 'REPORT'
            return {'choices': [{'message': {'content': result if isinstance(result, str) else json.dumps(result)}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
        async def publish(record):
            pass
        engine = WorkflowEngine(template, bindings, 'Goal', invoke, publish)
        from open_webui.execution.claude import ExecutionError
        with self.assertRaises(ExecutionError):
            await asyncio.wait_for(engine.execute(), 3)
        self.assertEqual(engine.record['node_states']['review']['status'], 'failed')

    async def test_control_replay_keeps_dialogue_while_rebasing_superseded_native_snapshots(self):
        from open_webui.execution.prompt_context import CONTROLLER_PREFIX
        template, bindings = self.compile(adaptive_definition())
        next(n for n in template['graph']['nodes'] if n['op'] == 'plan')['planning']['on_events'] = False
        next(p for p in bindings if p['id'] == 'controller')['framework_id'] = 'dsh'
        async def invoke(binding, messages, native):
            if binding['id'] == 'controller':
                self.assertIsNone(native)
                self.assertNotIn('OLD_SNAPSHOT_BODY', messages[0]['content'])
                self.assertIn('retained private decision', messages[1]['content'])
                result = {'action': 'propose_plan', 'graph': {'nodes': [{'id': 'work', 'op': 'call', 'binding': BACKEND['id'], 'context_key': 'worker', 'inputs': {}}], 'edges': [], 'result_node': 'work'}}
            elif binding['id'] == 'reviewer':
                result = self.approval(engine, 'REPLAYED')
            else:
                result = 'REPORT'
            return {'choices': [{'message': {'content': result if isinstance(result, str) else json.dumps(result)}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
        async def publish(record):
            pass
        engine = WorkflowEngine(template, bindings, 'Goal', invoke, publish, registry=[BACKEND])
        history = [{'role': 'user', 'content': CONTROLLER_PREFIX + ' OLD_SNAPSHOT_BODY'}, {'role': 'assistant', 'content': 'retained private decision'}]
        engine.contexts['controller'] = history
        engine.role_states['controller']['messages'] = history
        engine.native_sessions['controller'] = 'old-native-session'
        self.assertEqual(await asyncio.wait_for(engine.execute(), 3), 'REPLAYED')
        decision = next(c for c in engine.record['calls'] if c['kind'] == 'decision')
        self.assertTrue(decision['request_stats']['native_rebased_for_compaction'])
        self.assertIn('OLD_SNAPSHOT_BODY', engine.contexts['controller'][0]['content'])
        self.assertNotIn('controller', engine.native_sessions)

