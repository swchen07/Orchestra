"""Read native conversations without relying on CC Switch's sync index or Project cwd.

Hermes schema/chain rules follow the installed hermes_state.py. Do not import its
SessionDB: its constructor can migrate the source database. All connections here
are read-only and only public user/assistant content is selected.
"""

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from open_webui.execution import dsh_history

from open_webui.integrations.cc_switch.adapter import (
    open_database,
    session_metadata,
    read_session_file,
    text_content,
)


def public_session(row):
    return {key: value for key, value in row.items() if not key.startswith('_')}


class NativeHistory:
    def __init__(self, home=None):
        self.home = Path(home or Path.home()).resolve()

    @contextmanager
    def hermes_db(self):
        path = self.home / '.hermes/state.db'
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            db.row_factory = sqlite3.Row
            yield db
        finally:
            db.close()

    def hermes_sessions(self):
        with self.hermes_db() as db:
            # Never select model_config, system_prompt, identity or reasoning.
            records = [
                dict(row)
                for row in db.execute(
                    """SELECT s.id,s.source,s.model,s.title,s.parent_session_id,
                          s.started_at,s.ended_at,s.end_reason,
                          COALESCE((SELECT MAX(timestamp) FROM messages WHERE session_id=s.id),s.started_at) AS updated_at,
                          (SELECT SUBSTR(content,1,160) FROM messages WHERE session_id=s.id
                           AND role='user' AND content IS NOT NULL ORDER BY timestamp,id LIMIT 1) AS preview
                   FROM sessions s"""
                )
            ]
        by_id = {row['id']: row for row in records}
        children = {}
        for row in records:
            children.setdefault(row['parent_session_id'], []).append(row)
        result = []
        for root in records:
            parent = by_id.get(root['parent_session_id'])
            if root['parent_session_id'] and not (
                parent
                and parent['end_reason'] == 'branched'
                and root['started_at'] >= (parent['ended_at'] or float('inf'))
            ):
                continue
            chain = [root]
            while chain[-1]['end_reason'] == 'compression' and len(chain) < 50:
                current = chain[-1]
                candidates = [
                    r
                    for r in children.get(current['id'], [])
                    if r['started_at'] >= (current['ended_at'] or float('inf'))
                    and r['id'] not in {p['id'] for p in chain}
                ]
                if not candidates:
                    break
                chain.append(max(candidates, key=lambda r: r['started_at']))
            tip = chain[-1]
            result.append(
                {
                    'id': 'hermes-session:' + hashlib.sha256(root['id'].encode()).hexdigest()[:24],
                    'session_id': root['id'],
                    'framework_id': 'hermes',
                    'title': tip['title'] or root['title'] or root['preview'] or 'Hermes conversation',
                    'updated_at': max(r['updated_at'] for r in chain),
                    'model': tip['model'],
                    'directory': None,
                    'source': root['source'],
                    '_chain': [r['id'] for r in chain],
                }
            )
        return result

    def sessions(self, directory=None, include_automation=False):
        paths = set()
        warnings = []
        try:
            with open_database(self.home) as db:
                paths.update(row[0] for row in db.execute('SELECT file_path FROM session_log_sync'))
        except (OSError, sqlite3.Error, RuntimeError):
            warnings.append('CC Switch index unavailable; scanning native conversation directories directly.')
        paths.update(str(p) for p in (self.home / '.claude/projects').glob('*/*.jsonl'))
        paths.update(str(p) for p in (self.home / '.codex/sessions').glob('*/*/*/*.jsonl'))
        result = []
        for path in (self.home / '.dsh/sessions').glob('*/*/session.v4.jsonl.zstd'):
            row = dsh_history.metadata(path, self.home)
            if row:
                result.append(row)
        for path in paths:
            row = session_metadata(path, self.home)
            if row:
                result.append({**row, 'source': 'native-file'})
        try:
            hermes = self.hermes_sessions()
            if not include_automation:
                hermes = [r for r in hermes if r['source'] != 'cron']
            # Hermes does not record a trustworthy cwd. Classify by an explicit
            # import/binding rather than pretending every Project owns it.
            if not directory:
                result.extend(hermes)
        except (OSError, sqlite3.Error):
            warnings.append('Hermes history is unavailable.')
        if directory:
            directory = str(Path(directory).expanduser().resolve())
            result = [r for r in result if r['directory'] == directory]
        return sorted(result, key=lambda r: r['updated_at'], reverse=True), warnings

    def session(self, identity, directory=None):
        rows, _ = self.sessions(directory, include_automation=True)
        row = next((r for r in rows if r['id'] == identity), None)
        if not row:
            raise ValueError('原生会话不存在、不可读或不属于当前项目目录')
        if row['framework_id'] == 'dsh':
            try:
                return dsh_history.preview(row)
            except Exception:
                raise ValueError(
                    'DSH history is currently incomplete or unreadable; try again after the native turn finishes'
                ) from None
        if row['framework_id'] != 'hermes':
            return read_session_file(row)
        with self.hermes_db() as db:
            placeholders = ','.join('?' for _ in row['_chain'])
            records = db.execute(
                'SELECT role,SUBSTR(content,1,32001) AS content,timestamp FROM messages '
                f"WHERE session_id IN ({placeholders}) AND role IN ('user','assistant') "
                "AND content IS NOT NULL AND content<>'' ORDER BY timestamp DESC,id DESC LIMIT 201",
                row['_chain'],
            ).fetchall()
        messages = []
        truncated = len(records) > 200
        budget = 2 * 1024 * 1024
        for item in records[:200]:
            value = item['content']
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list) and any(isinstance(p, dict) and 'type' in p for p in parsed):
                    value = text_content(parsed)
            except (ValueError, TypeError):
                if value.lstrip().startswith('['):
                    # A cut structured content array cannot be safely treated
                    # as public text: it may include thinking/image blocks.
                    truncated = True
                    continue
            truncated |= len(value) > 32000
            value = value[:32000]
            budget -= len(value.encode())
            if budget < 0:
                truncated = True
                break
            if value:
                messages.append({'role': item['role'], 'content': value, 'timestamp': item['timestamp']})
        return {
            'session': public_session(row),
            'messages': list(reversed(messages)),
            'truncated': truncated,
            'read_only': True,
        }


def imported_chat_payload(preview, model_id):
    """Materialize visible text as a new Chat; no native resume or model call."""
    session = preview['session']
    history = {}
    parent = None
    for item in preview['messages']:
        identity = str(uuid.uuid4())
        message = {
            'id': identity,
            'role': item['role'],
            'content': item['content'],
            'parentId': parent,
            'childrenIds': [],
            'timestamp': int(item.get('timestamp') or session['updated_at']),
        }
        if item['role'] == 'assistant':
            framework = {'hermes': 'Hermes', 'claude-code': 'Claude Code', 'codex': 'Codex', 'dsh': 'DSH'}[
                session['framework_id']
            ]
            message.update(
                model=model_id,
                done=True,
                statusHistory=[
                    {
                        'action': 'execution_context',
                        'done': True,
                        'description': framework
                        + ' Native history · '
                        + (item.get('model') or 'Configured model: ' + (session.get('model') or 'Model not recorded')),
                        'execution': {
                            'framework_id': session['framework_id'],
                            'framework_name': framework,
                            'provider_name': 'Native history',
                            'requested_model': session.get('model') or 'Model not recorded',
                            'actual_model': item.get('model'),
                        },
                    }
                ],
            )
        if parent:
            history[parent]['childrenIds'] = [identity]
        history[identity] = message
        parent = identity
    return {
        'title': session['title'][:120],
        'models': [model_id],
        'history': {'messages': history, 'currentId': parent},
    }
