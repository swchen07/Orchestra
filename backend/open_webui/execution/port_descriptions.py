"""Carry declared port meanings to both the producer and its consumers."""


def reference_description(ref, graph, seen=()):
    source = ref.get('source')
    if source == 'output':
        port = ref.get('port')
        if port in seen:
            return ''
        target = graph.get('outputs', {}).get(port)
        if not target:
            return ''
        return reference_description({**target, 'pointer': target.get('pointer', '') + ref.get('pointer', '')}, graph, (*seen, port))
    if source == 'input':
        return 'The current request and visible chat context.' if ref.get('pointer') == '/task' else ''
    if source != 'node':
        return ''
    node = next((n for n in graph['nodes'] if n['id'] == ref.get('node')), {})
    path = ref.get('pointer', '')
    schema = node.get('output_schema') or {}
    report_schema = schema.get('properties', {}).get('report') if isinstance(schema, dict) else None
    if path == '/report' and node.get('report_description') and not (
            isinstance(report_schema, dict) and report_schema.get('description')):
        return node['report_description'][:2000]
    for part in path.split('/')[1:]:
        key = part.replace('~1', '/').replace('~0', '~')
        if not isinstance(schema, dict):
            return ''
        schema = schema.get('properties', {}).get(key, {})
    return str(schema.get('description', ''))[:2000] if isinstance(schema, dict) else ''


def input_descriptions(node, graph):
    return {name: description for name, ref in node.get('inputs', {}).items()
            if (description := reference_description(ref, graph))}
