import asyncio
import copy
import json
import unittest

from open_webui.execution.prompt_context import CONTROLLER_PREFIX, REVIEW_PREFIX, NODE_REVIEW_PREFIX, compact_controller_snapshots, review_evidence_packet
from open_webui.execution.workflows import WorkflowEngine
from open_webui.execution.trajectory import public_run, has_token_usage


class EfficiencyTests(unittest.IsolatedAsyncioTestCase):
    def test_review_evidence_deduplication_is_lossless_with_json_escaping_and_prior_reports(self):
        body = 'Report with quotes " and newline\n中文证据' * 300
        composite = json.dumps({'answer': 'Worker report:\n' + body}, ensure_ascii=False)
        history = {'first_retained_report_ref': 'old/leaf', 'history_messages': 4}
        def row(identity, text, kind='work'):
            return {'id': identity, 'role_id': 'worker', 'name': 'Worker', 'kind': kind,
                    'output': text, 'envelope': {'id': identity, 'history': history}}
        current = [row('current/composite', composite, 'composite'), row('current/leaf', body)]
        prior = [row('old/leaf', body)]
        inputs = {'goal': 'Keep historical evidence', 'candidate': composite, 'leaf': body,
                  'typed': [0, False, None, {'value': 42}], 'partial': 'prefix\n'+body+'\nsuffix'}
        original = copy.deepcopy((inputs, current, prior))
        encoded, stats = review_evidence_packet(inputs, current, prior, {'worker': history}, [])
        packet = json.loads(encoded)
        sources = {}
        def decode(value):
            if isinstance(value, dict) and list(value) == ['text_parts']:
                parts = []
                for part in value['text_parts']:
                    if 'text' in part:
                        parts.append(part['text'])
                    else:
                        text = sources[part['report_ref']]
                        parts.append(json.dumps(text, ensure_ascii=False)[1:-1] if part['encoding']=='json_string' else text)
                return ''.join(parts)
            if isinstance(value, dict): return {k: decode(v) for k,v in value.items()}
            if isinstance(value, list): return [decode(v) for v in value]
            return value
        for r in packet['reports']:
            sources[r['id']] = decode(r['body'])
            self.assertEqual(packet['history_bank'][r['envelope']['history_ref']], history)
        self.assertEqual(decode(packet['inputs']), inputs)
        self.assertEqual(sources, {r['id']: r['output'] for r in [*current, *prior]})
        self.assertEqual((inputs, current, prior), original)
        self.assertEqual(stats['report_count'], 3)
        self.assertEqual(stats['historical_report_count'], 1)
        self.assertLess(stats['packed_evidence_chars'], stats['raw_evidence_chars'] / 2)

    def test_review_snapshot_compaction_preserves_verdicts_and_never_compacts_worker_dialogue(self):
        messages = [{'role': 'user', 'content': REVIEW_PREFIX + '\nOLD_EVIDENCE\nReference ID for this response: old/review'},
                    {'role': 'assistant', 'content': 'Earlier verdict with its cited evidence and correction'},
                    {'role': 'user', 'content': REVIEW_PREFIX + '\nCURRENT_EVIDENCE'}]
        original = copy.deepcopy(messages)
        replay, count = compact_controller_snapshots(messages, reviewer=True)
        self.assertEqual(count, 1)
        self.assertNotIn('OLD_EVIDENCE', replay[0]['content'])
        self.assertIn('old/review', replay[0]['content'])
        self.assertEqual(replay[1:], messages[1:])
        self.assertEqual(compact_controller_snapshots(messages), (messages, 0))
        self.assertEqual(messages, original)

    def test_review_rename_still_compacts_legacy_manager_wording(self):
        for title in ('Manager', 'Controller'):
            messages = [{'role': 'user', 'content': 'You are the independent Reviewer, not the '+title+' or author.\nReference ID for this response: old/review'},
                        {'role': 'assistant', 'content': 'original verdict'},
                        {'role': 'user', 'content': REVIEW_PREFIX + ' latest evidence'}]
            replay, count = compact_controller_snapshots(messages, reviewer=True)
            self.assertEqual(count, 1)
            self.assertIn('old/review', replay[0]['content'])
            self.assertEqual(replay[1:], messages[1:])

    def test_composable_review_compacts_old_snapshot_but_retains_original_verdict(self):
        messages = [{'role': 'user', 'content': NODE_REVIEW_PREFIX + '\nOLD_BODY'},
                    {'role': 'assistant', 'content': 'original verdict and private evidence'},
                    {'role': 'user', 'content': NODE_REVIEW_PREFIX + '\nCURRENT_BODY'}]
        replay, count = compact_controller_snapshots(messages, reviewer=True)
        self.assertEqual(count, 1)
        self.assertNotIn('OLD_BODY', replay[0]['content'])
        self.assertEqual(replay[1:], messages[1:])

    async def test_scoped_workers_do_not_receive_unrequested_global_material(self):
        async def invoke(binding, context, native):
            self.assertNotIn('UNRELATED_GLOBAL_MATERIAL', context[-1]['content'])
            self.assertIn('assigned work', context[-1]['content'])
            self.assertIn('selected evidence', context[-1]['content'])
            return self.response('done')
        engine = self.engine({'input_policy': 'scoped', 'nodes': [
            {'id': 'work', 'op': 'call', 'binding': 'lead', 'instruction': 'assigned work',
             'inputs': {'evidence': {'source': 'literal', 'value': 'selected evidence'}}}],
            'edges': [], 'result_node': 'work'}, invoke, 'UNRELATED_GLOBAL_MATERIAL')
        await engine.execute()

    async def test_scoped_worker_can_explicitly_request_the_goal_without_losing_it_as_a_duplicate(self):
        async def invoke(binding, context, native):
            self.assertEqual(context[-1]['content'].count('EXPLICIT_GOAL'), 1)
            return self.response('done')
        engine = self.engine({'input_policy': 'scoped', 'nodes': [
            {'id': 'work', 'op': 'call', 'binding': 'lead', 'instruction': 'Use supplied task',
             'inputs': {'task': {'source': 'input', 'pointer': '/task'}}}],
            'edges': [], 'result_node': 'work'}, invoke, 'EXPLICIT_GOAL')
        await engine.execute()

    async def publish(self, record):
        pass

    @staticmethod
    def response(text):
        return {'choices': [{'message': {'content': text}}]}

    def engine(self, plan, invoke, task='shared task'):
        return WorkflowEngine(
            {'id': 'efficiency', 'name': 'Efficiency', 'lead_id': 'lead', 'graph': plan},
            [{'id': 'lead', 'name': 'Lead', 'framework_id': 'direct'}],
            task, invoke, self.publish,
        )

    def test_only_superseded_controller_requests_are_compacted(self):
        messages = [
            {'role': 'user', 'content': CONTROLLER_PREFIX + 'old graph\nReference ID for this response: run/old'},
            {'role': 'assistant', 'content': 'private decision and report reference'},
            {'role': 'user', 'content': 'an ordinary role task'},
            {'role': 'assistant', 'content': 'private report'},
            {'role': 'user', 'content': CONTROLLER_PREFIX + 'latest complete state'},
        ]
        original = copy.deepcopy(messages)
        value, count = compact_controller_snapshots(messages)
        self.assertEqual(count, 1)
        self.assertIn('run/old', value[0]['content'])
        self.assertNotIn('old graph', value[0]['content'])
        self.assertEqual(value[1:], messages[1:])
        self.assertEqual(messages, original)

    async def test_native_usage_survives_final_response_with_no_usage(self):
        async def invoke(binding, context, native):
            await engine.observe('lead', 'usage', {'prompt_tokens': 42, 'completion_tokens': 7})
            await engine.observe('lead', 'usage', {'completion_tokens': 8})
            return self.response('done')
        engine = self.engine({'nodes': [{'id': 'work', 'op': 'call', 'binding': 'lead', 'inputs': {}}], 'edges': [], 'result_node': 'work'}, invoke)
        await engine.execute()
        self.assertEqual(engine.record['calls'][0]['usage'], {'prompt_tokens': 42, 'completion_tokens': 8})

    async def test_shared_task_is_supplied_once_and_other_inputs_remain_exact(self):
        for field, expected in (('task', 1), ('draft', 2)):
            async def invoke(binding, context, native):
                prompt = context[-1]['content']
                self.assertEqual(prompt.count('UNIQUE_SHARED_TASK'), expected)
                self.assertIn('other evidence', prompt)
                if field == 'draft':
                    self.assertIn('"draft"', prompt)
                return self.response('done')
            engine = self.engine({'nodes': [{'id': 'work', 'op': 'call', 'binding': 'lead', 'inputs': {field: {'source': 'input', 'pointer': '/task'}, 'evidence': {'source': 'literal', 'value': 'other evidence'}}}], 'edges': [], 'result_node': 'work'}, invoke, 'UNIQUE_SHARED_TASK')
            await engine.execute()

    async def test_completed_result_does_not_trigger_another_controller_call(self):
        calls = []
        async def invoke(binding, context, native):
            prompt = context[-1]['content']; calls.append(prompt)
            return self.response('{"action":"wait"}' if prompt.startswith(CONTROLLER_PREFIX) else 'finished')
        engine = self.engine({'nodes': [{'id': 'work', 'op': 'call', 'binding': 'lead', 'inputs': {}}], 'edges': [], 'result_node': 'work', 'controller': {'binding': 'lead', 'initial': False, 'on_events': True}}, invoke)
        self.assertEqual(await engine.execute(), 'finished')
        self.assertEqual(len(calls), 1)

    async def test_transport_compaction_preserves_durable_role_conversation(self):
        async def invoke(binding, context, native):
            self.assertNotIn('OLD_SNAPSHOT_BODY', context[0]['content'])
            self.assertIn('private fact', context[1]['content'])
            return self.response('continued')
        engine = self.engine({'nodes': [{'id': 'work', 'op': 'call', 'binding': 'lead', 'inputs': {}}], 'edges': [], 'result_node': 'work'}, invoke)
        history = [{'role': 'user', 'content': CONTROLLER_PREFIX + 'OLD_SNAPSHOT_BODY' * 500}, {'role': 'assistant', 'content': 'private fact'}]
        engine.contexts['lead'] = history
        engine.role_states['lead']['messages'] = history
        await engine.execute()
        self.assertIn('OLD_SNAPSHOT_BODY', engine.contexts['lead'][0]['content'])
        stats = engine.record['calls'][0]['request_stats']
        self.assertEqual(stats['compacted_snapshots'], 1)
        self.assertGreater(stats['replay_chars_removed'], 7000)
        self.assertLess(stats['adapter_context_chars'], stats['retained_context_chars'])

    def test_old_empty_usage_is_recovered_for_public_and_project_views_without_editing_history(self):
        record = {'status': 'completed', 'calls': [{'id': 'call', 'status': 'completed', 'usage': {}}],
                  'events': [{'call_id': 'call', 'type': 'model.usage', 'data': {'prompt_tokens': 42, 'completion_tokens': 8}}]}
        self.assertEqual(public_run(record)['calls'][0]['usage']['prompt_tokens'], 42)
        self.assertEqual(record['calls'][0]['usage'], {})

    def test_missing_partial_or_cost_only_usage_is_not_falsely_metered(self):
        for value in ({}, {'reported_cost_usd': 0.1}, {'prompt_tokens': 2}, {'prompt_tokens': True, 'completion_tokens': 0}):
            self.assertFalse(has_token_usage(value))
        self.assertTrue(has_token_usage({'prompt_tokens': 0, 'completion_tokens': 0}))

    async def test_explicit_controller_answer_keeps_precedence_over_a_committed_graph_result(self):
        from open_webui.execution.graph_runtime import GraphRunner
        async def invoke(binding, context, native):
            self.fail('No extra model call is needed after completion')
        plan = {'nodes': [{'id': 'work', 'op': 'call', 'binding': 'lead', 'inputs': {}}], 'edges': [], 'result_node': 'work'}
        engine = self.engine(plan, invoke)
        runner = GraphRunner(engine, plan)
        runner.states['work'].update(status='completed', output={'report': 'graph result'})
        runner.final_answer = 'controller final'
        self.assertEqual(await runner.run(), 'controller final')


if __name__ == '__main__':
    unittest.main()
