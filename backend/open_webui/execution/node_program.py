"""Composable node capabilities and the one-way import of legacy graph policies.

Roles/backend bindings are parameters, never operation selectors. New programs
store planning and review as nodes and expose named graph output ports.
"""

import copy


def _capability_schema(operation):
    if operation in {'command', 'evaluate', 'integrate', 'publish'}:
        return {'type': 'object', 'properties': {'report': {'type': 'string'}, 'status': {'type': 'string'},
            '__workspace': {'type': 'object'}, '__artifacts': {'type': 'array', 'items': {'type': 'object'}},
            'commands': {'type': 'array'}, 'evaluations': {'type': 'array'}, 'conflicts': {'type': 'array'},
            'retained': {'type': 'boolean'}},
            'required': ['report', 'status']}
    if operation == 'dispatch':
        return {'type':'object','properties':{'selected_nodes':{'type':'array','items':{'type':'string'},
                'minItems':1,'uniqueItems':True}},'required':['selected_nodes']}
    if operation in {'join', 'finish', 'checkpoint'}:
        fields = {'report': {'type': 'string'} if operation != 'finish' else {}}
        if operation == 'join':
            fields.update(results={'type': 'object'}, outcomes={'type': 'object'})
        if operation == 'checkpoint':
            fields['answer'] = {}
        return {'type': 'object', 'properties': fields, 'required': list(fields)}
    if operation == 'plan':
        return {'type': 'object', 'properties': {'action': {'type': 'string'}}, 'required': ['action']}
    if operation == 'review':
        return {'type': 'object', 'properties': {
            'verdict': {'type': 'string', 'enum': ['approve', 'revise']}, 'summary': {'type': 'string'},
            'issues': {'type': 'array', 'items': {'type': 'string'}},
            'checked_report_refs': {'type': 'array', 'items': {'type': 'string'}}, 'answer': {'type': 'string'},
        }, 'required': ['verdict', 'summary', 'issues', 'checked_report_refs', 'answer']}
    return None


def capability_schema(operation):
    from open_webui.execution.node_actions import BY_OPERATION
    schema = _capability_schema(operation)
    if schema:
        meanings = BY_OPERATION[operation]['output_meanings']
        for key, value in schema.get('properties', {}).items():
            if key in meanings:
                value.setdefault('description', meanings[key])
    return schema


def planner_node(graph):
    return next((n for n in graph.get('nodes', []) if n.get('op') == 'plan'), None)


def planner_config(graph):
    node = planner_node(graph)
    if node:
        return {'initial': True, 'on_events': True, 'catalog_scope': 'project', 'output_port': 'candidate',
                **node.get('planning', {}), 'binding': node['binding'],
                'instructions': node.get('instruction', '')}
    return graph.get('controller')


def review_nodes(graph):
    return [n for n in graph.get('nodes', []) if n.get('op') == 'review']


def output_ref(graph, name=None):
    if graph.get('schema_version') == '2.0':
        name = name or graph.get('delivery_port') or ('answer' if 'answer' in graph.get('outputs', {}) else None)
        return (graph.get('outputs') or {}).get(name)
    return {'source': 'node', 'node': graph['result_node'], 'pointer': '/report'}


def delivery_ports(graph):
    """Aliases around delivery are protected; the planner owns its declared work port."""
    mutable = (planner_config(graph) or {}).get('output_port', 'candidate')
    port = graph.get('delivery_port') or ('answer' if 'answer' in graph.get('outputs', {}) else None)
    protected, ref = ({port} if port else set()), (graph.get('outputs') or {}).get(port, {})
    while ref.get('source') == 'output' and ref['port'] not in protected:
        port = ref['port']
        if port == mutable:
            break
        protected.add(port)
        ref = (graph.get('outputs') or {}).get(port, {})
    return protected


def referenced_nodes(ref, graph, seen=()):
    if ref.get('source') == 'node':
        return [ref['node']]
    if ref.get('source') == 'output':
        port = ref['port']
        if port in seen:
            raise ValueError('Graph output references cannot form a cycle')
        target = (graph.get('outputs') or {}).get(port)
        return referenced_nodes(target, graph, (*seen, port)) if target else []
    return []


def legacy_context_keys(value):
    """Freeze the old implicit role key on a copy, never rewrite checkpoints."""
    import re
    graph = copy.deepcopy(value)
    for node in graph.get('nodes', []):
        binding = node.get('binding', '')
        if binding:
            node.setdefault('context_key', binding if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}', binding) else node['id'])
    return graph


def upgrade_graph(value, *, legacy_contexts=False):
    """Import a v1 policy once; keep old run journals byte-for-byte unchanged."""
    graph = copy.deepcopy(value)
    if legacy_contexts or graph.get('schema_version') != '2.0':
        graph = legacy_context_keys(graph)
    from open_webui.execution.graph_boundaries import with_boundaries
    if graph.get('schema_version') == '2.0':
        from open_webui.execution.node_actions import normalize_action
        for node in graph.get('nodes', []):
            normalize_action(node)
        return with_boundaries(graph)
    controller, reviewer = graph.pop('controller', None), graph.pop('reviewer', None)
    graph['schema_version'] = '2.0'
    graph['outputs'] = {'answer': {'source': 'node', 'node': graph['result_node'], 'pointer': '/report'}}
    ids = {n['id'] for n in graph['nodes']}

    def identity(base):
        candidate = base
        while candidate in ids:
            candidate += '_node'
        ids.add(candidate)
        return candidate

    plan_id = identity('plan_graph') if controller else None
    if controller:
        if (controller.get('initial') and len(graph['nodes']) == 1
                and graph['nodes'][0]['op'] == 'finish'
                and all(r.get('source') == 'literal' for r in graph['nodes'][0].get('inputs', {}).values())):
            graph['nodes'] = []
            graph['edges'] = []
            graph['outputs'].pop('answer')
        graph['nodes'].insert(0, {
            'id': plan_id, 'label': 'Plan and adapt work', 'op': 'plan',
            'binding': controller['binding'], 'context_key': controller['binding'],
            'instruction': controller.get('instructions', ''),
            'inputs': {'goal': {'source': 'input', 'pointer': '/task'}},
            'planning': {k: controller[k] for k in ('initial', 'on_events', 'catalog_scope') if k in controller}
                        | {'output_port': 'candidate'},
        })
    if reviewer:
        if 'answer' in graph['outputs']:
            graph['outputs']['candidate'] = graph['outputs'].pop('answer')
        review_id = identity('review_result')
        graph['nodes'].append({
            'id': review_id, 'label': 'Review and deliver', 'op': 'review',
            'binding': reviewer['binding'], 'context_key': reviewer['binding'],
            'instruction': reviewer.get('instructions', ''),
            'inputs': {'goal': {'source': 'input', 'pointer': '/task'},
                       'candidate': {'source': 'output', 'port': 'candidate', 'pointer': ''}},
            'review': {'max_repairs': reviewer.get('max_repairs', 1),
                       **({'repair_node': plan_id} if plan_id else {})},
        })
        graph['outputs']['answer'] = {'source': 'node', 'node': review_id, 'pointer': '/answer'}
        graph['result_node'] = review_id
    elif controller:
        # Without an independent gate, the generated candidate is also delivery.
        if 'answer' in graph['outputs']:
            graph['outputs']['candidate'] = graph['outputs'].pop('answer')
        graph['outputs']['answer'] = {'source': 'output', 'port': 'candidate', 'pointer': ''}
    return with_boundaries(graph)
