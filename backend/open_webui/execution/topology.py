"""Bounded project DAG prototypes. Every node owns an ordinary persisted Chat."""

import asyncio
import copy
import re
import time
import uuid
from fastapi import HTTPException
from starlette.requests import Request
from open_webui.execution.project_data import project_lock, require_project, update_overlay

active_runs = {}


def layers_for(nodes):
    remaining = {n['id']: set(n.get('depends_on', [])) for n in nodes}
    layers = []
    resolved = set()
    while remaining:
        ready = [identity for identity, deps in remaining.items() if deps <= resolved]
        if not ready:
            raise ValueError('拓扑存在依赖环，不能执行')
        layers.append(ready)
        resolved.update(ready)
        for identity in ready:
            del remaining[identity]
    return layers


def validate_topology(value, models, provider_ids, profiles):
    if not isinstance(value, dict) or set(value) - {'id', 'name', 'nodes'}:
        raise ValueError('拓扑格式不正确')
    topology_id = value.get('id')
    if topology_id is not None and (
        not isinstance(topology_id, str) or not re.fullmatch('[a-zA-Z0-9_-]{1,64}', topology_id)
    ):
        raise ValueError('拓扑 ID 无效')
    name = value.get('name')
    nodes = value.get('nodes')
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 100
        or not isinstance(nodes, list)
        or not 1 <= len(nodes) <= 8
    ):
        raise ValueError('拓扑需要名称及 1–8 个节点')
    result = []
    seen = set()
    for node in nodes:
        if not isinstance(node, dict) or set(node) - {'id', 'name', 'model_id', 'profile_id', 'prompt', 'depends_on'}:
            raise ValueError('节点格式不正确')
        identity = node.get('id', '')
        if (
            not isinstance(identity, str)
            or not re.fullmatch('[a-zA-Z][a-zA-Z0-9_-]{0,39}', identity)
            or identity in seen
        ):
            raise ValueError('节点 ID 重复或无效')
        seen.add(identity)
        model = next((m for m in models if m['id'] == node.get('model_id')), None)
        if not model or model.get('execution', {}).get('provider_id') not in provider_ids:
            raise ValueError('节点只能使用项目已添加的 Provider 和可用模型')
        framework = model['execution']['framework_id']
        if framework not in {'claude-code', 'hermes'}:
            raise ValueError('第一版拓扑执行支持 Claude Code 和 Hermes')
        profile_id = node.get('profile_id') or None
        if profile_id and not any(
            p['id'] == profile_id and p.get('available') and p['framework_id'] == framework for p in profiles
        ):
            raise ValueError('节点 Profile 与框架不兼容')
        if (
            not isinstance(node.get('name'), str)
            or not node['name'].strip()
            or len(node['name']) > 100
            or not isinstance(node.get('prompt', ''), str)
            or len(node.get('prompt', '')) > 16000
        ):
            raise ValueError('请输入节点名称及有效指令')
        dependencies = node.get('depends_on', [])
        if (
            not isinstance(dependencies, list)
            or any(not isinstance(d, str) for d in dependencies)
            or len(set(dependencies)) != len(dependencies)
        ):
            raise ValueError('依赖节点列表不正确')
        result.append(
            {
                'id': identity,
                'name': node['name'].strip(),
                'model_id': model['id'],
                'profile_id': profile_id,
                'prompt': node.get('prompt', ''),
                'depends_on': dependencies,
            }
        )
    if any(dep not in seen or dep == node['id'] for node in result for dep in node['depends_on']):
        raise ValueError('依赖节点不存在或引用了自身')
    layers = layers_for(result)
    return {
        'id': value.get('id') or str(uuid.uuid4()),
        'name': name.strip(),
        'nodes': result,
        'layers': layers,
        'updated_at': int(time.time()),
    }


async def persist_run(folder_id, user, run):
    async with project_lock(folder_id):
        folder = await require_project(folder_id, user, write=True)
        records = folder.data.get('execution_runs', [])
        records = [copy.deepcopy(run) if item['id'] == run['id'] else item for item in records]
        if not any(item['id'] == run['id'] for item in records):
            records.insert(0, copy.deepcopy(run))
        await update_overlay(folder_id, user, 'execution_runs', records[:20])


def visible_runs(records):
    result = copy.deepcopy(records)
    for record in result:
        if record['status'] in {'queued', 'running'} and record['id'] not in active_runs:
            record['status'] = 'interrupted'
            record['error'] = '服务已重启；本轮不会自动重放，请显式新建执行'
            for node in record['nodes'].values():
                if node['status'] in {'running', 'waiting'}:
                    node['status'] = 'interrupted'
    return result


async def start_run(request, user, folder, topology, task_text):
    if not isinstance(task_text, str) or not task_text.strip() or len(task_text) > 16000:
        raise HTTPException(422, '请输入不超过 16000 字的任务')
    async with project_lock(folder.id):
        if any(item['folder_id'] == folder.id for item in active_runs.values()):
            raise HTTPException(409, '该项目已有拓扑在执行，请先等待或停止')
        run = {
            'id': str(uuid.uuid4()),
            'topology_id': topology['id'],
            'name': topology['name'],
            'task': task_text,
            'status': 'queued',
            'created_at': int(time.time()),
            'nodes': {n['id']: {'name': n['name'], 'status': 'waiting', 'chat_id': None} for n in topology['nodes']},
        }
        # Reserve before the first await to serialize starts for this project.
        active_runs[run['id']] = {'folder_id': folder.id, 'task': None}

    async def execute():
        from open_webui.models.chats import Chats, ChatForm
        from open_webui.tasks import create_task
        from open_webui.execution.bridge import generate_chat

        pending = []
        outputs = {}
        node_slots = asyncio.Semaphore(2)

        async def node_turn(node):
            record = run['nodes'][node['id']]
            chat_id = str(uuid.uuid4())
            user_id = str(uuid.uuid4())
            assistant_id = str(uuid.uuid4())
            record.update(status='running', chat_id=chat_id)
            await persist_run(folder.id, user, run)
            upstream = '\n\n'.join(
                '前置节点 ' + identity + '：\n' + outputs[identity] for identity in node['depends_on']
            )
            prompt = task_text + '\n\n' + upstream + '\n\n本节点指令：' + node['prompt']
            if len(prompt.encode()) > 384000:
                raise ValueError('前置结果超过本次上下文上限')
            first = {
                'id': user_id,
                'role': 'user',
                'content': prompt,
                'parentId': None,
                'childrenIds': [],
                'timestamp': int(time.time()),
            }
            created = await Chats.insert_new_chat(
                chat_id,
                user.id,
                ChatForm(
                    chat={
                        'title': topology['name'] + ' · ' + node['name'],
                        'models': [node['model_id']],
                        'params': {'execution_profile_id': node.get('profile_id')},
                        'history': {'messages': {user_id: first}, 'currentId': user_id},
                    },
                    folder_id=folder.id,
                ),
                internal_meta={
                    'execution_topology': {'run_id': run['id'], 'node_id': node['id']},
                    'execution_board': 'running',
                },
            )
            if not created:
                raise ValueError('无法创建节点聊天')

            async def inference():
                model = request.app.state.MODELS.get(node['model_id'])
                if not model:
                    raise ValueError('节点模型已不可用')
                scoped = Request({**request.scope, 'state': {}})
                messages = [{'role': 'user', 'content': prompt}]
                if folder.data.get('system_prompt'):
                    messages.insert(0, {'role': 'system', 'content': folder.data['system_prompt']})
                result = await generate_chat(
                    scoped,
                    {
                        'stream': False,
                        'messages': messages,
                        'metadata': {
                            'chat_id': chat_id,
                            'execution_profile_id': node.get('profile_id'),
                            'execution_project': {
                                'id': folder.id,
                                'name': folder.name,
                                'directory': folder.data['execution_project'].get('directory'),
                            },
                        },
                    },
                    user,
                    model,
                )
                return result

            _, child = await create_task(request.app.state.redis, inference(), id=chat_id)
            pending.append(child)
            error = None
            try:
                result = await child
                text = result['choices'][0]['message']['content']
                usage = result.get('usage', {})
                context = result.get('execution', {})
                outputs[node['id']] = text
                record.update(status='done')
            except asyncio.CancelledError:
                text = '本节点执行已停止。'
                usage = {}
                context = {}
                error = 'cancelled'
                record.update(status='cancelled')
            except Exception:
                text = '节点执行失败，请检查 Provider、模型和本机连接。'
                usage = {}
                context = {}
                error = 'failed'
                record.update(status='failed', error=text)
            answer = {
                'id': assistant_id,
                'role': 'assistant',
                'content': text,
                'model': node['model_id'],
                'parentId': user_id,
                'childrenIds': [],
                'timestamp': int(time.time()),
                'done': True,
                'usage': usage,
                'statusHistory': [
                    {
                        'action': 'execution_context',
                        'done': True,
                        'description': str(context.get('framework_name', ''))
                        + ' · '
                        + str(context.get('provider_name', ''))
                        + (' · ' + context['profile_name'] if context.get('profile_name') else ''),
                        'execution': context,
                    }
                ],
            }
            if error:
                answer['error'] = {'message': text}
            first['childrenIds'] = [assistant_id]
            await Chats.update_chat_by_id(
                chat_id, {'history': {'messages': {user_id: first, assistant_id: answer}, 'currentId': assistant_id}}
            )
            from open_webui.execution.project_data import set_stage

            await set_stage(folder, chat_id, 'review' if not error else 'backlog')
            await persist_run(folder.id, user, run)
            if error == 'cancelled':
                raise asyncio.CancelledError

        try:
            run['status'] = 'running'
            await persist_run(folder.id, user, run)
            async with asyncio.timeout(600):
                for layer in topology['layers']:
                    ready = []
                    for identity in layer:
                        node = next(n for n in topology['nodes'] if n['id'] == identity)
                        if any(run['nodes'][dep]['status'] != 'done' for dep in node['depends_on']):
                            run['nodes'][identity]['status'] = 'blocked'
                        else:
                            ready.append(node)

                    async def bounded(node):
                        async with node_slots:
                            await node_turn(node)

                    async with asyncio.TaskGroup() as group:
                        for node in ready:
                            group.create_task(bounded(node))
            statuses = {n['status'] for n in run['nodes'].values()}
            run['status'] = 'cancelled' if 'cancelled' in statuses else 'failed' if 'failed' in statuses else 'done'
        except (asyncio.CancelledError, TimeoutError):
            for child in pending:
                if not child.done():
                    child.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            run['status'] = 'cancelled'
            for record in run['nodes'].values():
                if record['status'] in {'waiting', 'running'}:
                    record['status'] = 'cancelled'
        except Exception:
            for child in pending:
                if not child.done():
                    child.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            run['status'] = 'failed'
            run['error'] = '拓扑执行异常，请检查节点聊天和资源配置'
        finally:
            run['ended_at'] = int(time.time())
            try:
                await persist_run(folder.id, user, run)
            finally:
                active_runs.pop(run['id'], None)

    try:
        await persist_run(folder.id, user, run)
        task = asyncio.create_task(execute())
        active_runs[run['id']]['task'] = task
        return run
    except BaseException:
        active_runs.pop(run['id'], None)
        raise


async def stop_run(folder_id, run_id):
    active = active_runs.get(run_id)
    if not active or active['folder_id'] != folder_id or not active['task']:
        raise HTTPException(409, '该执行已结束或中断')
    active['task'].cancel()
    await asyncio.gather(active['task'], return_exceptions=True)
    return {'id': run_id, 'stopped': True}
