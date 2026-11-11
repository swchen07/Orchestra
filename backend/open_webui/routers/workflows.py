import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from open_webui.execution.profiles import discover_profiles
from open_webui.execution.security import require_origin
from open_webui.execution.workflow_config import participants, validate_participant, validate_workflow
from open_webui.execution.workflow_templates import (
    template_view,
    validate_template,
    normalize_graph_template,
    effective_bindings,
    compile_template,
)
from open_webui.models.config import Config
from open_webui.execution.workflow_presets import ensure_presets, match_participants, resolve_workflow, archive_template, restore_template, starter_templates, CATALOG_REVISION
from open_webui.execution.compare_workflow import normalize_compare
from open_webui.routers.execution import project_models
from open_webui.utils.auth import get_admin_user

router = APIRouter(dependencies=[Depends(require_origin)])
configuration_lock = asyncio.Lock()


async def available_participants(request, user, project_id=None, ignore_selection=False):
    models = await project_models(request, user)
    directory = None
    custom = []
    selected = None
    if project_id:
        from open_webui.execution.project_data import require_project

        folder = await require_project(project_id, user)
        config = folder.data['execution_project']
        models = [m for m in models if m.get('execution', {}).get('provider_id') in config['provider_ids']]
        directory, custom = config.get('directory'), folder.data.get('execution_profiles', [])
        selected = folder.data.get('execution_participants')
    profiles = await asyncio.to_thread(discover_profiles, directory, None, custom)
    available = participants(models, profiles, await Config.get('execution.participants') or [])
    if isinstance(selected, list) and not ignore_selection:
        available = [p for p in available if p['id'] in selected]
    return models, profiles, available


@router.get('/projects/{identity}/participant-options')
async def project_participant_options(identity: str, request: Request, user=Depends(get_admin_user)):
    from open_webui.execution.project_data import require_project

    folder = await require_project(identity, user)
    available = (await available_participants(request, user, identity, True))[2]
    return {
        'available': available,
        'selected_ids': folder.data.get('execution_participants') or [p['id'] for p in available],
    }


@router.put('/projects/{identity}/participants')
async def set_project_participants(identity: str, request: Request, value: dict, user=Depends(get_admin_user)):
    from open_webui.execution.project_data import project_lock, update_overlay

    available = (await available_participants(request, user, identity, True))[2]
    ids = value.get('participant_ids')
    allowed = {p['id'] for p in available}
    if (
        set(value) != {'participant_ids'}
        or not isinstance(ids, list)
        or not 1 <= len(ids) <= 80
        or any(not isinstance(i, str) or i not in allowed for i in ids)
    ):
        raise HTTPException(422, 'Choose 1–80 participants from this project’s providers')
    from open_webui.execution.project_data import require_project

    folder = await require_project(identity, user, write=True)
    default = folder.data['execution_project'].get('default_model_id')
    if not any(p['id'] in ids and p['model_id'] == default and not p.get('profile_id') for p in available):
        raise HTTPException(422, 'Keep the current default participant enabled, or change the project’s default first')
    async with project_lock(identity):
        await update_overlay(identity, user, 'execution_participants', list(dict.fromkeys(ids)))
    return {'participant_ids': list(dict.fromkeys(ids))}


@router.get('/participants')
async def list_participants(request: Request, project_id: str = '', user=Depends(get_admin_user)):
    return (await available_participants(request, user, project_id))[2]


async def available_modules(request, user, project_id, *, definitions=None, overlays=None):
    """The same authorized reference catalog backs roles and graph nodes."""
    available = (await available_participants(request, user, project_id))[2]
    if project_id:
        from open_webui.execution.project_data import require_project
        from open_webui.execution.workflow_module import workflow_catalog
        if overlays is None:
            folder = await require_project(project_id, user)
            overlays = folder.data.get('execution_workflow_bindings') or {}
        available += workflow_catalog(definitions if definitions is not None else await ensure_presets(), overlays, available)
    return available


def public_modules(rows):
    fields = {'id', 'name', 'model_id', 'profile_id', 'effort', 'framework_id', 'provider_id',
              'provider_name', 'model_name', 'builtin', 'module_type', 'input_schema', 'output_schema'}
    return [{**{k: v for k, v in row.items() if k in fields},
             **({'configuration': {'workflow_id': row['_workflow_template']['id'],
                                  'roles': [{k: role[k] for k in ('id', 'name')} for role in row['_workflow_template'].get('roles', [])],
                                  'bindings': {b['id']: b.get('binding_id') for b in row['_workflow_bindings']}}}
                if row.get('module_type') == 'workflow' else {})} for row in rows]


@router.get('/modules')
async def list_modules(request: Request, project_id: str = '', user=Depends(get_admin_user)):
    return public_modules(await available_modules(request, user, project_id))


@router.post('/participants')
async def save_participant(request: Request, value: dict, user=Depends(get_admin_user)):
    models, profiles, _ = await available_participants(request, user)
    try:
        binding = validate_participant(value, models, profiles)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    async with configuration_lock:
        rows = await Config.get('execution.participants') or []
        if len(rows) >= 80 and binding['id'] not in {p['id'] for p in rows}:
            raise HTTPException(422, 'Save up to 80 participant presets')
        await Config.upsert({'execution.participants': [r for r in rows if r['id'] != binding['id']] + [binding]})
    return binding


@router.get('/workflows')
async def list_workflows(request: Request, project_id: str = '', user=Depends(get_admin_user), archived_id: str = ''):
    available = (await available_participants(request, user, project_id))[2] if project_id else []
    overlays = {}
    if project_id:
        from open_webui.execution.project_data import require_project

        folder = await require_project(project_id, user)
        overlays = folder.data.get('execution_workflow_bindings') or {}
    result = []
    rows = await ensure_presets()
    if project_id:
        from open_webui.execution.workflow_module import workflow_catalog
        available = [*available, *workflow_catalog(rows, overlays, available)]
    if archived_id and archived_id not in {r['id'] for r in rows}:
        archived = await resolve_workflow(archived_id)
        if archived:
            rows = [*rows, archived]
    for row in rows:
        binding = effective_bindings(row, overlays) if project_id else {}
        reason = '' if project_id else 'Choose a Project to bind workflow roles'
        if project_id:
            try:
                compile_template(row, binding, available)
            except ValueError as error:
                reason = str(error)
        view = template_view(row)
        try:
            from open_webui.execution.node_program import upgrade_graph
            from open_webui.execution.graph_spec import validate_graph
            view['graph'] = validate_graph(upgrade_graph(view['graph']))
        except ValueError as error:
            reason = str(error)
        result.append(
            {**view, 'bindings': binding, 'available': not reason, 'unavailable_reason': reason,
             'archived': bool(row.get('archived_at'))}
        )
    return result


@router.get('/projects/{identity}/workflow-bindings')
async def project_workflow_bindings(identity: str, request: Request, user=Depends(get_admin_user)):
    return {
        'workflows': await list_workflows(request, identity, user),
        'participants': (await available_participants(request, user, identity))[2],
        'modules': public_modules(await available_modules(request, user, identity)),
    }


@router.put('/projects/{identity}/workflow-bindings')
async def save_project_workflow_bindings(identity: str, request: Request, value: dict, user=Depends(get_admin_user)):
    from open_webui.execution.project_data import require_project, project_lock, update_overlay

    if set(value) != {'workflow_id', 'bindings'}:
        raise HTTPException(422, 'Choose a workflow and bind its roles')
    definitions = await ensure_presets()
    definition = next((row for row in definitions if row['id'] == value.get('workflow_id')), None)
    if not definition:
        raise HTTPException(404, 'Workflow template not found')
    async with project_lock(identity):
        folder = await require_project(identity, user, write=True)
        overlays = {**(folder.data.get('execution_workflow_bindings') or {}), definition['id']: value['bindings']}
        available = await available_modules(request, user, identity, definitions=definitions, overlays=overlays)
        try:
            compile_template(definition, value.get('bindings'), available)
            if 'workflow:' + definition['id'] not in {p['id'] for p in available}:
                raise ValueError('Workflow bindings contain unavailable or recursive child modules')
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        await update_overlay(identity, user, 'execution_workflow_bindings', overlays)
    return {'bindings': value['bindings']}


@router.post('/projects/{identity}/workflow-bindings/match')
async def match_project_workflows(identity: str, request: Request, value: dict, user=Depends(get_admin_user)):
    from open_webui.execution.project_data import require_project, project_lock, update_overlay

    if set(value) - {'workflow_id', 'replace'} or type(value.get('replace', False)) is not bool:
        raise HTTPException(422, 'Choose a workflow or match all templates')
    definitions = await ensure_presets()
    if value.get('workflow_id'):
        definitions = [r for r in definitions if r['id'] == value['workflow_id']]
        if not definitions:
            raise HTTPException(404, 'Workflow template not found')
    available = (await available_participants(request, user, identity))[2]
    async with project_lock(identity):
        folder = await require_project(identity, user, write=True)
        overlays = dict(folder.data.get('execution_workflow_bindings') or {})
        try:
            for definition in definitions:
                overlays[definition['id']] = match_participants(
                    definition,
                    available,
                    effective_bindings(definition, overlays),
                    folder.data['execution_project'].get('default_model_id'),
                    value.get('replace', False),
                )
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        await update_overlay(identity, user, 'execution_workflow_bindings', overlays)
    return await project_workflow_bindings(identity, request, user)


@router.post('/workflows')
async def save_workflow(request: Request, value: dict, user=Depends(get_admin_user)):
    try:
        result = normalize_graph_template(value)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    async with configuration_lock:
        rows = await ensure_presets()
        existing = next((r for r in rows if r['id'] == result['id']), None)
        if existing and value.get('version') != existing.get('version', 1):
            raise HTTPException(409, 'This workflow changed. Reload it before saving.')
        if not existing and value.get('id'):
            raise HTTPException(409, 'This template is no longer active. Reload before saving.')
        if not existing and len(rows) >= 40:
            raise HTTPException(422, 'Save up to 40 workflow templates')
        if any(r['id'] != result['id'] and r['name'].casefold() == result['name'].casefold() for r in rows):
            raise HTTPException(422, 'Choose a unique template name')
        key = existing.get('preset_key') if existing else value.get('preset_key')
        result.update(catalog_revision=CATALOG_REVISION)
        if key:
            result['preset_key'] = key

        if existing:
            for key in ('preset_key', 'preset_revision', 'catalog_revision', 'legacy_bindings', 'builtin_key', 'binding_migration'):
                if key in existing:
                    result[key] = existing[key]
        result.update(version=(existing.get('version', 1) + 1) if existing else 1, updated_at=int(time.time()))
        await Config.upsert({'execution.workflows': [r for r in rows if r['id'] != result['id']] + [result]})
    return template_view(result)


@router.get('/workflows/starters')
async def workflow_starters(user=Depends(get_admin_user)):
    return [template_view(row) for row in starter_templates()]


@router.get('/graphs/actions')
async def graph_actions(user=Depends(get_admin_user)):
    from open_webui.execution.node_actions import action_catalog
    return action_catalog()


@router.get('/workflows/deleted')
async def deleted_workflows(user=Depends(get_admin_user)):
    active = {r['id'] for r in await ensure_presets()}
    archive = await Config.get('execution.workflow_archive') or []
    rows = {}
    for row in archive:
        if row.get('deleted_at') and row['id'] not in active:
            if row.get('version', 1) >= rows.get(row['id'], {}).get('version', 0):
                rows[row['id']] = row
    return [template_view(r) for r in rows.values()]


@router.delete('/workflows/{identity}')
async def delete_workflow(identity: str, version: int, user=Depends(get_admin_user)):
    async with configuration_lock:
        rows = await ensure_presets()
        archive = await Config.get('execution.workflow_archive') or []
        try:
            rows, archive = archive_template(rows, archive, identity, version)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        await Config.upsert({'execution.workflows': rows, 'execution.workflow_archive': archive})
    return {'id': identity, 'deleted': True, 'recoverable': True}


@router.post('/workflows/{identity}/restore')
async def restore_workflow(identity: str, user=Depends(get_admin_user)):
    async with configuration_lock:
        rows = await ensure_presets()
        archive = await Config.get('execution.workflow_archive') or []
        try:
            rows, archive = restore_template(rows, archive, identity)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        await Config.upsert({'execution.workflows': rows, 'execution.workflow_archive': archive})
    return template_view(next(r for r in rows if r['id'] == identity))


@router.get('/workflow-runs/{identity}')
async def get_workflow_run(identity: str, user=Depends(get_admin_user)):
    from open_webui.execution.workflows import read_run
    from open_webui.execution.trajectory import public_run

    return public_run(await asyncio.to_thread(read_run, identity, user.id))


@router.post('/workflow-runs/{identity}/control')
async def control_workflow_run(identity: str, value: dict, user=Depends(get_admin_user)):
    from open_webui.execution.workflows import active_engines
    from open_webui.execution.trajectory import public_run

    engine = active_engines.get(identity)
    if not engine or engine.record.get('user_id') != user.id or not engine.runner:
        raise HTTPException(409, 'This run is no longer active. Completed results remain available.')
    if set(value) - {'action', 'revision', 'changes', 'reason', 'node_id', 'value', 'policy', 'workflow_path'}:
        raise HTTPException(422, 'Invalid execution control fields')
    if 'workflow_path' in value and (not isinstance(value['workflow_path'], str) or len(value['workflow_path']) > 256):
        raise HTTPException(422, 'Choose an existing child Workflow path')
    try:
        await engine.runner.control(value)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    return public_run(engine.record)


@router.post('/graphs/validate')
async def validate_workflow_graph(value: dict, user=Depends(get_admin_user)):
    from open_webui.execution.graph_spec import validate_graph

    try:
        return validate_graph(value)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.get('/graphs/schema')
async def workflow_graph_schema(version: str = '2.0', user=Depends(get_admin_user)):
    from open_webui.execution.graph_schema import SCHEMA, SCHEMA_V2
    if version not in {'1.0', '2.0'}:
        raise HTTPException(422, 'Choose graph schema 1.0 or 2.0')
    return SCHEMA_V2 if version == '2.0' else SCHEMA
