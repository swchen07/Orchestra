import copy
import unittest

from open_webui.execution.claude import ExecutionError
from open_webui.execution.history_provenance import dialogue_digest, history_manifest, historical_review_evidence


def report(ref, body, status='failed', role='b'):
    return {'id': ref, 'role_id': role, 'output': body, 'run_id': ref.split('/')[0],
            'run_status': status, 'review_status': 'approved' if status == 'completed' else 'changes_requested'}


def pair(ref, body):
    return [{'role': 'user', 'content': 'Choose a tag\n\nReference ID for this response: ' + ref},
            {'role': 'assistant', 'content': body}]


class HistoryProvenanceTests(unittest.TestCase):
    def test_first_ever_and_accepted_attempt_are_distinct_without_broadcasting_values(self):
        rows = [report('failed/one', 'amber'), report('retry/two', 'cobalt'),
                report('accepted/three', 'saffron', 'completed'), report('peer/four', 'C_PRIVATE', role='c')]
        messages = pair('failed/one', 'amber') + pair('retry/two', 'cobalt') + pair('accepted/three', 'saffron')
        original = copy.deepcopy(messages)
        value = history_manifest('b', messages, rows)
        self.assertEqual(value['first_retained_report_ref'], 'failed/one')
        self.assertEqual(value['latest_approved_run_id'], 'accepted')
        self.assertEqual(value['attributed_reports'], 3)
        self.assertEqual(value['history_sha256'], dialogue_digest(messages))
        self.assertEqual(value['attempts'][0]['run_status'], 'failed')
        for secret in ('amber', 'cobalt', 'saffron', 'C_PRIVATE', 'peer/four'):
            self.assertNotIn(secret, str(value))
        self.assertEqual(messages, original)

    def test_unattributed_legacy_history_is_not_presented_as_a_complete_window(self):
        messages = [{'role': 'user', 'content': 'old'}, {'role': 'assistant', 'content': 'old result'}]
        messages += pair('new/report', 'new result')
        value = history_manifest('b', messages, [report('new/report', 'new result')])
        self.assertEqual(value['unattributed_assistant_messages'], 1)

    def test_review_keeps_cited_and_first_reports_outside_recent_twelve(self):
        prior = [report(f'run{i}/call', str(i)) for i in range(20)]
        selected = historical_review_evidence(prior, [{'output': 'Recall run2/call'}], 'candidate',
                                              {'b': {'first_retained_report_ref': 'run0/call'}})
        self.assertEqual(len(selected), 12)
        self.assertIn('run0/call', [r['id'] for r in selected])
        self.assertIn('run2/call', [r['id'] for r in selected])
        self.assertEqual(selected[0]['run_status'], 'failed')

    def test_review_does_not_silently_drop_referenced_evidence_when_budget_is_too_small(self):
        prior = [report(f'run{i}/call', str(i)) for i in range(13)]
        with self.assertRaisesRegex(ExecutionError, 'bounded window'):
            historical_review_evidence(prior, [], ' '.join(r['id'] for r in prior), {})

    def test_digest_changes_for_dropped_reordered_or_foreign_history(self):
        messages = pair('a/1', 'first') + pair('a/2', 'second')
        original = dialogue_digest(messages)
        self.assertNotEqual(original, dialogue_digest(messages[2:]))
        self.assertNotEqual(original, dialogue_digest(messages[2:] + messages[:2]))
        self.assertNotEqual(original, dialogue_digest(messages + pair('c/3', 'foreign')))
