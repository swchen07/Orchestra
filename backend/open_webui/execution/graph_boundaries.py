"""Explicit, host-executed input/output nodes using the ordinary port contract."""

import copy


def bind_input_references(graph):
    """Canonicalize old planner host references at the proposal boundary."""
    # Planner output is untrusted JSON. Reject malformed references before
    # compatibility rewriting, so the Controller can correct its proposal.
    for node in graph['nodes']:
        inputs=node.get('inputs',{})
        if not isinstance(inputs,dict):
            raise ValueError('Node inputs must map field names to typed source references')
        if any(not isinstance(ref,dict) for ref in [*inputs.values(),*([node['items']] if 'items' in node else [])]):
            raise ValueError('Every input needs a typed source reference; a fixed value uses {"source":"literal","value":VALUE}')
    entry = next((n for n in graph['nodes'] if n.get('op') == 'input'), None)
    if entry:
        for node in graph['nodes']:
            if node.get('op') == 'input':
                continue
            for ref in [*node.get('inputs', {}).values(), *([node['items']] if node.get('items') else [])]:
                if ref.get('source') == 'input':
                    field = next((name for name, source in entry['inputs'].items()
                                  if source.get('source') == 'input'
                                  and source.get('pointer', '') == ref.get('pointer', '')), None)
                    if field is None:
                        raise ValueError('Map the requested host field on the Workflow input node first')
                    ref.update(source='node', node=entry['id'], pointer='/' + field.replace('~', '~0').replace('/', '~1'))
    return graph


def with_boundaries(value):
    graph = copy.deepcopy(value)
    if graph.get('schema_version') != '2.0':
        return graph
    nodes = graph['nodes']
    existing = {n['op'] for n in nodes} & {'input', 'output'}
    if existing:
        # Partial/edited boundaries are validated, never silently repaired.
        return graph
    ids = {n['id'] for n in nodes}

    def identity(name):
        while name in ids:
            name += '_node'
        ids.add(name)
        return name

    entry, exit = identity('workflow_input'), identity('workflow_output')

    def rebind(ref):
        if ref.get('source') == 'input':
            ref.update(source='node', node=entry)

    for node in nodes:
        for ref in node.get('inputs', {}).values():
            rebind(ref)
        if node.get('items'):
            rebind(node['items'])
    for ref in graph['outputs'].values():
        rebind(ref)
    from open_webui.execution.port_types import TASK_SCHEMA
    public_inputs = graph.get('input_schema', TASK_SCHEMA)
    fields = public_inputs.get('properties', {}) if isinstance(public_inputs, dict) else {}
    consumed = {ref['port'] for n in nodes for ref in n.get('inputs', {}).values() if ref.get('source') == 'output'}
    outputs = {name: copy.deepcopy(ref) for name, ref in graph['outputs'].items()
               if name not in consumed or name == graph.get('delivery_port', 'answer')}
    # Derive public boundary names from the saved interface, never a transport
    # convention. Legacy graphs still contain task/answer and retain that map.
    def escape(name):
        return name.replace('~', '~0').replace('/', '~1')
    graph['nodes'] = [
        {'id': entry, 'label': 'Workflow input', 'op': 'input',
         'inputs': {name: {'source': 'input', 'pointer': '/' + escape(name)} for name in fields},
         'output_schema': {'type': 'object', 'properties': copy.deepcopy(fields),
                           'required': list(fields), 'additionalProperties': False}},
        *nodes,
        {'id': exit, 'label': 'Workflow output', 'op': 'output', 'inputs': outputs,
         'output_schema': {'type': 'object', 'properties': {name: {} for name in outputs},
                           'required': list(outputs), 'additionalProperties': False}},
    ]
    graph['outputs'].update({name: {'source': 'node', 'node': exit, 'pointer': '/' + escape(name)} for name in outputs})
    graph['result_node'] = exit
    return graph


def validate_boundaries(graph):
    boundaries = [n for n in graph['nodes'] if n['op'] in {'input', 'output'}]
    if not boundaries:
        return  # Archived v2 definitions still have their original meaning.
    if sorted(n['op'] for n in boundaries) != ['input', 'output']:
        raise ValueError('Define exactly one Workflow input and one Workflow output node')
    entry = next(n for n in boundaries if n['op'] == 'input')
    exit = next(n for n in boundaries if n['op'] == 'output')
    for node in boundaries:
        schema = node.get('output_schema') or {}
        if (not isinstance(schema, dict) or schema.get('type') != 'object'
                or set(schema.get('properties', {})) != set(node['inputs'])
                or set(schema.get('required', [])) != set(node['inputs'])):
            raise ValueError(node['label'] + ': output fields must match its named input bindings')
        if node.get('binding') or node.get('context_key') or node.get('enabled') is False:
            raise ValueError('Input/output nodes run in the host and cannot use an Agent binding or be held')
    if any(r['source'] not in {'input', 'literal', 'invocation'} for r in entry['inputs'].values()):
        raise ValueError('Workflow input maps public input, host invocation fields or fixed values')
    for name in exit['inputs']:
        ref = graph['outputs'].get(name)
        if ref != {'source': 'node', 'node': exit['id'], 'pointer': '/' + name.replace('~', '~0').replace('/', '~1')}:
            raise ValueError('Public outputs must map named fields of the Workflow output node')
    if any(e['to'] == entry['id'] or e['from'] == exit['id'] for e in graph.get('edges', [])):
        raise ValueError('Workflow input has no predecessors; Workflow output has no downstream nodes')
    for node in graph['nodes']:
        refs = [*node['inputs'].values(), *([node['items']] if node['op'] == 'map' else [])]
        if node['op'] != 'input' and any(r['source'] in {'input', 'invocation'} for r in refs):
            raise ValueError('Read public input and invocation fields from the Workflow input node')


def validate_reference_fields(graph):
    """Reject undeclared source fields before spending tokens on a graph."""
    nodes = {n['id']: n for n in graph['nodes']}

    def schema_for(ref, seen=()):
        if ref['source'] == 'input':
            from open_webui.execution.port_types import TASK_SCHEMA
            return graph.get('input_schema', TASK_SCHEMA)
        if ref['source'] == 'invocation':
            from open_webui.execution.module_invocation import invocation_schema
            return invocation_schema()
        if ref['source'] == 'output':
            if ref['port'] in seen:
                return None
            target = graph.get('outputs', {}).get(ref['port'])
            if target:
                return at_pointer(schema_for(target, (*seen, ref['port'])), target.get('pointer', ''), 'Graph output ' + ref['port'])
        if ref['source'] == 'node' and ref['node'] in nodes:
            node = nodes[ref['node']]
            from open_webui.execution.port_types import node_output_schema
            if node['op'] in {'call', 'workflow'} and node.get('output_schema') is None:
                return None
            return node_output_schema(node)
        return None

    def at_pointer(schema, path, label):
        for part in path.split('/')[1:]:
            key = part.replace('~1', '/').replace('~0', '~')
            if isinstance(schema, dict) and 'properties' in schema:
                if key not in schema['properties']:
                    raise ValueError(label + ': source field ' + path + ' is not declared')
                schema = schema['properties'][key]
            elif isinstance(schema, dict) and schema.get('type') == 'array':
                if not key.isdecimal() or len(key) > 1 and key.startswith('0'):
                    raise ValueError(label + ': array source fields require a nonnegative index')
                schema = schema.get('items')
            elif isinstance(schema, dict) and schema.get('type') in ('string', 'integer', 'number', 'boolean', 'null'):
                raise ValueError(label + ': source field ' + path + ' traverses a scalar value')
            else:
                return None
        return schema

    for node in graph['nodes']:
        for name, ref in node['inputs'].items():
            at_pointer(schema_for(ref), ref.get('pointer', ''), node['label'] + ' input ' + name)
        if node.get('items'):
            at_pointer(schema_for(node['items']), node['items'].get('pointer', ''), node['label'] + ' items')
    for name, ref in graph.get('outputs', {}).items():
        at_pointer(schema_for(ref), ref.get('pointer', ''), 'Graph output ' + name)
