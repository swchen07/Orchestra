"""Public, host-owned invocation data; never a grant of control authority."""

import copy

def invocation_schema(operation=None):
    context = {
        'feedback': {'type': 'object', 'description': 'Host-issued corrections for this invocation.'},
    }
    if operation in {None, 'plan', 'review'}:
        context['prompt'] = {'type': 'string', 'description':
            'The host-authorized planning or review packet. Its result is still checked by the caller Action.'}
    if operation in {None, 'dispatch'}:
        context['dispatch'] = {'type': 'object', 'description': 'Allowed branch targets and correction state.'}
    return {'type': 'object', 'properties': {
        'action': {'type': 'string', 'description': 'The registered caller Action; this is data, not authority.'},
        'instruction': {'type': 'string', 'description': 'Responsibilities assigned by the caller node.'},
        'inputs': {'type': 'object', 'description': 'Only the caller node’s selected input values.'},
        'output_schema': {'description': 'The output contract checked by the caller host.'},
        'context': {'type': 'object', 'properties': context, 'additionalProperties': False},
    }, 'required': ['action', 'instruction', 'inputs', 'output_schema', 'context'], 'additionalProperties': False}


def boundary_context(graph, schema):
    """Reject unavailable host fields before any descendant work is admitted."""
    for node in graph['nodes']:
        for ref in node.get('inputs', {}).values():
            if ref.get('source') != 'invocation':
                continue
            if node['op'] != 'input':
                raise ValueError('Invocation fields must enter through the Workflow input boundary')
            cursor = schema
            for part in ref.get('pointer', '').split('/')[1:]:
                key = part.replace('~1', '/').replace('~0', '~')
                if isinstance(cursor, dict) and cursor.get('type') == 'object':
                    properties = cursor.get('properties', {})
                    if key not in properties and cursor.get('additionalProperties') is False:
                        raise ValueError('Unavailable host invocation field: ' + ref.get('pointer', ''))
                    cursor = properties.get(key, {})
                else:
                    break


def snapshot(node, inputs, *, prompt=None, feedback=None, dispatch=None):
    from open_webui.execution.node_actions import resolve_action
    action = resolve_action(node)
    context = {}
    if prompt is not None and action['operation'] in {'plan', 'review'}:
        context['prompt'] = prompt
    if feedback:
        context['feedback'] = copy.deepcopy(feedback)
    if dispatch is not None and action['operation'] == 'dispatch':
        context['dispatch'] = copy.deepcopy(dispatch)
    return {'action': action['id'], 'instruction': node.get('instruction', ''),
            'inputs': copy.deepcopy(inputs), 'output_schema': copy.deepcopy(node.get('output_schema')),
            'context': context}
