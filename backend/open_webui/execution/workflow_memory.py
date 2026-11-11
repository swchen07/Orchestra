"""Branch-aware role checkpoints. Native handles are consumed once, never shared by forks."""

import copy
import asyncio
import hashlib
import json
import os
from pathlib import Path
import uuid

from open_webui.execution.claude import ExecutionError
from open_webui.execution.module_protocol import report_envelope


def context_signature(template, binding, environment=None):
    value = {
        'role': {
            k: binding.get(k)
            for k in ('framework_id', 'provider_id', 'model_id', 'profile_id', 'effort', 'role_instructions')
        },
        'policy': {**{k: template.get(k) for k in ('mode', 'lead_id', 'instructions')},
                   'input_policy': (template.get('graph') or {}).get('input_policy', 'shared')},
        'environment': environment,
    }
    from open_webui.execution.compare_workflow import is_comparison_graph
    if (template.get('graph') or {}).get('schema_version') == '2.0':
        value['policy']['module_contract'] = 'node-program-v2'
    elif (template.get('graph') and not template['graph'].get('controller')
            and not template.get('mode') and not is_comparison_graph(template)):
        value['policy']['module_contract'] = 'predefined-graph-v1'
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def branch_records(messages, user_message_id, scope, read):
    """Use server-owned ancestry, not the latest run in a chat (which may be another branch)."""
    if user_message_id not in messages or messages[user_message_id].get('role') != 'user':
        raise ExecutionError('Cannot restore workflow history: the current chat turn was not saved')
    identity = messages[user_message_id].get('parentId')
    seen, runs, records = set(), set(), []
    while identity:
        if identity in seen or identity not in messages:
            raise ExecutionError('Cannot restore workflow history: the chat branch is incomplete')
        seen.add(identity)
        message = messages[identity]
        candidates = [
            (s.get('workflow') or {}).get('run_id') for s in message.get('statusHistory', []) if isinstance(s, dict)
        ]
        candidates.append((message.get('meta') or {}).get('workflow_run_id'))
        run_id = next((r for r in reversed(candidates) if r), None)
        if run_id and run_id not in runs:
            runs.add(run_id)
            try:
                record = read(run_id)
            except Exception:
                raise ExecutionError('A saved workflow checkpoint is unavailable; history was not reset') from None
            if (
                all(record.get(k) == v for k, v in scope.items() if k != 'workflow_id')
                and record.get('template', {}).get('id') == scope['workflow_id']
            ):
                records.append(record)
        identity = message.get('parentId')
    return list(reversed(records))


def restore_roles(records):
    states = {}
    for record in records:
        if record.get('role_contexts') is not None:
            # A checkpoint contains the instances available in that run. An unused
            # dynamic instance may be absent; omission is not a request to erase it.
            states.update(copy.deepcopy(record['role_contexts']))
            continue
        # Migrate pre-checkpoint runs from retained successful calls, without replaying executions.
        for call in record.get('calls', []):
            if call.get('status') != 'completed' or not call.get('output'):
                continue
            state = states.setdefault(call['participant_id'], {'messages': []})
            state['messages'].extend(
                [
                    {'role': 'user', 'content': call['input']},
                    {'role': 'assistant', 'content': call['output']},
                ]
            )
            state['source_run_id'] = record['id']
            # Old runs have no trustworthy signature. Keep their dialogue, start a new native session.
            state['native_session_id'] = None
    return states


def report_archive(records):
    from open_webui.execution.module_protocol import report_rows
    composites = [row for record in records for row in report_rows(record) if row.get('kind') == 'composite']
    return composites + [
        {
            'id': call.get('report_id') or f'{record["id"]}/{call["id"]}',
            'role_id': call['participant_id'],
            'name': call['name'],
            'output': call['output'],
            'kind': call.get('kind', 'work'),
            'run_id': record['id'], 'run_status': record.get('status', 'unknown'),
            'review_status': (record.get('review') or {}).get('status'),
            'envelope': report_envelope(call),
        }
        for record in records
        for call in record.get('calls', [])
        if call.get('status') == 'completed' and call.get('output')
    ]


def claim_native_context(state, role_id, run_id):
    """An atomic claim survives restarts and excludes siblings, retries and cancelled native turns."""
    from open_webui.env import DATA_DIR

    source = state.get('source_run_id')
    if not source:
        return False
    try:
        source = str(uuid.UUID(source))
    except (ValueError, TypeError):
        return False
    root = Path(DATA_DIR) / 'execution-context-claims' / source
    root.mkdir(parents=True, exist_ok=True)
    handle = state.get('native_session_id') or state.get('scope_id')
    if handle:
        # Legacy claims contain only a role hash, so their handle cannot be
        # recovered reliably after a key migration. Replay conservatively if
        # this checkpoint has any legacy consumption; never re-resume it.
        if any(not p.name.startswith('handle-') for p in root.glob('*.claim')):
            return False
        identity = 'handle-' + hashlib.sha256(str(handle).encode()).hexdigest()
    else:
        # Pre-handle checkpoints retain the old role-based claim semantics.
        identity = hashlib.sha256(role_id.encode()).hexdigest()
    path = root / (identity + '.claim')
    try:
        with open(path, 'x', opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
            stream.write(run_id)
        return True
    except FileExistsError:
        return False


async def load_chat_records(meta, user_id, project_id, workflow_id):
    from open_webui.models.chats import Chats
    from open_webui.execution.workflows import read_run
    from open_webui.utils.chat_id import is_saved_chat_id

    chat_id = meta.get('chat_id')
    if not is_saved_chat_id(chat_id):
        raise ExecutionError('Choose a saved chat for workflows so role conversations can be retained')
    chat = await Chats.get_chat_by_id(chat_id)
    if not chat or chat.user_id != user_id:
        raise ExecutionError('Cannot restore workflow history: chat is unavailable')
    messages = await Chats.get_messages_map_by_chat_id(chat_id) or {}
    scope = {'user_id': user_id, 'chat_id': chat_id, 'project_id': project_id, 'workflow_id': workflow_id}
    # workflow_id lives in the frozen template rather than the run's top level.
    return await asyncio.to_thread(
        branch_records, messages, meta.get('user_message_id'), scope, lambda identity: read_run(identity, user_id)
    )
