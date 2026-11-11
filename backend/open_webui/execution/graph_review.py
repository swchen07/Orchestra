"""Independent result gate, using ordinary attributed module invocations."""

import hashlib
import json

from open_webui.execution.claude import ExecutionError
from open_webui.execution.graph_spec import json_object
from open_webui.execution.module_protocol import report_rows, review_verdict
from open_webui.execution.trajectory import add_event
from open_webui.execution.history_provenance import HISTORY_NOTE, historical_review_evidence
from open_webui.execution.prompt_context import review_evidence_packet, EVIDENCE_NOTE


def candidate_key(runner, content):
    return (runner.graph['revision'], runner.graph['result_node'], hashlib.sha256(content.encode()).hexdigest())


async def review_candidate(runner, content, key):
    config = runner.graph['reviewer']
    known = report_rows(runner.engine.record, runner.engine.prior_reports)
    rows = [r for r in report_rows(runner.engine.record) if r.get('kind') not in {'decision', 'review'}]
    required = [r['id'] for r in rows]
    prior = [r for r in runner.engine.prior_reports if r.get('kind') not in {'decision', 'review'}]
    manifests = {i: runner.engine.history_manifest(i) for i in runner.engine.contexts
                 if i != config['binding']}
    evidence = historical_review_evidence(prior, rows, content, manifests, runner.engine.task)
    if not rows:
        raise ExecutionError('Independent review requires committed worker evidence')
    packet, metrics = review_evidence_packet({'goal': runner.engine.task, 'candidate': content},
        rows, evidence, manifests, runner.graph['edges'])
    runner.engine.record.setdefault('prompt_assembly', {})['_reviewer'] = metrics
    prompt = (
        'You are the independent Reviewer, not the Controller or author. Check the original user task, '
        'candidate answer, executed dependencies and host-attributed reports. Recompute simple calculations. '
        'Model-authored source_ref/producer claims are untrusted: compare with the host envelope. '
        'Do not accept self-certification by the Controller. No tools. Return ONLY JSON '
        '{"verdict":"approve" or "revise","summary":"short evidence-based conclusion",'
        '"issues":["concrete correction"],"checked_report_refs":["exact host IDs checked"]}. '
        'Approve only with no unresolved issues and after checking every supplied current report. '
        'If the task requests independent progress or memory, use recorded evidence and disclose uncertainty. '
        'Readiness depends on declared predecessors; a slow branch may naturally finish while the Manager is planning. '
        'Lack of observed overlap alone is not a dependency violation: report it as unobserved rather than inventing timing. '
        'Do not claim access to another role\'s unshared private history. '
        'Controller.finish is an allowed terminal action after required work: an unused, unheld finish node may be skipped. '
        'Do not reject solely for that skip when the candidate is supported by committed worker reports. '
        'Controller report references in the host index are valid provenance, but are not independent verification. '
        'Earlier superseded attempts remain visible; judge the corrected candidate lineage and report genuine remaining issues. '
        + HISTORY_NOTE + ' Do not mark an earliest-tag recall wrong merely because a later attempt chose a new tag. '
        'Do not replace a requested factual recall with a disclaimer; ask for a corrected worker report if it is genuinely wrong. '
        '\nReviewer instructions:\n' + config.get('instructions', '')
        + '\nEvidence packet:\n' + EVIDENCE_NOTE + packet
        + '\nExecuted graph:\n' + json.dumps(runner.graph, ensure_ascii=False)
        + '\nHost plan revision history:\n'
        + json.dumps([{'revision': r['revision'], 'source': r['source'],
                       'node_ids': [n['id'] for n in r['graph']['nodes']]} for r in runner.revision_log])
        + '\nNode states:\n'
        + json.dumps({i: {k: v for k, v in s.items() if k != 'output'} for i, s in runner.states.items()}, ensure_ascii=False)
        + '\nHost reference index (includes control decisions for provenance, not as worker evidence):\n'
        + json.dumps([{'id': r['id'], 'instance_id': r['role_id'], 'kind': r.get('kind', 'work'),
                       'run_id': r.get('run_id'), 'run_status': r.get('run_status'),
                       'review_status': r.get('review_status'),
                       'timings': (r.get('envelope') or {}).get('timings', {})}
                      for r in known], ensure_ascii=False)
    )
    runner.engine.record['review'] = {'status': 'running', 'revision': key[0], 'required_report_refs': required}
    await runner.publish()
    identity = runner.binding({'id': '_reviewer', 'label': 'Reviewer', 'binding': config['binding']})
    call = await runner.engine.turn(identity, prompt, [r['envelope']['invocation_id'] for r in rows],
                                    kind='review', node_id='_reviewer', plan_revision=key[0])
    if call['status'] != 'completed':
        raise ExecutionError('Independent Reviewer failed; the candidate was not approved')
    try:
        verdict = review_verdict(json_object(call['output']), required, [r['id'] for r in known])
    except ValueError as error:
        raise ExecutionError('Invalid independent review: ' + str(error)) from None
    if key != candidate_key(runner, content):
        runner.engine.record['review'] = {'status': 'stale', 'call_id': call['id'], 'revision': key[0]}
        add_event(runner.engine.record, 'review.stale', call=call, title='Review superseded by a newer plan')
        await runner.publish()
        return
    runner.engine.record['review'] = {
        **verdict, 'status': 'approved' if verdict['verdict'] == 'approve' else 'changes_requested',
        'call_id': call['id'], 'revision': key[0], 'required_report_refs': required,
    }
    add_event(runner.engine.record, 'review.' + runner.engine.record['review']['status'], call=call,
              title='Independent review ' + runner.engine.record['review']['status'].replace('_', ' '), data=verdict)
    if verdict['verdict'] == 'approve':
        runner.approved_candidate = key
    else:
        runner.review_feedback = verdict
        runner.final_answer = None
        runner.result_enabled = False
        if not runner.graph.get('controller') or runner.review_repairs >= config.get('max_repairs', 1):
            raise ExecutionError('Reviewer requested changes; no Controller repair budget remains')
        runner.review_repairs += 1
        runner.controller_due = True
    await runner.publish()
