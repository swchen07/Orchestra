"""Host-owned transcript lineage, independent of a native session's lifetime."""

import hashlib
import json
import re

from open_webui.execution.claude import ExecutionError


HISTORY_NOTE = (
    'Native/transport context IDs may rotate while this role dialogue is retained. '
    'Completed role reports in failed runs remain part of its history; run failure does not erase them. '
    'First-ever means the earliest retained report, not the first report in the newest native session. '
    'For a particular accepted attempt, use its exact run/report reference. '
    'If there are unattributed historical messages, do not certify first-ever recall from the attributed window alone. '
    'This manifest contains references and statuses, never recalled values or peer dialogue.'
)


def dialogue_digest(messages):
    return hashlib.sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def history_manifest(identity, messages, reports):
    own = {r['id']: r for r in reports if r['role_id'] == identity}
    retained = []
    for index, message in enumerate(messages[:-1]):
        if message.get('role') != 'user' or messages[index + 1].get('role') != 'assistant':
            continue
        found = re.search(r'Reference ID for this response: ([^\s]+)\s*$', message.get('content', ''))
        if found and found[1] in own and found[1] not in retained:
            retained.append(found[1])
    attempts = {}
    for ref in retained:
        row = own[ref]
        run_id = row.get('run_id') or ref.split('/')[0]
        attempt = attempts.setdefault(run_id, {'run_id': run_id, 'run_status': row.get('run_status', 'unknown'),
                                                'review_status': row.get('review_status'), 'report_refs': []})
        attempt['report_refs'].append(ref)
    approved = [a for a in attempts.values() if a['run_status'].startswith('completed')
                and a['review_status'] == 'approved']
    rows = list(attempts.values())
    visible = rows if len(rows) <= 5 else [rows[0], *rows[-4:]]
    return {
        'instance_id': identity, 'history_messages': len(messages), 'history_sha256': dialogue_digest(messages),
        'attributed_reports': len(retained),
        'unattributed_assistant_messages': sum(m.get('role') == 'assistant' for m in messages) - len(retained),
        'first_retained_report_ref': retained[0] if retained else None,
        'latest_retained_report_ref': retained[-1] if retained else None,
        'latest_approved_run_id': approved[-1]['run_id'] if approved else None,
        'attempts': visible, 'omitted_attempts': len(rows) - len(visible),
    }


def historical_review_evidence(prior, current, candidate, manifests, task='', limit=12, max_chars=96000):
    """Never silently evict a cited/first-history report in favor of the last N bodies."""
    texts = '\n'.join([task, candidate, *(r['output'] for r in current)])
    first_refs = {m.get('first_retained_report_ref') for m in manifests.values()}
    required = [r for r in prior if r['id'] in texts or r['id'] in first_refs]
    if len(required) > limit or sum(len(r['output']) for r in required) > max_chars:
        raise ExecutionError('Independent review historical evidence exceeds its bounded window; narrow the task references')
    selected = {r['id'] for r in required}
    size = sum(len(r['output']) for r in required)
    for row in reversed(prior):
        if len(selected) >= limit:
            break
        if row['id'] not in selected and size + len(row['output']) <= max_chars:
            selected.add(row['id']); size += len(row['output'])
    return [r for r in prior if r['id'] in selected]
