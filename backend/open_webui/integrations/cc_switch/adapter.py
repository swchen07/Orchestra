"""CC Switch 4.0.21 data hooks, pinned schema 23. No upstream mutations."""

import hashlib
import json
import sqlite3
from datetime import datetime
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

PACKAGE = Path(__file__).parent
FRAMEWORKS = {'claude-code': 'claude', 'codex': 'codex', 'hermes': 'hermes', 'dsh': 'dsh'}


@lru_cache(maxsize=1)
def contract():
    value = json.loads((PACKAGE / 'integration.lock.json').read_text())
    for filename, expected in value['vendor_blobs'].items():
        data = (PACKAGE / 'vendor' / filename).read_bytes()
        actual = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if actual != expected:
            raise RuntimeError('CC Switch 固定版本源文件校验失败')
    for filename, expected in value.get('derived_sha256', {}).items():
        if hashlib.sha256((PACKAGE / filename).read_bytes()).hexdigest() != expected:
            raise RuntimeError('CC Switch 派生查询契约校验失败')
    return value


@contextmanager
def open_database(home=None):
    path = Path(home or Path.home()) / '.cc-switch/cc-switch.db'
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    try:
        db.row_factory = sqlite3.Row
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version != contract()['upstream']['schema_version']:
            raise sqlite3.DatabaseError(f'CC Switch schema {version} 与固定版本 23 不兼容')
        yield db
    finally:
        db.close()


def integration_status(home=None):
    value = contract()
    try:
        with open_database(home) as db:
            schema = db.execute('PRAGMA user_version').fetchone()[0]
        return {
            'package': value['package'],
            **value['upstream'],
            'compatible': True,
            'actual_schema': schema,
            'hooks': value['hooks'],
            'mode': value['mode'],
        }
    except (OSError, sqlite3.Error) as error:
        return {
            'package': value['package'],
            **value['upstream'],
            'compatible': False,
            'error': str(error),
            'hooks': value['hooks'],
            'mode': value['mode'],
        }


def text_content(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(
            str(p.get('text', ''))
            for p in value
            if isinstance(p, dict) and p.get('type') in {'text', 'input_text', 'output_text'}
        )
    return ''


def json_lines(data):
    for line in data.splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                yield value
        except (ValueError, UnicodeError):
            continue


def session_metadata(path, home):
    try:
        path = Path(path).resolve()
        stat = path.stat()
        return _session_metadata(str(path), str(home), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


@lru_cache(maxsize=5000)
def _session_metadata(path, home, modified, size):
    home = Path(home)
    roots = {'codex': home / '.codex/sessions', 'claude-code': home / '.claude/projects'}
    path = Path(path).resolve()
    framework = next((f for f, root in roots.items() if path.is_relative_to(root.resolve())), None)
    if not framework or path.suffix != '.jsonl':
        return None
    if framework == 'claude-code' and (
        path.parent.parent != roots['claude-code'].resolve() or path.name.startswith('agent-')
    ):
        return None
    try:
        with path.open('rb') as stream:
            head = stream.read(65536)
            stream.seek(max(0, path.stat().st_size - 65536))
            tail = stream.read(65536)
        session_id = None
        directory = None
        title = ''
        model = None
        for event in json_lines(head):
            payload = event.get('payload') or {}
            if not isinstance(payload, dict):
                payload = {}
            if framework == 'codex' and event.get('type') == 'session_meta':
                if isinstance(payload.get('source'), dict) and payload['source'].get('subagent'):
                    return None
                session_id, directory = payload.get('id'), payload.get('cwd')
            elif framework == 'claude-code':
                if event.get('isSidechain'):
                    return None
                session_id = session_id or event.get('sessionId')
                directory = directory or event.get('cwd')
            message = event.get('message') if framework == 'claude-code' else payload
            if isinstance(message, dict) and message.get('role') == 'user' and not title:
                candidate = text_content(message.get('content', ''))
                if '## My request:' in candidate:
                    candidate = candidate.rsplit('## My request:', 1)[-1].strip()
                if not candidate.startswith(('<environment_context>', '# AGENTS.md', '<turn_aborted>', '<external_codex_apps_', '<in-app-browser-context>')):
                    title = candidate[:120]
            if isinstance(message, dict):
                model = model or message.get('model')
        for event in json_lines(tail):
            if event.get('type') == 'relocated' and event.get('relocatedCwd'):
                directory = event['relocatedCwd']
        if not session_id or not directory:
            return None
        return {
            'id': 'cc-session:' + hashlib.sha256(str(path).encode()).hexdigest()[:24],
            'session_id': session_id,
            'framework_id': framework,
            'directory': str(Path(directory).expanduser().resolve()),
            'title': title or str(session_id),
            'updated_at': path.stat().st_mtime,
            'model': model,
            '_path': path,
        }
    except (OSError, ValueError, TypeError):
        return None


class CcSwitchAdapter:
    def __init__(self, home=None):
        self.home = Path(home or Path.home())

    def resources(self):
        with open_database(self.home) as db:
            skills = []
            for row in db.execute('SELECT * FROM skills ORDER BY name'):
                path = self.skill_path(row['directory'])
                skills.append(
                    {
                        'id': 'cc-skill:' + row['id'],
                        'name': row['name'],
                        'description': row['description'] or '',
                        'frameworks': [
                            f
                            for f, app in FRAMEWORKS.items()
                            if 'enabled_' + app in row.keys() and row['enabled_' + app]
                        ],
                        'available': bool(path),
                        'kind': 'skill',
                    }
                )
            prompts = [
                {
                    'id': 'cc-prompt:' + r['id'],
                    'name': r['name'],
                    'description': r['description'] or '',
                    'frameworks': [next((f for f, app in FRAMEWORKS.items() if app == r['app_type']), r['app_type'])],
                    'available': True,
                    'enabled': bool(r['enabled']),
                    'kind': 'prompt',
                }
                for r in db.execute('SELECT id,name,description,app_type,enabled FROM prompts ORDER BY name')
            ]
            servers = []
            for row in db.execute('SELECT * FROM mcp_servers ORDER BY name'):
                try:
                    config = json.loads(row['server_config'])
                except (ValueError, TypeError):
                    config = {}
                if not isinstance(config, dict):
                    config = {}
                servers.append(
                    {
                        'id': 'cc-mcp:' + row['id'],
                        'name': row['name'],
                        'description': row['description'] or '',
                        'frameworks': [
                            f
                            for f, app in FRAMEWORKS.items()
                            if 'enabled_' + app in row.keys() and row['enabled_' + app]
                        ],
                        'transport': config.get('type', 'stdio' if config.get('command') else 'http'),
                        'available': True,
                        'executable': False,
                        'kind': 'mcp',
                        'note': '资源绑定钩子已接入；当前执行器未开放 MCP 启动',
                    }
                )
            presets = [
                {'id': 'cc-preset:' + r['id'], 'name': r['name'], 'kind': 'connection-preset', 'available': True}
                for r in db.execute('SELECT id,name FROM profiles ORDER BY sort_order,name')
            ]
        return {'skills': skills, 'prompts': prompts, 'mcp': servers, 'connection_presets': presets}

    def skill_path(self, directory):
        root = (self.home / '.cc-switch/skills').resolve()
        path = (root / directory / 'SKILL.md').resolve()
        return path if path.is_relative_to(root) and path.is_file() else None

    def resource_text(self, identity, framework=None):
        with open_database(self.home) as db:
            if identity.startswith('cc-skill:'):
                row = db.execute('SELECT * FROM skills WHERE id=?', (identity[len('cc-skill:') :],)).fetchone()
                if not row:
                    raise ValueError('Skill 不存在')
                app = FRAMEWORKS.get(framework)
                if app and ('enabled_' + app not in row.keys() or not row['enabled_' + app]):
                    return ''
                path = self.skill_path(row['directory'])
                if not path:
                    raise ValueError('Skill 文件缺失或路径超出固定存储目录')
                if path.stat().st_size > 65536:
                    raise ValueError('Skill 超过本轮读取上限')
                return path.read_text()
            if identity.startswith('cc-prompt:'):
                row = db.execute(
                    'SELECT app_type,content FROM prompts WHERE id=?', (identity[len('cc-prompt:') :],)
                ).fetchone()
                if not row:
                    raise ValueError('预设不存在')
                if framework and FRAMEWORKS.get(framework) != row['app_type']:
                    return ''
                return row['content'][:65536]
        raise ValueError('该资源仅提供配置元数据，不返回认证信息')

    def sessions(self, directory, limit=200):
        if not directory:
            return []
        directory = str(Path(directory).resolve())
        with open_database(self.home) as db:
            paths = [r[0] for r in db.execute('SELECT file_path FROM session_log_sync ORDER BY last_synced_at DESC')]
        result = []
        for path in paths:
            meta = session_metadata(path, self.home)
            if meta and meta['directory'] == directory:
                result.append(meta)
        ordered = sorted(result, key=lambda r: r['updated_at'], reverse=True)
        return ordered[:limit] if limit else ordered

    def session(self, identity, directory):
        row = next((r for r in self.sessions(directory) if r['id'] == identity), None)
        if not row:
            raise ValueError('会话不属于当前 Project 或已不可读取')
        return read_session_file(row)

    def usage(self, directory, excluded_session_ids=()):
        sessions = self.sessions(directory, limit=0)
        identities = list({r['session_id'] for r in sessions} - set(excluded_session_ids))
        if not identities:
            return {
                'records': [],
                'coverage': '仅可归属到该目录的原始会话记录；不把全局日汇总计入项目',
                'session_count': len(sessions),
            }
        with open_database(self.home) as db:
            sql_filter = (PACKAGE / 'effective-usage-filter.sql').read_text()
            rows = []
            for start in range(0, len(identities), 500):
                batch = identities[start : start + 500]
                rows.extend(
                    db.execute(
                        'SELECT l.* FROM proxy_request_logs l WHERE l.session_id IN ('
                        + ','.join('?' for _ in batch)
                        + ') AND '
                        + sql_filter,
                        batch,
                    ).fetchall()
                )
        records = []
        for row in rows:
            value = dict(row)
            fresh = int(value['input_tokens'] or 0)
            read, write = int(value['cache_read_tokens'] or 0), int(value['cache_creation_tokens'] or 0)
            if value['app_type'] in {'codex', 'gemini', 'grokbuild'}:
                if value.get('input_token_semantics') == 1 and fresh >= read + write:
                    fresh -= read + write
                elif value.get('input_token_semantics', 0) == 0 and fresh >= read:
                    fresh -= read
            try:
                cost = Decimal(str(value['total_cost_usd']))
                cost = str(cost) if cost.is_finite() and cost >= 0 else None
            except (InvalidOperation, ValueError):
                cost = None
            records.append(
                {
                    'id': value['request_id'],
                    'session_id': value['session_id'],
                    'model': value['model'],
                    'source': 'cc-switch:' + str(value['data_source']),
                    'input_tokens': fresh,
                    'output_tokens': int(value['output_tokens'] or 0),
                    'cache_read_tokens': read,
                    'cache_write_tokens': write,
                    'cost_usd': cost,
                    'created_at': value['created_at'],
                }
            )
        return {
            'records': records,
            'coverage': '按固定版本跨来源去重；仅有 session_id 可归属的原始记录，已归档日汇总不分摊到项目',
            'session_count': len(sessions),
        }


def read_session_file(row):
    path = row['_path']
    size = path.stat().st_size
    with path.open('rb') as stream:
        start = max(0, size - 4 * 1024 * 1024)
        stream.seek(start)
        raw = stream.read(4 * 1024 * 1024)
    if start:
        raw = raw.partition(b'\n')[2]
    messages = []
    text_truncated = False
    for event in json_lines(raw):
        message = event.get('message') if row['framework_id'] == 'claude-code' else event.get('payload')
        if not isinstance(message, dict) or message.get('role') not in {'user', 'assistant'}:
            continue
        if message.get('role') == 'assistant' and message.get('channel') not in {None, 'final', 'commentary'}:
            continue
        text = text_content(message.get('content', ''))
        if text:
            text_truncated |= len(text) > 32000
            entry = {'role': message['role'], 'content': text[:32000]}
            if isinstance(message.get('model'), str):
                entry['model'] = message['model']
            if isinstance(event.get('timestamp'), str):
                try:
                    entry['timestamp'] = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00')).timestamp()
                except (ValueError, OverflowError):
                    pass
            messages.append(entry)
    return {
        'session': {k: v for k, v in row.items() if not k.startswith('_')},
        'messages': messages[-200:],
        'truncated': bool(start or len(messages) > 200 or text_truncated),
        'read_only': True,
    }
