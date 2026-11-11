"""Read provider snapshots without changing CC Switch or native CLI files."""

import copy
import json
import sqlite3
import tomllib
from pathlib import Path
from urllib.parse import urlparse
from open_webui.integrations.cc_switch import open_database

MODEL_KEYS = {
    'default': 'ANTHROPIC_MODEL',
    'opus': 'ANTHROPIC_DEFAULT_OPUS_MODEL',
    'sonnet': 'ANTHROPIC_DEFAULT_SONNET_MODEL',
    'haiku': 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
}


def object_json(raw):
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
        value = json.loads(value) if isinstance(value, str) else value
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def merge_config(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        result[key] = (
            merge_config(result[key], value)
            if isinstance(value, dict) and isinstance(result.get(key), dict)
            else copy.deepcopy(value)
        )
    return result


def contains_config(config, common):
    return all(
        contains_config(config.get(k, {}), v) if isinstance(v, dict) else config.get(k) == v for k, v in common.items()
    )


def claude_profiles(home=None):
    home = Path(home or Path.home())
    directory = home / '.cc-switch'
    rows = []
    common = {}
    preferences = {}
    try:
        preferences = object_json((directory / 'settings.json').read_text())
    except OSError:
        pass
    try:
        with open_database(home) as db:
            rows = db.execute(
                "SELECT id,name,settings_config,is_current,meta FROM providers WHERE app_type='claude' ORDER BY sort_index,name"
            ).fetchall()
            item = db.execute("SELECT value FROM settings WHERE key='common_config_claude'").fetchone()
            common = object_json(item[0]) if item else {}
    except sqlite3.Error:
        pass
    profiles = []
    for identity, name, raw, current, meta in rows:
        config = object_json(raw)
        extra = object_json(meta)
        use_common = extra.get('commonConfigEnabled', contains_config(config, common))
        # CC Switch's shared fragment is applied first; provider-specific values win.
        effective = merge_config(common if use_common else {}, config)
        profiles.append(
            {
                'id': 'claude-code:ccswitch:' + identity,
                'name': name,
                'current': identity == preferences['currentProviderClaude']
                if preferences.get('currentProviderClaude')
                else bool(current),
                'config': effective,
                'source': 'CC Switch',
                'common_config': bool(common and use_common),
                'managed_auth': extra.get('providerType') in {'xai_oauth', 'github_copilot', 'codex_oauth'},
            }
        )
    if not profiles:
        config = {}
        for filename in ('settings.json', 'settings.local.json'):
            try:
                config = merge_config(config, object_json((home / '.claude' / filename).read_text()))
            except OSError:
                pass
        profiles.append(
            {
                'id': 'claude-code:native',
                'name': 'Local Claude Code',
                'current': True,
                'config': config,
                'source': 'Claude Code native configuration',
                'common_config': False,
                'managed_auth': False,
            }
        )
    return profiles


def public_claude_providers(home=None):
    result = []
    for profile in claude_profiles(home):
        config = profile['config']
        env = config.get('env') or {}
        mappings = {
            role: env[key] for role, key in MODEL_KEYS.items() if isinstance(env.get(key), str) and env[key].strip()
        }
        default = config.get('model') or mappings.get('default') or mappings.get('sonnet') or 'sonnet'
        models = list(dict.fromkeys([default, *mappings.values()] if mappings else [default, 'opus', 'haiku']))
        result.append(
            {
                **{k: v for k, v in profile.items() if k not in {'config', 'managed_auth'}},
                'framework_id': 'claude-code',
                'models': models,
                'default_model': default,
                'model_mappings': mappings,
                'endpoint_host': urlparse(env.get('ANTHROPIC_BASE_URL', '')).hostname or 'Native authentication',
                'available': not profile['managed_auth'],
                'unavailable_reason': 'This provider requires a CC Switch managed authentication proxy, which is not connected.'
                if profile['managed_auth']
                else '',
            }
        )
    return result


def claude_provider_settings(provider_id):
    profile = next((p for p in claude_profiles() if p['id'] == provider_id), None)
    if not profile or profile['managed_auth']:
        raise ValueError('Claude Code Provider 不可用，请重新读取配置')
    config = profile['config']
    # Execution permissions/custom commands belong to the adapter, never a provider snapshot.
    return {
        k: copy.deepcopy(v)
        for k, v in config.items()
        if k in {'env', 'model', 'alwaysThinkingEnabled', 'effortLevel', 'includeCoAuthoredBy'}
    }


def direct_profiles(home=None):
    """Private API snapshots. CLI OAuth remains with the native framework."""
    rows = []
    for profile in claude_profiles(home):
        env = profile['config'].get('env') or {}
        endpoint = env.get('ANTHROPIC_BASE_URL') or 'https://api.anthropic.com'
        key = env.get('ANTHROPIC_API_KEY') or env.get('ANTHROPIC_AUTH_TOKEN')
        mappings = {role: env[k] for role, k in MODEL_KEYS.items() if isinstance(env.get(k), str) and env[k].strip()}
        default = profile['config'].get('model') or mappings.get('default') or mappings.get('sonnet')
        models = list(dict.fromkeys([m for m in [default, *mappings.values()] if m]))
        if not models:
            continue
        rows.append(
            {
                'id': 'direct:' + profile['id'],
                'name': profile['name'],
                'endpoint': endpoint,
                'key': key,
                'protocol': 'anthropic',
                'models': models,
                'default_model': default,
                'current': profile['current'],
                'available': bool(key) and not profile['managed_auth'],
                'source': 'CC Switch · Messages API',
            }
        )
    try:
        with open_database(home) as db:
            codex = db.execute(
                "SELECT id,name,settings_config FROM providers WHERE app_type='codex' ORDER BY sort_index,name"
            ).fetchall()
        for identity, name, raw in codex:
            value = object_json(raw)
            cfg = tomllib.loads(value.get('config') or '')
            provider = (cfg.get('model_providers') or {}).get(cfg.get('model_provider') or 'openai', {})
            key = (value.get('auth') or {}).get('OPENAI_API_KEY')
            if not isinstance(key, str) or not key or not cfg.get('model'):
                continue
            protocol = provider.get('wire_api', 'responses')
            if protocol not in {'responses', 'chat'}:
                continue
            rows.append(
                {
                    'id': 'direct:codex:ccswitch:' + identity,
                    'name': name,
                    'endpoint': provider.get('base_url') or 'https://api.openai.com/v1',
                    'key': key,
                    'protocol': protocol,
                    'models': [cfg['model']],
                    'default_model': cfg['model'],
                    'current': False,
                    'available': True,
                    'source': 'CC Switch · Model API',
                }
            )
    except (sqlite3.Error, ValueError, TypeError):
        pass
    return rows


def public_direct_providers(home=None):
    return [
        {
            **{k: v for k, v in row.items() if k not in {'key', 'endpoint'}},
            'framework_id': 'direct',
            'api_adapter': row['protocol'],
            'model_mappings': {},
            'endpoint_host': urlparse(row['endpoint']).hostname,
            'unavailable_reason': ''
            if row['available']
            else 'This provider has no API key; use its native framework or add an API connection.',
        }
        for row in direct_profiles(home)
    ]


def direct_provider_settings(identity):
    row = next((p for p in direct_profiles() if p['id'] == identity and p['available']), None)
    if not row:
        raise ValueError('Direct API provider is unavailable; refresh its connection')
    return row


def provider_catalog(models, local, advertised=None):
    advertised = models if advertised is None else advertised
    result = {
        p['id']: p.copy()
        for framework in ('direct', 'claude-code', 'codex', 'dsh')
        for p in local.get(framework, {}).get('providers', [])
    }
    for model in advertised:
        execution = model.get('execution', {})
        identity = execution.get('provider_id')
        if not identity or identity in result:
            continue
        result[identity] = {
            'id': identity,
            'name': execution.get('provider_name', identity),
            'framework_id': execution.get('framework_id', 'direct'),
            'source': execution.get('source', 'Configured API'),
            'available': True,
            'models': [],
            'model_mappings': {},
        }
    for provider in result.values():
        provider['models'] = list(
            dict.fromkeys(
                provider.get('models', [])
                + [
                    m['execution']['model_id']
                    for m in advertised
                    if m.get('execution', {}).get('provider_id') == provider['id']
                ]
            )
        )
        provider['model_ids'] = [m['id'] for m in models if m.get('execution', {}).get('provider_id') == provider['id']]
    return list(result.values())
