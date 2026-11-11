"""Claude stream accounting: result.usage is SELF; modelUsage is INCLUSIVE.

Forwarded subagent messages/task notifications supply attribution. Stream
replays and split assistant messages are keyed by native message ID, not added.
"""

import time
import uuid
import re
import copy

from open_webui.execution.native_accounting import ledger, add_usage, number


def task_receipt(content):
    if isinstance(content, list):
        content = '\n'.join(p.get('text', '') for p in content if isinstance(p, dict))
    text = str(content)
    match = re.search(r'<usage>\s*(?:total_tokens|subagent_tokens):\s*(\d+)\s*tool_uses:\s*\d+\s*duration_ms:\s*(\d+)\s*</usage>\s*$', text)
    identity = re.search(r'\nagentId:\s*([A-Za-z0-9_-]+)\s*\n<usage>', text)
    return (int(match[1]), int(match[2]), identity[1] if identity else None) if match else None


def recover_call(call):
    """Reconcile retained native completion receipts without replay or journal edits."""
    tree = (call.get('execution') or {}).get('native_usage')
    if not tree or tree.get('framework') != 'claude-code' or tree.get('complete'):
        return
    agents, changed = copy.deepcopy(tree['agents']), False
    for tool in call.get('tools', {}).values():
        if tool.get('name') not in {'Agent', 'Task'} or not tool.get('done'):
            continue
        receipt = task_receipt(tool.get('output'))
        if not receipt:
            continue
        total, duration, identity = receipt
        agent = next((a for a in agents if a.get('native_agent_id') == identity), None)
        if agent and number(agent['usage'].get('prompt_tokens')) and total >= agent['usage']['prompt_tokens']:
            agent['usage'].update(total_tokens=total, completion_tokens=total - agent['usage']['prompt_tokens'])
            if number(agent.get('timings', {}).get('finished_at')):
                agent['timings']['started_at'] = agent['timings']['finished_at'] - duration
            changed = True
    if changed:
        value = ledger('claude-code', tree['root_id'], agents, reported_inclusive=tree['inclusive'],
                       warnings=[w for w in tree['warnings'] if w != 'Native aggregate and attributed agent facts do not reconcile'])
        call['execution']['native_usage'] = value
        value['reconciled_from_receipts'] = True
        from open_webui.execution.native_accounting import accounting_usage
        call['usage'] = {**call.get('usage', {}), **accounting_usage(value)}


def anthropic_usage(value, camel=False):
    get = lambda snake, mixed: value.get(mixed if camel else snake)
    fresh, out = get('input_tokens', 'inputTokens'), get('output_tokens', 'outputTokens')
    cached, written = get('cache_read_input_tokens', 'cacheReadInputTokens'), get('cache_creation_input_tokens', 'cacheCreationInputTokens')
    result = {}
    if number(fresh):
        result['prompt_tokens'] = fresh + (cached or 0) + (written or 0)
    if number(out):
        result['completion_tokens'] = out
    if number(cached):
        result['cache_read_tokens'] = cached
    if number(written):
        result['cache_write_tokens'] = written
    return result


class ClaudeNativeUsage:
    def __init__(self):
        self.started_at = int(time.time() * 1000)
        self.session_id = None
        self.invocation = uuid.uuid4().hex
        self.aliases, self.parents, self.messages, self.current, self.agents = {}, {}, {}, {}, {}
        self.result = None
        self.task_results = {}
        self.models = {}

    def canonical(self, key):
        for _ in range(8):
            if key not in self.aliases or self.aliases[key] == key:
                break
            key = self.aliases[key]
        return key

    def feed(self, event):
        at = int(time.time() * 1000)
        if event.get('type') == 'system' and event.get('subtype') == 'init':
            self.session_id = event.get('session_id')
        if event.get('type') == 'result':
            self.result = event
        parent_tool = event.get('parent_tool_use_id')
        owner = event.get('agent_id') or parent_tool or 'root'
        if parent_tool and event.get('agent_id'):
            self.aliases[parent_tool] = owner
        subtype = event.get('subtype')
        if subtype in {'task_started', 'task_progress', 'task_notification'}:
            identity = event.get('task_id') or event.get('agent_id') or event.get('tool_use_id')
            if identity:
                if event.get('tool_use_id'):
                    self.aliases[event['tool_use_id']] = identity
                agent = self.agents.setdefault(identity, {'started_at': at, 'status': 'running'})
                if subtype == 'task_notification':
                    agent.update(status=event.get('status', 'completed'), finished_at=at)
                    usage = event.get('usage') or {}
                    agent['reported_total'] = usage.get('total_tokens')
                    if number(usage.get('duration_ms')):
                        agent['started_at'] = at - usage['duration_ms']
        message = event.get('message') or {}
        if isinstance(message.get('model'), str):
            self.models[owner] = message['model']
        if event.get('type') == 'user':
            for block in message.get('content', []):
                tool_id = block.get('tool_use_id') if isinstance(block, dict) else None
                if not isinstance(block, dict) or block.get('type') != 'tool_result' or tool_id not in self.parents:
                    continue
                receipt = task_receipt(block.get('content', ''))
                if receipt:
                    total, duration, _ = receipt
                    self.task_results[tool_id] = {'reported_total': total, 'finished_at': at,
                                                  'started_at': at - duration, 'status': 'completed'}
        if event.get('type') == 'assistant':
            identity = message.get('id')
            if identity:
                key = (owner, identity)
                row = self.messages.setdefault(key, {})
                normalized = anthropic_usage(message.get('usage') or {})
                # Assembled messages can carry output_tokens=1 placeholders.
                for k, v in normalized.items():
                    if k != 'completion_tokens' or not row.get('_stream_output'):
                        row[k] = v
            for block in message.get('content', []):
                if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('name') in {'Agent', 'Task'}:
                    child = block['id']
                    self.parents[child] = owner
                    self.agents.setdefault(child, {'started_at': at, 'status': 'running'})
        if event.get('type') == 'stream_event':
            item = event.get('event') or {}
            if item.get('type') == 'message_start':
                message = item.get('message') or {}
                if isinstance(message.get('model'), str):
                    self.models[owner] = message['model']
                if message.get('id'):
                    self.current[owner] = message['id']
                    self.messages[(owner, message['id'])] = anthropic_usage(message.get('usage') or {})
            elif item.get('type') == 'message_delta' and owner in self.current:
                row = self.messages.setdefault((owner, self.current[owner]), {})
                if number((item.get('usage') or {}).get('output_tokens')):
                    row.update(completion_tokens=item['usage']['output_tokens'], _stream_output=True)

    def finish(self):
        if not self.result:
            return None
        end = int(time.time() * 1000)
        root_id = 'claude:' + str(self.session_id) + ':' + self.invocation
        by_owner, sources = {}, {}
        # Re-key after tool IDs are associated with task/agent IDs.
        messages = {}
        for (owner, identity), value in self.messages.items():
            messages[(self.canonical(owner), identity)] = value
        for (owner, identity), value in messages.items():
            by_owner.setdefault(owner, []).append(value)
            sources.setdefault(owner, []).append(f'claude:{self.session_id}:{identity}')
        own = anthropic_usage(self.result.get('usage') or {})
        inclusive = add_usage([anthropic_usage(v, True) for v in (self.result.get('modelUsage') or {}).values()])
        agents = [{'id': root_id, 'native_session_id': self.session_id, 'parent_id': None,
                   'model': self.models.get('root'),
                   'status': 'completed' if not self.result.get('is_error') else 'failed', 'usage': own,
                   'source_event_ids': sources.get('root') or [root_id + ':result-self'],
                   'timings': {'started_at': self.started_at, 'finished_at': end}}]
        native_agents, parents = {}, {}
        for identity, agent in self.agents.items():
            canonical = self.canonical(identity)
            native_agents[canonical] = {**native_agents.get(canonical, {}), **agent}
        for identity, result in self.task_results.items():
            key = self.canonical(identity)
            native_agents[key] = {**native_agents.get(key, {}), **result}
        for identity, parent in self.parents.items():
            parents[self.canonical(identity)] = self.canonical(parent)
        for owner in set(by_owner) - {'root'}:
            native_agents.setdefault(owner, {'started_at': self.started_at, 'finished_at': end, 'status': 'completed'})
        for identity, agent in native_agents.items():
            usage = add_usage(by_owner.get(identity, []))
            total = agent.get('reported_total')
            if number(total):
                # Native task completion's total is authoritative. Infer the
                # complementary output only when the exact input is available.
                usage['total_tokens'] = total
                if number(usage.get('prompt_tokens')) and total >= usage['prompt_tokens']:
                    usage['completion_tokens'] = total - usage['prompt_tokens']
            status = agent.get('status', 'unknown')
            agents.append({'id': root_id + ':' + identity, 'native_agent_id': identity,
                'model': next((v for k, v in self.models.items() if self.canonical(k) == identity), None),
                'parent_id': root_id if parents.get(identity, 'root') == 'root' else root_id + ':' + parents[identity],
                'status': status, 'usage': usage, 'source_event_ids': sources.get(identity, []),
                'timings': {k: agent.get(k) for k in ('started_at', 'finished_at')}})
        return ledger('claude-code', root_id, agents, reported_inclusive=inclusive,
                      complete=bool(inclusive) or not native_agents)
