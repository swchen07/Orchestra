import copy
import json
import os
from pathlib import Path
from urllib.parse import quote, urlparse
import yaml
from open_webui.execution.runtimes import runtime_path, refresh_runtimes
from open_webui.execution.providers import public_claude_providers, public_direct_providers
from open_webui.execution.native_config import (
    codex_selection,
    dsh_selection,
    refresh_codex_catalog,
    refresh_dsh_catalog,
)

FRAMEWORKS = (
    {'id': 'direct', 'name': 'Direct', 'description': 'Call a configured model API'},
    {'id': 'hermes', 'name': 'Hermes', 'description': 'Use the existing Hermes gateway and native tools'},
    {
        'id': 'claude-code',
        'name': 'Claude Code',
        'description': 'Run Claude Code with the selected provider and model',
    },
    {'id': 'codex', 'name': 'Codex', 'description': 'Run the installed Codex harness'},
    {'id': 'dsh', 'name': 'DSH', 'description': 'Run DeepSeek Harness through ACP'},
)


def read_json(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def discover_local(home=None):
    home = Path(home or Path.home())
    # Read only the public model fields. Never return env maps, auth files or keys.
    claude = {}
    for name in ('settings.json', 'settings.local.json'):
        settings = read_json(home / '.claude' / name)
        claude.update({k: v for k, v in settings.items() if k != 'env'})
        claude['env'] = {**claude.get('env', {}), **settings.get('env', {})}
    env = {**claude.get('env', {}), **{k: v for k, v in os.environ.items() if k.startswith('ANTHROPIC_')}}
    default = claude.get('model') or env.get('ANTHROPIC_MODEL')
    choices = list(
        dict.fromkeys(
            v
            for v in [default]
            + [
                env.get(k)
                for k in (
                    'ANTHROPIC_DEFAULT_OPUS_MODEL',
                    'ANTHROPIC_DEFAULT_SONNET_MODEL',
                    'ANTHROPIC_DEFAULT_HAIKU_MODEL',
                )
            ]
            if isinstance(v, str) and v.strip()
        )
    )
    try:
        cfg = yaml.safe_load((home / '.hermes/config.yaml').read_text()) or {}
        raw = cfg.get('model', {})
        hermes = {
            'model': raw.get('default') if isinstance(raw, dict) else raw,
            'provider': raw.get('provider') if isinstance(raw, dict) else None,
        }
    except (OSError, ValueError, yaml.YAMLError):
        hermes = {}
    return {
        'direct': {'providers': public_direct_providers(home)},
        'codex': codex_selection(home),
        'dsh': dsh_selection(home),
        'hermes': hermes,
        'claude-code': {
            'providers': public_claude_providers(home),
            'models': choices,
            'default': default,
            'installed': bool(runtime_path('claude-code')),
            'provider': urlparse(env.get('ANTHROPIC_BASE_URL', '')).hostname or 'Claude Code native authentication',
            'source': '~/.claude/settings.json / 当前进程环境',
        },
    }


def is_hermes_url(url, options=None):
    try:
        p = urlparse(url)
        return (
            p.scheme in {'http', 'https'} and p.hostname in {'localhost', '127.0.0.1', '::1'}
            and (p.port == 8642 or (options or {}).get('framework_id') == 'hermes')
            and p.path.rstrip('/') == '/v1' and not p.username and not p.password
            and not p.query and not p.fragment
        )
    except ValueError:
        return False


def defaults():
    return {'frameworks': {}, 'workspaces': []}


def validate_settings(value):
    if not isinstance(value, dict) or set(value) - {'frameworks', 'workspaces'}:
        raise ValueError('配置格式不正确')
    frameworks = value.get('frameworks', {})
    if not isinstance(frameworks, dict) or set(frameworks) - {f['id'] for f in FRAMEWORKS}:
        raise ValueError('不支持的框架')
    for options in frameworks.values():
        if not isinstance(options, dict) or set(options) - {'enabled', 'disabled_models', 'disabled_providers'}:
            raise ValueError('框架配置只允许启用状态和模型可见性')
        if 'enabled' in options and not isinstance(options['enabled'], bool):
            raise ValueError('启用状态必须为布尔值')
        if not isinstance(options.get('disabled_models', []), list) or any(
            not isinstance(x, str) for x in options.get('disabled_models', [])
        ):
            raise ValueError('模型列表格式不正确')
        if not isinstance(options.get('disabled_providers', []), list) or any(
            not isinstance(x, str) for x in options.get('disabled_providers', [])
        ):
            raise ValueError('Provider 列表格式不正确')
    workspaces = value.get('workspaces', [])
    if not isinstance(workspaces, list) or len(workspaces) > 30:
        raise ValueError('工作区最多 30 个')
    seen = set()
    for w in workspaces:
        if (
            not isinstance(w, dict)
            or set(w) - {'id', 'name', 'path'}
            or not all(isinstance(w.get(k), str) and w[k].strip() for k in ('id', 'name', 'path'))
        ):
            raise ValueError('工作区需要 ID、名称和本地目录')
        if w['id'] == 'default' or w['id'] in seen or len(w['id']) > 80 or len(w['name']) > 120:
            raise ValueError('工作区 ID 重复或无效')
        seen.add(w['id'])
        path = Path(w['path']).expanduser().resolve()
        if (
            not path.is_dir()
            or path == Path('/')
            or path == Path.home()
            or any(p in {'.ssh', '.aws', '.codex', '.claude'} for p in path.parts)
        ):
            raise ValueError('请选择存在的具体项目目录，不允许系统根目录或认证目录')
        w['path'] = str(path)
    return {'frameworks': copy.deepcopy(frameworks), 'workspaces': copy.deepcopy(workspaces)}


async def settings():
    from open_webui.models.config import Config

    return await Config.get('execution.settings') or defaults()


def descriptor(framework, model, **extra):
    name = next(f['name'] for f in FRAMEWORKS if f['id'] == framework)
    return {'framework_id': framework, 'framework_name': name, 'model_id': model, **extra}


def model_disabled(options, model_id, provider_id):
    disabled = options.get('disabled_models', [])
    return model_id in disabled or f'{provider_id}::{model_id}' in disabled


def build_models(base, config, local, urls, include_disabled=False, api_configs=None):
    models = []
    framework_options = config.get('frameworks', {})
    for item in copy.deepcopy(base):
        idx = item.get('urlIdx')
        ollama = item.get('owned_by') == 'ollama'
        if ollama:
            idx = next(iter(item.get('ollama', {}).get('urls', [])), None)
        connection = ((api_configs or {}).get(str(idx), (api_configs or {}).get(urls[idx], {})) or {}) if isinstance(idx, int) and 0 <= idx < len(urls) else {}
        framework = (
            'hermes'
            if not ollama and isinstance(idx, int) and 0 <= idx < len(urls) and is_hermes_url(urls[idx], connection)
            else 'direct'
        )
        model_id = item['id']
        if framework == 'hermes':
            native = local.get('hermes', {})
            model_id = connection.get('native_model') or native.get('model') or 'Gateway model (not reported)'
            item['name'] = model_id
            item['execution'] = descriptor(
                framework,
                model_id,
                provider=connection.get('native_provider') or native.get('provider'),
                source='Hermes gateway profile' if connection.get('native_model') else '~/.hermes/config.yaml',
                wire_model=connection.get('native_model') or item['id'],
                model_switching=False,
                workspace_mode='managed',
                connection_id=f'openai:{idx}',
                provider_id=f'hermes:openai:{idx}',
                provider_name=connection.get('name') or 'Local gateway · ' + str(native.get('provider') or 'Default connection'),
            )
        else:
            item['execution'] = descriptor(
                'direct',
                model_id,
                source='Configured API',
                workspace_mode='none',
                model_switching=True,
                provider_id=f'direct:{item.get("owned_by", "openai")}:{idx if idx is not None and not (ollama and idx == 0) else "default"}',
                provider_name=item.get('provider', {}).get('name')
                if isinstance(item.get('provider'), dict)
                else (
                    'Ollama'
                    if item.get('owned_by') == 'ollama'
                    else f'Model API {idx + 1 if isinstance(idx, int) else "Default"}'
                ),
            )
        options = framework_options.get(framework, {})
        if include_disabled or (
            options.get('enabled', True)
            and not model_disabled(options, model_id, item['execution']['provider_id'])
            and item['execution']['provider_id'] not in options.get('disabled_providers', [])
        ):
            if framework != 'direct':
                item.setdefault('info', {}).setdefault('meta', {})['capabilities'] = {
                    'builtin_tools': False,
                    'vision': False,
                    'web_search': False,
                    'code_interpreter': False,
                }
            models.append(item)
    native = local.get('claude-code', {})
    options = framework_options.get('claude-code', {})
    if native.get('installed') and (include_disabled or options.get('enabled', True)):
        providers = native.get('providers') or [
            {
                'id': 'claude-code:native',
                'name': 'Local Claude Code',
                'models': native.get('models', []),
                'default_model': native.get('default'),
                'current': True,
                'available': True,
            }
        ]
        for provider in providers:
            if not provider.get('available', True) or (
                not include_disabled and provider['id'] in options.get('disabled_providers', [])
            ):
                continue
            for model_id in provider.get('models', []):
                if not include_disabled and model_disabled(options, model_id, provider['id']):
                    continue
                models.append(
                    {
                        'id': 'execution:claude-code:'
                        + quote(provider['id'], safe='')
                        + ':'
                        + quote(model_id, safe=''),
                        'name': model_id,
                        'object': 'model',
                        'created': 0,
                        'owned_by': 'openai',
                        'connection_type': 'local',
                        'execution': descriptor(
                            'claude-code',
                            model_id,
                            provider=provider.get('endpoint_host'),
                            provider_id=provider['id'],
                            provider_name=provider['name'],
                            source=provider.get('source', native.get('source')),
                            workspace_mode='project',
                            model_switching=True,
                            default=model_id == provider.get('default_model'),
                            current_provider=provider.get('current', False),
                        ),
                        'info': {
                            'meta': {
                                'description': 'Claude Code · ' + provider['name'] + ' · ' + model_id,
                                'capabilities': {
                                    'builtin_tools': False,
                                    'vision': False,
                                    'web_search': False,
                                    'code_interpreter': False,
                                },
                            }
                        },
                    }
                )
    for framework in ('direct', 'codex', 'dsh'):
        native = local.get(framework, {})
        options = framework_options.get(framework, {})
        if (framework != 'direct' and not native.get('installed')) or (
            not include_disabled and not options.get('enabled', True)
        ):
            continue
        for provider in native.get('providers', []):
            if not provider.get('available') or (
                not include_disabled and provider['id'] in options.get('disabled_providers', [])
            ):
                continue
            for model_id in provider.get('models', []):
                if not include_disabled and model_disabled(options, model_id, provider['id']):
                    continue
                models.append(
                    {
                        'id': f'execution:{framework}:'
                        + quote(provider['id'], safe='')
                        + ':'
                        + quote(model_id, safe=''),
                        'name': model_id,
                        'object': 'model',
                        'created': 0,
                        'owned_by': 'openai',
                        'connection_type': 'local',
                        'execution': descriptor(
                            framework,
                            model_id,
                            provider_id=provider['id'],
                            provider_name=provider['name'],
                            native_provider=provider.get('native_provider'),
                            source=provider['source'],
                            workspace_mode='project',
                            model_switching=True,
                            default=model_id == provider.get('default_model'),
                            current_provider=provider.get('current', False),
                            api_adapter=provider.get('api_adapter'),
                            **provider.get('model_details', {}).get(model_id, {}),
                        ),
                        'info': {
                            'meta': {
                                'capabilities': {
                                    key: False for key in ('builtin_tools', 'vision', 'web_search', 'code_interpreter')
                                }
                            }
                        },
                    }
                )
    return models


async def enrich_models(base):
    from open_webui.models.config import Config

    await refresh_runtimes()
    import asyncio

    await asyncio.gather(refresh_codex_catalog(), refresh_dsh_catalog())
    return build_models(
        base, await settings(), discover_local(), await Config.get('openai.api_base_urls') or [], include_disabled=True,
        api_configs=await Config.get('openai.api_configs') or {},
    )


def filter_models(models, config):
    result = []
    for model in models:
        execution = model.get('execution', {})
        options = config.get('frameworks', {}).get(execution.get('framework_id', 'direct'), {})
        if (
            options.get('enabled', True)
            and execution.get('provider_id') not in options.get('disabled_providers', [])
            and not model_disabled(options, execution.get('model_id', model['id']), execution.get('provider_id'))
        ):
            result.append(model)
    return result


def resolve_workspace(config, workspace_id):
    if workspace_id in (None, '', 'default'):
        from open_webui.env import DATA_DIR

        path = Path(DATA_DIR) / 'execution-workspaces/default'
        path.mkdir(parents=True, exist_ok=True)
        return {'id': 'default', 'name': 'Default workspace', 'path': str(path), 'tools': False}
    row = next((w for w in config.get('workspaces', []) if w['id'] == workspace_id), None)
    if not row or not Path(row['path']).is_dir():
        raise ValueError('工作区不可用，请在配置中心重新选择')
    return {**row, 'tools': True}
