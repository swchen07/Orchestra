"""Initial project data hooks and topology prototype, using existing login/ACLs."""

import asyncio
import copy
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.execution.security import require_origin
from open_webui.execution.project_data import require_project, project_lock, update_overlay, dashboard, set_stage
from open_webui.execution.profiles import discover_profiles, public_profiles, validate_profile
from open_webui.execution.hooks import validate_bindings
from open_webui.execution.topology import validate_topology, start_run, stop_run, visible_runs
from open_webui.integrations.cc_switch import CcSwitchAdapter, integration_status
from open_webui.execution.native_history import NativeHistory, public_session, imported_chat_payload

router = APIRouter(dependencies=[Depends(require_origin)])


@router.get('/projects/{identity}/native-accounting-check')
async def native_accounting_check_status(identity: str, user=Depends(get_admin_user)):
    from open_webui.execution.native_examples import active_checks
    folder = await require_project(identity, user)
    suite = (folder.data or {}).get('execution_native_check')
    if suite and suite['status'] == 'running' and identity not in active_checks:
        suite = {**suite, 'status': 'interrupted'}
    return suite


@router.post('/projects/{identity}/native-accounting-check')
async def start_native_accounting_check(identity: str, value: dict, request: Request, user=Depends(get_admin_user)):
    from open_webui.execution.native_examples import active_checks, run_check
    from open_webui.execution.adaptive_examples import select_flash
    from open_webui.execution.workflow_presets import ensure_presets
    from open_webui.routers.workflows import available_participants, save_participant
    from open_webui.models.chats import Chats, ChatForm
    if value not in ({'action': 'start'}, {'action': 'stop'}, {'action': 'continue'}):
        raise HTTPException(422, 'Choose Start or Stop')
    await require_project(identity, user, write=True)
    if value['action'] == 'stop':
        task = active_checks.get(identity)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return await native_accounting_check_status(identity, user)
    async with project_lock(identity):
        folder = await require_project(identity, user, write=True)
        if identity in active_checks:
            raise HTTPException(409, 'A native accounting check is already running')
        if value['action'] == 'continue':
            suite = copy.deepcopy(folder.data.get('execution_native_check'))
            if not suite or len(suite['chats'][0].get('runs', [])) != 1:
                raise HTTPException(409, 'No completed first turn is ready to continue')
            suite['status'] = suite['chats'][0]['status'] = 'running'
            suite.pop('error', None)
            project = {'id': folder.id, 'name': folder.name, 'directory': folder.data['execution_project'].get('directory')}
            await update_overlay(identity, user, 'execution_native_check', suite)
            active_checks[identity] = asyncio.create_task(run_check(request, user, project, suite))
            return suite
        from open_webui.execution.compare_workflow import is_comparison_graph
        workflow = next((r for r in await ensure_presets() if is_comparison_graph(r) and len(r['roles']) == 3), None)
        if not workflow:
            raise HTTPException(422, 'Create a three-slot predefined comparison graph first')
        if len(workflow['roles']) != 3:
            raise HTTPException(422, 'This check needs Compare with three module slots')
        models, profiles, available = await available_participants(request, user, identity, True)
        chosen = select_flash(models, available)
        standard = next((p for p in available if p['model_id'] == chosen['dsh']['model_id']
                         and p.get('profile_id') == 'dsh-preset:standard'), None)
        if not standard:
            if not any(p['id'] == 'dsh-preset:standard' and p.get('available') for p in profiles):
                raise HTTPException(422, 'The installed DSH Standard native preset is unavailable')
            saved = await save_participant(request, {'id': str(uuid.uuid5(uuid.NAMESPACE_URL,
                'native-check/' + chosen['dsh']['model_id'] + '/standard')),
                'name': 'DSH Standard · Flash', 'model_id': chosen['dsh']['model_id'],
                'profile_id': 'dsh-preset:standard'}, user)
            available = (await available_participants(request, user, identity, True))[2]
            standard = next(p for p in available if p['id'] == saved['id'])
        chosen['dsh'] = standard
        selected = folder.data.get('execution_participants')
        if isinstance(selected, list):
            await update_overlay(identity, user, 'execution_participants', list(dict.fromkeys(
                [*selected, *(p['id'] for p in chosen.values())])))
        bindings = {r['id']: chosen[f]['id'] for r, f in zip(workflow['roles'], ('dsh', 'claude-code', 'hermes'))}
        chat_id, name = str(uuid.uuid4()), 'Native teams · DSH / Claude Code / Hermes Flash'
        payload = {'title': name, 'models': [chosen['dsh']['model_id']], 'messages': [],
            'history': {'messages': {}, 'currentId': None},
            'params': {'execution_workflow_id': workflow['id'], 'execution_workflow_policy': 'automatic',
                'execution_workflow_overrides': {'workflow_id': workflow['id'],
                    'roles': {r: {'participant_id': p} for r, p in bindings.items()}}}}
        chat = await Chats.insert_new_chat(chat_id, user.id, ChatForm(chat=payload, folder_id=identity))
        if not chat:
            raise HTTPException(500, 'Could not save the native check chat')
        suite = {'id': str(uuid.uuid4()), 'workflow_id': workflow['id'], 'status': 'running',
            'chats': [{'chat_id': chat.id, 'name': name, 'model_id': chosen['dsh']['model_id'],
                       'bindings': bindings, 'status': 'running', 'runs': []}]}
        await update_overlay(identity, user, 'execution_native_check', suite)
        project = {'id': folder.id, 'name': folder.name, 'directory': folder.data['execution_project'].get('directory')}
        active_checks[identity] = asyncio.create_task(run_check(request, user, project, suite))
        return suite


@router.get('/projects/{identity}/adaptive-examples')
async def adaptive_example_status(identity: str, request: Request, user=Depends(get_admin_user)):
    from open_webui.execution.adaptive_examples import active_suites, select_flash
    from open_webui.routers.workflows import available_participants

    folder = await require_project(identity, user)
    models, _, available = await available_participants(request, user, identity)
    chosen = select_flash(models, available)
    suite = (folder.data or {}).get('execution_example_suite')
    if suite and suite['status'] == 'running' and identity not in active_suites:
        # Catalog discovery can outlast the final suite write. Re-read before
        # classifying a completed queue as interrupted after a stale snapshot.
        suite = (await require_project(identity, user)).data.get('execution_example_suite')
        if suite and suite['status'] == 'running':
            suite = {**suite, 'status': 'interrupted'}
    return {'participants': chosen, 'suite': suite}


@router.post('/projects/{identity}/adaptive-examples')
async def start_adaptive_examples(identity: str, request: Request, value: dict, user=Depends(get_admin_user)):
    import time
    from open_webui.execution.adaptive_examples import active_suites, select_flash, matrix, definition, run_suite
    from open_webui.routers.workflows import available_participants
    from open_webui.execution.workflow_presets import ensure_presets
    from open_webui.models.chats import Chats, ChatForm

    if value == {'action': 'stop'}:
        await require_project(identity, user, write=True)
        task = active_suites.get(identity)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return (await require_project(identity, user)).data.get('execution_example_suite')
    if value == {'action': 'retry'}:
        async with project_lock(identity):
            folder = await require_project(identity, user, write=True)
            suite = folder.data.get('execution_example_suite')
            if not suite or identity in active_suites or all(j['status'] == 'completed' for j in suite['chats']):
                raise HTTPException(409, 'No failed or interrupted example chats are ready to continue')
            suite['status'] = 'running'
            project = {'id': folder.id, 'name': folder.name, 'directory': folder.data['execution_project'].get('directory')}
            active_suites[identity] = asyncio.create_task(run_suite(request, user, project, suite))
            await update_overlay(identity, user, 'execution_example_suite', suite)
        return suite
    if value != {'action': 'start'}:
        raise HTTPException(422, 'Choose Start to create ten real-provider chats')
    async with project_lock(identity):
        folder = await require_project(identity, user, write=True)
        if identity in active_suites:
            raise HTTPException(409, 'An example suite is already running in this Project')
        if folder.data.get('execution_example_suite'):
            raise HTTPException(409, 'This Project already has an example suite; its existing chats are retained')
        models, _, available = await available_participants(request, user, identity)
        chosen = select_flash(models, available)
        from open_webui.execution.node_program import planner_config, review_nodes
        workflow = next((r for r in await ensure_presets() if planner_config(r.get('graph') or {}) and (review_nodes(r.get('graph') or {}) or (r.get('graph') or {}).get('reviewer'))), None)
        if not workflow:
            raise HTTPException(422, 'Create an AI-generated graph template with Controller and Reviewer first')
        await update_overlay(identity, user, 'execution_workflow_bindings', {
            **folder.data.get('execution_workflow_bindings', {}),
            workflow['id']: {r['id']: chosen['dsh']['id'] for r in workflow['roles']},
        })
        suite = {'id': str(uuid.uuid4()), 'workflow_id': workflow['id'], 'status': 'running',
                 'created_at': int(time.time()), 'chats': []}
        indexed = {p['id']: p for p in chosen.values()}
        for job in matrix(chosen):
            model_id = indexed[job['bindings']['controller']]['model_id']
            chat_id = str(uuid.uuid4())
            payload = {'title': job['name'], 'models': [model_id], 'messages': [],
                       'history': {'messages': {}, 'currentId': None},
                       'params': {'execution_workflow_id': workflow['id'], 'execution_workflow_policy': 'automatic',
                                  'execution_workflow_overrides': {'workflow_id': workflow['id'],
                                      'roles': {r: {'participant_id': p} for r, p in job['bindings'].items()},
                                      'executor_pool': job['executor_pool']}}}
            chat = await Chats.insert_new_chat(chat_id, user.id, ChatForm(chat=payload, folder_id=identity))
            if not chat:
                raise HTTPException(500, 'Could not create an example chat; previously created chats remain saved')
            suite['chats'].append({**job, 'chat_id': chat.id, 'model_id': model_id, 'status': 'queued', 'runs': []})
        await update_overlay(identity, user, 'execution_example_suite', suite)
        project = {'id': folder.id, 'name': folder.name,
                   'directory': folder.data['execution_project'].get('directory')}
        active_suites[identity] = asyncio.create_task(run_suite(request, user, project, suite))
    return suite


@router.get('/integration')
async def integration(user=Depends(get_admin_user)):
    return await asyncio.to_thread(integration_status)


@router.get('/resources/catalog')
async def resource_catalog(user=Depends(get_admin_user)):
    info = await asyncio.to_thread(integration_status)
    resources = {'skills': [], 'prompts': [], 'mcp': [], 'connection_presets': []}
    if info['compatible']:
        resources = await asyncio.to_thread(CcSwitchAdapter().resources)
    return {'integration': info, 'resources': resources}


@router.get('/resources/{resource:path}')
async def global_resource_preview(resource: str, user=Depends(get_admin_user)):
    try:
        return {'content': await asyncio.to_thread(CcSwitchAdapter().resource_text, resource)}
    except (ValueError, OSError) as error:
        raise HTTPException(404, str(error)) from None


@router.get('/native-sessions')
async def list_native_sessions(
    framework: str = '',
    q: str = '',
    offset: int = 0,
    limit: int = 40,
    include_automation: bool = False,
    user=Depends(get_admin_user),
):
    rows, warnings = await asyncio.to_thread(NativeHistory().sessions, None, include_automation)
    counts = {name: sum(r['framework_id'] == name for r in rows) for name in ['hermes', 'claude-code', 'codex', 'dsh']}
    if framework:
        rows = [r for r in rows if r['framework_id'] == framework]
    if q:
        rows = [r for r in rows if q.casefold() in (str(r['title']) + ' ' + str(r.get('directory') or '')).casefold()]
    offset, limit = max(0, offset), max(1, min(100, limit))
    return {
        'items': [public_session(r) for r in rows[offset : offset + limit]],
        'total': len(rows),
        'counts': counts,
        'warnings': warnings,
    }


@router.get('/native-sessions/{session_id}')
async def preview_native_session(session_id: str, user=Depends(get_admin_user)):
    try:
        return await asyncio.to_thread(NativeHistory().session, session_id)
    except (ValueError, OSError) as error:
        raise HTTPException(404, str(error)) from None


@router.get('/conversations')
async def list_conversations(framework: str = '', q: str = '', offset: int = 0, limit: int = 40,
                             include_automation: bool = False, user=Depends(get_admin_user)):
    from open_webui.models.chats import Chats

    rows, warnings = await asyncio.to_thread(NativeHistory().sessions, None, include_automation)
    chats = await Chats.get_chat_title_id_list_by_user_id(user.id, include_folders=True, include_pinned=True)
    rows.extend({'id': chat.id, 'webui_chat_id': chat.id, 'framework_id': 'webui', 'title': chat.title,
                 'updated_at': chat.updated_at, 'directory': None, 'source': 'WebUI'} for chat in chats)
    counts = {name: sum(r['framework_id'] == name for r in rows) for name in ('webui', 'hermes', 'claude-code', 'codex', 'dsh')}
    if framework:
        rows = [row for row in rows if row['framework_id'] == framework]
    if q:
        rows = [row for row in rows if q.casefold() in (row['title'] + ' ' + str(row.get('directory') or '')).casefold()]
    rows.sort(key=lambda row: row['updated_at'], reverse=True)
    offset, limit = max(0, offset), min(100, max(1, limit))
    return {'items': [public_session(row) for row in rows[offset:offset + limit]], 'total': len(rows), 'counts': counts, 'warnings': warnings}


@router.post('/native-sessions/{session_id}/import')
async def import_native_session(session_id: str, value: dict, request: Request, user=Depends(get_admin_user)):
    from open_webui.models.chats import Chats, ChatForm
    from open_webui.routers.execution import project_models

    try:
        preview = await asyncio.to_thread(NativeHistory().session, session_id)
    except (ValueError, OSError) as error:
        raise HTTPException(404, str(error)) from None
    if not preview['messages']:
        raise HTTPException(422, '该会话没有可导入的用户或助手文本')
    models = await project_models(request, user)
    model = next((m for m in models if m['id'] == value.get('model_id')), None)
    if not model or model.get('execution', {}).get('framework_id') != preview['session']['framework_id']:
        raise HTTPException(422, '请选择原框架下当前可用的回复模型')
    project_id = value.get('project_id') or None
    if project_id:
        if not isinstance(project_id, str):
            raise HTTPException(422, '项目格式不正确')
        project = await require_project(project_id, user, write=True)
        if model['execution']['provider_id'] not in project.data['execution_project']['provider_ids']:
            raise HTTPException(422, '该 Provider 尚未添加到项目')
    identity = str(uuid.uuid5(uuid.NAMESPACE_URL, f'webui-native:{user.id}:{session_id}:{project_id}'))
    existing = await Chats.get_chat_by_id(identity)
    if existing:
        if existing.user_id != user.id:
            raise HTTPException(403, '无权访问已导入的聊天')
        return {'id': existing.id, 'reused': True}
    result = await Chats.insert_new_chat(
        identity,
        user.id,
        ChatForm(
            chat=imported_chat_payload(preview, model['id']),
            folder_id=project_id,
        ),
        internal_meta={
            'execution_import': {
                'source_id': session_id,
                'framework_id': preview['session']['framework_id'],
                'truncated': preview['truncated'],
            }
        },
    )
    if not result:
        raise HTTPException(500, '无法导入聊天')
    return {'id': result.id, 'reused': False}


@router.get('/projects/{identity}/dashboard')
async def get_dashboard(identity: str, request: Request, user=Depends(get_verified_user)):
    folder = await require_project(identity, user)
    result = await dashboard(folder, user)
    result['runs'] = visible_runs(result['runs'])
    from open_webui.tasks import has_active_tasks

    for row in result['sessions']:
        row['active'] = await has_active_tasks(request.app.state.redis, row['id'])
    return result


@router.put('/projects/{identity}/bindings')
async def save_bindings(identity: str, value: dict, user=Depends(get_admin_user)):
    async with project_lock(identity):
        folder = await require_project(identity, user, write=True)
        resources = await asyncio.to_thread(CcSwitchAdapter().resources)
        profiles = await asyncio.to_thread(
            discover_profiles,
            folder.data['execution_project'].get('directory'),
            None,
            folder.data.get('execution_profiles', []),
        )
        try:
            bindings = validate_bindings(value, resources, profiles)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        await update_overlay(identity, user, 'execution_bindings', bindings)
    return bindings


@router.get('/projects/{identity}/resources/{resource:path}')
async def resource_preview(identity: str, resource: str, user=Depends(get_admin_user)):
    await require_project(identity, user)
    try:
        return {'content': await asyncio.to_thread(CcSwitchAdapter().resource_text, resource)}
    except ValueError as error:
        raise HTTPException(404, str(error)) from None


@router.get('/projects/{identity}/native-sessions/{session_id}')
async def native_session(identity: str, session_id: str, user=Depends(get_admin_user)):
    folder = await require_project(identity, user)
    if not folder.data['execution_project'].get('directory'):
        raise HTTPException(404, '项目没有工作目录；请从全局原生会话入口选择导入')
    try:
        return await asyncio.to_thread(
            NativeHistory().session, session_id, folder.data['execution_project'].get('directory')
        )
    except ValueError as error:
        raise HTTPException(404, str(error)) from None


@router.get('/projects/{identity}/profiles')
async def get_profiles(identity: str, user=Depends(get_admin_user)):
    folder = await require_project(identity, user)
    profiles = await asyncio.to_thread(
        discover_profiles,
        folder.data['execution_project'].get('directory'),
        None,
        folder.data.get('execution_profiles', []),
    )
    return public_profiles(profiles)


@router.post('/projects/{identity}/profiles')
async def create_profile(identity: str, value: dict, user=Depends(get_admin_user)):
    try:
        profile = validate_profile(value)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    async with project_lock(identity):
        folder = await require_project(identity, user, write=True)
        profiles = folder.data.get('execution_profiles', [])
        if len(profiles) >= 30:
            raise HTTPException(422, '项目最多保存 30 个 Profiles')
        profile['id'] = 'project-profile:' + str(uuid.uuid4())
        await update_overlay(identity, user, 'execution_profiles', profiles + [profile])
    return public_profiles([profile])[0]


@router.put('/projects/{identity}/sessions/{chat_id}/stage')
async def move_session(identity: str, chat_id: str, value: dict, user=Depends(get_verified_user)):
    folder = await require_project(identity, user, write=True)
    return await set_stage(folder, chat_id, value.get('stage'))


@router.post('/projects/{identity}/tasks')
async def create_board_task(identity: str, value: dict, user=Depends(get_verified_user)):
    from open_webui.models.chats import Chats, ChatForm

    folder = await require_project(identity, user, write=True)
    name = value.get('name', '')
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise HTTPException(422, '请输入任务名称')
    chat = await Chats.insert_new_chat(
        str(uuid.uuid4()),
        user.id,
        ChatForm(
            chat={
                'title': name.strip(),
                'models': [folder.data['execution_project']['default_model_id']],
                'history': {'messages': {}, 'currentId': None},
            },
            folder_id=identity,
        ),
        internal_meta={'execution_board': 'backlog'},
    )
    if not chat:
        raise HTTPException(500, '无法创建任务聊天')
    return {'id': chat.id, 'title': chat.title}


async def checked_topology(request, user, folder, value):
    from open_webui.routers.execution import project_models

    models = await project_models(request, user)
    profiles = await asyncio.to_thread(
        discover_profiles,
        folder.data['execution_project'].get('directory'),
        None,
        folder.data.get('execution_profiles', []),
    )
    try:
        return validate_topology(value, models, folder.data['execution_project']['provider_ids'], profiles)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.post('/projects/{identity}/topologies')
async def save_topology(identity: str, request: Request, value: dict, user=Depends(get_admin_user)):
    async with project_lock(identity):
        folder = await require_project(identity, user, write=True)
        topology = await checked_topology(request, user, folder, value)
        definitions = folder.data.get('execution_topologies', [])
        if topology['id'] not in {d['id'] for d in definitions} and len(definitions) >= 20:
            raise HTTPException(422, '项目最多保存 20 个拓扑预设')
        definitions = [d for d in definitions if d['id'] != topology['id']] + [topology]
        await update_overlay(identity, user, 'execution_topologies', definitions)
    return topology


@router.post('/projects/{identity}/topologies/{topology_id}/runs')
async def run_topology(identity: str, topology_id: str, request: Request, value: dict, user=Depends(get_admin_user)):
    folder = await require_project(identity, user, write=True)
    definition = next((d for d in folder.data.get('execution_topologies', []) if d['id'] == topology_id), None)
    if not definition:
        raise HTTPException(404, '拓扑预设不存在')
    topology = await checked_topology(
        request, user, folder, {k: v for k, v in definition.items() if k in {'id', 'name', 'nodes'}}
    )
    return await start_run(request, user, folder, topology, value.get('task'))


@router.post('/projects/{identity}/runs/{run_id}/stop')
async def stop_topology(identity: str, run_id: str, user=Depends(get_admin_user)):
    await require_project(identity, user, write=True)
    return await stop_run(identity, run_id)
