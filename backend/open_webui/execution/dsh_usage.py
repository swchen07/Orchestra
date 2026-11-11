"""Attribute committed DSH v4 usage to one invocation, never a session total.

ACP usage_update.used is context occupancy. The native session-stats plugin
instead folds assistant/message usage and step/tool boundaries; use those same
durable facts after the ACP session has flushed.
"""

from functools import lru_cache
import copy
import json
from pathlib import Path
import uuid

from open_webui.execution.dsh_history import open_log
from open_webui.integrations.cc_switch.adapter import text_content
from open_webui.execution.native_accounting import ledger, accounting_usage


def matches_reference(text, reference):
    if text.rstrip().endswith('Execution usage reference: ' + reference):
        return True
    # Older first turns replayed a serialized conversation. Only the final
    # user's reference owns this invocation; quoted earlier reports do not.
    if text.startswith('Continue this conversation and answer the final user message.'):
        try:
            messages = json.loads(text.split('\n', 1)[1])
            text = next(m['content'] for m in reversed(messages) if m.get('role') == 'user')
        except (ValueError, TypeError, KeyError, StopIteration, IndexError):
            return False
    return isinstance(text, str) and text.rstrip().endswith('Reference ID for this response: ' + reference)


def read_turn_usage(session_id, reference, workspace=None, home=None, *, allow_partial=False):
    """Read a server-owned session/ref pair; paths and transcript stay private."""
    try:
        session_id = str(uuid.UUID(session_id))
        if not isinstance(reference, str) or not 1 <= len(reference) <= 100:
            return None
        root = (Path(home or Path.home()) / '.dsh/sessions').resolve()
        candidates = list(root.glob('*/' + session_id + '/session.v4.jsonl.zstd'))
        if len(candidates) != 1:
            return None
        path = candidates[0].resolve()
        if not path.is_relative_to(root):
            return None
        stat = path.stat()
        return copy.deepcopy(_read(str(path), stat.st_mtime_ns, stat.st_size, session_id, reference, workspace, allow_partial))
    except (OSError, TypeError, ValueError):
        return None


def number(value):
    return type(value) in (int, float) and 0 <= value < float('inf')


@lru_cache(maxsize=256)
def _read(path, modified, size, session_id, reference, workspace, allow_partial=False):
    if size > 32 * 1024 * 1024:
        return None
    current, matched, rows, valid_header = None, False, [], False
    try:
        with open_log(path) as stream:
            header = json.loads(stream.readline(65536))
            if (header.get('type') != 'session' or header.get('version') != 4
                    or header.get('id') != session_id or header.get('parentSession')
                    or header.get('delegationDepth', 0) > 0
                    or (workspace and Path(header.get('cwd', '')).resolve() != Path(workspace).resolve())):
                return None
            valid_header = True
            current, matched, rows, seen, total = None, False, [], set(), 0
            while line := stream.readline(1024 * 1024 + 1):
                total += len(line.encode())
                if total > 32 * 1024 * 1024 or len(line) > 1024 * 1024:
                    return None
                event = json.loads(line)
                seq = event.get('seq')
                if not isinstance(seq, int) or seq in seen:
                    continue
                seen.add(seq)
                kind, data = event.get('type'), event.get('data') or {}
                if kind == 'turn/start':
                    if matched:
                        return fold_partial_turn(rows, session_id, current) if allow_partial else None

                    current, rows = data.get('turn'), []
                if current is None:
                    continue
                rows.append(event)
                if kind == 'user/message' and (data.get('source') or {}).get('kind', 'user') == 'user':
                    matched |= matches_reference(text_content(data.get('content')), reference)
                if kind == 'turn/end' and data.get('turn') == current and matched:
                    return fold_turn(rows, session_id, current)
        return fold_partial_turn(rows, session_id, current) if allow_partial and matched else None
    except Exception:
        if allow_partial and valid_header and matched:
            return fold_partial_turn(rows, session_id, current)
        # A truncated frame, unsupported runtime or missing optional decoder
        # means unknown usage, never a broken chat or an invented zero.
        return None


def fold_turn(rows, session_id, turn):
    fresh = output = cached = written = 0
    sources, steps, tools = [], {}, {}
    llm_ms = tool_ms = 0
    for event in rows:
        kind, data, at = event.get('type'), event.get('data') or {}, event.get('time')
        if kind == 'step/start' and data.get('turn') == turn and number(at):
            steps[data.get('step')] = at
        elif kind == 'assistant/message' and data.get('turn') == turn:
            usage = data.get('usage') or {}
            if not all(number(usage.get(k)) for k in ('inputTokens', 'outputTokens')):
                return None
            if not all(number(usage.get(k, 0)) for k in ('cacheReadTokens', 'cacheWriteTokens')):
                return None
            fresh += usage['inputTokens']
            output += usage['outputTokens']
            cached += usage.get('cacheReadTokens', 0)
            written += usage.get('cacheWriteTokens', 0)
            sources.append(f'dsh:{session_id}:{event["seq"]}')
            start = steps.pop(data.get('step'), None)
            if start is not None and number(at):
                llm_ms += max(0, at - start)
        elif kind == 'tool/call' and number(at):
            tools[data.get('callId')] = at
        elif kind == 'tool/result' and number(at):
            start = tools.pop((data.get('message', {}).get('source') or {}).get('callId'), None)
            if start is not None:
                tool_ms += max(0, at - start)
    if not sources:
        return None
    return {
        'usage': {
            # DSH inputTokens excludes cache; the chat contract includes it once.
            'prompt_tokens': fresh + cached + written, 'completion_tokens': output,
            'fresh_input_tokens': fresh, 'cache_read_tokens': cached, 'cache_write_tokens': written,
            'usage_source': 'dsh-native-v4', 'usage_scope': 'native_session_own',
            'measurement': 'native_reported', 'source_event_ids': sources,
        },
        'native_metrics': {
            'llm_ms': llm_ms, 'tool_ms': tool_ms, 'native_turn_id': turn,
            'usage_scope': 'native_session_own',
        },
    }


def fold_partial_turn(rows, session_id, turn):
    # Keep only fully attributable committed counters; a later incomplete
    # response cannot erase earlier known costs or turn missing usage into zero.
    retained = []
    for row in rows:
        if row.get('type') == 'assistant/message':
            usage = (row.get('data') or {}).get('usage') or {}
            if not all(number(usage.get(k)) for k in ('inputTokens', 'outputTokens')) or not all(
                    number(usage.get(k, 0)) for k in ('cacheReadTokens', 'cacheWriteTokens')):
                continue
        retained.append(row)
    measured = fold_turn(retained, session_id, turn)
    if measured:
        measured['usage'].update(usage_scope='native_session_own_partial', measurement='native_reported_partial')
        measured['native_metrics']['usage_scope'] = 'native_session_own_partial'
    return measured


def read_interrupted_usage(session_id, reference, workspace=None, home=None, status='failed'):
    """Known costs survive an interrupted turn, without a fabricated full tree."""
    measured = read_native_tree(session_id, reference, workspace, home)
    if measured:
        measured = copy.deepcopy(measured)
        tree = measured['native_usage']
        tree.update(complete=False, aggregate_complete=False)
        tree.setdefault('warnings', []).append('Invocation interrupted; native accounting is a retained known subtotal')
        measured['usage'] = {**measured['usage'], **accounting_usage(tree)}
        return measured
    own = read_turn_usage(session_id, reference, workspace, home, allow_partial=True)
    if not own:
        return None
    root_id = f'dsh:{session_id}:turn:{own["native_metrics"]["native_turn_id"]}:partial'
    tree = ledger('dsh', root_id, [{'id': root_id, 'native_session_id': session_id, 'parent_id': None,
        'status': status, 'usage': own['usage'], 'source_event_ids': own['usage']['source_event_ids']}],
        complete=False, warnings=['Only committed interrupted-turn usage is known; descendant totals are unavailable'])
    return {**own, 'native_usage': tree, 'usage': {**own['usage'], **accounting_usage(tree)}}


def read_native_tree(session_id, reference, workspace=None, home=None):
    """Follow parent-owned catalogs; exclude fork seed events and other turns."""
    own = read_turn_usage(session_id, reference, workspace, home)
    if not own:
        return None
    root = (Path(home or Path.home()) / '.dsh/sessions').resolve()

    def read(identity):
        identity = str(uuid.UUID(identity))
        paths = list(root.glob('*/' + identity + '/session.v4.jsonl.zstd'))
        if len(paths) != 1 or not paths[0].resolve().is_relative_to(root):
            raise ValueError('Native child transcript unavailable')
        if paths[0].stat().st_size > 32 * 1024 * 1024:
            raise ValueError('Native child transcript exceeds bound')
        with open_log(paths[0]) as stream:
            header = json.loads(stream.readline(65536))
            rows, seen, size = [], set(), 0
            while line := stream.readline(1024 * 1024 + 1):
                size += len(line.encode())
                if size > 32 * 1024 * 1024 or len(line) > 1024 * 1024:
                    raise ValueError('Native child transcript exceeds bound')
                row = json.loads(line)
                if type(row.get('seq')) is int and row['seq'] not in seen:
                    rows.append(row)
                    seen.add(row['seq'])
        if header.get('id') != identity or header.get('version') != 4:
            raise ValueError('Invalid native child identity')
        # A fork can contain historical requests and catalogs copied from its
        # parent. Only the suffix after its own durable descriptor is billable.
        if header.get('parentSession'):
            descriptors = [r['seq'] for r in rows if r['type'] == 'subagent/descriptor']
            if not descriptors:
                raise ValueError('Child has no durable descriptor')
            rows = [r for r in rows if r['seq'] >= descriptors[-1]]
        return header, rows

    def turns(rows):
        groups, current = [], []
        for row in rows:
            if row['type'] == 'turn/start':
                current = []
            current.append(row)
            if row['type'] == 'turn/end':
                groups.append(current)
                current = []
        if any(r['type'] == 'turn/start' for r in current):
            groups.append(current)
        return groups

    try:
        header, rows = read(session_id)
        turn = own['native_metrics']['native_turn_id']
        selected = next(g for g in turns(rows) if any(
            r['type'] == 'turn/end' and r['data'].get('turn') == turn for r in g))
        start, end = selected[0]['time'], selected[-1]['time']
        root_id = f'dsh:{session_id}:turn:{turn}'
        agents = [{'id': root_id, 'native_session_id': session_id, 'parent_id': None, 'status': 'completed',
                   'model': next((r['data'].get('model') for r in reversed(selected) if r['type'] == 'request/context'), None),
                   'usage': own['usage'], 'source_event_ids': own['usage']['source_event_ids'],
                   'timings': {'started_at': start, 'finished_at': end, **own['native_metrics']}}]
        warnings, visited = [], {session_id}

        def children(parent_session, parent_id, parent_rows, low, high, depth=0):
            if depth >= 8 or len(agents) >= 128:
                warnings.append('Native tree traversal bound reached')
                return
            ids = dict.fromkeys(r['data'].get('childId') for r in parent_rows
                                if r['type'] == 'subagent/catalog' and r.get('data', {}).get('childId'))
            for child in ids:
                if child in visited:
                    continue
                visited.add(child)
                touched = False
                try:
                    child_header, child_rows = read(child)
                    if child_header.get('parentSession') != parent_session:
                        raise ValueError('Native child belongs to a different parent')
                    for group in turns(child_rows):
                        first, last = group[0]['time'], group[-1]['time']
                        if last < low or first > high:
                            continue
                        touched = True
                        if first < low or last > high:
                            warnings.append('Child activation crosses invocation boundary')
                            continue
                        child_turn = next(r['data']['turn'] for r in group if r['type'] == 'turn/start')
                        settled = group[-1]['type'] == 'turn/end'
                        measured = fold_turn(group, child, child_turn)
                        identity = f'dsh:{child}:turn:{child_turn}'
                        agents.append({'id': identity, 'native_session_id': child, 'parent_id': parent_id,
                                       'model': next((r['data'].get('model') for r in reversed(group) if r['type'] == 'request/context'), None),
                                       'status': 'completed' if settled else 'running', 'usage': measured['usage'] if measured else {},
                                       'source_event_ids': measured['usage']['source_event_ids'] if measured else [],
                                       'timings': {'started_at': first, 'finished_at': last if settled else None,
                                                   **(measured['native_metrics'] if measured else {})}})
                        children(child, identity, child_rows, first, last, depth + 1)
                    if not touched and any(r['type'] == 'subagent/catalog' and r['data'].get('childId') == child
                                           and low <= r['time'] <= high for r in parent_rows):
                        raise ValueError('New native child has not settled')
                except (OSError, ValueError, KeyError, StopIteration):
                    warnings.append('Native child transcript unavailable or incompatible')
                    agents.append({'id': f'dsh:{child}:unsettled', 'native_session_id': child,
                                   'parent_id': parent_id, 'status': 'unknown', 'usage': {}, 'timings': {}})

        children(session_id, root_id, rows, start, end)
        value = ledger('dsh', root_id, agents, warnings=warnings)
        return {**own, 'native_usage': value, 'usage': {**own['usage'], **accounting_usage(value)}}
    except (OSError, ValueError, TypeError, KeyError, StopIteration):
        return {**own, 'native_usage': ledger('dsh', 'root', [{'id': 'root', 'parent_id': None,
            'status': 'completed', 'usage': own['usage']}], complete=False,
            warnings=['Native child discovery unavailable'])}


def recover_usage(record, home=None):
    """Enrich authorized retained results in memory; never rewrite native history."""
    from open_webui.execution.trajectory import has_token_usage

    for call in record.get('calls', []):
        execution = call.get('execution') or {}
        if (execution.get('framework_id') != 'dsh' or (execution.get('native_usage') or {}).get('complete')
                or call.get('status') != 'completed'):
            continue
        reference = execution.get('usage_reference') or call.get('report_id') or record['id'] + '/' + call['id']
        recovered = read_native_tree(execution.get('native_session_id'), reference, home=home)
        if recovered:
            call['usage'] = {**(call.get('usage') or {}), **recovered['usage']}
            execution['native_metrics'] = recovered['native_metrics']
            execution['native_usage'] = recovered['native_usage']
    return record
