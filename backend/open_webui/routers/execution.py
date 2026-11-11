from fastapi import APIRouter, Depends, HTTPException, Request
from open_webui.execution.catalog import FRAMEWORKS, discover_local, settings, validate_settings
from open_webui.models.config import Config
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.execution.security import require_origin
from open_webui.execution.providers import provider_catalog
from open_webui.execution.projects import project_config, validate_project
from open_webui.execution.runtimes import refresh_runtimes, discovered_frameworks, ADAPTERS, inspect_path

router = APIRouter(dependencies=[Depends(require_origin)])


@router.get('/profiles')
async def native_profiles(user=Depends(get_admin_user)):
    import asyncio
    from open_webui.execution.profiles import discover_profiles, public_profiles

    return public_profiles(await asyncio.to_thread(discover_profiles))


@router.get('/catalog')
async def catalog(request: Request, user=Depends(get_verified_user)):
    from open_webui.utils.models import get_all_models, get_filtered_models

    models = await get_filtered_models(await get_all_models(request, user=user), user)
    if user.role != 'admin':
        models = [m for m in models if m.get('execution', {}).get('framework_id', 'direct') == 'direct']
    local = discover_local() if user.role == 'admin' else {}
    providers = provider_catalog(models, local)
    if user.role == 'admin':
        from open_webui.execution.catalog import build_models

        complete = build_models(
            request.app.state.BASE_MODELS or [],
            await settings(),
            local,
            await Config.get('openai.api_base_urls') or [],
            include_disabled=True,
            api_configs=await Config.get('openai.api_configs') or {},
        )
        # Management retains disabled entries without exposing them as runnable chat models.
        providers = provider_catalog(models, local, complete)
        from open_webui.execution.api_connections import public_connections, connection_values

        apis = [row for kind in ('openai', 'ollama') for row in public_connections(kind, await connection_values(kind))]
        for provider in providers:
            api = next((a for a in apis if a['id'] == provider['id']), None)
            if api:
                provider.update(
                    name=api['name'],
                    endpoint_host=api['endpoint_host'],
                    source='Ollama API' if api['kind'] == 'ollama' else 'OpenAI-compatible API',
                )
    return {
        'frameworks': discovered_frameworks(FRAMEWORKS, models),
        'models': models,
        'providers': providers,
    }


@router.get('/settings')
async def get_settings(user=Depends(get_admin_user)):
    runtimes = await refresh_runtimes()
    return {
        'settings': {'frameworks': (await settings()).get('frameworks', {})},
        'native': discover_local(),
        'frameworks': discovered_frameworks(FRAMEWORKS),
        'runtimes': runtimes,
        'adapters': [{'id': key, 'name': value[0]} for key, value in ADAPTERS.items()],
    }


@router.put('/settings')
async def save_settings(request: Request, form: dict, user=Depends(get_admin_user)):
    try:
        if set(form) - {'frameworks'}:
            raise ValueError('连接配置只管理框架与 Provider；工作目录请在 Project 中设置')
        config = validate_settings({**form, 'workspaces': (await settings()).get('workspaces', [])})
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    await Config.upsert({'execution.settings': config})
    request.app.state.BASE_MODELS = []
    request.app.state.MODELS = {}
    return {
        'settings': {'frameworks': config['frameworks']},
        'native': discover_local(),
        'frameworks': discovered_frameworks(FRAMEWORKS),
    }


@router.post('/refresh')
async def refresh_connections(request: Request, user=Depends(get_admin_user)):
    import asyncio
    from open_webui.execution.native_config import refresh_codex_catalog, refresh_dsh_catalog
    from open_webui.routers.openai import clear_models_cache
    from open_webui.routers.ollama import clear_models_cache as clear_ollama

    await refresh_runtimes(force=True)
    await asyncio.gather(refresh_codex_catalog(True), refresh_dsh_catalog(True))
    await clear_models_cache(request)
    await clear_ollama(request)
    return await get_settings(user)


@router.put('/runtimes/{family}')
async def set_runtime(family: str, request: Request, value: dict, user=Depends(get_admin_user)):
    import asyncio
    from open_webui.execution.api_connections import lock

    if family not in ADAPTERS or set(value) != {'path'} or not isinstance(value['path'], str):
        raise HTTPException(422, 'Select a supported adapter and executable path')
    try:
        inspected = await asyncio.to_thread(inspect_path, family, value['path']) if value['path'] else None
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    async with lock:
        paths = dict(await Config.get('execution.runtime_paths') or {})
        if inspected:
            paths[family] = inspected['path']
        else:
            paths.pop(family, None)
        await Config.upsert({'execution.runtime_paths': paths})
    return await refresh_connections(request, user)


@router.get('/api-providers')
async def get_api_providers(user=Depends(get_admin_user)):
    from open_webui.execution.api_connections import public_connections, connection_values

    return [row for kind in ('openai', 'ollama') for row in public_connections(kind, await connection_values(kind))]


@router.put('/api-providers')
async def save_api_provider(request: Request, value: dict, user=Depends(get_admin_user)):
    from open_webui.execution.api_connections import lock, edit_connection, connection_values
    from open_webui.routers.openai import clear_models_cache
    from open_webui.routers.ollama import clear_models_cache as clear_ollama

    kind = value.get('kind')
    if kind not in {'openai', 'ollama'}:
        raise HTTPException(422, 'Choose OpenAI-compatible API or Ollama')
    async with lock:
        try:
            updates, _ = edit_connection(kind, await connection_values(kind), value)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        await Config.upsert(updates)
    await clear_models_cache(request)
    await clear_ollama(request)
    return await get_api_providers(user)


async def project_models(request, user):
    from open_webui.utils.models import get_all_models, get_filtered_models

    models = await get_filtered_models(await get_all_models(request, user=user), user)
    return (
        models
        if user.role == 'admin'
        else [m for m in models if m.get('execution', {}).get('framework_id', 'direct') == 'direct']
    )


@router.get('/projects')
async def list_projects(user=Depends(get_verified_user)):
    from open_webui.models.folders import Folders

    return [folder for folder in await Folders.get_folders_by_user_id(user.id) if project_config(folder) is not None]


@router.get('/projects/{id}')
async def get_project(id: str, user=Depends(get_verified_user)):
    from open_webui.models.folders import Folders
    from open_webui.utils.access_control.folders import has_folder_access

    folder = await Folders.get_folder_by_id(id)
    if not folder or not await has_folder_access(user.id, folder, 'read', None):
        raise HTTPException(404, '项目不存在或无权访问')
    return folder if project_config(folder) is not None else None


@router.post('/projects')
async def create_project(request: Request, form: dict, user=Depends(get_verified_user)):
    from open_webui.models.folders import Folders, FolderForm
    from open_webui.routers.folders import check_folders_permission

    await check_folders_permission(request, user)
    try:
        value = validate_project(form, await project_models(request, user), user)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    if await Folders.get_folder_by_parent_id_and_user_id_and_name(None, user.id, value['name']):
        raise HTTPException(409, '已存在同名项目或文件夹')
    folder = await Folders.insert_new_folder(user.id, FolderForm(**value))
    if not folder:
        raise HTTPException(500, '创建项目失败')
    return folder


@router.put('/projects/{id}')
async def update_project(id: str, request: Request, form: dict, user=Depends(get_verified_user)):
    from open_webui.models.folders import Folders, FolderUpdateForm

    folder = await Folders.get_folder_by_id_and_user_id(id, user.id)
    if project_config(folder) is None:
        raise HTTPException(404, '项目不存在或无权编辑')
    try:
        value = validate_project(form, await project_models(request, user), user)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    result = await Folders.update_folder_by_id_and_user_id(id, user.id, FolderUpdateForm(**value))
    if not result:
        raise HTTPException(409, '无法保存，请检查是否有同名项目')
    return result
