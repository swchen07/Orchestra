"""Owned command processes. Docker scopes host grants; local execution is trusted."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from open_webui.execution.claude import ExecutionError, stop_group


def command_digest(command):
    return hashlib.sha256(json.dumps(command, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def docker_available():
    executable = shutil.which('docker')
    if not executable:
        raise ExecutionError('Docker command backend is unavailable; no command was admitted')
    try:
        import subprocess
        result = subprocess.run([executable, 'info', '--format', '{{.ServerVersion}}'], capture_output=True, timeout=10)
        if result.returncode or not result.stdout.strip():
            raise ExecutionError('Docker engine is unavailable; no command was admitted')
    except (OSError, subprocess.TimeoutExpired):
        raise ExecutionError('Docker engine is unavailable; no command was admitted') from None
    return executable


def docker_capability(image):
    """Read-only admission probe; never pull images or start a container."""
    executable = docker_available()
    if not image:
        raise ExecutionError('Choose an administrator-approved Docker image')
    import subprocess
    try:
        inspected = subprocess.run([executable, 'image', 'inspect', image, '--format', '{{.Id}}'],
                                   capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise ExecutionError('The approved Docker image could not be inspected') from None
    image_id = inspected.stdout.strip()
    if inspected.returncode or not __import__('re').fullmatch(r'sha256:[a-f0-9]{64}', image_id):
        raise ExecutionError('The approved Docker image is unavailable locally; no command was admitted')
    return {'executable': executable, 'image': image, 'image_id': image_id, 'backend': 'docker'}


async def execute_command(directory, command, *, backend='local-worktree', image=None, runtime=None, read_only_mounts=()):
    """Execute only a previously validated administrator-owned command definition."""
    directory = Path(directory).resolve(strict=True)
    started = int(time.time() * 1000)
    invocation = uuid.uuid4().hex
    container = 'owui-eval-' + invocation
    argv = list(command['argv'])
    environment = {k: v for k, v in os.environ.items() if k in {'PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT'}}
    environment.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    actual = {'backend': backend, 'filesystem': 'trusted-local', 'network': 'host-owned', 'os_sandbox': False}
    docker = None
    if backend == 'docker':
        capability = await asyncio.to_thread(docker_capability, image)
        docker, image_id = capability['executable'], capability['image_id']
        argv = [docker, 'run', '--label', 'open-webui.workspace-goal=20261110', '--name', container, '--rm', '--init', '--network', 'none', '--read-only',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '64',
                '--memory', '256m', '--cpus', '1', '--user', '65534:65534',
                '--tmpfs', '/tmp:rw,nosuid,nodev,size=32m', '--mount', f'type=bind,src={directory},dst=/workspace',
                '--workdir', '/workspace', '--env', 'PYTHONDONTWRITEBYTECODE=1',
                *[argument for source, target in read_only_mounts for argument in ('--mount', f'type=bind,src={source},dst=/workspace/{target},readonly')], image_id, *argv]
        actual = {'backend': 'docker', 'image': image, 'image_id': image_id, 'filesystem': 'only-this-scratch-mounted',
                  'network': 'none', 'os_sandbox': True, 'authority': 'Docker engine'}
    elif backend != 'local-worktree':
        raise ExecutionError('Unsupported host command backend; no command was admitted')
    process = None
    streams = []
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    truncated = {'stdout': False, 'stderr': False}

    async def drain(name, stream):
        while data := await stream.read(8192):
            left = 128 * 1024 - len(output[name])
            output[name].extend(data[:max(0, left)])
            truncated[name] |= len(data) > left

    receipt = {'id': invocation, 'command_id': command['id'], 'command_sha256': command_digest(command),
               'argv': command['argv'], 'started_at': started, 'environment': actual, 'status': 'failed'}
    try:
        process = await asyncio.create_subprocess_exec(*argv, cwd=str(directory), env=environment,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        streams = [asyncio.create_task(drain('stdout', process.stdout)), asyncio.create_task(drain('stderr', process.stderr))]
        try:
            async with asyncio.timeout(command['timeout_seconds']):
                await process.wait()
                # Stop same-group descendants before collecting pipe EOF; otherwise
                # a successful parent could leave background writers or open pipes.
                await stop_group(process)
                await asyncio.gather(*streams)
            receipt.update(exit_code=process.returncode, status='passed' if process.returncode == 0 else 'failed')
        except TimeoutError:
            receipt.update(status='timeout', exit_code=None)
        except asyncio.CancelledError:
            receipt.update(status='cancelled', exit_code=None)
            raise
    except OSError:
        receipt.update(error='The configured command could not start', exit_code=None)
    finally:
        if process:
            await asyncio.shield(stop_group(process))
        if docker:
            # Killing the client does not reliably stop its container.
            cleanup = await asyncio.create_subprocess_exec(docker, 'rm', '-f', container,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.shield(cleanup.wait())
        if streams:
            for task in streams:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*streams, return_exceptions=True)
        receipt.update(finished_at=int(time.time() * 1000),
            stdout=bytes(output['stdout']).decode(errors='replace'), stderr=bytes(output['stderr']).decode(errors='replace'),
            truncated=truncated, output_bytes=sum(len(v) for v in output.values()))
        receipt['elapsed_ms'] = receipt['finished_at'] - started
        if runtime is not None:
            runtime.append(receipt)
    return receipt
