"""Public native selection metadata. Authentication stays with each installed harness."""

import asyncio
import json
import tempfile
import time
import tomllib
from pathlib import Path

import yaml

from open_webui.execution.runtimes import runtime_path

_codex_models = None
_codex_checked_at = 0
_codex_path = None
_codex_error = ''
_dsh_error = ''
_dsh_catalog = None
_dsh_checked_at = 0
_dsh_path = None
_dsh_lock = asyncio.Lock()
_codex_catalog_lock = asyncio.Lock()


async def refresh_codex_catalog(force=False):
    global _codex_models, _codex_checked_at, _codex_path, _codex_error
    async with _codex_catalog_lock:
        path = runtime_path('codex')
        if path != _codex_path:
            _codex_models, _codex_checked_at, _codex_path = None, 0, path
        if not path or (not force and time.monotonic() - _codex_checked_at < 120):
            return
        from open_webui.execution.native_rpc import NativeRpc, native_environment

        try:
            async with (
                asyncio.timeout(15),
                NativeRpc([path, 'app-server'], str(Path.home()), native_environment()) as rpc,
            ):
                await rpc.request('initialize', {'clientInfo': {'name': 'open-webui-catalog', 'version': '0.1'}})
                await rpc.notify('initialized')
                rows, cursor, seen = [], None, set()
                for _ in range(10):
                    result = await rpc.request('model/list', {'includeHidden': True, 'limit': 100, 'cursor': cursor})
                    rows.extend(m for m in result.get('data', []) if isinstance(m.get('model'), str))
                    cursor = result.get('nextCursor')
                    if not cursor:
                        _codex_models = rows
                        _codex_error = ''
                        break
                    if cursor in seen:
                        raise ValueError('Codex returned a repeated catalog cursor')
                    seen.add(cursor)
        except Exception:
            _codex_error = (
                'The runtime catalog could not be refreshed. Showing the last observation; try Scan & refresh again.'
            )
            # Keep the last native observation. A shared desktop model cache is not proof of CLI eligibility.
            if _codex_models is None:
                _codex_models = []
        finally:
            _codex_checked_at = time.monotonic()


def codex_config(home=None):
    root = Path(home or Path.home()) / '.codex'
    try:
        return tomllib.loads((root / 'config.toml').read_text())
    except (OSError, ValueError):
        return {}


def codex_selection(home=None):
    root = Path(home or Path.home()) / '.codex'
    config = codex_config(home)
    choices = []
    try:
        cache = json.loads((root / 'models_cache.json').read_text())
        choices.extend(
            row['slug']
            for row in cache.get('models', [])
            if isinstance(row.get('slug'), str) and row.get('visibility') == 'list'
        )
    except (OSError, ValueError, KeyError):
        pass
    details = {}
    configured = list(dict.fromkeys([config['model']] + choices)) if isinstance(config.get('model'), str) else choices
    if Path(home or Path.home()) == Path.home() and _codex_models is not None and _codex_path == runtime_path('codex'):
        details = {
            row['model']: {
                'hidden': bool(row.get('hidden')),
                'effort_options': [
                    v['reasoningEffort']
                    for v in row.get('supportedReasoningEfforts', [])
                    if isinstance(v.get('reasoningEffort'), str)
                ],
                'default_effort': row.get('defaultReasoningEffort'),
            }
            for row in _codex_models
        }
        choices = list(details)
    provider = config.get('model_provider') or 'openai'
    if not choices and _codex_models is None and isinstance(config.get('model'), str):
        choices = [config['model']]
    return {
        'installed': bool(runtime_path('codex')),
        'providers': [
            {
                'id': 'codex:native:' + provider,
                'name': config.get('model_providers', {}).get(provider, {}).get('name')
                or ('OpenAI' if provider == 'openai' else provider),
                'framework_id': 'codex',
                'models': list(dict.fromkeys(choices)),
                'default_model': config.get('model')
                if config.get('model') in choices
                else (choices[0] if choices else None),
                'native_provider': provider,
                'model_mappings': {},
                'model_details': details,
                'unlisted_models': [m for m in configured if m not in choices],
                'catalog_warning': _codex_error,
                'current': True,
                'available': True,
                'source': 'Codex native model/list' if _codex_models is not None else 'Codex native configuration',
            }
        ],
    }


class MetadataLoader(yaml.SafeLoader):
    pass


MetadataLoader.add_constructor('tag:yaml.org,2002:js', lambda loader, node: None)


def dsh_selection(home=None):
    root = Path(home or Path.home()) / '.dsh'
    selected = {}
    # Only selection metadata is read; no credential store or environment values are returned.
    for path in [root / 'profiles/desktop/cordis.yml', root / 'profiles/desktop/cordis.patch.yml']:
        try:
            rows = yaml.load(path.read_text(), Loader=MetadataLoader)
        except (OSError, ValueError, yaml.YAMLError):
            continue
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get('id') == 'agent-default-model' and isinstance(row.get('config'), dict):
                selected.update(row['config'])
    provider = selected.get('provider') or 'deepseek-official'
    model = selected.get('model') or 'deepseek-v4-flash'
    if Path(home or Path.home()) == Path.home() and _dsh_catalog and _dsh_path == runtime_path('dsh'):
        providers = [dict(p, catalog_warning=_dsh_error) for p in _dsh_catalog]
        if selected:
            for item in providers:
                item['current'] = item['native_provider'] == provider
                if item['current'] and model in item['models']:
                    item['default_model'] = model
        return {'installed': bool(runtime_path('dsh')), 'providers': providers}
    return {
        'installed': bool(runtime_path('dsh')),
        'providers': [
            {
                'id': 'dsh:native:' + provider,
                'name': 'DeepSeek Account' if provider == 'deepseek-account' else provider,
                'framework_id': 'dsh',
                'models': [model],
                'default_model': model,
                'native_provider': provider,
                'current': True,
                'model_mappings': {},
                'available': True,
                'source': 'DSH native selection',
                'catalog_warning': _dsh_error,
            }
        ],
    }


def parse_dsh_catalog(options):
    providers = {}
    for option in options:
        if option.get('id') != 'model':
            continue
        for group in option.get('options', []):
            for row in group.get('options', []):
                try:
                    provider, model = json.loads(row['value'])
                    if not isinstance(provider, str) or not isinstance(model, str):
                        continue
                except (ValueError, KeyError, TypeError):
                    continue
                entry = providers.setdefault(
                    provider,
                    {
                        'id': 'dsh:native:' + provider,
                        'name': group.get('name') or provider,
                        'framework_id': 'dsh',
                        'models': [],
                        'model_details': {},
                        'native_provider': provider,
                        'model_mappings': {},
                        'available': True,
                        'source': 'DSH native ACP model catalog',
                    },
                )
                entry['models'].append(model)
                entry['model_details'][model] = {'display_name': row.get('name') or model}
                if row['value'] == option.get('currentValue'):
                    entry.update(current=True, default_model=model)
                    effort = next((v for v in options if v.get('id') == 'reasoning_effort'), {})
                    choices = [v['value'] for v in effort.get('options', [])
                               if isinstance(v.get('value'), str) and v['value']]
                    if choices:
                        entry['model_details'][model].update(effort_options=choices,
                            default_effort=effort.get('currentValue') or None)
    return list(providers.values())


async def refresh_dsh_catalog(force=False):
    global _dsh_catalog, _dsh_checked_at, _dsh_path, _dsh_error
    async with _dsh_lock:
        path = runtime_path('dsh')
        if path != _dsh_path:
            _dsh_catalog, _dsh_checked_at, _dsh_path = None, 0, path
        if not path or (not force and time.monotonic() - _dsh_checked_at < 120):
            return
        from open_webui.execution.native_rpc import NativeRpc, native_environment

        try:
            with tempfile.TemporaryDirectory(prefix='owui-dsh-catalog-') as cwd:
                async with (
                    asyncio.timeout(20),
                    NativeRpc(
                        [path, '--profile', 'acp'],
                        cwd,
                        native_environment(),
                        log_prefixes=(b'[llm-deepseek-account]',),
                    ) as rpc,
                ):
                    await rpc.request(
                        'initialize',
                        {
                            'protocolVersion': 1,
                            'clientInfo': {'name': 'open-webui-catalog', 'version': '0.1.0'},
                            'clientCapabilities': {},
                        },
                    )
                    result = await rpc.request('session/new', {'cwd': cwd, 'mcpServers': []})
                    rows = parse_dsh_catalog(result.get('configOptions', []))
                    # Reasoning choices depend on the selected provider/model.
                    # Probe them in this disposable ACP agent, never copy one
                    # model's choices onto all other catalog entries.
                    initial = next((o.get('currentValue') for o in result.get('configOptions', []) if o.get('id') == 'model'), None)
                    deadline, probes = time.monotonic() + 10, 0
                    for provider in rows:
                        for model in provider['models']:
                            wire = json.dumps([provider['native_provider'], model], separators=(',', ':'))
                            if wire == initial or time.monotonic() >= deadline or probes >= 32:
                                continue
                            probes += 1
                            try:
                                selected = await asyncio.wait_for(rpc.request('session/set_config_option',
                                    {'sessionId': result['sessionId'], 'configId': 'model', 'value': wire}), 2)
                                observed = parse_dsh_catalog(selected.get('configOptions', []))
                                match = next((p for p in observed if p['id'] == provider['id']), {})
                                detail = match.get('model_details', {}).get(model, {})
                                for field in ('effort_options', 'default_effort'):
                                    if field in detail:
                                        provider['model_details'][model][field] = detail[field]
                            except Exception:
                                deadline = 0  # Keep the valid model list; unavailable capabilities stay unadvertised.
                    await rpc.request('session/close', {'sessionId': result['sessionId']})
                    if rows:
                        _dsh_catalog = rows
                        _dsh_error = ''
        except Exception:
            _dsh_error = (
                'The runtime catalog could not be refreshed. Showing the last observation; try Scan & refresh again.'
            )
        finally:
            _dsh_checked_at = time.monotonic()
