"""Project data views and overlays; CC Switch retains its own source database."""

import asyncio
from decimal import Decimal, InvalidOperation
from fastapi import HTTPException
from sqlalchemy import select
from open_webui.integrations.cc_switch import CcSwitchAdapter, integration_status
from open_webui.execution.profiles import discover_profiles, public_profiles
from open_webui.execution.native_history import NativeHistory
from open_webui.execution.trajectory import has_token_usage

_project_locks = {}


def project_lock(identity):
    return _project_locks.setdefault(identity, asyncio.Lock())


async def require_project(identity, user, write=False):
    from open_webui.models.folders import Folders
    from open_webui.utils.access_control.folders import has_folder_access

    folder = await Folders.get_folder_by_id(identity)
    if (
        not folder
        or not (folder.data or {}).get('execution_project')
        or not await has_folder_access(user.id, folder, 'write' if write else 'read', None)
    ):
        raise HTTPException(404, 'Project 不存在或无权访问')
    return folder


async def update_overlay(identity, user, key, value):
    from open_webui.models.folders import Folders, FolderUpdateForm

    folder = await require_project(identity, user, write=True)
    result = await Folders.update_folder_by_id_and_user_id(
        identity, folder.user_id, FolderUpdateForm(data={key: value})
    )
    if not result:
        raise HTTPException(500, '无法保存项目配置')
    return result


async def local_project_data(folder):
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.chats import Chat
    from open_webui.models.chat_messages import ChatMessage

    async with get_async_db_context() as db:
        chats = (
            await db.execute(
                select(Chat.id, Chat.title, Chat.updated_at, Chat.meta, Chat.user_id)
                .where(Chat.folder_id == folder.id, Chat.archived == False)
                .order_by(Chat.updated_at.desc())
                .limit(200)
            )
        ).all()
        messages = (
            (
                await db.execute(
                    select(ChatMessage)
                    .join(Chat, Chat.id == ChatMessage.chat_id)
                    .where(Chat.folder_id == folder.id, ChatMessage.role == 'assistant')
                    .order_by(ChatMessage.created_at.desc())
                    .limit(5000)
                )
            )
            .scalars()
            .all()
        )
    last_by_chat = {}
    usage = []
    native_sessions = set()
    owners = {chat.id: chat.user_id for chat in chats}
    workflow_states = {}
    for message in messages:
        last_by_chat.setdefault(message.chat_id, message)
        execution = {}
        workflow_usage = []
        run_id = None
        for item in message.status_history or []:
            if not isinstance(item, dict):
                continue
            execution = item.get('execution') or execution
            native = (item.get('execution') or {}).get('native_session_id')
            if native:
                native_sessions.add(native)
            workflow = item.get('workflow') or {}
            run_id = workflow.get('run_id') or run_id
            native_sessions.update(workflow.get('native_session_ids', []))
            workflow_usage = workflow.get('calls_usage') or workflow_usage
        if run_id and message.chat_id in owners:
            from open_webui.execution.workflows import read_run

            try:
                run = await asyncio.to_thread(read_run, run_id, owners[message.chat_id])
                if last_by_chat[message.chat_id] is message:
                    workflow_states[message.chat_id] = run['status']
                workflow_usage = [
                    {
                        'id': call['id'],
                        'model': call['name'],
                        'usage': call.get('usage') or {},
                        'created_at': call['created_at'],
                    }
                    for call in run['calls']
                ]
                native_sessions.update(
                    call['execution']['native_session_id']
                    for call in run['calls']
                    if call.get('execution', {}).get('native_session_id')
                )
            except HTTPException:
                pass  # A missing retained run does not hide the chat's recorded usage.
        if workflow_usage:
            for call in workflow_usage:
                value = call.get('usage') or {}
                usage.append(
                    {
                        'id': 'workflow:' + call['id'],
                        'chat_id': message.chat_id,
                        'model': call['model'],
                        'source': 'workflow',
                        'input_tokens': int(value.get('fresh_input_tokens', value.get('prompt_tokens', 0)) or 0),
                        'output_tokens': int(value.get('completion_tokens', 0) or 0),
                        'cache_read_tokens': int(value.get('cache_read_tokens', 0) or 0),
                        'cache_write_tokens': int(value.get('cache_write_tokens', 0) or 0),
                        'cost_usd': str(value['reported_cost_usd'])
                        if value.get('reported_cost_usd') is not None
                        else None,
                        'cost_kind': value.get('cost_source', 'unknown'),
                        'created_at': call['created_at'],
                        'usage_available': has_token_usage(value),
                    }
                )
            continue
        value = message.usage or {}
        if value or execution:
            cost = value.get('reported_cost_usd')
            usage.append(
                {
                    'id': 'webui:' + message.id,
                    'chat_id': message.chat_id,
                    'model': execution.get('actual_model') or execution.get('requested_model') or message.model_id,
                    'source': 'webui',
                    'input_tokens': int(
                        value.get('fresh_input_tokens', value.get('input_tokens', value.get('prompt_tokens', 0))) or 0
                    ),
                    'output_tokens': int(value.get('output_tokens', value.get('completion_tokens', 0)) or 0),
                    'cache_read_tokens': int(value.get('cache_read_tokens', 0) or 0),
                    'cache_write_tokens': int(value.get('cache_write_tokens', 0) or 0),
                    'cost_usd': str(cost) if cost is not None else None,
                    'cost_kind': value.get('cost_source', 'unknown'),
                    'created_at': message.created_at,
                    'usage_available': bool(value),
                }
            )
    sessions = []
    for chat in chats:
        last = last_by_chat.get(chat.id)
        state = (chat.meta or {}).get('execution_board') or ('review' if last and last.done else 'backlog')
        sessions.append(
            {
                'id': chat.id,
                'title': chat.title,
                'updated_at': chat.updated_at,
                'stage': state,
                'response_done': bool(last and last.done),
                'workflow_status': workflow_states.get(chat.id),
                'error': bool(last and last.error),
                'source': 'webui',
                'topology': (chat.meta or {}).get('execution_topology'),
            }
        )
    return sessions, usage, native_sessions


def usage_summary(records):
    totals = {key: 0 for key in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens')}
    cost = Decimal('0')
    unknown = 0
    unmetered = 0
    seen = set()
    for row in records:
        if row['id'] in seen:
            continue
        seen.add(row['id'])
        unmetered += row.get('usage_available') is False
        for key in totals:
            totals[key] += int(row.get(key) or 0)
        try:
            value = Decimal(str(row.get('cost_usd')))
            if not value.is_finite() or value < 0:
                raise InvalidOperation
            cost += value
        except (InvalidOperation, ValueError):
            unknown += 1
    return {
        **totals,
        'total_tokens': sum(totals.values()),
        'known_cost_usd': str(cost),
        'unknown_cost_records': unknown,
        'unmetered_records': unmetered,
        'request_count': len(seen),
    }


async def dashboard(folder, user, refresh=False):
    project = folder.data['execution_project']
    sessions, own_usage, native_ids = await local_project_data(folder)
    info = await asyncio.to_thread(integration_status)
    resources = {'skills': [], 'prompts': [], 'mcp': [], 'connection_presets': []}
    native = []
    cc_usage = {'records': [], 'coverage': 'CC Switch 未连接或版本不兼容'}
    if user.role == 'admin' and info['compatible']:
        adapter = CcSwitchAdapter()
        resources = await asyncio.to_thread(adapter.resources)
        native_rows, _ = await asyncio.to_thread(NativeHistory().sessions, project.get('directory'))
        if not project.get('directory'):
            native_rows = []
        native = [{k: v for k, v in row.items() if not k.startswith('_')} for row in native_rows]
        cc_usage = await asyncio.to_thread(adapter.usage, project.get('directory'), native_ids)
    profiles = (
        await asyncio.to_thread(
            discover_profiles, project.get('directory'), None, folder.data.get('execution_profiles', [])
        )
        if user.role == 'admin'
        else []
    )
    records = [*own_usage, *cc_usage['records']]
    return {
        'project': folder,
        'integration': info if user.role == 'admin' else {'compatible': False, 'error': '本机资源仅管理员可管理'},
        'resources': resources,
        'profiles': public_profiles(profiles),
        'bindings': folder.data.get('execution_bindings', {}),
        'sessions': sessions,
        'native_sessions': native,
        'usage': {
            'summary': usage_summary(records),
            'records': sorted(records, key=lambda r: r.get('created_at') or 0, reverse=True)[:200],
            'coverage': 'Project calls and native history matching the working directory. '
            'Up to the latest 5000 WebUI replies; native records are deduplicated by session. '
            'Missing usage and costs remain unknown. Framework costs are estimates.'
            + (' CC Switch is unavailable or incompatible.' if not info['compatible'] else ''),
        },
        'topologies': folder.data.get('execution_topologies', []),
        'runs': folder.data.get('execution_runs', []),
    }


async def set_stage(folder, chat_id, stage):
    if stage not in {'backlog', 'running', 'review', 'done'}:
        raise HTTPException(422, '看板状态不正确')
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.chats import Chat

    async with get_async_db_context() as db:
        chat = (await db.execute(select(Chat).where(Chat.id == chat_id, Chat.folder_id == folder.id))).scalars().first()
        if not chat:
            raise HTTPException(404, '会话不属于当前项目')
        chat.meta = {**(chat.meta or {}), 'execution_board': stage}
        await db.commit()
    return {'id': chat_id, 'stage': stage}
