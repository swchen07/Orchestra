"""Bounded stdio JSON-RPC transport for owned native harness processes."""

import asyncio
import json
import os

from open_webui.execution.claude import ExecutionError, stop_group


class NativeRpc:
    def __init__(self, argv, cwd, env=None, request_handler=None, log_prefixes=()):
        self.argv, self.cwd, self.env = argv, cwd, env
        self.request_handler = request_handler
        self.log_prefixes = log_prefixes
        self.pending = {}
        self.events = asyncio.Queue(maxsize=256)
        self.sequence = 0
        self.process = None
        self.tasks = []

    async def __aenter__(self):
        self.process = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=self.cwd,
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=4 * 1024 * 1024,
        )
        self.tasks = [asyncio.create_task(self.read()), asyncio.create_task(self.drain())]
        return self

    async def drain(self):
        # Native stderr can contain credentials or private diagnostics. Never expose it as chat text.
        while await self.process.stderr.read(8192):
            pass

    async def write(self, value):
        if self.process.returncode is not None:
            raise ExecutionError('Native process exited before completing the request')
        self.process.stdin.write((json.dumps(value) + '\n').encode())
        await self.process.stdin.drain()

    async def notify(self, method, params=None):
        await self.write({'jsonrpc': '2.0', 'method': method, **({'params': params} if params is not None else {})})

    async def request(self, method, params=None):
        self.sequence += 1
        identity = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        try:
            await self.write({'jsonrpc': '2.0', 'id': identity, 'method': method, 'params': params or {}})
            return await future
        finally:
            self.pending.pop(identity, None)

    async def read(self):
        try:
            diagnostic_size = 0
            while line := await self.process.stdout.readline():
                # DSH 0.2.1-rc.3's account catalog writes a diagnostic to stdout.
                # Accept only its known prefix, with a strict aggregate bound.
                if self.log_prefixes and line.startswith(self.log_prefixes):
                    diagnostic_size += len(line)
                    if diagnostic_size > 65536:
                        raise ExecutionError('Native diagnostics exceeded the protocol limit')
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    raise ExecutionError('Native process returned invalid JSON-RPC') from None
                if not isinstance(event, dict):
                    raise ExecutionError('Native process returned an invalid protocol event')
                if 'id' in event and 'method' not in event:
                    future = self.pending.get(event['id'])
                    if future and not future.done():
                        if event.get('error'):
                            error = event['error']
                            # Native errors can contain requests/keys. Only the code is public.
                            future.set_exception(
                                ExecutionError(f'Native request failed (code {error.get("code", "unknown")})')
                            )
                        else:
                            future.set_result(event.get('result') or {})
                elif 'id' in event:
                    result = await self.request_handler(event) if self.request_handler else None
                    await self.write(
                        {'jsonrpc': '2.0', 'id': event['id'], 'result': result}
                        if result is not None
                        else {
                            'jsonrpc': '2.0',
                            'id': event['id'],
                            'error': {'code': -32601, 'message': 'Unsupported client request'},
                        }
                    )
                else:
                    await self.events.put(event)
            raise ExecutionError('Native process closed before a terminal event')
        except asyncio.CancelledError:
            raise
        except Exception as error:
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(error)
            await self.events.put({'method': '_transport_error', 'error': error})

    async def next_event(self):
        event = await self.events.get()
        if event.get('method') == '_transport_error':
            raise event['error']
        return event

    async def __aexit__(self, *args):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for future in self.pending.values():
            if not future.done():
                future.cancel()
        if self.process:
            await asyncio.shield(stop_group(self.process))


def native_environment():
    # A child must not inherit the host chat's native identity or recursion markers.
    return {
        k: v
        for k, v in os.environ.items()
        if k not in {'CODEX_THREAD_ID', 'CLAUDE_SESSION_ID', 'CLAUDECODE', 'AUTOSWARM_PARENT'}
    }
