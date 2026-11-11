"""One framework-neutral event ledger for calls, tools, reports and handoffs."""

import copy
import math
import re
import time

SECRET_KEY = re.compile(r'authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|cookie', re.I)
SECRET_TEXT = re.compile(
    r'(?i)((?:authorization|api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*[\"\']?)(?:Bearer\s+)?[^\s\"\',}]+'
)


def clean(value, limit=16000, depth=0):
    if depth > 8:
        return '[nested value omitted]'
    if isinstance(value, dict):
        return {
            str(k)[:100]: '[redacted]' if SECRET_KEY.search(str(k)) else clean(v, limit, depth + 1)
            for k, v in list(value.items())[:80]
        }
    if isinstance(value, list):
        return [clean(v, limit, depth + 1) for v in value[:100]]
    if isinstance(value, str):
        text = SECRET_TEXT.sub(r'\1[redacted]', value)
        return text if len(text) <= limit else text[:limit] + '\n[truncated]'
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:limit]


def stamp():
    return int(time.time() * 1000)


def has_token_usage(value):
    return all(type(value.get(key)) in (int, float) and math.isfinite(value[key]) and value[key] >= 0
               for key in ('prompt_tokens', 'completion_tokens'))


def tool_state(payload, updated=False):
    if payload.get('status') in {'declined', 'denied', 'cancelled', 'canceled'}:
        return 'tool.blocked', 'blocked'
    if payload.get('status') in {'failed', 'error'}:
        return 'tool.failed', 'failed'
    if payload.get('done'):
        return 'tool.completed', 'finished'
    return ('tool.updated', 'updated') if updated else ('tool.started', 'started')


def add_event(record, kind, *, call=None, title='', **payload):
    events = record.setdefault('events', [])
    if len(events) >= 2048:
        record['events_omitted'] = record.get('events_omitted', 0) + 1
        return None
    event = {
        'seq': len(events) + 1,
        'at': stamp(),
        'type': kind,
        'title': title,
        **({'call_id': call['id'], 'role_id': call['participant_id']} if call else {}),
        **clean(payload),
    }
    events.append(event)
    record['last_event_at'] = event['at']
    if call:
        call['last_event_at'] = event['at']
    return event


def public_run(record):
    """Private checkpoints are for restoration, not repeated downloads by the live viewer."""
    value = copy.deepcopy({k: v for k, v in record.items() if k != 'role_contexts'})
    if 'child_workflows' in value:
        value['child_workflows'] = {k: public_run(v) for k, v in value['child_workflows'].items()}
    if 'events' not in value:
        value['legacy_trace'] = True
        for call in value.get('calls', []):
            event = add_event(value, 'call.started', call=call, title='Call started')
            event['at'] = call.get('created_at', value.get('created_at', 0)) * 1000
            if call['status'] not in {'queued', 'running'}:
                event = add_event(value, 'call.' + call['status'], call=call, title=call['status'].title())
                event['at'] = call.get('finished_at', call.get('created_at', 0)) * 1000
        value['events'].sort(key=lambda e: e['at'])
        for index, event in enumerate(value['events']):
            event['seq'] = index + 1
        value['last_event_at'] = max((e['at'] for e in value['events']), default=value.get('created_at', 0) * 1000)
    # A stopped server must not leave individual nodes appearing to be live.
    if value['status'] == 'interrupted':
        for call in value.get('calls', []):
            if call['status'] in {'running', 'queued'}:
                call['status'] = 'interrupted'
                call['phase'] = 'Interrupted'
    for call in value.get('calls', []):
        usage = next((e.get('data', {}) for e in reversed(value.get('events', []))
                      if e.get('call_id') == call['id'] and e['type'] == 'model.usage'), {})
        if usage:
            call['usage'] = {**usage, **(call.get('usage') or {})}
    for event in value.get('events', []):
        if event['type'].startswith('tool.') and event.get('data'):
            kind, suffix = tool_state(event['data'], event['type'] == 'tool.updated')
            event.update(type=kind, title=event['data'].get('name', 'Tool') + ' ' + suffix)
    return value
