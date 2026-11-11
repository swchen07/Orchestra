"""Redacted, per-provider editing of the existing OpenAI/Ollama connection store."""

import asyncio
import copy
from urllib.parse import urlsplit

lock = asyncio.Lock()


async def connection_values(kind):
    from open_webui.models.config import Config

    keys = (
        ('openai.api_base_urls', 'openai.api_keys', 'openai.api_configs', 'openai.enable')
        if kind == 'openai'
        else (
            'ollama.base_urls',
            'ollama.api_configs',
            'ollama.enable',
        )
    )
    return await Config.get_many(*keys)


def public_connections(kind, values):
    from open_webui.execution.catalog import is_hermes_url

    result = []
    urls = values.get('openai.api_base_urls' if kind == 'openai' else 'ollama.base_urls') or []
    configs = values.get(kind + '.api_configs') or {}
    for index, url in enumerate(urls):
        cfg = configs.get(str(index), configs.get(url, {}))
        family = 'hermes' if kind == 'openai' and is_hermes_url(url, cfg) else 'direct'
        parts = urlsplit(url)
        safe_url = parts._replace(netloc=parts.netloc.rsplit('@', 1)[-1], query='', fragment='').geturl()
        key = (values.get('openai.api_keys') or [])[index : index + 1] if kind == 'openai' else [cfg.get('key')]
        result.append(
            {
                'id': f'{family}:{kind}:{"default" if kind == "ollama" and index == 0 else index}',
                'kind': kind,
                'index': index,
                'framework_id': family,
                'name': ((cfg.get('provider') or {}).get('name') if isinstance(cfg.get('provider'), dict) else None)
                or cfg.get('name')
                or (
                    'Hermes gateway'
                    if family == 'hermes'
                    else ('Ollama' if kind == 'ollama' else f'Model API {index + 1}')
                ),
                'url': safe_url,
                'endpoint_host': parts.hostname,
                'has_key': bool(key and key[0]),
                'enabled': cfg.get('enable', True),
                'service_enabled': values.get(kind + '.enable', True),
                'model_ids': cfg.get('model_ids', []),
                'prefix_id': cfg.get('prefix_id') or '',
                'native_model': cfg.get('native_model') or '',
                'native_provider': cfg.get('native_provider') or '',
            }
        )
    return result


def edit_connection(kind, values, form):
    if kind not in {'openai', 'ollama'} or set(form) - {
        'kind',
        'index',
        'name',
        'url',
        'key',
        'clear_key',
        'enabled',
        'model_ids',
        'prefix_id',
        'framework_id',
        'native_model',
        'native_provider',
    }:
        raise ValueError('Invalid API provider settings')
    if not isinstance(form.get('url'), str) or len(form['url']) > 2000:
        raise ValueError('Enter an HTTP(S) base URL')
    url = form['url'].strip().rstrip('/')
    try:
        parts = urlsplit(url)
        valid = parts.scheme in {'http', 'https'} and parts.hostname and parts.port != 0
    except ValueError:
        valid = False
    if not valid or parts.username or parts.password or parts.query or parts.fragment or any(c.isspace() for c in url):
        raise ValueError('Enter an HTTP(S) base URL without credentials or query parameters')
    if form.get('framework_id', 'direct') not in {'direct', 'hermes'}:
        raise ValueError('Choose Direct or Hermes')
    if form.get('framework_id') == 'hermes':
        from open_webui.execution.catalog import is_hermes_url

        if kind != 'openai' or not is_hermes_url(url, form):
            raise ValueError('Hermes gateways must use a loopback /v1 endpoint')
    for field in ('native_model', 'native_provider'):
        if field in form and (not isinstance(form[field], str) or len(form[field]) > 256):
            raise ValueError('Use a short native profile model and provider name')
    if not isinstance(form.get('name'), str) or not 1 <= len(form['name'].strip()) <= 100:
        raise ValueError('Enter a provider name (1–100 characters)')
    if type(form.get('enabled', True)) is not bool or type(form.get('clear_key', False)) is not bool:
        raise ValueError('Provider enabled state must be a boolean')
    models = form.get('model_ids', [])
    if (
        not isinstance(models, list)
        or len(models) > 500
        or any(not isinstance(m, str) or not 1 <= len(m) <= 256 for m in models)
    ):
        raise ValueError('Enter up to 500 model IDs')
    if not isinstance(form.get('key', ''), str) or len(form.get('key', '')) > 8192:
        raise ValueError('Invalid API key')
    prefix = form.get('prefix_id', '')
    if not isinstance(prefix, str) or len(prefix) > 100:
        raise ValueError('Keep the model prefix within 100 characters')
    url_key = 'openai.api_base_urls' if kind == 'openai' else 'ollama.base_urls'
    urls = list(values.get(url_key) or [])
    index = form.get('index')
    adding = index is None
    if adding:
        if len(urls) >= 40:
            raise ValueError('Save up to 40 API providers')
        index = len(urls)
        urls.append(url)
    elif type(index) is not int or not 0 <= index < len(urls):
        raise ValueError('API provider no longer exists')
    configs = copy.deepcopy(values.get(kind + '.api_configs') or {})
    cfg = dict(configs.get(str(index), configs.get(urls[index], {}))) if not adding else {}
    urls[index] = url
    cfg.update(
        enable=form.get('enabled', True),
        model_ids=list(dict.fromkeys(models)),
        prefix_id=prefix,
        name=form['name'].strip(),
        provider={
            **(cfg.get('provider') if isinstance(cfg.get('provider'), dict) else {}),
            'name': form['name'].strip(),
        },
    )
    cfg.update({field: form[field] for field in ('framework_id', 'native_model', 'native_provider') if field in form})
    configs[str(index)] = cfg
    update = {url_key: urls, kind + '.api_configs': configs}
    key = form.get('key', '')
    if kind == 'openai':
        keys = list(values.get('openai.api_keys') or [])
        keys += [''] * max(0, len(urls) - len(keys))
        if key or form.get('clear_key'):
            keys[index] = key
        update['openai.api_keys'] = keys
    elif key or form.get('clear_key'):
        cfg['key'] = key
    if form.get('enabled', True):
        update[kind + '.enable'] = True
    return update, index
