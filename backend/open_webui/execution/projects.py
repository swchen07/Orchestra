"""Projects own conversations, provider bindings and optional working directories."""

from pathlib import Path
from fastapi import HTTPException


def validate_directory(value):
    if value in (None, ''):
        return None
    if not isinstance(value, str):
        raise ValueError('项目目录必须是路径')
    path = Path(value).expanduser().resolve()
    if (
        not path.is_dir()
        or path in {Path('/'), Path.home()}
        or any(part in {'.ssh', '.aws', '.codex', '.claude', '.cc-switch'} for part in path.parts)
    ):
        raise ValueError('请选择存在的具体项目目录，不允许根目录或认证目录')
    return str(path)


def project_config(folder):
    return (folder.data or {}).get('execution_project') if folder else None


def validate_project(form, models, user):
    if not isinstance(form, dict) or set(form) - {
        'name',
        'description',
        'directory',
        'provider_ids',
        'default_model_id',
        'system_prompt',
        'workspace',
    }:
        raise ValueError('项目配置格式不正确')
    raw_name = form.get('name', '')
    if not isinstance(raw_name, str):
        raise ValueError('项目名称必须为文本')
    name = raw_name.strip()
    if not name or len(name) > 120:
        raise ValueError('请输入 1–120 字的项目名称')
    identities = form.get('provider_ids', [])
    available = {m.get('execution', {}).get('provider_id') for m in models}
    if (
        not isinstance(identities, list)
        or not identities
        or any(not isinstance(p, str) or p not in available for p in identities)
    ):
        raise ValueError('请为项目添加至少一个可用的框架 / Provider')
    selected = next((m for m in models if m['id'] == form.get('default_model_id')), None)
    if not selected or selected.get('execution', {}).get('provider_id') not in identities:
        raise ValueError('默认回复模型必须属于项目已添加的 Provider')
    directory = validate_directory(form.get('directory'))
    if directory and user.role != 'admin':
        raise ValueError('本机目录仅管理员可配置')
    from open_webui.execution.workspaces import validate_policy
    workspace = validate_policy(form.get('workspace'), directory)
    if workspace and user.role != 'admin':
        raise ValueError('Coding workspace configuration requires an administrator')
    for key in ('description', 'system_prompt'):
        if not isinstance(form.get(key, ''), str) or len(form.get(key, '')) > 32000:
            raise ValueError('项目说明格式不正确或过长')
    return {
        'name': name,
        'data': {
            'description': form.get('description', '').strip(),
            'system_prompt': form.get('system_prompt', ''),
            'model_ids': [selected['id']],
            'execution_project': {
                'provider_ids': list(dict.fromkeys(identities)),
                'default_model_id': selected['id'],
                'directory': directory,
                **({'workspace': workspace} if workspace is not None else {}),
            },
        },
    }


async def resolve_project(user, model, folder_id=None, chat=None):
    from open_webui.models.folders import Folders
    from open_webui.utils.access_control.folders import has_folder_write_access

    if chat:
        if folder_id and folder_id != chat.folder_id:
            raise HTTPException(400, '聊天所属项目与请求不一致')
        folder_id = chat.folder_id
    if not folder_id:
        return None
    folder = await Folders.get_folder_by_id(folder_id)
    if not folder or not await has_folder_write_access(user.id, folder_id):
        raise HTTPException(403, '没有该项目的聊天权限')
    config = project_config(folder)
    if config is None:
        return None
    if model.get('execution', {}).get('provider_id') not in config.get('provider_ids', []):
        raise HTTPException(400, '请使用当前 Project 已添加的框架 / Provider')
    try:
        directory = validate_directory(config.get('directory'))
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    return {'id': folder.id, 'name': folder.name, 'directory': directory,
            **({'workspace': config['workspace']} if config.get('workspace') is not None else {})}
