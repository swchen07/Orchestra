"""Invocation-scoped native agent trees. Aggregate own facts, never rolled-up rows.

Tokens include cache input once. Elapsed time and exclusive agent work are
different quantities: exclusive elapsed removes the UNION of direct-child
active intervals, not their sum. Concurrent siblings may overlap in work time.
"""

import copy
import math


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def tokens(raw):
    value = {k: raw[k] for k in ('prompt_tokens', 'completion_tokens', 'cache_read_tokens',
                                'cache_write_tokens', 'total_tokens') if number(raw.get(k))}
    if all(k in value for k in ('prompt_tokens', 'completion_tokens')):
        value['total_tokens'] = value['prompt_tokens'] + value['completion_tokens']
    return value


def add_usage(rows):
    result = {}
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'cache_read_tokens', 'cache_write_tokens'):
        values = [r[key] for r in rows if number(r.get(key))]
        if values:
            result[key] = sum(values)
    return result


def union_ms(intervals, start, end):
    intervals = sorted((max(start, a), min(end, b)) for a, b in intervals if number(a) and number(b)
                       and b >= a and b > start and a < end)
    total, through = 0, start
    for a, b in intervals:
        total += max(0, b - max(a, through))
        through = max(through, b)
    return total


def ledger(framework, root_id, agents, *, complete=True, reported_inclusive=None, warnings=()):
    unique, source_owner, issues = {}, {}, list(warnings)
    for agent in copy.deepcopy(agents):
        identity = agent['id']
        sources = list(dict.fromkeys(agent.get('source_event_ids') or []))
        agent.update(usage=tokens(agent.get('usage') or {}), source_event_ids=sources)
        if identity in unique:
            if agent != unique[identity]:
                issues.append('Conflicting native agent snapshot: ' + identity)
            continue
        # A source event belongs to exactly one native agent. Streaming replay
        # of a cumulative snapshot replaces that row before reaching this fold.
        if any(s in source_owner and source_owner[s] != identity for s in sources):
            raise ValueError('A native usage source was attributed to two agents')
        for source in sources:
            source_owner[source] = identity
        unique[identity] = agent
    if root_id not in unique:
        raise ValueError('A native tree needs its root agent')
    for identity, agent in unique.items():
        parent = agent.get('parent_id')
        if identity != root_id and parent not in unique:
            raise ValueError('A native child has an unknown parent')
        seen = {identity}
        while parent:
            if parent in seen:
                raise ValueError('Native agent parent cycle')
            seen.add(parent)
            parent = unique[parent].get('parent_id')
        timing = agent.setdefault('timings', {})
        start, end = timing.get('started_at'), timing.get('finished_at')
        if number(start) and number(end) and end >= start:
            timing['wall_ms'] = end - start
            children = [c.get('timings') or {} for c in unique.values() if c.get('parent_id') == identity]
            if all(number(c.get('started_at')) and number(c.get('finished_at')) for c in children):
                timing['exclusive_wall_ms'] = max(0, end - start - union_ms(
                    [(c['started_at'], c['finished_at']) for c in children], start, end))
    own = unique[root_id]['usage']
    children = [a for i, a in unique.items() if i != root_id]
    child_usage = add_usage([a['usage'] for a in children])
    if not children and complete:
        child_usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    summed = add_usage([a['usage'] for a in unique.values()])
    reported = tokens(reported_inclusive or {})
    if reported and any(k in summed and summed[k] != v for k, v in reported.items()
                        if k in ('prompt_tokens', 'completion_tokens', 'total_tokens')):
        issues.append('Native aggregate and attributed agent facts do not reconcile')
    complete = complete and not issues and all(
        all(number(a['usage'].get(k)) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
        and a.get('status') in {'completed', 'failed', 'cancelled'}
        for a in unique.values())
    inclusive = reported or summed
    # An authoritative whole-tree report can preserve the exact aggregate even
    # if a child breakdown is missing. Do not add it to the per-agent sum.
    if reported and all(number(own.get(k)) and reported.get(k, -1) >= own[k]
                        for k in ('prompt_tokens', 'completion_tokens')):
        child_usage = {k: reported[k] - own[k] for k in ('prompt_tokens', 'completion_tokens')}
        child_usage['total_tokens'] = sum(child_usage.values())
    return {
        'schema_version': '1.0', 'framework': framework, 'scope': 'invocation', 'root_id': root_id,
        'agents': list(unique.values()), 'self': own, 'children': child_usage, 'inclusive': inclusive,
        'child_count': len(children), 'complete': complete, 'warnings': issues,
        'aggregate_complete': complete or all(number(reported.get(k)) for k in ('prompt_tokens', 'completion_tokens')),
        'timings': {'wall_ms': unique[root_id].get('timings', {}).get('wall_ms'),
                    'self_exclusive_ms': unique[root_id].get('timings', {}).get('exclusive_wall_ms'),
                    'children_exclusive_ms': sum(a['timings']['exclusive_wall_ms'] for a in children)
                        if all('exclusive_wall_ms' in a.get('timings', {}) for a in children) else None,
                    'tree_exclusive_ms': sum(a['timings']['exclusive_wall_ms'] for a in unique.values())
                        if all('exclusive_wall_ms' in a.get('timings', {}) for a in unique.values()) else None},
    }


def accounting_usage(value):
    """Compatibility counters are inclusive exactly once, never self + inclusive."""
    usage = {**value['inclusive'], 'usage_scope': 'native_tree', 'measurement': 'native_reported',
             'native_tree_complete': value['complete'],
             'native_tree_total_complete': value['aggregate_complete'],
             'source_event_ids': list(dict.fromkeys(s for a in value['agents'] for s in a.get('source_event_ids', [])))}
    if all(number(usage.get(k)) for k in ('prompt_tokens', 'cache_read_tokens', 'cache_write_tokens')):
        usage['fresh_input_tokens'] = max(0, usage['prompt_tokens'] - usage['cache_read_tokens'] - usage['cache_write_tokens'])
    return usage


def module_timings(timing, native):
    """Own host/admission overhead at the module boundary, once."""
    result = copy.deepcopy(timing)
    if not native or not native.get('complete'):
        return result
    spans = [a.get('timings') or {} for a in native['agents'] if a.get('parent_id') == native['root_id']]
    start, end = timing.get('started_at'), timing.get('finished_at')
    if (not all(number(v) for v in (start, end, timing.get('invocation_ms')))
            or not all(number(s.get('started_at')) and number(s.get('finished_at')) for s in spans)):
        return result
    child_active = min(timing['invocation_ms'], union_ms(
        [(s['started_at'], s['finished_at']) for s in spans], start, end))
    result['self_exclusive_ms'] = max(0, timing['invocation_ms'] - child_active)
    result['children_exclusive_ms'] = native.get('timings', {}).get('children_exclusive_ms')
    if number(result['children_exclusive_ms']):
        result['tree_exclusive_ms'] = result['self_exclusive_ms'] + result['children_exclusive_ms']
    return result


def invocation_counts(record):
    """Logical wrappers, leaf work and observed native entries are distinct units.

    Native model-request totals are unknown without full per-request receipts.
    Observed response source IDs and native-reported own call counts are retained
    as separately named facts, never silently treated as complete totals.
    """
    leaves, wrappers = {}, set()
    def visit(current):
        for call in current.get('calls') or []:
            if call.get('accounting') != 'rollup-only':
                leaves[call['id']] = call
        for report in current.get('module_reports') or []:
            if report.get('kind') == 'composite':
                wrappers.add((report.get('envelope') or {}).get('invocation_id') or report['id'])
        for state in (current.get('node_states') or {}).values():
            for invocation in state.get('invocations') or []:
                if invocation.get('accounting') == 'rollup-only':
                    wrappers.add(invocation['id'])
        for child in (current.get('child_workflows') or {}).values():
            visit(child)
    visit(record)
    known_entries = [c.get('execution', {}).get('native_entry_calls') for c in leaves.values()]
    entries_known = [v for v in known_entries if type(v) is int and v >= 0]
    reported = [c.get('execution', {}).get('native_model_request_count') for c in leaves.values()]
    known_requests = [v for v in reported if type(v) is int and v >= 0]
    sources = {s for c in leaves.values() for s in (c.get('usage') or {}).get('source_event_ids', [])}
    return {'logical_module_invocations': len(leaves) + len(wrappers),
            'leaf_module_invocations': len(leaves), 'workflow_module_invocations': len(wrappers),
            'native_entry_calls': sum(entries_known), 'native_entry_counts_complete': len(entries_known) == len(leaves),
            'native_entry_unknown_leaf_calls': len(leaves) - len(entries_known),
            'native_self_model_requests_reported': sum(known_requests) if known_requests else None,
            'native_self_model_request_known_leaf_calls': len(known_requests),
            'native_self_model_request_unknown_leaf_calls': len(leaves) - len(known_requests),
            'observed_usage_source_events': len(sources), 'model_requests_total': None,
            'model_request_counts_complete': False}
