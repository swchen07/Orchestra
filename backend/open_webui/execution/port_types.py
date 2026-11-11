"""One local JSON Schema boundary for graph ports; no coercion or remote refs."""

import copy
import json
from jsonschema import Draft202012Validator


TASK_SCHEMA = {'type': 'object', 'properties': {'task': {'type': 'string'}},
               'required': ['task'], 'additionalProperties': False}


def check_schema(schema, label):
    try:
        encoded = json.dumps(schema, allow_nan=False)
        if len(encoded) > 16000 or any(k in encoded for k in ('"$ref"', '"$dynamicRef"')):
            raise ValueError()
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise ValueError(label + ': use a valid local JSON Schema within 16 KiB') from None


def validate_value(value, schema, label):
    if schema is None:
        return
    try:
        json.dumps(value, allow_nan=False)
        error = next(Draft202012Validator(schema).iter_errors(value), None)
    except (ValueError, TypeError, RecursionError):
        raise ValueError(label + ': value must be finite JSON') from None
    if error:
        path = '.'.join(map(str, error.absolute_path)) or '(root)'
        # Never echo the input value: it may be a private report or credential.
        raise ValueError(label + ': ' + path + ' violates ' + str(error.validator))


def inferred(value):
    if value is None:
        return {'type': 'null'}
    if type(value) is bool:
        return {'type': 'boolean'}
    if isinstance(value, dict):
        return {'type': 'object', 'properties': {k: inferred(v) for k, v in value.items()},
                'required': list(value), 'additionalProperties': False}
    if isinstance(value, list):
        return {'type': 'array', 'items': {'anyOf': [inferred(v) for v in value]} if value else {}}
    return {'type': 'integer' if type(value) is int else 'number' if type(value) is float else 'string'}


def selected_schema(schema, path):
    for part in path.split('/')[1:]:
        key = part.replace('~1', '/').replace('~0', '~')
        if isinstance(schema, dict):
            if schema.get('type') == 'array':
                schema = schema.get('items', {})
            else:
                schema = schema.get('properties', {}).get(key, schema.get('additionalProperties', {}))
        else:
            return {}
    return copy.deepcopy(schema)


def module_output_schema(node):
    from open_webui.execution.node_program import capability_schema
    schema = copy.deepcopy(node.get('output_schema', capability_schema(node['op'])))
    if schema is None:
        schema = {'type': 'object', 'properties': {'report': {'type': 'string'}}, 'required': ['report']}
    if isinstance(schema, dict) and schema.get('type') not in (None, 'object'):
        schema = {'type': 'object', 'properties': {'value': schema}, 'required': ['value']}
    if isinstance(schema, dict) and node['op'] not in {'input', 'output'}:
        schema.setdefault('properties', {}).setdefault('report', {'type': 'string'})
        schema['properties']['__report'] = {}
        from open_webui.execution.workspace_contracts import WORKSPACE_SCHEMA, ARTIFACTS_SCHEMA
        schema['properties']['__workspace'] = copy.deepcopy(WORKSPACE_SCHEMA)
        schema['properties']['__artifacts'] = copy.deepcopy(ARTIFACTS_SCHEMA)
    return schema


def node_output_schema(node):
    """Map/repeat declare their per-invocation output; the host owns aggregation."""
    item = module_output_schema(node)
    if node['op'] == 'map':
        return {'type': 'object', 'properties': {
            'items': {'type': 'array', 'items': {'type': 'object', 'properties': {
                'key': {'type': 'string'}, 'status': {'enum': ['completed', 'failed']},
                'output': item, 'error': {'type': 'string'}}, 'required': ['key', 'status']}},
            'partial': {'type': 'boolean'}, 'report': {'type': 'string'}},
            'required': ['items', 'partial', 'report']}
    if node['op'] == 'loop':
        return {'type': 'object', 'properties': {'last': item, 'iterations': {'type': 'integer'},
                'stop_reason': {'type': 'string'}, 'report': {'type': 'string'}},
                'required': ['last', 'iterations', 'stop_reason', 'report']}
    return item


def invocation_input_schema(node, graph):
    schema = copy.deepcopy(node.get('input_schema'))
    if isinstance(schema, dict) and node['op'] in {'map', 'loop'}:
        extra = {'item': selected_schema(source_schema(node['items'], graph), '/0')} if node['op'] == 'map' else {'previous': {}}
        schema.setdefault('properties', {}).update(extra)
        schema['required'] = list(dict.fromkeys([*schema.get('required', []), *extra]))
    return schema


def source_schema(ref, graph, seen=()):
    from open_webui.execution.graph_spec import pointer
    source = ref['source']
    if source == 'literal':
        return inferred(pointer(ref['value'], ref.get('pointer', '')))
    if source == 'invocation':
        from open_webui.execution.module_invocation import invocation_schema
        return selected_schema(invocation_schema(), ref.get('pointer', ''))
    if source == 'input':
        schema = graph.get('input_schema', TASK_SCHEMA)
    elif source == 'output':
        if ref['port'] in seen:
            return {}
        target = graph.get('outputs', {}).get(ref['port'])
        schema = source_schema(target, graph, (*seen, ref['port'])) if target else {}
    elif source == 'report':
        schema = {'type': 'object', 'properties': {'report': {'type': 'string'}, 'report_ref': {'type': 'string'}, '__report': {}}}
    else:
        node = next(n for n in graph['nodes'] if n['id'] == ref['node'])
        schema = node_output_schema(node)
        if node['op'] in {'input', 'output'} and isinstance(schema, dict):
            # Host boundaries copy values. An unspecified boundary field does
            # not erase the known type of its selected source.
            for name, source in node['inputs'].items():
                if schema.get('properties', {}).get(name) in ({}, True):
                    schema['properties'][name] = source_schema(source, graph, seen)
    return selected_schema(schema, ref.get('pointer', ''))


def compatible(producer, consumer):
    """Conservative structural assignment; unknown producer types defer to runtime.

    This is not a general JSON Schema theorem prover. Exact values, unions,
    object required fields, arrays and scalar type disjointness are checked here;
    constraints not provable statically are always enforced on the actual value.
    """
    if consumer in (None, True, {}) or producer in (None, True, {}):
        return True
    if producer is False:
        return True
    if consumer is False:
        return False
    if 'const' in producer or 'enum' in producer:
        return all(Draft202012Validator(consumer).is_valid(v)
                   for v in ([producer['const']] if 'const' in producer else producer['enum']))
    for keyword in ('anyOf', 'oneOf'):
        if keyword in producer:
            return all(compatible(p, consumer) for p in producer[keyword])
        if keyword in consumer:
            return any(compatible(producer, c) for c in consumer[keyword])
    p, c = producer.get('type'), consumer.get('type')
    ps, cs = set(p if isinstance(p, list) else [p]), set(c if isinstance(c, list) else [c])
    if p and c and not all(t in cs or t == 'integer' and 'number' in cs for t in ps):
        return False
    if p == c == 'object':
        pp, cp = producer.get('properties', {}), consumer.get('properties', {})
        if set(consumer.get('required', [])) - set(producer.get('required', [])):
            return False
        if consumer.get('additionalProperties') is False and (set(pp) - set(cp) or producer.get('additionalProperties', True) is not False):
            return False
        return all(compatible(pp[k], cp[k]) for k in pp.keys() & cp.keys())
    if p == c == 'array':
        return compatible(producer.get('items', {}), consumer.get('items', {}))
    return True


def compile_ports(graph):
    graph.setdefault('input_schema', copy.deepcopy(TASK_SCHEMA))
    check_schema(graph['input_schema'], 'Workflow input')
    for node in graph['nodes']:
        props = {k: source_schema(ref, graph) for k, ref in node['inputs'].items()}
        # Optional references expose an explicit unavailable outcome on failure.
        for name, ref in node['inputs'].items():
            if ref.get('optional') or node['op'] == 'join' and node.get('policy') != 'all_success':
                props[name] = {'anyOf': [props[name], {'type': 'object', 'properties': {
                    'status': {'type': 'string'}, 'error': {'type': 'string'}}, 'required': ['status', 'error']}]}
        schema = None if node.get('input_schema_inferred') else node.get('input_schema')
        if schema is None:
            schema = {'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False}
            node['input_schema'] = schema
            node['input_schema_inferred'] = True
        check_schema(schema, node['label'] + ' inputs')
        if not compatible({'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False}, schema):
            raise ValueError(node['label'] + ': source output type is incompatible with input_schema')
        if node['op'] in {'input', 'output'} and not compatible(
                {'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False}, node['output_schema']):
            raise ValueError(node['label'] + ': host boundaries copy inputs; output types are incompatible')
    if graph.get('schema_version') == '2.0':
        boundary = next((n for n in graph['nodes'] if n['op'] == 'output'), None)
        names = list(boundary['inputs']) if boundary else list(graph['outputs'])
        public = {'type': 'object', 'properties': {k: source_schema(graph['outputs'][k], graph) for k in names},
                  'required': names, 'additionalProperties': False}
        if graph.get('output_schema') is not None and graph.get('output_schema') is not True and not graph.get('output_schema_inferred'):
            check_schema(graph['output_schema'], 'Workflow outputs')
            if not compatible(public, graph['output_schema']):
                raise ValueError('Workflow output types are incompatible with output_schema')
        else:
            graph['output_schema'] = public
            graph['output_schema_inferred'] = True
    return graph
