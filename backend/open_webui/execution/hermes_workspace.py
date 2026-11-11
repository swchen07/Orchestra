"""Workspace-bound installed Hermes runtime; legacy HTTP gateway stays unchanged."""
import asyncio
import json
import os
from pathlib import Path
import uuid
import yaml
from open_webui.execution.claude import ExecutionError, stop_group
from open_webui.execution.native_rpc import native_environment
from open_webui.execution.history_provenance import dialogue_digest


def configuration(policy, execution):
    profile = policy.get('hermes_profile')
    source = Path(policy.get('hermes_source') or Path.home() / '.hermes/hermes-agent').resolve()
    python = Path(policy.get('hermes_python') or source / 'venv/bin/python')
    if not profile or not Path(profile).is_dir() or not (source / 'run_agent.py').is_file() or not python.is_file():
        raise ExecutionError('Hermes coding requires a configured compatible native runtime and explicit trusted profile; no model call was admitted')
    try:
        config = yaml.safe_load((Path(profile) / 'config.yaml').read_text()) or {}
        selected = config.get('model') or {}
        provider = selected.get('provider') if isinstance(selected, dict) else None
        model = selected.get('default') if isinstance(selected, dict) else selected
    except (OSError, ValueError, yaml.YAMLError):
        raise ExecutionError('Hermes native profile configuration is unavailable') from None
    if model != execution['model_id'] or provider != execution.get('provider'):
        raise ExecutionError('Hermes native profile does not match the selected provider/model; no model call was admitted')
    return {'profile': str(Path(profile).resolve()), 'source': str(source), 'python': str(python),
            'model': model, 'provider': provider}


class HermesWorkspaceAdapter:
    def __init__(self, workspace, runtime, config, timeout=120):
        self.workspace, self.runtime, self.config, self.timeout = workspace, runtime, config, timeout
        self.context_key = None

    async def stream(self, messages):
        from open_webui.execution.native_accounting import ledger, accounting_usage
        environment = native_environment()
        environment.update(HERMES_HOME=str(self.runtime), TERMINAL_CWD=str(self.workspace), TERMINAL_ENV='local',
                           PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
        # No gateway/platform config or native auth state is copied into candidate files.
        packet = {**self.config, 'workspace': str(self.workspace), 'runtime': str(self.runtime),
                  'messages': messages, 'session_id': self.context_key or str(uuid.uuid4())}
        process = None
        stderr = None
        complete = False
        own = {}
        node = 'hermes-native:' + uuid.uuid4().hex
        def usage_tree(status):
            return ledger('hermes', node, [{'id': node, 'parent_id': None, 'native_session_id': packet['session_id'],
                'model': packet['model'], 'status': status, 'usage': own, 'source_event_ids': [node],
                'timings': {'started_at': int(started), 'finished_at': int(__import__('time').time() * 1000)}}],
                complete=False, warnings=['Scoped native Hermes exposes own counters; descendant lineage is unavailable'])
        started = __import__('time').time() * 1000
        async def drain(stream):
            while await stream.read(8192):
                pass
        try:
            async with asyncio.timeout(self.timeout):
                process = await asyncio.create_subprocess_exec(self.config['python'],
                    str(Path(__file__).with_name('hermes_workspace_bridge.py')), cwd=str(self.workspace), env=environment,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    start_new_session=True, limit=2 * 1024 * 1024)
                yield 'phase', {'title': 'Native Hermes process started', 'native_entry_calls': 1}
                stderr = asyncio.create_task(drain(process.stderr))
                process.stdin.write((json.dumps(packet) + '\n').encode())
                await process.stdin.drain()
                process.stdin.close()
                total = 0
                while line := await process.stdout.readline():
                    total += len(line)
                    if total > 2 * 1024 * 1024:
                        raise ExecutionError('Hermes native output exceeds its bounded transport')
                    event = json.loads(line)
                    kind, payload = event['kind'], event['payload']
                    if kind == 'error':
                        raise ExecutionError(payload['message'])
                    if kind == 'done':
                        complete = True
                    elif kind == 'usage':
                        own.update(payload)
                        tree = usage_tree('running')
                        yield 'usage', {**accounting_usage(tree), 'native_entry_calls': 1, 'model_request_count': own.get('model_request_count')}
                        yield 'metadata', {'native_usage': tree, 'native_model_request_count': own.get('model_request_count')}
                    else:
                        if kind == 'metadata':
                            payload.update(submitted_history_messages=len([m for m in messages[:-1] if m.get('role') in {'user', 'assistant'}]),
                                submitted_history_sha256=dialogue_digest([m for m in messages[:-1] if m.get('role') in {'user', 'assistant'}]))
                        yield kind, payload
                await process.wait()
                if process.returncode or not complete:
                    raise ExecutionError('Hermes native workspace turn did not complete')
                tree = usage_tree('completed')
                yield 'usage', {**accounting_usage(tree), 'native_entry_calls': 1, 'model_request_count': own.get('model_request_count')}
                yield 'metadata', {'native_usage': tree, 'native_model_request_count': own.get('model_request_count')}
        except ExecutionError:
            if own:
                yield 'metadata', {'native_usage': usage_tree('failed'),
                                   'native_model_request_count': own.get('model_request_count')}
            raise
        except TimeoutError:
            raise ExecutionError('Hermes native workspace timed out; owned process was stopped') from None
        except (OSError, ValueError, KeyError):
            raise ExecutionError('Hermes native workspace bridge is unavailable or incompatible') from None
        finally:
            if process:
                await asyncio.shield(stop_group(process))
            if stderr:
                stderr.cancel()
                await asyncio.gather(stderr, return_exceptions=True)
