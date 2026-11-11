"""Read only a returned Hermes session's tree from known local state stores."""

from pathlib import Path
import sqlite3
import json
from contextlib import closing
from urllib.parse import urlparse

from open_webui.execution.native_accounting import ledger, accounting_usage

FIELDS = ('id', 'parent_session_id', 'started_at', 'ended_at', 'input_tokens', 'output_tokens',
          'cache_read_tokens', 'cache_write_tokens', 'api_call_count')


def local_roots(endpoint):
    if urlparse(endpoint).hostname not in {'127.0.0.1', 'localhost', '::1'}:
        return []
    return [Path.home() / '.hermes', Path(__file__).resolve().parents[2] / 'data/hermes-example-flash']


def snapshot(session_id, roots):
    if not isinstance(session_id, str) or not 1 <= len(session_id) <= 512:
        return {}
    found = []
    for root in roots:
        path = (Path(root) / 'state.db').resolve()
        if not path.is_relative_to(Path(root).resolve()) or not path.is_file():
            continue
        try:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                db.row_factory = sqlite3.Row
                # Native lineage, not messages containing a claimed child ID.
                rows = db.execute('WITH RECURSIVE tree(id,depth) AS ('
                    'SELECT id,0 FROM sessions WHERE id=? UNION ALL '
                    'SELECT s.id,t.depth+1 FROM sessions s JOIN tree t ON s.parent_session_id=t.id '
                    'WHERE t.depth<8) SELECT ' + ','.join('s.' + f for f in FIELDS) +
                    ' FROM sessions s JOIN tree t ON s.id=t.id LIMIT 129', (session_id,)).fetchall()
                if rows:
                    values = {r['id']: dict(r) for r in rows}
                    for identity, row in values.items():
                        try:
                            row['model'] = db.execute('SELECT model FROM sessions WHERE id=?', (identity,)).fetchone()[0]
                        except sqlite3.Error:
                            pass
                        last = db.execute('SELECT role,timestamp FROM messages WHERE session_id=? '
                                          'ORDER BY id DESC LIMIT 1', (identity,)).fetchone()
                        if last:
                            row.update(last_role=last['role'], last_message_at=last['timestamp'])
                        results = db.execute("SELECT content,timestamp FROM messages WHERE session_id=? "
                            "AND role='tool' AND tool_name='delegate_task' ORDER BY id DESC LIMIT 20", (identity,)).fetchall()
                        row['delegations'] = []
                        for result in results:
                            try:
                                data = json.loads(result['content'])
                                row['delegations'].append({'at': result['timestamp'] * 1000,
                                    'statuses': [v.get('status') for v in data.get('results', [])]})
                            except (ValueError, TypeError, AttributeError):
                                continue
                    found.append(values)
        except (OSError, sqlite3.Error):
            continue
    return found[0] if len(found) == 1 else {}


def measure(session_id, own, started_at, finished_at, roots, baseline=None, invocation_id=''):
    rows = snapshot(session_id, roots)
    baseline = baseline or {}
    root_id = 'hermes:' + str(session_id) + ':' + invocation_id
    root_row = rows.get(session_id)
    if root_row:
        # Session counters are canonical fresh/cache buckets, while SSE's
        # prompt_tokens already includes cache. Only enrich when they reconcile.
        cached, written = root_row.get('cache_read_tokens', 0), root_row.get('cache_write_tokens', 0)
        if root_row.get('input_tokens', 0) + cached + written == own.get('prompt_tokens'):
            own = {**own, 'cache_read_tokens': cached, 'cache_write_tokens': written}
    agents = [{'id': root_id, 'native_session_id': session_id, 'parent_id': None,
               'model': (root_row or {}).get('model'),
               'status': 'completed', 'usage': own,
               'source_event_ids': [root_id],
               'timings': {'started_at': started_at, 'finished_at': finished_at}}]
    selected, warnings = {}, []
    for identity, row in rows.items():
        if identity == session_id:
            continue
        before = baseline.get(identity)
        if before and row['api_call_count'] == before['api_call_count']:
            continue
        # With no prior cursor, old descendants must not become a new turn's
        # charges. A resumed old child without a baseline remains unassigned.
        if not before and row['started_at'] * 1000 < started_at:
            continue
        selected[identity] = row
    def node_id(identity):
        return root_id if identity == session_id else 'hermes:' + identity + ':' + invocation_id
    for identity, row in selected.items():
        before = baseline.get(identity) or {}
        delta = {k: row.get(k, 0) - before.get(k, 0) for k in FIELDS[4:]}
        if any(v < 0 for v in delta.values()) or row['parent_session_id'] not in {session_id, *selected}:
            warnings.append('Unattributed or reset native child counters')
            continue
        cached, written = delta['cache_read_tokens'], delta['cache_write_tokens']
        parent = rows.get(row['parent_session_id']) or {}
        receipts = [r for r in parent.get('delegations', []) if started_at <= r['at'] <= finished_at]
        settled = bool(receipts) and all(s == 'completed' for r in receipts for s in r['statuses'])
        agents.append({'id': node_id(identity), 'native_session_id': identity,
            'model': row.get('model'),
            'parent_id': node_id(row['parent_session_id']),
            # Hermes keeps completed leaf sessions open for history. The
            # parent's synchronous delegate_task has returned; the child's
            # persisted final assistant message is the completion boundary.
            'status': 'completed' if row['ended_at'] or (settled and row.get('last_role') == 'assistant') else 'unknown',
            'source_event_ids': [f'hermes:{identity}:calls:{before.get("api_call_count", 0)}-{row["api_call_count"]}'],
            'usage': {'prompt_tokens': delta['input_tokens'] + cached + written,
                      'completion_tokens': delta['output_tokens'], 'cache_read_tokens': cached,
                      'cache_write_tokens': written},
            'timings': {'started_at': max(started_at, row['started_at'] * 1000),
                        'finished_at': (row['ended_at'] or row.get('last_message_at', 0)) * 1000
                            if row['ended_at'] or (settled and row.get('last_role') == 'assistant') else None}})
    value = ledger('hermes', root_id, agents, complete=bool(rows) and len(rows) < 129, warnings=warnings)
    if not rows:
        value['warnings'].append('Local native lineage unavailable; child usage is unknown')
    return value
