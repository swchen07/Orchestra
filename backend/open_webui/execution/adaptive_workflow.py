"""Two core roles; execution instances come from the enabled Project catalog."""

import copy


def adaptive_definition():
    return {
        'name': 'Adaptive Modules',
        'description': 'Controller plans work using Project participants; an independent Reviewer checks results.',
        'roles': [
            {'id': 'controller', 'name': 'Controller', 'instructions': 'Plan and adapt work; delegate execution to available Project participants. Never self-approve.'},
            {'id': 'reviewer', 'name': 'Reviewer', 'instructions': 'Independently verify the original task, reported evidence, dependencies and retained facts. Request concrete corrections.'},
        ],
        'lead_role_id': 'controller', 'instructions': 'Keep assignments and reports concise. Use only authorized Project participants.',
        'max_rounds': 3, 'execution_policy': 'automatic',
        'graph': {
            'input_policy': 'scoped',
            'nodes': [{'id': 'answer', 'op': 'finish', 'label': 'Initial result',
                       'inputs': {'answer': {'source': 'literal', 'value': 'Awaiting Controller plan'}}}],
            'edges': [], 'result_node': 'answer',
            'controller': {'binding': 'controller', 'initial': True, 'on_events': True, 'catalog_scope': 'project',
                           'instructions': 'Generate the graph appropriate to the task. Select execution backends from the Project module catalog. Allocate a distinct stable context_key for each independent executor; reuse that key for its later tasks and turns. Use explicit report dependencies. Do not execute worker tasks yourself or invent report references. The host runs independent result review.'},
            'reviewer': {'binding': 'reviewer', 'max_repairs': 1, 'instructions': 'Require the requested result and check its supporting reports. Distinguish dependency validity from observed parallel execution; require corrections for unsupported claims.'},
            'limits': {'max_calls': 16, 'max_concurrency': 2, 'max_replans': 5, 'max_seconds': 600, 'max_tokens': 200000},
        },
    }


def normalize_adaptive(value):
    """Core roles are structural; a planner cannot grant itself extra backends."""
    result = copy.deepcopy(value)
    graph = result.get('graph') or {}
    controller, reviewer = graph.get('controller'), graph.get('reviewer')
    if not controller or not reviewer or controller['binding'] == reviewer['binding']:
        raise ValueError('Adaptive templates require separate Controller and Reviewer roles')
    if {r['id'] for r in result['roles']} != {controller['binding'], reviewer['binding']}:
        raise ValueError('Adaptive templates define only Controller and Reviewer; executors come from Project participants')
    graph['controller']['catalog_scope'] = 'project'
    result['lead_role_id'] = controller['binding']
    return result
