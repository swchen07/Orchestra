"""Reusable participant bindings and versioned collaboration presets."""

import uuid

MODES = {'single', 'parallel', 'summarize', 'delegate'}


async def enforce_project_participant(project, model, profile_id):
    if not project:
        return
    from fastapi import HTTPException
    from open_webui.models.folders import Folders
    from open_webui.models.config import Config
    from open_webui.execution.profiles import discover_profiles
    import asyncio

    folder = await Folders.get_folder_by_id(project['id'])
    selected = (folder.data or {}).get('execution_participants') if folder else None
    if not isinstance(selected, list):
        return
    profiles = await asyncio.to_thread(
        discover_profiles, project.get('directory'), None, folder.data.get('execution_profiles', [])
    )
    available = participants([model], profiles, await Config.get('execution.participants') or [])
    if not any(p['id'] in selected and (p.get('profile_id') or '') == (profile_id or '') for p in available):
        raise HTTPException(403, 'Choose a participant enabled for this project')


def participants(models, profiles, saved=()):
    native = {m['id']: m for m in models}
    defaults = [
        {
            'id': 'model:' + m['id'],
            'name': m.get('execution', {}).get('framework_name', 'Direct') + ' · ' + m.get('name', m['id']),
            'model_id': m['id'],
            'profile_id': None,
            'builtin': True,
        }
        for m in models
    ]
    result = []
    for row in [*defaults, *saved]:
        model = native.get(row.get('model_id'))
        if not model:
            continue
        execution = model.get('execution', {})
        if row.get('profile_id') and not any(
            p['id'] == row['profile_id'] and p.get('available') and p['framework_id'] == execution.get('framework_id')
            for p in profiles
        ):
            continue
        result.append(
            {
                **row,
                'framework_id': execution.get('framework_id', 'direct'),
                'provider_id': execution.get('provider_id'),
                'provider_name': execution.get('provider_name'),
                'model_name': model.get('name', model['id']),
            }
        )
    return result


def validate_participant(value, models, profiles):
    if not isinstance(value, dict) or set(value) - {'id', 'name', 'model_id', 'profile_id', 'effort'}:
        raise ValueError('Invalid participant binding')
    if value.get('id') and not valid_id(value['id']):
        raise ValueError('Invalid participant ID')
    if not isinstance(value.get('name'), str) or not 1 <= len(value['name'].strip()) <= 100:
        raise ValueError('Enter a participant name (1–100 characters)')
    probe = {**value, 'id': value.get('id') or str(uuid.uuid4()), 'name': value['name'].strip()}
    if not participants(models, profiles, [probe]) or not any(
        p['id'] == probe['id'] for p in participants(models, profiles, [probe])
    ):
        raise ValueError('Model or native preset is unavailable or incompatible')
    model = next(m for m in models if m['id'] == value['model_id'])
    if value.get('effort') and value['effort'] not in model.get('execution', {}).get('effort_options', []):
        raise ValueError('This model does not advertise the selected effort')
    return {k: probe.get(k) for k in ('id', 'name', 'model_id', 'profile_id', 'effort')}


def valid_id(identity):
    try:
        return str(uuid.UUID(identity)) == identity
    except (ValueError, TypeError, AttributeError):
        return False


def validate_workflow(value, available):
    if not isinstance(value, dict) or set(value) - {
        'id',
        'name',
        'mode',
        'participant_ids',
        'lead_id',
        'instructions',
        'max_rounds',
        'version',
    }:
        raise ValueError('Invalid workflow definition')
    if value.get('id') and not valid_id(value['id']):
        raise ValueError('Invalid workflow ID')
    if not isinstance(value.get('name'), str) or not 1 <= len(value['name'].strip()) <= 100:
        raise ValueError('Enter a workflow name (1–100 characters)')
    mode, ids = value.get('mode'), value.get('participant_ids')
    allowed = {p['id'] for p in available}
    if (
        mode not in MODES
        or not isinstance(ids, list)
        or not 1 <= len(ids) <= 8
        or any(not isinstance(i, str) or i not in allowed for i in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError('Choose 1–8 available participants and a valid mode')
    if mode != 'single' and len(ids) < 2:
        raise ValueError('This workflow needs at least two participants')
    lead = value.get('lead_id') or ids[0]
    if lead not in ids:
        raise ValueError('The lead must be a selected participant')
    if mode == 'single' and len(ids) != 1:
        raise ValueError('Single mode uses exactly one participant')
    instructions = value.get('instructions', '')
    rounds = value.get('max_rounds', 3)
    if (
        not isinstance(instructions, str)
        or len(instructions.encode()) > 16000
        or type(rounds) is not int
        or not 1 <= rounds <= 5
    ):
        raise ValueError('Instructions exceed 16 KiB or round limit is invalid (1–5)')
    return {
        'id': value.get('id') or str(uuid.uuid4()),
        'name': value['name'].strip(),
        'mode': mode,
        'participant_ids': ids,
        'lead_id': lead,
        'instructions': instructions,
        'max_rounds': rounds,
    }


def parse_dispatch(text, workers):
    import json

    raw = text.strip()
    if raw.startswith('```') and raw.endswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    try:
        value = json.loads(raw)
    except ValueError:
        raise ValueError('The lead did not return a valid dispatch action') from None
    if not isinstance(value, dict):
        raise ValueError('The lead returned an invalid dispatch action')
    if (
        value.get('action') == 'final'
        and set(value) == {'action', 'content'}
        and isinstance(value.get('content'), str)
        and value['content'].strip()
    ):
        return value
    tasks = value.get('tasks')
    if (
        value.get('action') != 'dispatch'
        or set(value) != {'action', 'tasks'}
        or not isinstance(tasks, list)
        or not 1 <= len(tasks) <= 8
    ):
        raise ValueError('The lead must dispatch tasks or return a final answer')
    seen = set()
    for task in tasks:
        if (
            not isinstance(task, dict)
            or set(task) != {'participant_id', 'input'}
            or task.get('participant_id') not in workers
            or task['participant_id'] in seen
            or not isinstance(task.get('input'), str)
            or not 1 <= len(task['input'].encode()) <= 32000
        ):
            raise ValueError('A dispatch references an unavailable participant or invalid task')
        seen.add(task['participant_id'])
    return value
