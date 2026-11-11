import copy
import unittest

from open_webui.execution.native_accounting import ledger, accounting_usage, module_timings
from open_webui.execution.claude_usage import ClaudeNativeUsage, task_receipt, recover_call


def agent(identity, parent, start, end, amount):
    return {'id': identity, 'parent_id': parent, 'status': 'completed',
            'usage': {'prompt_tokens': amount, 'completion_tokens': 1, 'cache_read_tokens': amount // 2},
            'source_event_ids': ['source:' + identity], 'timings': {'started_at': start, 'finished_at': end}}


class NativeAccountingTests(unittest.TestCase):
    def test_native_footer_variants_and_retained_projection_reconcile_without_changing_sources(self):
        self.assertEqual(task_receipt('answer\nagentId: child\n<usage>subagent_tokens: 27\ntool_uses: 0\nduration_ms: 3</usage>'), (27, 3, 'child'))
        own, child = agent('root', None, 0, 10, 10), agent('child', 'root', 1, 8, 20)
        child['native_agent_id'] = 'child'
        tree = ledger('claude-code', 'root', [own, child], reported_inclusive={'prompt_tokens': 30, 'completion_tokens': 8})
        call = {'execution': {'native_usage': tree}, 'tools': {'tool': {'name': 'Agent', 'done': True,
            'output': [{'type': 'text', 'text': 'answer\nagentId: child\n<usage>subagent_tokens: 27\ntool_uses: 0\nduration_ms: 3</usage>'}]}}}
        before = copy.deepcopy(call)
        recover_call(call)
        self.assertTrue(call['execution']['native_usage']['complete'])
        self.assertEqual(call['usage']['total_tokens'], 38)
        self.assertEqual(call['tools'], before['tools'])
        self.assertEqual(call['execution']['native_usage']['agents'][1]['source_event_ids'], child['source_event_ids'])
    def test_self_descendants_and_overlapping_intervals_are_not_double_added(self):
        rows = [agent('root', None, 0, 100, 10), agent('a', 'root', 10, 60, 20),
                agent('b', 'root', 30, 80, 30), agent('grandchild', 'a', 20, 40, 40)]
        value = ledger('test', 'root', rows + [copy.deepcopy(rows[1])])
        self.assertTrue(value['complete'])
        self.assertEqual(value['self']['total_tokens'], 11)
        self.assertEqual(value['children']['total_tokens'], 93)
        self.assertEqual(accounting_usage(value)['total_tokens'], 104)
        self.assertEqual(value['timings']['self_exclusive_ms'], 30)  # union [10,80], not 50+50
        self.assertEqual(value['agents'][1]['timings']['exclusive_wall_ms'], 30)
        self.assertEqual(value['timings']['tree_exclusive_ms'], 130)  # parallel work, not wall time
        self.assertEqual(value['timings']['wall_ms'], 100)
        module = module_timings({'started_at': 0, 'finished_at': 120, 'invocation_ms': 120}, value)
        self.assertEqual(module['self_exclusive_ms'], 50)  # native root 30 + host overhead 20
        self.assertEqual(module['tree_exclusive_ms'], 150)

    def test_conflicting_source_ownership_is_rejected_and_missing_children_stay_unknown(self):
        root, child = agent('root', None, 0, 10, 1), agent('child', 'root', 1, 8, 2)
        child['source_event_ids'] = root['source_event_ids']
        with self.assertRaises(ValueError):
            ledger('test', 'root', [root, child])
        value = ledger('test', 'root', [root], complete=False)
        self.assertNotIn('total_tokens', value['children'])
        self.assertFalse(value['complete'])

    def test_authoritative_inclusive_is_a_crosscheck_not_an_additional_charge(self):
        root, child = agent('root', None, 0, 10, 10), agent('child', 'root', 1, 8, 20)
        inclusive = {'prompt_tokens': 30, 'completion_tokens': 2}
        value = ledger('test', 'root', [root, child], reported_inclusive=inclusive)
        self.assertTrue(value['complete'])
        self.assertEqual(accounting_usage(value)['total_tokens'], 32)
        mismatch = ledger('test', 'root', [root, child], reported_inclusive={'prompt_tokens': 40, 'completion_tokens': 2})
        self.assertFalse(mismatch['complete'])
        self.assertEqual(mismatch['inclusive']['total_tokens'], 42)
        self.assertEqual(mismatch['children']['total_tokens'], 31)

    def test_claude_native_completion_footer_overrides_placeholder_output_and_stream_replay(self):
        meter = ClaudeNativeUsage()
        meter.feed({'type': 'system', 'subtype': 'init', 'session_id': 'session'})
        meter.feed({'type': 'assistant', 'message': {'id': 'parent', 'content': [
            {'type': 'tool_use', 'id': 'tool', 'name': 'Agent'}]}})
        child = {'type': 'assistant', 'agent_id': 'child', 'parent_tool_use_id': 'tool',
                 'message': {'id': 'child-response', 'usage': {'input_tokens': 20, 'output_tokens': 1}}}
        meter.feed(child); meter.feed(child)
        meter.feed({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'tool',
            'content': 'actual answer\n<usage>total_tokens: 27\ntool_uses: 0\nduration_ms: 3</usage>'}]}})
        meter.feed({'type': 'result', 'usage': {'input_tokens': 10, 'output_tokens': 2},
                    'modelUsage': {'flash': {'inputTokens': 30, 'outputTokens': 9}}})
        value = meter.finish()
        self.assertTrue(value['complete'])
        self.assertEqual(value['self']['total_tokens'], 12)
        self.assertEqual(value['children']['total_tokens'], 27)
        self.assertEqual(value['inclusive']['total_tokens'], 39)
        self.assertEqual(len(value['agents']), 2)
