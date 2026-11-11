"""Adapt native harness turns to the existing Open WebUI chat pipeline."""

import asyncio
import json
import time
import uuid
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from open_webui.execution.catalog import is_hermes_url, resolve_workspace, settings, model_disabled
from open_webui.execution.runtimes import runtime_path
from open_webui.execution.claude import ClaudeAdapter, ExecutionError
from open_webui.execution.hermes import HermesAdapter
from open_webui.execution.codex import CodexAdapter
from open_webui.execution.dsh import DshAdapter
from open_webui.execution.security import require_origin
from open_webui.execution.providers import claude_provider_settings, direct_provider_settings
from open_webui.execution.direct import DirectAdapter
from open_webui.execution.hooks import prepare_turn

_slots = asyncio.Semaphore(2)


def authorize(request, user):
    if user.role != 'admin':
        raise HTTPException(403, '本机 Agent 连接目前仅向管理员开放')
    require_origin(request)


async def generate_chat(request, form_data, user, model):
    from open_webui.models.config import Config

    authorize(request, user)
    execution = model['execution']
    framework = execution['framework_id']
    config = await settings()
    options = config.get('frameworks', {}).get(framework, {})
    if (
        not options.get('enabled', True)
        or model_disabled(options, execution['model_id'], execution.get('provider_id'))
        or execution.get('provider_id') in options.get('disabled_providers', [])
    ):
        raise HTTPException(400, '当前框架或模型已停用，请重新选择')
    meta = form_data.get('metadata', {})
    from open_webui.execution.module_environment import resolve_environment
    try:
        environment = resolve_environment({'framework_id': framework,
            '_environment_base': {'directory': (meta.get('execution_project') or {}).get('directory'),
                'workspace': (meta.get('execution_project') or {}).get('workspace') or {}},
            '_environment_request': meta.get('execution_environment_request') or {}},
            meta.get('execution_module_namespace') or meta.get('execution_context_key') or 'direct-chat')
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    managed = None
    if environment.filesystem in {'workspace-write', 'read-only'} and ((meta.get('execution_project') or {}).get('workspace') or {}).get('enabled'):
        from open_webui.execution.workspaces import WorkspaceService
        project = meta.get('execution_project') or {}
        try:
            owner = {'user_id': user.id, 'project_id': project.get('id'), 'chat_id': meta.get('chat_id')}
            manager = getattr(request.state, 'execution_workspace_service', None)
            if manager is None:
                manager = await asyncio.to_thread(WorkspaceService, project, owner)
            elif manager.owner != owner or manager.project.get('directory') != project.get('directory') or manager.policy != project.get('workspace'):
                raise ExecutionError('The host workspace service does not match this Project chat')
            receipt = meta.get('execution_workspace_lease')
            if not isinstance(receipt, dict):
                raise ExecutionError('Coding requires a host-owned module workspace lease; choose a Workflow call node')
            lease = manager.load(receipt.get('id'), environment.namespace)
            if manager.public(lease) != receipt:
                raise ExecutionError('Stale or forged coding workspace receipt')
            managed = {'manager': manager, 'lease': lease, 'directory': str(manager.checkout(lease)),
                       'runtime': str(manager.runtime(lease)), 'policy': manager.policy}
        except (ValueError, ExecutionError) as error:
            raise HTTPException(422, str(error)) from None
    if framework == 'direct' and execution.get('api_adapter'):
        try:
            adapter = DirectAdapter(direct_provider_settings(execution['provider_id']), execution['model_id'])
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        workspace_name = 'Model API'
    elif framework == 'claude-code':
        try:
            if not runtime_path('claude-code'):
                raise ValueError('Claude Code runtime is unavailable; check Connections')
            project = meta.get('execution_project')
            if project:
                directory = managed['directory'] if managed else project.get('directory')
                workspace = (
                    {'path': directory, 'name': project['name'], 'tools': bool(directory)}
                    if directory
                    else resolve_workspace({}, 'default')
                )
            else:
                workspace = resolve_workspace(config, meta.get('execution_workspace_id', 'default'))
            provider_settings = (
                claude_provider_settings(execution['provider_id']) if execution.get('provider_id') else None
            )
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        adapter = (
            ClaudeAdapter(
                runtime_path('claude-code'),
                workspace['path'],
                execution['model_id'],
                workspace['tools'],
                provider_settings=provider_settings,
            )
            if provider_settings is not None
            else ClaudeAdapter(
                runtime_path('claude-code'), workspace['path'], execution['model_id'], workspace['tools']
            )
        )
        workspace_name = workspace['name']
    elif framework in {'codex', 'dsh'}:
        try:
            project = meta.get('execution_project')
            directory = managed['directory'] if managed else project.get('directory') if project else None
            workspace = {'path': directory, 'name': project['name']} if directory else resolve_workspace({}, 'default')
            adapter_type = CodexAdapter if framework == 'codex' else DshAdapter
            adapter = adapter_type(
                workspace['path'],
                execution['model_id'],
                execution.get('native_provider') or ('openai' if framework == 'codex' else 'deepseek-official'),
            )
            workspace_name = workspace['name']
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
    elif framework == 'hermes':
        urls = await Config.get('openai.api_base_urls') or []
        keys = await Config.get('openai.api_keys') or []
        idx = int(execution['connection_id'].split(':')[1])
        api_configs = await Config.get('openai.api_configs') or {}
        api_config = (api_configs.get(str(idx), api_configs.get(urls[idx], {})) or {}) if 0 <= idx < len(urls) else {}
        if not 0 <= idx < min(len(urls), len(keys)) or not is_hermes_url(urls[idx], api_config):
            raise HTTPException(503, 'Hermes 连接发生变化，请刷新模型列表')
        if not await Config.get('openai.enable') or not api_config.get('enable', True):
            raise HTTPException(503, 'Hermes 连接已停用，请在配置中心启用后重试')
        if managed:
            from open_webui.execution.hermes_workspace import HermesWorkspaceAdapter, configuration
            try:
                native_config = configuration(managed['policy'], execution)
            except ExecutionError as error:
                raise HTTPException(422, str(error)) from None
            adapter = HermesWorkspaceAdapter(managed['directory'], managed['runtime'], native_config)
        else:
            adapter = HermesAdapter(urls[idx], keys[idx], wire_model=execution.get('wire_model', 'hermes-agent'))
        if meta.get('execution_context_key'):
            try:
                adapter.context_key = str(uuid.UUID(meta['execution_context_key']))
            except (ValueError, TypeError, AttributeError):
                raise HTTPException(422, 'Invalid workflow context ID') from None
        workspace_name = 'Hermes owned native workspace' if managed else 'Hermes gateway environment'
    else:
        raise HTTPException(400, '未知框架')
    if framework in {'claude-code', 'codex', 'dsh'}:
        from dataclasses import replace
        environment = replace(environment, directory=workspace['path'])
    if managed:
        from dataclasses import replace
        environment = replace(environment, directory=managed['directory'], workspace=manager.public(lease))
        adapter.coding = environment.filesystem == 'workspace-write'
        adapter.runtime_directory = managed['runtime']
    messages = form_data.get('messages', [])
    try:
        messages, hook_context = await prepare_turn(
            meta.get('execution_project'), framework, messages, meta.get('execution_profile_id')
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    native_profile = hook_context.pop('_native_profile', None)
    if framework == 'claude-code' and environment.tools == 'none':
        if native_profile and native_profile.get('execution_mode') == 'native-agent':
            raise HTTPException(422, 'A native Agent preset cannot be combined with tools=none')
        adapter.tools = False
        adapter.disable_delegation = True
    if native_profile:
        adapter.profile = native_profile
    if framework in {'codex', 'dsh'}:
        adapter.native_session_id = meta.get('execution_native_session_id')
    if framework == 'hermes':
        adapter.usage_session_id = meta.get('execution_usage_session_id')
    effort = meta.get('execution_effort')
    if effort:
        if effort not in execution.get('effort_options', []):
            raise HTTPException(422, 'This model does not advertise the selected effort')
        adapter.effort = effort
    response_id = 'chatcmpl-' + uuid.uuid4().hex
    context = {
        'framework_id': framework,
        'framework_name': execution['framework_name'],
        'requested_model': execution['model_id'],
        'actual_model': None,
        'native_entry_calls': 0,
        'provider_id': execution.get('provider_id'),
        'provider_name': execution.get('provider_name'),
        'project': meta.get('execution_project', {}).get('name') if meta.get('execution_project') else None,
        'workspace': workspace_name,
        **hook_context,
        'environment': environment.public(),
    }

    def chunk(**values):
        return {
            'id': response_id,
            'object': 'chat.completion.chunk',
            'created': int(time.time()),
            'model': model['id'],
            **values,
        }

    def context_event():
        actual = context['actual_model'] or 'Not reported (configured: ' + context['requested_model'] + ')'
        return {
            'event': {
                'type': 'status',
                'data': {
                    'action': 'execution_context',
                    'done': True,
                    'description': context['framework_name']
                    + ' · '
                    + str(context.get('provider_name') or '')
                    + ' · Model: '
                    + actual
                    + ' · '
                    + (context.get('project') or workspace_name)
                    + (' · Profile：' + context['profile_name'] if context.get('profile_name') else ''),
                    'execution': dict(context),
                },
            }
        }

    async def events():
        observer = getattr(request.state, 'execution_observer', None)

        async def observe(kind, payload):
            for key in ('native_entry_calls', 'native_model_request_count'):
                if key in payload:
                    context[key] = payload[key]
            if observer:
                await observer(kind, payload)

        try:
            await observe('phase', {'title': 'Waiting for framework slot'})
            async with asyncio.timeout(125), _slots:
                await observe('phase', {'title': 'Calling ' + execution['framework_name']})
                if meta.get('chat_id') and not meta['chat_id'].startswith('channel:'):
                    from open_webui.models.chats import Chats

                    chat = await Chats.get_chat_by_id(meta['chat_id'])
                    first = next((m.get('content') for m in messages if m.get('role') == 'user'), '')
                    if (
                        chat
                        and chat.user_id == user.id
                        and chat.title in {'New Chat', '新对话'}
                        and isinstance(first, str)
                        and first.strip()
                    ):
                        title = first.strip().splitlines()[0][:80]
                        await Chats.update_chat_title_by_id(chat.id, title)
                        yield {'event': {'type': 'chat:title', 'data': title}}
                yield context_event()
                await observe('metadata', context)
                has_text = False
                output_size = 0
                async for kind, payload in adapter.stream(messages):
                    if framework == 'hermes' and not managed and kind == 'metadata':
                        payload = {**payload, 'native_entry_calls': 1, 'native_entry_source': 'gateway-accepted-request'}
                    await observe(kind, payload)
                    if kind == 'text':
                        text = payload.get('text', '')
                        output_size += len(text.encode())
                        if output_size > 1024 * 1024:
                            raise ExecutionError('本次回答超过输出上限')
                        has_text = has_text or bool(text.strip())
                        yield chunk(choices=[{'index': 0, 'delta': {'content': text}, 'finish_reason': None}])
                    elif kind == 'metadata':
                        context.update(payload)
                        yield context_event()
                    elif kind == 'usage':
                        yield chunk(choices=[], usage=payload)
                    elif kind == 'tool':
                        yield {
                            'event': {
                                'type': 'status',
                                'data': {
                                    'action': 'harness_tool',
                                    'done': payload['done'],
                                    'description': execution['framework_name'] + ' · ' + payload['name'],
                                },
                            }
                        }
                if not has_text:
                    raise ExecutionError('执行器未返回文本结果')
                yield chunk(choices=[{'index': 0, 'delta': {}, 'finish_reason': 'stop'}])
        except asyncio.CancelledError:
            raise
        except (ExecutionError, TimeoutError) as error:
            yield chunk(error={'message': str(error) or '执行超时，本次连接已关闭'})
        except Exception:
            yield chunk(error={'message': '框架执行异常，请检查本机连接配置'})

    if form_data.get('stream'):

        async def wire():
            async for event in events():
                yield 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
            yield 'data: [DONE]\n\n'

        return StreamingResponse(wire(), media_type='text/event-stream')
    text = ''
    usage = {}
    async for event in events():
        if event.get('error'):
            raise HTTPException(502, event['error']['message'])
        usage.update(event.get('usage', {}))
        for choice in event.get('choices', []):
            text += choice.get('delta', {}).get('content', '')
    return {
        'id': response_id,
        'object': 'chat.completion',
        'created': int(time.time()),
        'model': model['id'],
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}],
        'usage': usage,
        'execution': context,
    }
