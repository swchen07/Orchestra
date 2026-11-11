"""Typed public receipt ports, with no file bytes, credentials or runtime state."""
WORKSPACE_SCHEMA = {'type': 'object', 'properties': {
    'id': {'type': 'string'}, 'instance_id': {'type': 'string'}, 'backend': {'type': 'string'},
    'source_baseline': {'type': 'string'}, 'base_revision': {'type': 'string'}, 'current_revision': {'type': 'string'},
    'generation': {'type': 'integer', 'minimum': 1}, 'status': {'type': 'string'},
    'guarantees': {'type': 'object', 'properties': {'os_sandbox': {'type': 'boolean'}}},
    'conflicts': {'type': 'array', 'items': {'type': 'string'}},
}, 'required': ['id', 'instance_id', 'backend', 'source_baseline', 'base_revision', 'current_revision', 'generation', 'status', 'guarantees']}
ARTIFACT_SCHEMA = {'type': 'object', 'properties': {
    'kind': {'const': 'workspace-artifact'}, 'id': {'type': 'string'}, 'producer': {'type': 'string'},
    'lease_id': {'type': 'string'}, 'revision': {'type': 'string'}, 'base_revision': {'type': 'string'},
    'previous_revision': {'type': 'string'}, 'source_baseline': {'type': 'string'}, 'tree': {'type': 'string'},
    'sha256': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}, 'size': {'type': 'integer', 'minimum': 0},
    'media_type': {'type': 'string'}, 'created_at': {'type': 'integer'}, 'provenance': {'type': 'object'},
    'changed_paths': {'type': 'array', 'items': {'type': 'string'}},
    'files': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'path': {'type': 'string'}, 'sha256': {'type': 'string'}, 'size': {'type': 'integer', 'minimum': 0},
        'media_type': {'type': 'string'}}, 'required': ['path', 'sha256', 'size', 'media_type']}},
}, 'required': ['kind', 'id', 'producer', 'lease_id', 'revision', 'base_revision', 'previous_revision',
    'source_baseline', 'tree', 'sha256', 'size', 'media_type', 'created_at', 'provenance', 'changed_paths', 'files']}
ARTIFACTS_SCHEMA = {'type': 'array', 'items': ARTIFACT_SCHEMA}
