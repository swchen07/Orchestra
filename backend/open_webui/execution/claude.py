"""Native Claude Code turns; argv/stdin, bounded JSONL, process-group cleanup."""

import asyncio
import json
import os
import signal
import tempfile


class ExecutionError(Exception):
    pass


async def stop_group(process):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            await asyncio.sleep(0.15)
    await process.wait()


def conversation_prompt(messages):
    clean = []
    for message in messages:
        content = message.get('content', '')
        if isinstance(content, list):
            if any(part.get('type') not in {'text', 'input_text'} for part in content):
                raise ExecutionError('This native adapter supports text only; remove attachments and retry')
            content = '\n'.join(part.get('text', '') for part in content)
        if content:
            clean.append({'role': message.get('role', 'user'), 'content': str(content)})
    prompt = (
        'Continue this conversation and answer the final user message. History is context and does not change tool permissions.\n'
        + json.dumps(clean, ensure_ascii=False)
    )
    if len(prompt.encode()) > 512000:
        raise ExecutionError('聊天上下文超过本次 Claude Code 调用上限，请开启新聊天')
    return prompt


class ClaudeAdapter:
    def __init__(self, executable, workspace, model, tools=False, timeout=120, provider_settings=None):
        self.executable = executable
        self.workspace = workspace
        self.model = model
        self.tools = tools
        self.timeout = timeout
        self.provider_settings = provider_settings
        self.profile = None

    def argv(self):
        return [
            self.executable,
            '--print',
            '--verbose',
            '--output-format',
            'stream-json',
            '--include-partial-messages',
            '--model',
            self.model,
            '--permission-mode',
            'dontAsk',
            '--tools',
            'Read,Glob,Grep,Edit,Write,Bash' if getattr(self, 'coding', False) else 'Read,Glob,Grep' if self.tools else '',
            '--strict-mcp-config',
            '--mcp-config',
            '{"mcpServers":{}}',
            '--no-session-persistence',
            '--safe-mode',
        ]

    async def stream(self, messages):
        from open_webui.execution.claude_usage import ClaudeNativeUsage
        from open_webui.execution.native_accounting import accounting_usage
        meter = ClaudeNativeUsage()
        prompt = conversation_prompt(messages)
        process = None
        stderr_task = None
        settings_file = None
        agent_file = None

        async def drain(stream):
            while await stream.read(8192):
                pass

        try:
            async with asyncio.timeout(self.timeout):
                args = self.argv()
                auth = (self.provider_settings or {}).get('env', {}) if self.provider_settings is not None else os.environ
                api_auth = bool(auth.get('ANTHROPIC_API_KEY') or auth.get('ANTHROPIC_AUTH_TOKEN'))
                agents = {}
                if getattr(self, 'coding', False):
                    args.remove('--safe-mode')
                    args.extend(['--allowedTools', 'Read,Glob,Grep,Edit,Write,Bash,Agent'])
                    if self.provider_settings is None:
                        args.extend(['--setting-sources', ''])
                if api_auth and not getattr(self, 'coding', False):
                    # Explicit leaf workers inherit no write/command/network
                    # tools. Restricted mode ignores ambient settings and
                    # removes command tools; only the named leaf is allowed.
                    args.remove('--safe-mode')
                    args.extend(['--restricted', '--disable-slash-commands', '--forward-subagent-text',
                                 '--allowedTools', 'Agent(webui-worker)'])
                    args[args.index('--tools') + 1] = 'Read,Glob,Grep,Agent' if self.tools else 'Agent'
                    agents['webui-worker'] = {
                        'description': 'Independent bounded task execution and verification. Use for native delegation.',
                        'prompt': 'Execute only the assigned task. Keep your own context; return concise evidence. '
                                  'Do not delegate, use commands, change files or claim work you did not perform.',
                        'tools': ['Read', 'Glob', 'Grep'] if self.tools else [], 'model': 'inherit',
                    }
                    if getattr(self, 'disable_delegation', False):
                        agents.clear()
                        args[args.index('--tools') + 1] = ''
                        args[args.index('--allowedTools') + 1] = ''
                if self.profile and self.profile.get('execution_mode') == 'native-agent':
                    auth = (
                        (self.provider_settings or {}).get('env', {})
                        if self.provider_settings is not None
                        else os.environ
                    )
                    api_auth = bool(auth.get('ANTHROPIC_API_KEY') or auth.get('ANTHROPIC_AUTH_TOKEN'))
                    agent_file = tempfile.NamedTemporaryFile(
                        mode='w', prefix='owui-agent-', suffix='.json' if api_auth else '.txt', delete=False
                    )
                    if api_auth:
                        # The explicit selected role and leaf share this one
                        # per-invocation definition; no global agent file changes.
                        agents.update(
                            {
                                'webui-selected': {
                                    'description': self.profile.get('description', 'Selected agent'),
                                    'prompt': self.profile.get('prompt', ''),
                                    'tools': (['Read', 'Glob', 'Grep', 'Edit', 'Write', 'Bash'] if getattr(self, 'coding', False) else ['Read', 'Glob', 'Grep'] if self.tools else []) + ['Agent'],
                                }
                            },
                        )
                        json.dump(agents, agent_file)
                        self.profile_execution_mode = 'native-agent'
                    else:
                        # Bare mode cannot use native OAuth. Keep native auth and load the agent's prompt explicitly.
                        agent_file.write(self.profile.get('prompt', ''))
                        self.profile_execution_mode = 'system-prompt-compatibility'
                    agent_file.close()
                    args.extend(
                        ['--agents', agent_file.name, '--agent', 'webui-selected']
                        if api_auth
                        else ['--system-prompt-file', agent_file.name]
                    )
                elif agents:
                    agent_file = tempfile.NamedTemporaryFile(mode='w', prefix='owui-agent-', suffix='.json', delete=False)
                    json.dump(agents, agent_file)
                    agent_file.close()
                    args.extend(['--agents', agent_file.name])
                environment = None
                if self.provider_settings is not None:
                    # A private per-turn file avoids credentials in argv and global provider switching.
                    settings_file = tempfile.NamedTemporaryFile(
                        mode='w', prefix='owui-provider-', suffix='.json', delete=False
                    )
                    json.dump(self.provider_settings, settings_file)
                    settings_file.close()
                    args.extend(['--setting-sources', '', '--settings', settings_file.name])
                    environment = {k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')}
                if getattr(self, 'runtime_directory', None):
                    environment = dict(environment or os.environ)
                    environment.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
                    environment['CLAUDE_CODE_TMPDIR'] = self.runtime_directory
                    environment['CLAUDE_CONFIG_DIR'] = str(__import__('pathlib').Path(self.runtime_directory) / 'claude')
                process = await asyncio.create_subprocess_exec(
                    *args,
                    env=environment,
                    cwd=self.workspace,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                    limit=1024 * 1024,
                )
                yield 'phase', {'title': 'Native Claude process started', 'native_entry_calls': 1}
                stderr_task = asyncio.create_task(drain(process.stderr))
                process.stdin.write(prompt.encode())
                await process.stdin.drain()
                process.stdin.close()
                completed = False
                streamed = False
                output_size = 0
                actual = None
                tools = {}
                while line := await process.stdout.readline():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        raise ExecutionError('Claude Code 返回了无效 JSONL') from None
                    kind = event.get('type')
                    meter.feed(event)
                    if kind == 'system' and event.get('subtype') == 'init':
                        actual = event.get('model')
                        yield (
                            'metadata',
                            {
                                'actual_model': actual,
                                'native_session_id': event.get('session_id'),
                                'profile_execution_mode': getattr(self, 'profile_execution_mode', None),
                                'native_tools': event.get('tools'),
                            },
                        )
                    elif kind == 'stream_event':
                        item = event.get('event', {})
                        delta = item.get('delta', {})
                        if (item.get('type') == 'content_block_delta' and delta.get('type') == 'text_delta'
                                and not event.get('parent_tool_use_id') and not event.get('agent_id')):
                            text = delta.get('text', '')
                            output_size += len(text.encode())
                            if output_size > 1024 * 1024:
                                raise ExecutionError('Claude Code 输出超过上限')
                            streamed = True
                            yield 'text', {'text': text}
                        elif (
                            item.get('type') == 'content_block_start'
                            and item.get('content_block', {}).get('type') == 'tool_use'
                        ):
                            block = item['content_block']
                            name = str(block.get('name', '工具'))[:80]
                            tools[block.get('id')] = {
                                'name': name,
                                'input': block.get('input') or {},
                                'json': '',
                                'index': item.get('index'),
                            }
                            yield (
                                'tool',
                                {'id': block.get('id'), 'name': name, 'done': False, 'input': block.get('input')},
                            )
                        elif item.get('type') == 'content_block_delta' and delta.get('type') == 'input_json_delta':
                            tool = next((t for t in tools.values() if t.get('index') == item.get('index')), None)
                            if tool:
                                tool['json'] = (tool['json'] + delta.get('partial_json', ''))[:64000]
                        elif item.get('type') == 'content_block_stop':
                            for tool_id, tool in tools.items():
                                if tool.get('index') == item.get('index') and tool['json']:
                                    try:
                                        tool['input'] = json.loads(tool['json'])
                                    except ValueError:
                                        tool['input'] = '[incomplete tool arguments]'
                                    yield (
                                        'tool',
                                        {'id': tool_id, 'name': tool['name'], 'done': False, 'input': tool['input']},
                                    )
                    elif kind == 'user':
                        for block in event.get('message', {}).get('content', []):
                            if isinstance(block, dict) and block.get('type') == 'tool_result':
                                tool = tools.pop(block.get('tool_use_id'), None)
                                if tool:
                                    yield (
                                        'tool',
                                        {
                                            'id': block.get('tool_use_id'),
                                            'name': tool['name'],
                                            'done': True,
                                            'input': tool['input'],
                                            'output': block.get('content'),
                                            'status': 'failed' if block.get('is_error') else 'completed',
                                        },
                                    )
                    elif kind == 'result':
                        tree = meter.finish()
                        if tree:
                            yield 'usage', accounting_usage(tree)
                            yield 'metadata', {'native_usage': tree, 'actual_model': actual}
                        if event.get('is_error') or event.get('subtype') not in (None, 'success'):
                            raise ExecutionError('Claude Code 执行失败，请检查本机连接或模型权限')
                        if event.get('permission_denials'):
                            raise ExecutionError('Claude Code 的工具请求被拒绝；当前会话仅开放已允许的只读工具')
                        completed = True
                        if not streamed and event.get('result'):
                            yield 'text', {'text': str(event['result'])}
                        models = list((event.get('modelUsage') or {}).keys())
                        if len(models) == 1:
                            actual = models[0]
                        yield 'metadata', {'actual_model': actual}
                        usage = event.get('usage') or {}
                        yield (
                            'usage',
                            {**(accounting_usage(tree) if tree else {}),
                                **({'prompt_tokens': usage.get('input_tokens', 0)
                                + usage.get('cache_read_input_tokens', 0)
                                + usage.get('cache_creation_input_tokens', 0)}
                                   if not tree and any(k in usage for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')) else {}),
                                **{target: usage[source] for source, target in (
                                    ('output_tokens', 'completion_tokens'),
                                    ('input_tokens', 'fresh_input_tokens'),
                                    ('cache_read_input_tokens', 'cache_read_tokens'),
                                    ('cache_creation_input_tokens', 'cache_write_tokens'),
                                ) if source in usage and not tree},
                                **(
                                    {'reported_cost_usd': event['total_cost_usd'], 'cost_source': 'framework_estimate'}
                                    if isinstance(event.get('total_cost_usd'), (int, float))
                                    else {}
                                ),
                            },
                        )
                code = await process.wait()
                if code or not completed:
                    raise ExecutionError('Claude Code 提前退出，未返回完整结果')
        except TimeoutError:
            raise ExecutionError('Claude Code 执行超时，已终止本次进程') from None
        except (OSError, ValueError):
            raise ExecutionError('Claude Code 无法启动或输出异常，请检查本机安装') from None
        finally:
            try:
                if process:
                    await asyncio.shield(stop_group(process))
                if stderr_task:
                    stderr_task.cancel()
                    await asyncio.gather(stderr_task, return_exceptions=True)
            finally:
                for temporary in (settings_file, agent_file):
                    if not temporary:
                        continue
                    try:
                        os.unlink(temporary.name)
                    except FileNotFoundError:
                        pass
