"""Bounded review feedback and selective replay of an ordinary dependency graph.

Forward dependencies remain acyclic. A revise edge starts another attempt;
committed calls/reports and private module histories are never overwritten.
"""
import copy
import json

from open_webui.execution.claude import ExecutionError
from open_webui.execution.graph_spec import ACTIVE, pointer
from open_webui.execution.trajectory import add_event, stamp
from open_webui.execution.node_actions import feedback_policy


def descendants(runner, roots):
    found = set(roots)
    for _ in runner.graph['nodes']:
        for edge in runner.graph['edges']:
            if edge.get('on') != 'revise' and edge['from'] in found:
                found.add(edge['to'])
    return found


def reset_node(runner, identity, reason):
    state = runner.states[identity]
    if state['status'] in ACTIVE:
        raise ExecutionError('Cannot replay a node while its invocation is active')
    attempts = copy.deepcopy(state.get('attempts', []))
    if state.get('output') is not None or state.get('attempt_call_ids') or state['status'] not in {'pending', 'ready'}:
        attempts.append({k: copy.deepcopy(v) for k, v in state.items() if k != 'attempts'})
    runner.states[identity] = {'status': 'pending', 'call_ids': list(state.get('call_ids', [])), 'attempt_call_ids':[],
                               'attempts': attempts, 'attempt': len(attempts)+1, 'replay_reason': reason}


def request_repair(runner, review_node, output):
    config=review_node.get('review',{})
    repairs=runner.node_review_repairs.get(review_node['id'],0)
    if not config.get('repair_node') or repairs>=config.get('max_repairs',0):
        raise ExecutionError('Review requested changes; no configured repair budget remains')
    target = review_node['review']['repair_node']
    if target in runner.feedback_jobs:
        raise ExecutionError('The selected node already has an active review correction')
    policy=feedback_policy(runner.node(target))
    if not policy:
        raise ExecutionError('This action cannot receive review feedback')
    runner.node_review_repairs[review_node['id']]=repairs+1
    runner.review_repairs+=1
    runner.review_feedback={k:copy.deepcopy(output[k]) for k in ('verdict','summary','issues','checked_report_refs')}
    runner.feedback_jobs[target] = {
        'review_node': review_node['id'], 'feedback': copy.deepcopy(runner.review_feedback),
        'previous_output': copy.deepcopy(runner.states[target].get('output', {})),
        'attempt': runner.node_review_repairs[review_node['id']],
        'policy':policy, 'stage':'queued', 'scope':descendants(runner,{target})|descendants(runner,{review_node['id']}),
    }
    runner.final_answer = None
    runner.approved_candidate = None
    runner.result_enabled=False
    add_event(runner.engine.record, 'review.feedback', title='Review feedback queued',
              call=runner.states[review_node['id']].get('last_invocation'),
              data={'from':review_node['id'], 'to':target, 'attempt':runner.node_review_repairs[review_node['id']],
                    'policy':policy,'issues': output['issues']})


def replay_nodes(runner, roots, *, review_node=None, reason='Replay requested'):
    roots=set(roots)
    if not roots or any(key not in runner.states or feedback_policy(runner.node(key)) in {None,'replan'} for key in roots):
        raise ValueError('Replay needs existing feedback-capable work nodes')
    replay=descendants(runner,roots)
    if review_node:
        replay |= descendants(runner,{review_node})
    if any(runner.states[key]['status'] in ACTIVE or key in runner.tasks for key in replay):
        raise ValueError('Affected work is still running; wait before replaying it')
    runner.graph['revision']+=1
    for key in replay:
        reset_node(runner,key,reason)
        runner.review_waiting.pop(key,None)
    runner.final_answer=None
    runner.approved_candidate=None
    runner.result_enabled=True
    if runner.policy=='review': runner.paused=True
    runner.revision_log.append({'revision':runner.graph['revision'],'at':stamp(),'source':'feedback',
                               'reason':reason,'graph':copy.deepcopy(runner.graph)})
    add_event(runner.engine.record,'nodes.replayed',title=reason,
              data={'roots':sorted(roots),'invalidated':sorted(replay),'revision':runner.graph['revision']})
    return replay


def process_feedback(runner):
    """Called by the scheduler after completed tasks settle, never inside a model call."""
    for target,job in list(runner.feedback_jobs.items()):
        if job['stage']!='queued': continue
        # Replanning may observe active work. Replay never overwrites it; only
        # the affected cut waits, while independent branches keep advancing.
        if job['policy']!='replan' and any(runner.states[key]['status'] in ACTIVE or key in runner.tasks for key in job['scope']):
            continue
        job['stage']='active'
        if job['policy']=='replan':
            runner.controller_due=True
            runner.result_enabled=False
        elif job['policy']=='select':
            reset_node(runner,target,'Reviewer requested corrections')
            runner.result_enabled=True
        else:
            replay_nodes(runner,{target},review_node=job['review_node'],reason='Review correction '+str(job['attempt']))


def feedback_blocks(runner,identity):
    return any(identity in job['scope'] and (job['stage']=='queued' or
               (job['policy']=='select' and identity!=target)) for target,job in runner.feedback_jobs.items())


def feedback_input(runner,node):
    job=runner.feedback_jobs.get(node['id'])
    if not job or job['stage']!='active': return ''
    return '\nReview feedback control input (host-owned):\n'+json.dumps({
        'review_node':job['review_node'],'correction_attempt':job['attempt'],
        'policy':job['policy'],'review_feedback':job['feedback'],
        'rule':'Correct the identified issues using your declared inputs and own retained context. Earlier attempts remain historical evidence, not the current approved result.'},ensure_ascii=False)


def complete_feedback(runner,node):
    job=runner.feedback_jobs.get(node['id'])
    if job and job['policy']=='replay':
        runner.feedback_jobs.pop(node['id'])


def replan_gates(runner):
    """Only the rejected review/delivery cut may consume a revised output alias.

    Existing work remains frozen. Reopening review is a new attempt rather than
    retargeting the inputs of a committed invocation.
    """
    gates=set()
    for job in runner.feedback_jobs.values():
        if job['policy']=='replan' and job['stage']=='active':
            gates |= descendants(runner,{job['review_node']})
    if any(runner.states[key]['status'] in ACTIVE or key in runner.tasks for key in gates):
        raise ValueError('Review-dependent work is still running; wait before revising its candidate')
    return gates


def patch_states(runner):
    gates=replan_gates(runner)
    return {key:{**state,'status':'pending'} if key in gates else state for key,state in runner.states.items()}


def reopen_replan_gates(runner, gates, reason):
    for identity in gates:
        reset_node(runner,identity,reason)
        runner.review_waiting.pop(identity,None)
    if gates:
        runner.final_answer=None
        runner.approved_candidate=None
        add_event(runner.engine.record,'review.reopened',title='Review candidate revised',
                  data={'node_ids':sorted(gates),'revision':runner.graph['revision']})


def dispatch_context(runner, node):
    targets = node['dispatch']['targets']
    job = runner.feedback_jobs.get(node['id'])
    context = {'phase':'correction' if job else 'initial', 'allowed_targets':[
        {'node_id':key, 'title':runner.node(key)['label'], 'input_bindings':runner.node(key)['inputs']}
        for key in targets],
        'selection_field':'selected_nodes',
        'rule':'Return selected_nodes as a nonempty list of exact allowed target IDs. Initial distribution selects the branches needed by the goal. On correction select only branches requiring new work. Unselected committed branches keep their earlier assignments and reports.'}
    if job:
        context.update(previous_assignments={k:v for k,v in job['previous_output'].items() if k not in {'__report','report'}},
                       correction_attempt=job['attempt'], committed_reports={key:{'report_ref':(runner.states[key].get('output',{}).get('__report') or {}).get('id'),
                           'report':runner.states[key].get('output',{}).get('report')} for key in targets})
    return context


def dispatch_prompt(runner, node):
    return '\nDispatch control input (host-owned):\n'+json.dumps(dispatch_context(runner, node),ensure_ascii=False)


def preserve_field(result, previous, path):
    """Keep unselected assignments exact, including nested declared fields."""
    if not path:
        raise ExecutionError('Selective dispatch needs explicit source fields, not the entire dispatcher result')
    keys = [part.replace('~1','/').replace('~0','~') for part in path.lstrip('/').split('/')]
    value = copy.deepcopy(pointer(previous,path))
    cursor = result
    for key in keys[:-1]:
        cursor = cursor[int(key)] if isinstance(cursor,list) else cursor[key]
    if isinstance(cursor,list):
        cursor[int(keys[-1])] = value
    else:
        cursor[keys[-1]] = value


def apply_dispatch(runner, node, output):
    targets = set(node['dispatch']['targets'])
    selected = output.get('selected_nodes')
    if (not isinstance(selected,list) or not selected or any(not isinstance(i,str) for i in selected)
            or len(selected)!=len(set(selected)) or not set(selected)<=targets):
        raise ExecutionError('Dispatcher must select a nonempty, unique subset of its declared target nodes')
    selected = set(selected)
    job = runner.feedback_jobs.get(node['id'])
    if job:
        replay = descendants(runner, selected)
        if replay & (targets-selected):
            raise ExecutionError('A selected branch depends on an unselected dispatch target; select both')
        # Fields read by unselected branches are intentionally not applied.
        selected_paths = {ref.get('pointer','') for key in selected for ref in runner.node(key)['inputs'].values()
                          if ref.get('source')=='node' and ref.get('node')==node['id']}
        for key in targets-selected:
            for ref in runner.node(key)['inputs'].values():
                if ref.get('source')=='node' and ref.get('node')==node['id']:
                    path=ref.get('pointer','')
                    if any(path==p or path.startswith(p+'/') or p.startswith(path+'/') for p in selected_paths):
                        raise ExecutionError('Selected and retained branches share an assignment field; select both')
                    preserve_field(output,job['previous_output'],path)
        replay_nodes(runner,selected,review_node=job['review_node'],reason='Review correction '+str(job['attempt']))
        for key in targets-selected:
            runner.states[key]['reused_in_revision']=runner.graph['revision']
        runner.feedback_jobs.pop(node['id'])
    else:
        for key in targets-selected:
            runner.states[key].update(status='skipped',reason='Not selected by dispatcher',finished_at=stamp())
    add_event(runner.engine.record,'dispatch.selected',title='Execution branches selected',
              data={'node_id':node['id'],'selected_nodes':sorted(selected),'retained_nodes':sorted(targets-selected),
                    'correction':bool(job),'revision':runner.graph['revision']})
    return output
