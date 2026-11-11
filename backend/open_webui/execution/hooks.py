"""Project overlay hooks around the pinned CC Switch source catalog."""

import asyncio
from open_webui.integrations.cc_switch import CcSwitchAdapter
from open_webui.execution.profiles import discover_profiles


async def prepare_turn(project, framework, messages, profile_id=None):
    data = {}
    if project:
        from open_webui.models.folders import Folders
        folder = await Folders.get_folder_by_id(project['id'])
        data = folder.data or {} if folder else {}
    bindings = data.get('execution_bindings') or {}
    profiles = await asyncio.to_thread(
        discover_profiles, project.get('directory') if project else None, None, data.get('execution_profiles', [])
    )
    chosen_id = profile_id or bindings.get('profile_id')
    profile = next((p for p in profiles if p['id'] == chosen_id), None)
    if profile_id and (not profile or not profile.get('available') or profile['framework_id'] != framework):
        raise ValueError('所选 Profile 不适用于当前框架')
    if profile and profile['framework_id'] != framework:
        profile = None
    native_profile = profile if profile and profile.get('execution_mode') in {'native-agent', 'native-profile', 'native-preset', 'config-overlay'} else None
    instructions = [profile.get('prompt', '')] if profile and not native_profile else []
    adapter = CcSwitchAdapter()
    resources = []
    for identity in [*bindings.get('prompt_ids', []), *bindings.get('skill_ids', [])]:
        text = await asyncio.to_thread(adapter.resource_text, identity, framework)
        if text:
            resources.append(identity)
            instructions.append(text)
    combined = '\n\n'.join(instructions).strip()
    if len(combined.encode()) > 192000:
        raise ValueError('本轮项目资源超过 192 KiB，请减少绑定的 Skills / 预设')
    result = (
        [{'role': 'system', 'content': 'Project roles and resource instructions:\n' + combined}, *messages]
        if combined
        else messages
    )
    return result, {
        'profile_id': profile['id'] if profile else None,
        'profile_name': profile['name'] if profile else None,
        'resource_count': len(resources),
        'resource_ids': resources,
        'mcp_bindings': bindings.get('mcp_ids', []),
        'mcp_execution': False,
        '_native_profile': native_profile,
    }


def validate_bindings(value, resources, profiles):
    if not isinstance(value, dict) or set(value) - {'skill_ids', 'prompt_ids', 'mcp_ids', 'profile_id'}:
        raise ValueError('资源绑定格式不正确')
    result = {}
    for key, group in [('skill_ids', 'skills'), ('prompt_ids', 'prompts'), ('mcp_ids', 'mcp')]:
        ids = value.get(key, [])
        allowed = {r['id'] for r in resources[group] if r.get('available')}
        if not isinstance(ids, list) or len(ids) > 20 or any(not isinstance(v, str) or v not in allowed for v in ids):
            raise ValueError('资源不存在、不可用或超过绑定数量上限')
        result[key] = list(dict.fromkeys(ids))
    identity = value.get('profile_id') or None
    if identity and not any(p['id'] == identity and p.get('available') for p in profiles):
        raise ValueError('Profile 不可执行')
    result['profile_id'] = identity
    return result
