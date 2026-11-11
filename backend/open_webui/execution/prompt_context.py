"""Remove superseded graph snapshots from replay, retaining decisions and durable originals."""

import re
import json
import copy

CONTROLLER_PREFIX = 'You control a workflow graph over peer callable modules.'
REVIEW_PREFIX = 'You are the independent Reviewer,'
NODE_REVIEW_PREFIX = 'Execute the review responsibilities below'
EVIDENCE_NOTE = (
    'Each current report must be checked. reports[].body appears once; inputs/composites may reuse exact body text '
    'through text_parts. Concatenate literal text and report_ref bodies in order; encoding=json_string means '
    'JSON-string escaping without surrounding quotes. This is lossless transport, not a summary. '
    'history_ref selects history_bank. current=false marks earlier evidence, not another current answer. '
    'Check the supplied facts and constraints; do not re-litigate generic caveats without contradictory evidence. '
    'Keep summary concise; preserve all concrete issues and checked references.\nEvidence JSON:\n'
)


def review_evidence_packet(inputs, current, earlier, manifests, edges):
    """Lossless prompt transport: one body per report, explicit references elsewhere.

    Typed invocation values, durable histories and reviewer validation are not
    modified. Only exact text matches are factored; no model summary or evidence
    truncation is involved. Native leaves precede composite rollups to avoid
    cycles when a composite embeds its own leaf bodies.
    """
    rows = sorted([*current, *earlier], key=lambda r: r.get('kind') == 'composite')
    sources, reports, history_bank = [], [], {}
    current_ids = {r['id'] for r in current}
    literal_chars = reference_chars = 0

    def encode_text(text):
        nonlocal literal_chars, reference_chars
        parts, offset = [], 0
        while offset < len(text):
            found = []
            for identity, source in sources:
                for encoding, value in (('text', source), ('json_string', json.dumps(source, ensure_ascii=False)[1:-1])):
                    if len(value) < 80:
                        continue
                    index = text.find(value, offset)
                    if index >= 0:
                        found.append((index, -len(value), identity, encoding, value))
            if not found:
                parts.append({'text': text[offset:]})
                break
            index, _, identity, encoding, value = min(found)
            if index > offset:
                parts.append({'text': text[offset:index]})
            parts.append({'report_ref': identity, 'encoding': encoding})
            reference_chars += len(value)
            offset = index + len(value)
        if not any('report_ref' in part for part in parts):
            literal_chars += len(text)
            return text
        literal_chars += sum(len(p.get('text', '')) for p in parts)
        return {'text_parts': parts}

    def encode(value):
        if isinstance(value, str):
            return encode_text(value)
        if isinstance(value, list):
            return [encode(v) for v in value]
        if isinstance(value, dict):
            return {k: encode(v) for k, v in value.items()}
        return value

    for row in rows:
        envelope = copy.deepcopy(row.get('envelope') or {})
        history = envelope.pop('history', None)
        if history:
            key = next((k for k, v in history_bank.items() if v == history), None) or 'h' + str(len(history_bank) + 1)
            history_bank[key] = history
            envelope['history_ref'] = key
        reports.append({'id': row['id'], 'role_id': row['role_id'], 'name': row['name'],
            'kind': row.get('kind', 'work'), 'current': row['id'] in current_ids,
            'run_status': row.get('run_status'), 'review_status': row.get('review_status'),
            'envelope': envelope, 'body': encode_text(row['output'])})
        sources.append((row['id'], row['output']))
    roles = {}
    for identity, history in manifests.items():
        key = next((k for k,v in history_bank.items() if v == history), None) or 'h' + str(len(history_bank) + 1)
        history_bank[key] = history
        roles[identity] = {'history_ref': key}
    packet = {'encoding': 'evidence-text/v1', 'reports': reports, 'inputs': encode(inputs),
        'role_histories': roles, 'history_bank': history_bank, 'dependencies': edges}
    raw_chars = len(json.dumps({'inputs': inputs, 'current': current, 'earlier': earlier,
        'manifests': manifests, 'dependencies': edges}, ensure_ascii=False))
    compact = json.dumps(packet, ensure_ascii=False, separators=(',', ':'))
    return compact, {'raw_evidence_chars': raw_chars, 'packed_evidence_chars': len(compact),
        'referenced_text_chars': reference_chars, 'report_count': len(rows),
        'current_report_count': len(current), 'historical_report_count': len(earlier),
        'lossless': True}


def compact_controller_snapshots(messages, *, reviewer=False):
    result, replaced = [], 0
    prior_controller = False
    for index, message in enumerate(messages):
        content = message.get('content', '')
        if (
            index < len(messages) - 1
            and message.get('role') == 'user'
            and isinstance(content, str)
            and (content.startswith(CONTROLLER_PREFIX) or (reviewer and content.startswith((REVIEW_PREFIX, NODE_REVIEW_PREFIX))))
        ):
            reference = re.search(r'Reference ID for this response: ([^\s]+)', content)
            result.append({
                **message,
                'content': 'Earlier control snapshot superseded by the latest goal, graph and selected evidence. '
                'Its following decision remains part of this conversation. Original request reference: '
                + (reference.group(1) if reference else 'retained in the run journal'),
            })
            replaced += 1
            prior_controller = content.startswith(CONTROLLER_PREFIX)
        elif prior_controller and message.get('role') == 'assistant' and index < len(messages)-1:
            # CURRENT PLAN is authoritative. Replaying all prior graph JSON costs
            # tokens without adding history; retain the decision and its reason.
            try:
                value = json.loads(content)
            except (ValueError, TypeError):
                value = None
            if isinstance(value, dict) and value.get('action') in {'propose_plan', 'patch_plan', 'wait'}:
                result.append({**message, 'content': json.dumps({
                    'action': value['action'], 'reason': value.get('reason', ''),
                    'history_note': 'Full decision and graph remain in the run journal. Use CURRENT PLAN and retained report references.'})})
                replaced += 1
            else:
                result.append(message)
            prior_controller = False
        else:
            result.append(message)
            prior_controller = False
    return result, replaced
