"""Registered node actions shared by the interpreter, planner and visual editor.

The adjacent JSON is public metadata, not executable code. A matching host
handler is required; an arbitrary action name never executes a prompt or tool.
"""

import copy
import json
from pathlib import Path

CATALOG = json.loads(Path(__file__).with_suffix('.json').read_text())
BY_ID = {a['id']: a for a in CATALOG['actions']}
BY_OPERATION = {a['operation']: a for a in CATALOG['actions']}
ACTION_HANDLERS = {
    'input': 'action_boundary', 'output': 'action_boundary',
    'call': 'action_call', 'route': 'action_call', 'dispatch': 'action_dispatch',
    'review': 'action_review', 'map': 'action_map', 'loop': 'action_loop',
    'checkpoint': 'action_checkpoint', 'join': 'action_join', 'finish': 'action_finish',
    'plan': None,  # The serialized controller event loop owns planning invocations.
    'workflow': 'action_workflow',
    'command': 'action_workspace', 'evaluate': 'action_workspace',
    'integrate': 'action_workspace', 'publish': 'action_workspace',
}
if set(ACTION_HANDLERS) != set(BY_OPERATION):
    raise RuntimeError('Every registered node action needs an explicit implementation binding')


def resolve_action(node):
    action = BY_ID.get(node.get('action')) if node.get('action') else BY_OPERATION.get(node.get('op'))
    if not action:
        raise ValueError('Choose a registered node action')
    if node.get('op') and node['op'] != action['operation']:
        raise ValueError('Node action and its legacy operation alias disagree')
    return action


def normalize_action(node):
    action = resolve_action(node)
    node.update(action=action['id'], op=action['operation'])


def feedback_policy(node):
    return resolve_action(node).get('feedback_policy')


def action_catalog():
    return copy.deepcopy(CATALOG)


def planner_actions():
    return [{k: row[k] for k in ('id', 'operation', 'summary', 'executor', 'context_rule', 'feedback_policy')}
            for row in CATALOG['actions'] if row['operation'] not in {'input', 'output', 'plan', 'review'}]
