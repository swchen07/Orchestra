"""Discover local adapter runtimes; use one selected executable for catalog and execution."""

import asyncio
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

ADAPTERS = {
    'claude-code': ('Claude Code', 'claude'),
    'codex': ('Codex', 'codex'),
    'dsh': ('DSH', 'dsh'),
    'hermes': ('Hermes', 'hermes'),
}
_paths = {}
_snapshot = []
_checked = 0
_lock = asyncio.Lock()


def candidates(family):
    command = ADAPTERS[family][1]
    values = [shutil.which(command)]
    if family == 'codex':
        values += [
            '/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex',
            '/Applications/Codex.app/Contents/Resources/codex',
        ]
    values += [str(Path.home() / '.local/bin' / command), '/usr/local/bin/' + command, '/opt/homebrew/bin/' + command]
    return list(
        dict.fromkeys(str(Path(p).expanduser()) for p in values if p and os.path.isfile(p) and os.access(p, os.X_OK))
    )


def inspect_path(family, path):
    path = str(Path(path).expanduser().absolute())
    if not Path(path).is_file() or not os.access(path, os.X_OK):
        raise ValueError('Choose an existing executable file')
    version = ''
    if family in {'codex', 'claude-code'}:
        try:
            result = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=5)
            # Only expose the expected version token, never arbitrary process output.
            match = re.search(r'\b\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.]+)?', result.stdout[:500])
            version = match.group(0) if match else ''
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {'path': path, 'version': version}


def scan(paths):
    rows = []
    for family, (name, _) in ADAPTERS.items():
        found = []
        for path in list(dict.fromkeys(([paths[family]] if paths.get(family) else []) + candidates(family))):
            try:
                found.append(inspect_path(family, path))
            except ValueError:
                continue
        preferred = next((r for r in found if r['path'] == paths.get(family)), None)
        if not preferred and found:
            preferred = max(found, key=lambda r: tuple(int(n) for n in re.findall(r'\d+', r['version'])[:3]))
        if preferred:
            rows.append(
                {
                    'id': family,
                    'name': name,
                    **preferred,
                    'candidates': found,
                    'selection': 'manual' if family in paths else 'discovered',
                }
            )
        elif paths.get(family):
            rows.append(
                {
                    'id': family,
                    'name': name,
                    'path': paths[family],
                    'version': '',
                    'candidates': [],
                    'selection': 'manual',
                    'unavailable': True,
                }
            )
    return rows


async def refresh_runtimes(force=False):
    global _paths, _snapshot, _checked
    from open_webui.models.config import Config

    async with _lock:
        paths = await Config.get('execution.runtime_paths') or {}
        if force or paths != _paths or time.monotonic() - _checked > 120:
            _snapshot = await asyncio.to_thread(scan, paths)
            _paths, _checked = paths, time.monotonic()
    return _snapshot


def runtime_path(family):
    row = next((r for r in _snapshot if r['id'] == family), None)
    if row:
        return None if row.get('unavailable') else row['path']
    return None if family in _paths else next(iter(candidates(family)), None)


def discovered_frameworks(definitions, models=()):
    found = {row['id'] for row in _snapshot}
    found.update(m.get('execution', {}).get('framework_id') for m in models)
    return [dict(row) for row in definitions if row['id'] == 'direct' or row['id'] in found]
