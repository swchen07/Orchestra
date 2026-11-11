"""Public structural schemas for workflow programs and legacy imports.

Project scope, reference validity, readiness and immutable invocation
checks remain compiler/runtime rules; this schema is not an execution grant.
"""

import copy
from open_webui.execution.node_actions import BY_ID, BY_OPERATION

ID = {'type': 'string', 'pattern': '^[A-Za-z_][A-Za-z0-9_-]{0,63}$'}
TEXT = {'type': 'string'}


def object_schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


REF = object_schema(
    {
        'source': {'enum': ['input', 'node', 'report', 'literal']},
        'pointer': TEXT,
        'node': ID,
        'report_ref': TEXT,
        'optional': {'type': 'boolean'},
        'value': {},
    },
    ['source'],
)
NODE = object_schema(
    {
        'id': ID,
        'label': {'type': 'string', 'minLength': 1, 'maxLength': 150},
        'op': {'enum': ['call', 'route', 'map', 'join', 'loop', 'checkpoint', 'finish']},
        'enabled': {'type': 'boolean'},
        'binding': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
        'instruction': {'type': 'string', 'maxLength': 16000},
        'inputs': {'type': 'object', 'maxProperties': 40, 'additionalProperties': REF},
        'context_key': ID,
        'output_schema': {'type': ['boolean', 'object']},
        'cases': {'type': 'array', 'items': ID, 'minItems': 2, 'maxItems': 12, 'uniqueItems': True},
        'policy': {'enum': ['all_terminal', 'all_success', 'first_success', 'quorum']},
        'quorum': {'type': 'integer', 'minimum': 1},
        'items': REF,
        'max_iterations': {'type': 'integer', 'minimum': 1, 'maximum': 8},
        'stop_when': object_schema({'pointer': TEXT, 'equals': {}}, ['pointer', 'equals']),
        'question': {'type': 'string', 'maxLength': 8000},
        'acceptance': {'type': 'array', 'items': TEXT, 'maxItems': 20},
    },
    ['id', 'op'],
)
EDGE = object_schema(
    {
        'from': ID,
        'to': ID,
        'on': {'enum': ['success', 'terminal', 'case']},
        'case': ID,
    },
    ['from', 'to'],
)
SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    '$id': 'urn:open-webui:workflow-graph:1.0',
    'title': 'Open WebUI workflow graph 1.0',
    'description': 'Data references compile dependency edges. The executor advances ready nodes independently. Controller revisions can modify unstarted nodes. Bindings resolve only against the Project module catalog.',
    **object_schema(
        {
            'schema_version': {'const': '1.0'},
            'revision': {'type': 'integer', 'minimum': 1},
            'nodes': {'type': 'array', 'minItems': 1, 'maxItems': 80, 'items': NODE},
            'edges': {'type': 'array', 'maxItems': 320, 'items': EDGE},
            'result_node': ID,
            'input_policy': {'enum': ['shared', 'scoped']},
            'controller': {
                'anyOf': [
                    {'type': 'null'},
                    object_schema(
                        {
                            'binding': TEXT,
                            'initial': {'type': 'boolean'},
                            'on_events': {'type': 'boolean'},
                            'instructions': {'type': 'string', 'maxLength': 16000},
                            'catalog_scope': {'enum': ['project', 'roles']},
                        },
                        ['binding'],
                    ),
                ]
            },
            'reviewer': {
                'anyOf': [
                    {'type': 'null'},
                    object_schema({'binding': TEXT, 'instructions': {'type': 'string', 'maxLength': 8000},
                                   'max_repairs': {'type': 'integer', 'minimum': 0, 'maximum': 2}}, ['binding']),
                ]
            },
            'limits': object_schema(
                {
                    'max_calls': {'type': 'integer', 'minimum': 1, 'maximum': 24},
                    'max_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 600},
                    'max_concurrency': {'type': 'integer', 'minimum': 1, 'maximum': 2},
                    'max_replans': {'type': 'integer', 'minimum': 1, 'maximum': 5},
                    'max_tokens': {'type': 'integer', 'minimum': 1, 'maximum': 1000000},
                }
            ),
        },
        ['nodes', 'result_node'],
    ),
}

SCHEMA_V2 = copy.deepcopy(SCHEMA)
SCHEMA_V2.update({'$id': 'urn:open-webui:workflow-graph:2.0', 'title': 'Composable workflow graph 2.0'})
SCHEMA_V2['properties']['schema_version'] = {'const': '2.0'}
SCHEMA_V2['properties'].pop('controller')
SCHEMA_V2['properties'].pop('reviewer')
REF_V2 = copy.deepcopy(REF)
REF_V2['properties']['source']['enum'].append('output')
REF_V2['properties']['source']['enum'].append('invocation')
REF_V2['properties']['port'] = ID
NODE_V2 = SCHEMA_V2['properties']['nodes']['items']
NODE_V2['properties']['op']['enum'] = list(BY_OPERATION)
NODE_V2['properties']['action'] = {'enum': list(BY_ID)}
NODE_V2['properties']['workspace'] = object_schema({
    'instance': {'type': 'string', 'pattern': '^[A-Za-z_][A-Za-z0-9_-]{0,63}$'},
    'command_ids': {'type': 'array', 'items': ID, 'maxItems': 20, 'uniqueItems': True},
    'strategy': {'enum': ['select', 'merge']},
})
NODE_V2['properties']['report_description'] = {'type': 'string', 'maxLength': 4000}
NODE_V2['required'] = ['id']
NODE_V2['anyOf'] = [{'required': ['op']}, {'required': ['action']}]
NODE_V2['properties']['inputs']['additionalProperties'] = REF_V2
NODE_V2['properties']['items'] = REF_V2
NODE_V2['properties']['planning'] = object_schema({
    'initial': {'type': 'boolean'}, 'on_events': {'type': 'boolean'},
    'catalog_scope': {'enum': ['project', 'roles']}, 'output_port': ID,
    'observe_nodes': {'type': 'array', 'items': ID, 'maxItems': 80, 'uniqueItems': True},
})
NODE_V2['properties']['review'] = object_schema({
    'max_repairs': {'type': 'integer', 'minimum': 0, 'maximum': 8}, 'repair_node': ID,
})
NODE_V2['properties']['dispatch'] = object_schema({
    'targets': {'type': 'array', 'items': ID, 'minItems': 1, 'maxItems': 24, 'uniqueItems': True},
}, ['targets'])
SCHEMA_V2['properties']['outputs'] = {'type': 'object', 'maxProperties': 20, 'additionalProperties': REF_V2}
SCHEMA_V2['properties']['delivery_port'] = ID
SCHEMA_V2['properties']['edges']['items']['properties']['generated'] = {'type': 'boolean'}
SCHEMA_V2['properties']['edges']['items']['properties']['on']['enum'].append('revise')
SCHEMA_V2['required'] = ['schema_version', 'nodes', 'outputs']

# Port and environment contracts are shared by imported and modern programs.
for schema in (SCHEMA, SCHEMA_V2):
    schema['properties'].update(input_schema={'type': ['boolean', 'object']},
                                output_schema={'type': ['boolean', 'object']},
                                output_schema_inferred={'type': 'boolean'})
    schema['properties']['nodes']['items']['properties'].update(
        input_schema={'type': ['boolean', 'object']}, input_schema_inferred={'type': 'boolean'},
        environment={'type': 'object', 'properties': {
            'filesystem': {'enum': ['inherit', 'none', 'read-only', 'workspace-write']},
            'tools': {'enum': ['inherit', 'none']},
            'network': {'enum': ['inherit', 'provider-only']},
            'isolation': {'enum': ['logical', 'process', 'sandbox']},
        }, 'additionalProperties': False})
