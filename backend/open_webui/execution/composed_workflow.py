"""A saved composition of ordinary nodes, not a new collaboration mode."""


def distribute_review_definition():
    task = {'source': 'input', 'pointer': '/task'}
    return enable_review_loop({
        'name': 'Distribute & Review',
        'description': 'Controller assigns independent work; Executors run in parallel; Reviewer checks and delivers the answer.',
        'roles': [
            {'id': 'controller', 'name': 'Controller', 'instructions': 'Allocate self-contained assignments; do not execute or approve the work.'},
            {'id': 'executor_01', 'name': 'Executor 01', 'instructions': 'Execute only the supplied assignment, using your own retained dialogue.'},
            {'id': 'executor_02', 'name': 'Executor 02', 'instructions': 'Execute only the supplied assignment, using your own retained dialogue.'},
            {'id': 'reviewer', 'name': 'Reviewer', 'instructions': 'Independently verify the goal and evidence, then synthesize the final answer.'},
        ],
        'lead_role_id': 'controller', 'instructions': 'Keep work and reports concise. Respect the requested language and output format.',
        'max_rounds': 3, 'execution_policy': 'automatic',
        'graph': {'schema_version': '2.0', 'input_policy': 'scoped', 'nodes': [
            {'id': 'assign', 'label': 'Distribute work', 'op': 'call', 'binding': 'controller', 'context_key': 'controller',
             'instruction': 'Split the current request into two independent, self-contained work packages. Preserve user constraints and include each executor\'s needed data. Do not compute their answers or provide private memory values on recall requests. task_b is for Executor 01; task_c is for Executor 02.',
             'inputs': {'goal': task},
             'output_schema': {'type': 'object', 'properties': {'task_b': {'type': 'string'}, 'task_c': {'type': 'string'}},
                               'required': ['task_b', 'task_c'], 'additionalProperties': False}},
            {'id': 'execute_b', 'label': 'Execute assignment B', 'op': 'call', 'binding': 'executor_01', 'context_key': 'executor_01',
             'instruction': 'Execute the supplied assignment and return a concise evidence-based report. Use your own retained history when asked to recall earlier facts.',
             'inputs': {'assignment': {'source': 'node', 'node': 'assign', 'pointer': '/task_b'}}},
            {'id': 'execute_c', 'label': 'Execute assignment C', 'op': 'call', 'binding': 'executor_02', 'context_key': 'executor_02',
             'instruction': 'Execute the supplied assignment and return a concise evidence-based report. Use your own retained history when asked to recall earlier facts.',
             'inputs': {'assignment': {'source': 'node', 'node': 'assign', 'pointer': '/task_c'}}},
            {'id': 'review', 'label': 'Review and summarize', 'op': 'review', 'binding': 'reviewer', 'context_key': 'reviewer',
             'instruction': 'Check the original goal, each assignment and both executor reports. Recompute simple calculations and require supported claims. Deliver a concise combined answer in the requested format; never approve missing or inconsistent evidence.',
             'inputs': {'goal': task, 'report_b': {'source': 'node', 'node': 'execute_b', 'pointer': '/report'},
                        'report_c': {'source': 'node', 'node': 'execute_c', 'pointer': '/report'}},
             'review': {'max_repairs': 2, 'repair_node':'assign'}},
        ], 'edges': [], 'outputs': {'answer': {'source': 'node', 'node': 'review', 'pointer': '/answer'}},
          'limits': {'max_calls': 16, 'max_tokens': 300000, 'max_seconds': 600, 'max_concurrency': 2, 'max_replans': 5}},
    })


def enable_review_loop(definition):
    """Migrate the saved example composition, keeping role IDs and user contracts."""
    import copy
    value=copy.deepcopy(definition)
    graph=value.get('graph') or {}
    nodes=graph.get('nodes',[])
    assign=next((n for n in nodes if n['id']=='assign' and n.get('op') in {'call','dispatch'}
                 and {'task_b','task_c'}<=set((n.get('output_schema') or {}).get('properties',{}))),None)
    review=next((n for n in nodes if n.get('op')=='review' and n['id']=='review'),None)
    if not assign or not review:
        return value
    targets=[n['id'] for n in nodes if n.get('op')=='call' and any(
        ref.get('source')=='node' and ref.get('node')==assign['id'] for ref in n.get('inputs',{}).values())]
    if len(targets)!=2:
        return value
    assign.update(op='dispatch',action='tasks.dispatch',dispatch={'targets':targets})
    schema=assign['output_schema']
    schema['properties']['selected_nodes']={'type':'array','items':{'type':'string','enum':targets},
        'minItems':1,'uniqueItems':True,
        'description':'Target node IDs to execute. Initially select both Executors. On review feedback select only Executors whose reports need correction; unselected committed reports are retained.'}
    schema['required']=list(dict.fromkeys([*schema.get('required',[]),'selected_nodes']))
    if not review.get('review',{}).get('repair_node'):
        review['review']={'max_repairs':2,'repair_node':assign['id']}
    if value.get('example') and 'correction' not in value['example']['expected'].lower():
        value['example']['expected'] += ' A rejected review returns to Controller. Only selected correction branches rerun; delivery requires approval within the configured correction limit.'
    return value
