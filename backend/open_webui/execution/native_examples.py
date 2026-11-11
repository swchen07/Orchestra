"""A real nested-team acceptance task through the regular graph/chat pipeline."""
import asyncio
import copy
import time
import uuid
import json
from fastapi import HTTPException
from open_webui.execution.adaptive_examples import authorize_example_chat

active_checks = {}

PROMPTS = [
    'Native team accounting — Round 1. You MUST actually delegate to TWO native leaf subagents; '
    'do not answer on their behalf or simulate tools. DSH: use subagent, wait for their messages; '
    'Claude Code: use Agent with subagent_type webui-worker; Hermes: delegate_task(tasks=[...]) '
    'with empty child toolsets. Inherit the selected Flash model. No commands, files, network, '
    'or other tools. Give the LEFT child 12+18+20 and the RIGHT child 9+16+25. '
    'Each child chooses its own short marker and returns subtotal and marker. Wait for BOTH actual '
    'reports. Reply ONLY JSON: {"left":{"subtotal":number,"marker":"..."},'
    '"right":{"subtotal":number,"marker":"..."},"total":number}. '
    'If delegation is unavailable, report the failure; never claim it happened.',
    'Native team accounting — Round 2. Use YOUR OWN previous module response, not another '
    'module answer in the shared transcript. Actually delegate to TWO NEW native leaf subagents '
    'with the same no-tool/no-command restrictions and selected Flash model. Give each its '
    'corresponding original report from your retained context. LEFT applies a refund of 5; RIGHT '
    'adds shipping of 7. Each must verify and preserve the marker from its supplied report. '
    'Wait for both real native reports and return the same short JSON schema with adjusted '
    'subtotals and total. Do not solve the leaf tasks yourself or invent calls.'
]


def validate_round(record, turn, markers, sources):
    if record['status'] != 'completed' or len(record['calls']) != 3:
        raise ValueError('A native module call did not complete')
    for call in record['calls']:
        native = (call.get('execution') or {}).get('native_usage') or {}
        if not native.get('complete') or native.get('child_count') != 2:
            raise ValueError('Native child accounting is incomplete; inspect the retained call')
        if native['self']['total_tokens'] + native['children']['total_tokens'] != native['inclusive']['total_tokens']:
            raise ValueError('Native own and descendant totals do not reconcile')
        own_sources = {s for a in native['agents'] for s in a.get('source_event_ids', [])}
        if own_sources & sources:
            raise ValueError('A native usage source was counted by two invocations')
        sources.update(own_sources)
        try:
            output = call['output']
            answer = json.loads(output[output.index('{'):output.rindex('}') + 1])
            expected = (50, 50, 100) if turn == 0 else (45, 57, 102)
            if (answer['left']['subtotal'], answer['right']['subtotal'], answer['total']) != expected:
                raise ValueError()
            pair = (answer['left']['marker'], answer['right']['marker'])
            if not turn:
                markers[call['participant_id']] = pair
            elif markers.get(call['participant_id']) != pair:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ValueError('Native multi-turn result or retained markers did not match the task') from None


async def run_check(request, user, project, suite):
    from open_webui.execution.project_data import project_lock, update_overlay
    from open_webui.execution.workflows import generate_workflow, read_run
    from open_webui.models.chats import Chats

    async def persist():
        async with project_lock(project['id']):
            await update_overlay(project['id'], user, 'execution_native_check', copy.deepcopy(suite))

    async def turn(job, prompt):
        chat = await Chats.get_chat_by_id(job['chat_id'])
        authorize_example_chat(chat, user.id, project['id'])
        history = copy.deepcopy(chat.chat['history'])
        parent = history.get('currentId')
        if not history.get('messages'):
            parent = None
        uid, aid = str(uuid.uuid4()), str(uuid.uuid4())
        now = int(time.time())
        if parent:
            history['messages'][parent].setdefault('childrenIds', []).append(uid)
        history['messages'][uid] = {'id': uid, 'role': 'user', 'parentId': parent, 'childrenIds': [aid],
                                    'content': prompt, 'timestamp': now, 'models': [job['model_id']]}
        history['messages'][aid] = {'id': aid, 'role': 'assistant', 'parentId': uid, 'childrenIds': [],
                                    'content': '', 'timestamp': now, 'model': job['model_id'],
                                    'modelName': 'Native team check', 'done': False}
        history['currentId'] = aid
        if not await Chats.update_chat_by_id(chat.id, {'history': history}):
            raise HTTPException(500, 'Could not save the example chat turn')
        latest_status = None

        async def observe(record):
            nonlocal latest_status
            if latest_status == record['status']:
                return
            latest_status = record['status']
            status = {'action': 'workflow', 'done': record['status'] not in {'queued', 'running'},
                      'description': 'Compare · ' + record['status'],
                      'workflow': {'run_id': record['id'], 'name': 'Compare'}}
            await Chats.upsert_message_to_chat_by_id_and_message_id(chat.id, aid, {'statusHistory': [status]}, touch=False)
            job['current_run_id'] = record['id']
            if record['status'] not in {'queued', 'running'}:
                job.setdefault('runs', []).append({'id': record['id'], 'status': record['status'],
                                                   'review': record.get('review', {}),
                                                   'budget': (record.get('control') or {}).get('budget')})
            await persist()

        body = {'model': job['model_id'], 'messages': [{'role': 'user', 'content': prompt}], 'stream': False,
                'metadata': {'chat_id': chat.id, 'user_message_id': uid, 'message_id': aid,
                             'execution_project': project, 'execution_workflow_id': suite['workflow_id'],
                             'execution_workflow_policy': 'automatic',
                             'execution_workflow_overrides': {'workflow_id': suite['workflow_id'],
                                                              'roles': {r: {'participant_id': p} for r, p in job['bindings'].items()}}}}
        try:
            result = await generate_workflow(request, body, user, publish_observer=observe)
            await Chats.upsert_message_to_chat_by_id_and_message_id(chat.id, aid, {
                'content': result['choices'][0]['message']['content'], 'usage': result.get('usage'), 'done': True})
        except HTTPException as error:
            await Chats.upsert_message_to_chat_by_id_and_message_id(chat.id, aid, {
                'content': 'Run failed. Inspect the retained activity for details.',
                'error': {'content': str(error.detail)}, 'done': True})
            raise
        except asyncio.CancelledError:
            await Chats.upsert_message_to_chat_by_id_and_message_id(chat.id, aid, {
                'content': 'Example run stopped. Completed reports remain in Activity.', 'done': True,
                'error': {'content': 'Stopped'}})
            raise
        except Exception:
            await Chats.upsert_message_to_chat_by_id_and_message_id(chat.id, aid, {
                'content': 'Example execution failed. Retained activity is available for inspection.',
                'error': {'content': 'Example execution failed'}, 'done': True})
            raise

    job = suite['chats'][0]
    try:
        markers, sources = {}, set()
        previous = list(job.get('runs', []))
        for index, row in enumerate(previous):
            validate_round(await asyncio.to_thread(read_run, row['id'], user.id), index, markers, sources)
        for index, prompt in enumerate(PROMPTS[len(previous):], len(previous)):
            await turn(job, prompt)
            record = await asyncio.to_thread(read_run, job['current_run_id'], user.id)
            validate_round(record, index, markers, sources)
        job['status'] = suite['status'] = 'completed'
    except asyncio.CancelledError:
        job['status'] = suite['status'] = 'interrupted'
    except Exception as error:
        job['status'] = suite['status'] = 'failed'
        suite['error'] = str(error) if isinstance(error, (HTTPException, ValueError)) else 'Native check failed'
    finally:
        await persist()
        active_checks.pop(project['id'], None)
