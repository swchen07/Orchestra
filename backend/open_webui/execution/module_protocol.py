"""Host-owned reports, public module contracts and invocation-only accounting."""

import copy
import hashlib
import math


def report_envelope(call):
    scope = call.get('module') or {}
    return {
        'id': call.get('report_id'),
        'run_id': scope.get('run_id') or (call.get('report_id') or '').split('/')[0],
        'invocation_id': call['id'],
        'instance_id': call['participant_id'],
        'context_id': scope.get('context_id'),
        'definition_id': scope.get('definition_id'),
        'node_id': call.get('node_id'),
        **({k:call[k] for k in ('workflow_path', 'root_run_id') if k in call}),
        **({'action': call['action']} if call.get('action') else {}),
        'plan_revision': call.get('plan_revision'),
        'sha256': hashlib.sha256(call.get('output', '').encode()).hexdigest(),
        'usage_ref': call['id'],
        'depends_on': list(call.get('depends_on') or []),
        'timings': copy.deepcopy(scope.get('timings') or {}),
        'history': copy.deepcopy(call.get('history')),
        **{k:copy.deepcopy(call.get('execution', {})[k]) for k in ('__workspace', '__artifacts') if k in call.get('execution', {})},
    }


def report_rows(record, prior=()):
    rows = {r['id']: copy.deepcopy(r) for r in prior}
    for report in record.get('module_reports', []):
        rows[report['id']] = {**copy.deepcopy(report), 'run_id': record['id'],
            'run_status': record.get('status', 'unknown'),
            'review_status': (record.get('review') or {}).get('status')}
    for call in record.get('calls', []):
        if call.get('status') == 'completed' and call.get('output') and call.get('report_id'):
            rows[call['report_id']] = {
                'id': call['report_id'], 'role_id': call['participant_id'], 'name': call['name'],
                'output': call['output'], 'kind': call.get('kind', 'work'),
                'run_id': record['id'], 'run_status': record.get('status', 'unknown'),
                'review_status': (record.get('review') or {}).get('status'),
                'envelope': report_envelope(call),
            }
    return list(rows.values())


def module_catalog(registry):
    from open_webui.execution.module_environment import environment_capabilities
    contracts = {}
    for key, participant in registry.items():
        try:
            if participant.get('module_type') == 'workflow':
                from open_webui.execution.module_environment import narrow_requests, resolve_environment
                def check(current, inherited=None, ancestors=()):
                    request = narrow_requests(inherited, current.get('_environment_request'))
                    if current.get('module_type') != 'workflow':
                        resolve_environment({**current, '_environment_request': request}, 'catalog')
                        return
                    definition = current['_workflow_template']
                    if definition['id'] in ancestors:
                        raise ValueError('Recursive Workflow module')
                    choices = {**registry, **{b['id']: b for b in current['_workflow_bindings']}}
                    for node in definition['graph']['nodes']:
                        if node.get('binding'):
                            selected = choices.get(node['binding'])
                            if selected is None:
                                raise ValueError('Unavailable Workflow descendant')
                            check(selected, narrow_requests(request, node.get('environment')), (*ancestors, definition['id']))
                check(participant)
                contracts[key] = {'authority': 'host-graph-executor',
                    'rule': 'Requirements must be supported by every saved child role and actual descendant.'}
            else:
                contracts[key] = environment_capabilities(participant)
        except ValueError:
            continue  # Inherited restrictions make this backend unavailable.
    return [
        {
            **{k: p.get(k) for k in ('id', 'name', 'framework_id', 'model_name', 'provider_name', 'profile_id')},
            'capabilities': str(p.get('role_instructions') or p.get('description') or 'Execute an assigned task.')[:1200],
            'input_contract': 'Task instruction, explicitly referenced inputs and this instance\'s private history.',
            'output_contract': 'Text report, or JSON matching the node output_schema; host supplies __report metadata.',
            'context': 'Reuse a stable context_key for one role; separate keys isolate private dialogue.',
            **({'module_type': 'workflow', 'input_schema': p['input_schema'], 'output_schema': p['output_schema']}
               if p.get('module_type') == 'workflow' else {}),
            'environment_contract': contracts[key],
        }
        for key,p in registry.items() if key in contracts
    ]


def usage_budget(calls, limit=None):
    total = metered = 0
    for call in calls:
        usage = call.get('usage') or {}
        valid = lambda v: type(v) in (int, float) and math.isfinite(v) and v >= 0
        counts = [usage.get('prompt_tokens'), usage.get('completion_tokens')]
        total += sum(v for v in counts if valid(v))
        metered += int(all(valid(v) for v in counts) and usage.get('native_tree_total_complete') is not False)
    return {'reported_tokens': total, 'metered_calls': metered, 'calls': len(calls),
            'max_tokens': limit, 'exceeded': limit is not None and total >= limit}


def review_verdict(value, required_refs, available_refs):
    if (not isinstance(value, dict) or set(value) != {'verdict', 'summary', 'issues', 'checked_report_refs'}
            or value['verdict'] not in {'approve', 'revise'}
            or not isinstance(value['summary'], str) or not 1 <= len(value['summary']) <= 3000
            or not isinstance(value['issues'], list) or len(value['issues']) > 12
            or any(not isinstance(i, str) or not 1 <= len(i) <= 2000 for i in value['issues'])
            or not isinstance(value['checked_report_refs'], list)
            or any(not isinstance(r, str) for r in value['checked_report_refs'])):
        raise ValueError('Reviewer must return verdict, summary, issues and checked_report_refs')
    refs = value['checked_report_refs']
    if len(refs) != len(set(refs)) or not set(refs) <= set(available_refs):
        raise ValueError('Reviewer cited an unavailable or duplicate host report reference')
    if value['verdict'] == 'approve' and (value['issues'] or not set(required_refs) <= set(refs)):
        raise ValueError('Approval must check every candidate report and contain no unresolved issues')
    if value['verdict'] == 'revise' and not value['issues']:
        raise ValueError('A rejected candidate needs concrete correction issues')
    return copy.deepcopy(value)
