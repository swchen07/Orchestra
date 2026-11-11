"""Non-executable examples and port documentation for the three starter graphs."""

import copy

from open_webui.execution.node_actions import BY_OPERATION

TASK = ('Use only these facts and no tools. North: N1 quantity 3 at price 8; N2 quantity 4 at price 4. '
        'South: S1 quantity 2 at price 7; S2 quantity 2 at price 4. ')


def example_for(graph):
    work = [n for n in graph['nodes'] if n['op'] not in {'input', 'output'}]
    if any(n['op'] == 'plan' for n in work):
        return {'purpose': 'Let the Controller choose the work graph from the Project’s available modules, then independently review the result.',
                'task': TASK + 'Plan independent North and South calculations. Add a follow-up that uses only the North report to add 1. Keep the follow-up independent of South. Review the evidence and return JSON with north, south and total.',
                'expected': 'The Controller creates the execution nodes and dependencies. North becomes 41 after its follow-up; South is 22; total is 63. The North follow-up can start as soon as North completes. The Reviewer checks the candidate before delivery.'}
    if work and all(n['op'] in {'call', 'join'} for n in work) and sum(n['op'] == 'join' for n in work) == 1:
        return {'purpose': 'Give every module the same complete question and keep the independent answers separate.',
                'task': TASK + 'Independently calculate North, South and the combined total. Give the arithmetic and a brief explanation.',
                'expected': 'Each executor independently gets North 40, South 22 and total 62. The host collector preserves the separate answers; it does not ask a model to combine or rank them.'}
    if (sum(n['op'] in {'call','dispatch'} for n in work) == 3 and sum(n['op'] == 'review' for n in work) == 1
            and any({'task_b', 'task_c'} <= set((n.get('output_schema') or {}).get('properties', {})) for n in work)):
        return {'purpose': 'Use a saved graph: Controller writes two assignments, two Executors work independently, and Reviewer checks and combines their reports.',
                'task': TASK + 'Controller: assign North and South to different Executors. Each Executor returns only its assigned subtotal and arithmetic. Reviewer: independently verify both reports and return JSON with north, south and total.',
                'expected': 'Controller returns assignments and selected_nodes. Executors independently produce 40 and 22. Reviewer checks both reports and delivers 62 only on approval. A rejected review returns to Controller; only selected correction branches rerun, within the configured correction limit.'}
    return None


def document_definition(value):
    result = copy.deepcopy(value)
    graph = result['graph']
    for node in graph['nodes']:
        meanings = BY_OPERATION[node['op']]['output_meanings']
        schema = node.get('output_schema')
        if isinstance(schema, dict):
            for key, field in schema.get('properties', {}).items():
                if isinstance(field, dict) and key in meanings:
                    field.setdefault('description', meanings[key])
        if node['op'] in {'call', 'route', 'dispatch'}:
            node.setdefault('report_description', meanings.get('report', 'Complete the assigned task and return the requested evidence.'))
        if isinstance(schema, dict) and {'task_b','task_c'} <= set(schema.get('properties', {})):
            for key, label in (('task_b','first Executor'), ('task_c','second Executor')):
                schema['properties'][key].setdefault('description', 'A self-contained assignment for the '+label+'. Include its needed facts, constraints and required reply. Do not supply the answer or private recall values.')
    example = example_for(graph)
    if example:
        result.setdefault('example', example)
    return result
