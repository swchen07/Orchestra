"""Bounded public transcript reader for DSH's compressed v4 native session log."""

from collections import deque
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from open_webui.integrations.cc_switch.adapter import text_content


def open_log(path):
    # Python 3.14 provides zstd; older runtimes can use the optional compatible package.
    try:
        from compression import zstd
    except ImportError:
        import zstandard as zstd
    return zstd.open(path, 'rt')


def metadata(path, home):
    path = Path(path).resolve()
    if not path.is_relative_to(Path(home).resolve() / '.dsh/sessions'):
        return None
    try:
        stat = path.stat()
        return _metadata(str(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=2048)
def _metadata(path, modified, size):
    if size > 32 * 1024 * 1024:
        return None
    try:
        with open_log(path) as stream:
            data = stream.read(65536)
        lines = data.splitlines()
        header = json.loads(lines[0])
        if (
            header.get('type') != 'session'
            or header.get('version') != 4
            or header.get('parentSession')
            or header.get('delegationDepth', 0) > 0
        ):
            return None
        title, model = None, None
        for line in lines[1:]:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            content = event.get('data') or {}
            if event.get('type') == 'session/title':
                title = content.get('title') or title
            elif event.get('type') == 'user/message' and not title:
                title = text_content(content.get('content'))[:120]
            elif event.get('type') == 'request/context':
                model = content.get('model') or model
        return {
            'id': 'dsh-session:' + hashlib.sha256(path.encode()).hexdigest()[:24],
            'framework_id': 'dsh',
            'session_id': header['id'],
            'directory': header.get('cwd'),
            'title': title or 'DSH conversation',
            'model': model,
            'updated_at': modified / 1e9,
            'source': 'native-file',
            '_path': path,
        }
    except Exception:
        # An active writer can leave the final zstd frame incomplete. One native file must not break all history.
        return None


def preview(row):
    messages = deque(maxlen=200)
    total = 0
    truncated = False
    model = row.get('model')
    with open_log(row['_path']) as stream:
        while line := stream.readline(1024 * 1024 + 1):
            total += len(line.encode())
            if total > 32 * 1024 * 1024 or len(line) > 1024 * 1024:
                truncated = True
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind, data = event.get('type'), event.get('data') or {}
            if kind == 'request/context':
                model = data.get('model') or model
            if kind not in {'user/message', 'assistant/message'}:
                continue
            message = data.get('message', {}) if kind == 'assistant/message' else data
            if kind == 'user/message' and (message.get('source') or {}).get('kind') != 'user':
                continue
            content = text_content(message.get('content'))
            if not content:
                continue
            truncated |= len(content) > 32000 or len(messages) == 200
            messages.append(
                {
                    'role': 'assistant' if kind == 'assistant/message' else 'user',
                    'content': content[:32000],
                    'timestamp': (event.get('time') or 0) / 1000,
                    **({'model': model} if kind == 'assistant/message' and model else {}),
                }
            )
    return {
        'session': {k: v for k, v in row.items() if not k.startswith('_')},
        'messages': list(messages),
        'truncated': truncated,
        'read_only': True,
    }
