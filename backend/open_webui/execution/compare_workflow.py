"""Compile Compare's variable module slots to the shared graph contract.

No controller, perspective prompts, synthesis call or special runtime is needed.
Each slot receives the same task through its own context; the host joins reports.
"""

import copy

from open_webui.execution.graph_spec import validate_graph

TASK_INSTRUCTION = (
    'Solve the complete user task independently. Follow the requested language, constraints '
    'and output format. Return your own answer with evidence and any uncertainty. '
    'Do not assume access to another module\'s answer.'
)


def compare_graph(roles, instructions='', limits=None):
    if not 2 <= len(roles) <= 8:
        raise ValueError('Compare needs 2–8 module slots')
    ids = [role['id'] for role in roles]
    nodes = [
        {'id': 'compare_' + str(index + 1), 'op': 'call', 'binding': role['id'],
         'label': role['name'], 'instruction': TASK_INSTRUCTION + ('\n' + instructions if instructions else ''),
         'inputs': {'task': {'source': 'input', 'pointer': '/task'}}}
        for index, role in enumerate(roles)
    ]
    nodes.append({
        'id': 'answers', 'op': 'join', 'label': 'Independent answers', 'policy': 'all_terminal',
        'inputs': {role['id']: {'source': 'node', 'node': node['id'], 'pointer': '/report', 'optional': True}
                   for role, node in zip(roles, nodes)},
    })
    return validate_graph({
        'schema_version': '1.0', 'input_policy': 'scoped', 'nodes': nodes,
        'edges': [{'from': node['id'], 'to': 'answers', 'on': 'terminal'} for node in nodes[:-1]],
        'result_node': 'answers',
        # Graphs and native transports currently admit at most two calls at
        # once. Additional independent slots queue without sharing contexts.
        'limits': {**(limits or {}), 'max_concurrency': min(2, len(roles))},
    }, ids)


def normalize_compare(value):
    result = copy.deepcopy(value)
    # A slot chooses a backend/preset in the Project or chat, not a different
    # comparison stance. Shared instructions and task are identical for all.
    result['roles'] = [{**r, 'instructions': TASK_INSTRUCTION} for r in result['roles']]
    result['lead_role_id'] = result['roles'][0]['id']
    result['graph'] = compare_graph(result['roles'], result.get('instructions', ''),
                                    (result.get('graph') or {}).get('limits'))
    return result


def is_comparison_graph(template):
    """Recognize the same-task recipe from graph data, not a template name."""
    graph = template.get('graph') or {}
    roles = template.get('roles') or []
    nodes = copy.deepcopy([n for n in graph.get('nodes', []) if n['op'] not in {'input', 'output'}])
    entry = next((n for n in graph.get('nodes', []) if n['op'] == 'input'), None)
    exit = next((n for n in graph.get('nodes', []) if n['op'] == 'output'), None)
    if entry:
        for n in nodes:
            for ref in n.get('inputs', {}).values():
                if ref.get('source') == 'node' and ref.get('node') == entry['id']:
                    ref.pop('node')
                    ref['source'] = 'input'
    result_id = exit['inputs']['answer'].get('node') if exit else graph.get('result_node')
    calls = [n for n in nodes if n.get('op') == 'call']
    result = next((n for n in nodes if n.get('id') == result_id), {})
    instruction = TASK_INSTRUCTION + ('\n' + template['instructions'] if template.get('instructions') else '')
    if (graph.get('controller') or graph.get('reviewer') or not roles or len(nodes) != len(roles) + 1
            or len(calls) != len(roles) or result.get('op') != 'join'
            or result.get('policy') != 'all_terminal'
            or {n.get('binding') for n in calls} != {r['id'] for r in roles}
            or any(r.get('instructions', '') != TASK_INSTRUCTION for r in roles)):
        return False
    if any(n.get('instruction', '') not in {'', instruction}
           or n.get('inputs') != {'task': {'source': 'input', 'pointer': '/task'}} for n in calls):
        return False
    return all(e.get('from') in {n['id'] for n in calls} and e.get('to') == result['id']
               and e.get('on', 'success') == 'terminal' for e in graph.get('edges', [])
               if not e.get('generated'))
