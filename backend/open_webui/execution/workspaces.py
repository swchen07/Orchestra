"""Host-owned coding leases, immutable Git artifacts and separately scoped evaluation.

The local backend separates state and enforces this API's ownership and paths.
It is trusted local execution, not a security sandbox for native processes.
"""

import asyncio
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import time
import uuid

from open_webui.execution.claude import ExecutionError
from open_webui.execution.workspace_process import execute_command

HEX = re.compile(r'^[a-f0-9]{40,64}$')
ID = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]{0,63}$')
MAX_BYTES = 16 * 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def relative_path(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise ExecutionError('Use a safe relative workspace path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in {'', '.', '..', '.git', '.workspace-inputs', '.workspace-runtime'} for p in value.split('/')):
        raise ExecutionError('Unsafe workspace path')
    return path.as_posix()


def safe_file(root, value, *, exists=False):
    path = Path(root) / relative_path(value)
    current = Path(root)
    for part in PurePosixPath(value).parts:
        current = current / part
        if current.is_symlink():
            raise ExecutionError('Symlinks are not supported in managed artifacts')
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ExecutionError('Workspace path escaped its root')
    if exists and not path.exists():
        raise ExecutionError('The requested workspace file is unavailable')
    return path


def git(directory, *args, check=True, binary=False, input_bytes=None):
    directory = Path(directory).resolve()
    metadata = directory / '.git'
    if metadata.exists() or metadata.is_symlink():
        if metadata.is_symlink() or not metadata.is_dir():
            raise ExecutionError('Managed Git metadata must remain a private directory')
        for name in ('config', 'index', 'HEAD', 'objects', 'refs', 'logs'):
            if (metadata / name).is_symlink():
                raise ExecutionError('Managed Git metadata cannot point outside its lease')
    git_scope = ['--git-dir', str(metadata), '--work-tree', str(directory)] if metadata.is_dir() and args and args[0] not in {'clone', 'init'} else []
    environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0',
        GIT_AUTHOR_NAME='Workspace host', GIT_AUTHOR_EMAIL='workspace@localhost',
        GIT_COMMITTER_NAME='Workspace host', GIT_COMMITTER_EMAIL='workspace@localhost')
    result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
        '-c', 'core.autocrlf=false', '-c', 'core.fsmonitor=false', '-c', 'credential.helper=',
        '-C', str(directory), *git_scope, *args], capture_output=True, env=environment, timeout=30, input=input_bytes)
    if check and result.returncode:
        raise ExecutionError('Host Git operation failed: ' + args[0])
    if binary:
        return result.stdout
    return result.stdout.decode(errors='replace').strip() if check else result


def tree_entries(repository, revision):
    """Two bounded Git processes for a complete tree, regardless of file count."""
    listing = git(repository, 'ls-tree', '-rlz', '--full-tree', revision, binary=True)
    rows, total = [], 0
    for row in filter(None, listing.split(b'\0')):
        header, raw_name = row.split(b'\t', 1)
        mode, kind, identity, size = header.decode().split()
        if mode not in {'100644', '100755'} or kind != 'blob' or not size.isdecimal():
            raise ExecutionError('Git tree contains symlinks or unsupported entries')
        total += int(size)
        if total > MAX_BYTES:
            raise ExecutionError('Git tree exceeds the 16 MiB workspace limit')
        rows.append((relative_path(raw_name.decode()), mode, identity, int(size)))
    identities = list(dict.fromkeys(row[2] for row in rows))
    packed = git(repository, 'cat-file', '--batch', binary=True,
                 input_bytes=('\n'.join(identities) + ('\n' if identities else '')).encode())
    blobs, cursor = {}, 0
    for identity in identities:
        end = packed.find(b'\n', cursor)
        if end < 0:
            raise ExecutionError('Git object batch is incomplete')
        header = packed[cursor:end].decode().split()
        if len(header) != 3 or header[0] != identity or header[1] != 'blob' or not header[2].isdecimal():
            raise ExecutionError('Git object batch is invalid')
        size = int(header[2])
        start = end + 1
        if start + size >= len(packed) or packed[start + size:start + size + 1] != b'\n':
            raise ExecutionError('Git object bytes are incomplete')
        blobs[identity] = packed[start:start + size]
        cursor = start + size + 1
    return [(name, mode, blobs[identity]) for name, mode, identity, size in rows]


def validate_policy(value, directory):
    if value is None or value == {}:
        return None
    if not isinstance(value, dict) or set(value) - {'enabled', 'backend', 'baseline_revision', 'commands',
            'command_backend', 'docker_image', 'hermes_profile', 'hermes_python', 'hermes_source'}:
        raise ValueError('Invalid Project workspace configuration')
    if type(value.get('enabled', False)) is not bool:
        raise ValueError('Workspace enabled must be boolean')
    if not value.get('enabled'):
        return {'enabled': False}
    if value.get('backend', 'local-worktree') != 'local-worktree':
        raise ValueError('Native coding currently supports only trusted local-worktree; sandbox requests are unavailable')
    revision = value.get('baseline_revision')
    if not isinstance(revision, str) or not HEX.fullmatch(revision):
        raise ValueError('Pin a full Git baseline commit')
    if not directory:
        raise ValueError('Coding workspaces require an authorized Project Git directory')
    try:
        if git(directory, 'rev-parse', '--show-toplevel') != str(Path(directory).resolve()):
            raise ExecutionError('Select the Git repository root')
        if git(directory, 'status', '--porcelain', '--untracked-files=all'):
            raise ExecutionError('Coding source repository is dirty; provide a clean pinned baseline')
        if git(directory, 'rev-parse', '--verify', revision + '^{commit}') != revision:
            raise ExecutionError('The baseline commit is unavailable')
    except (OSError, subprocess.SubprocessError, ExecutionError) as error:
        raise ValueError(str(error)) from None
    commands = value.get('commands', [])
    if not isinstance(commands, list) or len(commands) > 20:
        raise ValueError('Use at most 20 named workspace commands')
    parsed, seen = [], set()
    for row in commands:
        if (not isinstance(row, dict) or set(row) - {'id', 'argv', 'timeout_seconds', 'authoritative_paths'}
                or not isinstance(row.get('id'), str) or not ID.fullmatch(row['id']) or row['id'] in seen
                or not isinstance(row.get('argv'), list) or not 1 <= len(row['argv']) <= 40
                or any(not isinstance(p, str) or not p or len(p) > 8000 or '\x00' in p for p in row['argv'])
                or type(row.get('timeout_seconds', 60)) is not int or not 1 <= row.get('timeout_seconds', 60) <= 300
                or not isinstance(row.get('authoritative_paths', []), list)):
            raise ValueError('Invalid named workspace command')
        try:
            paths = [relative_path(p) for p in row.get('authoritative_paths', [])]
        except ExecutionError as error:
            raise ValueError(str(error)) from None
        seen.add(row['id'])
        parsed.append({'id': row['id'], 'argv': list(row['argv']), 'timeout_seconds': row.get('timeout_seconds', 60),
                       'authoritative_paths': paths})
    command_backend = value.get('command_backend', 'local-worktree')
    if command_backend not in {'local-worktree', 'docker'}:
        raise ValueError('Unknown host command backend')
    image = value.get('docker_image', 'python:3.14-slim')
    if not isinstance(image, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]{0,200}', image):
        raise ValueError('Use an administrator-approved Docker image')
    result = {'enabled': True, 'backend': 'local-worktree', 'baseline_revision': revision,
              'commands': parsed, 'command_backend': command_backend, 'docker_image': image}
    for key in ('hermes_profile', 'hermes_python', 'hermes_source'):
        if value.get(key):
            if not isinstance(value[key], str):
                raise ValueError('Hermes runtime/profile paths must be text')
            path = Path(value[key]).expanduser().resolve()
            if not path.exists() or (key != 'hermes_python' and not path.is_dir()):
                raise ValueError('The configured Hermes runtime/profile path is unavailable')
            result[key] = str(path)
    return result


def receipts(value):
    result = []
    def visit(current):
        if isinstance(current, dict):
            if current.get('kind') == 'workspace-artifact':
                result.append(current)
            else:
                for item in current.values():
                    visit(item)
        elif isinstance(current, list):
            for item in current:
                visit(item)
    visit(value)
    return list({r['id']: r for r in result}.values())


class WorkspaceService:
    def __init__(self, project, owner, root=None):
        self.project = copy.deepcopy(project)
        self.policy = validate_policy(project.get('workspace'), project.get('directory'))
        if not self.policy or not self.policy.get('enabled'):
            raise ExecutionError('This Project has not enabled coding workspaces')
        if not all(isinstance(owner.get(k), str) and owner[k] for k in ('user_id', 'project_id', 'chat_id')):
            raise ExecutionError('Coding workspaces require a saved owned Project chat')
        self.owner = {k: owner[k] for k in ('user_id', 'project_id', 'chat_id')}
        if root is None:
            from open_webui.env import DATA_DIR
            root = Path(DATA_DIR) / 'execution-workspaces'
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.scope = self.root / digest(self.owner)
        self.scope.mkdir(mode=0o700, exist_ok=True)
        for name in ('leases', 'artifacts', 'evaluations'):
            (self.scope / name).mkdir(mode=0o700, exist_ok=True)
        self.baseline = self.scope / ('baseline-' + digest({'directory': project['directory'], 'policy': self.policy})[:24])
        with self.lock('baseline'):
            if not self.baseline.exists():
                self._create_baseline()
        self.base_revision = git(self.baseline, 'rev-parse', 'HEAD')

    @contextmanager
    def lock(self, identity):
        path = self.scope / ('lock-' + digest(identity))
        with path.open('a') as stream:
            os.chmod(path, 0o600)
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ExecutionError('This mutable workspace is already in use') from None
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _write(self, path, value):
        temporary = path.with_suffix('.tmp-' + uuid.uuid4().hex)
        with temporary.open('x') as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def _create_baseline(self):
        source = self.project['directory']
        temporary = Path(tempfile.mkdtemp(prefix='baseline-', dir=self.scope))
        try:
            # Export only tracked regular files from the pinned commit, never source config/history/hooks.
            rows = git(source, 'ls-tree', '-rz', '--full-tree', self.policy['baseline_revision'], binary=True).split(b'\0')
            total = 0
            for row in filter(None, rows):
                header, raw_name = row.split(b'\t', 1)
                mode, kind, identity = header.decode().split()
                name = raw_name.decode()
                if mode not in {'100644', '100755'} or kind != 'blob':
                    raise ExecutionError('Pinned baseline contains symlinks or unsupported Git entries')
                path = safe_file(temporary, name)
                data = git(source, 'cat-file', 'blob', identity, binary=True)
                total += len(data)
                if total > MAX_BYTES:
                    raise ExecutionError('Baseline exceeds the 16 MiB coding workspace limit')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                os.chmod(path, 0o755 if mode == '100755' else 0o644)
            git(temporary, 'init', '--quiet')
            git(temporary, 'add', '--all')
            git(temporary, 'commit', '--quiet', '--allow-empty', '-m', 'Pinned Project baseline')
            temporary.rename(self.baseline)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _path(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity):
            raise ExecutionError('Unknown workspace lease')
        path = self.scope / 'leases' / identity
        if not path.is_dir() or path.is_symlink():
            raise ExecutionError('Workspace lease is unavailable')
        return path

    def load(self, identity, instance=None):
        path = self._path(identity)
        try:
            value = json.loads((path / 'lease.json').read_text())
        except (OSError, ValueError):
            raise ExecutionError('Workspace checkpoint is unavailable') from None
        if value.get('owner') != self.owner or instance is not None and value.get('instance_id') != instance:
            raise ExecutionError('Workspace lease belongs to another owner or instance')
        if value.get('source_baseline') != self.policy['baseline_revision'] or value.get('base_revision') != self.base_revision:
            raise ExecutionError('Workspace baseline changed; select a new chat')
        return value

    def public(self, lease):
        return {k: copy.deepcopy(lease[k]) for k in ('id', 'instance_id', 'source_baseline', 'base_revision',
            'current_revision', 'generation', 'backend', 'guarantees', 'status', 'conflicts', 'pending_artifacts') if k in lease}

    def checkout(self, lease):
        path = self._path(lease['id']) / 'work'
        if path.is_symlink() or not path.is_dir():
            raise ExecutionError('Workspace checkout must remain a private owned directory')
        return path

    def runtime(self, lease):
        path = self._path(lease['id']) / 'runtime'
        if path.is_symlink() or not path.is_dir():
            raise ExecutionError('Workspace runtime must remain a private owned directory')
        return path

    def acquire(self, instance, previous=None):
        if not isinstance(instance, str) or not instance or len(instance) > 600:
            raise ExecutionError('Invalid qualified workspace instance')
        if previous is not None and (not isinstance(previous, dict) or not isinstance(previous.get('id'), str) or not isinstance(previous.get('current_revision'), str)):
            raise ExecutionError('Invalid workspace checkpoint receipt')
        old = self.load(previous['id'], instance) if previous else None
        if old and previous.get('current_revision') == old['current_revision']:
            if git(self.checkout(old), 'rev-parse', 'HEAD') != old['current_revision']:
                raise ExecutionError('Workspace revision was changed outside its host checkpoint')
            if old.get('status') == 'closed':
                old['status'] = 'ready'
                self._write(self._path(old['id']) / 'lease.json', old)
            return old, False
        identity = uuid.uuid4().hex
        path = self.scope / 'leases' / identity
        path.mkdir(mode=0o700)
        (path / 'runtime').mkdir(mode=0o700)
        origin = self.checkout(old) if old else self.baseline
        revision = previous.get('current_revision') if old else self.base_revision
        if not isinstance(revision, str) or not HEX.fullmatch(revision):
            raise ExecutionError('Invalid workspace checkpoint revision')
        git(path, 'clone', '--quiet', '--local', '--no-hardlinks', '--no-checkout', '--', str(origin), str(path / 'work'))
        git(path / 'work', 'checkout', '--quiet', '--detach', revision)
        lease = {'id': identity, 'owner': self.owner, 'instance_id': instance,
            'source_baseline': self.policy['baseline_revision'], 'base_revision': self.base_revision,
            'current_revision': revision, 'generation': 1, 'backend': 'local-worktree',
            'guarantees': {'private_git_tree': True, 'managed_api_ownership': True, 'os_sandbox': False,
                           'network_restriction': False, 'native_execution': 'trusted-local'},
            'status': 'ready', 'created_at': int(time.time() * 1000), 'consumed_artifacts': [], 'conflicts': [], 'pending_artifacts': []}
        if old:
            lease['forked_from'] = self.public(old)
        self._write(path / 'lease.json', lease)
        return lease, bool(old)

    def close(self, lease):
        with self.lock(lease['id']):
            canonical = self.load(lease['id'], lease['instance_id'])
            if canonical['current_revision'] != lease['current_revision']:
                raise ExecutionError('Cannot close a stale workspace revision')
            canonical.update(status='closed', closed_at=int(time.time() * 1000))
            self._write(self._path(lease['id']) / 'lease.json', canonical)
            return self.public(canonical)

    def verify_files(self, directory):
        rows, total = [], 0
        for parent, dirs, names in os.walk(directory, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d != '__pycache__' and not (Path(parent) == Path(directory) and d == '.git'))
            for name in [*dirs, *sorted(n for n in names if not n.endswith(('.pyc', '.pyo')))]:
                path = Path(parent) / name
                rel = path.relative_to(directory).as_posix()
                safe_file(directory, rel)
                if path.is_dir():
                    continue
                if not path.is_file() or path.stat().st_nlink > 1:
                    raise ExecutionError('Only private regular files may be published')
                size = path.stat().st_size
                total += size
                if total > MAX_BYTES:
                    raise ExecutionError('Workspace exceeds the 16 MiB publication limit')
                rows.append({'path': rel, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                             'size': size, 'media_type': mimetypes.guess_type(name)[0] or 'application/octet-stream'})
        return rows

    def snapshot(self, lease, *, provenance=None):
        path, work = self._path(lease['id']), self.checkout(lease)
        canonical = self.load(lease['id'], lease['instance_id'])
        if canonical != lease:
            raise ExecutionError('Stale mutable workspace revision')
        rows = self.verify_files(work)
        unresolved = list(dict.fromkeys([*lease.get('conflicts', []), *git(work, 'diff', '--name-only', '--diff-filter=U').splitlines()]))
        # A resolver edits all conflict files. Host stages only when no conflict markers remain.
        if unresolved:
            for name in unresolved:
                data = safe_file(work, name, exists=True).read_bytes()
                if any(line.startswith((b'<<<<<<< ', b'=======', b'>>>>>>> ')) for line in data.splitlines()):
                    raise ExecutionError('Unresolved Git conflict blocks publication')
        before = lease['current_revision']
        # Generated bytecode is private runtime output, not a source artifact.
        # Old immutable artifacts keep their original bytes and remain verifiable.
        git(work, 'rm', '--cached', '--ignore-unmatch', '--', '*.pyc', '*.pyo', ':(glob)**/__pycache__/**')
        git(work, 'add', '--all', '--force', '--', '.', ':(glob,exclude)**/__pycache__/**',
            ':(glob,exclude)**/*.pyc', ':(glob,exclude)**/*.pyo')
        if git(work, 'status', '--porcelain') or (work / '.git/MERGE_HEAD').exists():
            git(work, 'commit', '--quiet', '--allow-empty', '-m', 'Host workspace snapshot')
        revision = git(work, 'rev-parse', 'HEAD')
        changed = git(work, 'diff', '--name-only', self.base_revision, revision).splitlines()
        lease.update(current_revision=revision, generation=lease['generation'] + int(revision != before),
                     status='ready', conflicts=[])
        self._write(path / 'lease.json', lease)
        identity = uuid.uuid4().hex
        receipt = {'kind': 'workspace-artifact', 'id': identity, 'producer': lease['instance_id'],
            'lease_id': lease['id'], 'source_baseline': lease['source_baseline'], 'base_revision': self.base_revision,
            'previous_revision': before, 'revision': revision, 'tree': git(work, 'rev-parse', revision + '^{tree}'),
            'files': rows, 'changed_paths': changed, 'sha256': digest(rows),
            'size': sum(r['size'] for r in rows), 'media_type': 'application/vnd.open-webui.git-tree',
            'created_at': int(time.time() * 1000), 'provenance': copy.deepcopy(provenance or {}),
            'publication_filter': {'generated_python_bytecode': 'excluded'}}
        artifact = self.scope / 'artifacts' / identity
        artifact.mkdir(mode=0o700)
        git(artifact, 'clone', '--quiet', '--local', '--no-hardlinks', '--bare', '--', str(work), str(artifact / 'tree.git'))
        self._write(artifact / 'receipt.json', receipt)
        return self.public(lease), receipt

    def _artifact(self, receipt, *, granted):
        if not isinstance(receipt, dict) or receipt.get('id') not in granted:
            raise ExecutionError('Artifact was not authorized by declared inputs')
        identity = receipt.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity):
            raise ExecutionError('Unknown artifact reference')
        path = self.scope / 'artifacts' / identity
        try:
            stored = json.loads((path / 'receipt.json').read_text())
        except (OSError, ValueError):
            raise ExecutionError('Artifact is unknown in this Project chat') from None
        if receipt != stored or digest(stored['files']) != stored['sha256']:
            raise ExecutionError('Artifact receipt/hash mismatch')
        producer = self.load(stored['lease_id'], stored['producer'])
        if git(path / 'tree.git', 'rev-parse', stored['revision'] + '^{tree}') != stored['tree']:
            raise ExecutionError('Artifact Git snapshot hash mismatch')
        entries = tree_entries(path / 'tree.git', stored['revision'])
        actual = {name: data for name, mode, data in entries}
        if set(actual) != {r['path'] for r in stored['files']}:
            raise ExecutionError('Artifact file manifest does not match its Git tree')
        for row in stored['files']:
            data = actual[row['path']]
            if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                raise ExecutionError('Artifact content hash mismatch')
        return stored, path / 'tree.git', entries

    def artifact(self, receipt, *, granted):
        stored, repository, entries = self._artifact(receipt, granted=granted)
        return stored, repository

    def materialize_inputs(self, lease, selected):
        if not selected:
            return []
        # Shared inputs are separate from candidate publication and immutable in managed APIs.
        root = self.runtime(lease) / 'inputs'
        if root.is_symlink():
            raise ExecutionError('Materialized input root cannot be a symlink')
        root.mkdir(exist_ok=True)
        grants = {r['id'] for r in selected}
        exposed = []
        for receipt in selected:
            stored, repository, entries = self._artifact(receipt, granted=grants)
            destination = root / stored['id']
            if destination.is_symlink():
                raise ExecutionError('Materialized artifact directory was replaced by a symlink')
            if destination.exists():
                self._verify_input_copy(destination, stored)
            else:
                destination.mkdir(mode=0o755)
                self._export_entries(entries, destination)
                for parent, dirs, names in os.walk(destination):
                    for name in names:
                        os.chmod(Path(parent) / name, 0o444)
                    os.chmod(parent, 0o555)
            exposed.append({'artifact_id': stored['id'], 'directory': str(destination), 'sha256': stored['sha256'],
                            'permissions': 'read-only-copy; trusted local process can bypass chmod'})
        return exposed

    def _verify_input_copy(self, directory, receipt):
        if not directory.is_dir() or directory.is_symlink():
            raise ExecutionError('Materialized artifact directory is unavailable')
        expected = {row['path']: row for row in receipt['files']}
        seen = set()
        for parent, dirs, names in os.walk(directory, followlinks=False):
            for name in [*dirs, *names]:
                path = Path(parent) / name
                relative = path.relative_to(directory).as_posix()
                safe_file(directory, relative)
                if path.stat().st_mode & 0o222:
                    raise ExecutionError('Materialized artifact permissions were changed')
                if path.is_dir():
                    continue
                row = expected.get(relative)
                if (not path.is_file() or row is None or path.stat().st_nlink != 1
                        or path.stat().st_size != row['size'] or hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']):
                    raise ExecutionError('Materialized artifact bytes no longer match the canonical receipt')
                seen.add(relative)
        if seen != set(expected) or directory.stat().st_mode & 0o222:
            raise ExecutionError('Materialized artifact copy is incomplete or writable')

    def _export(self, repository, revision, destination):
        self._export_entries(tree_entries(repository, revision), destination)

    def _export_entries(self, entries, destination):
        for name, mode, data in entries:
            path = safe_file(destination, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.chmod(path, 0o755 if mode == '100755' else 0o644)

    def publish(self, lease, *, provenance=None):
        canonical = self.load(lease['id'], lease['instance_id'])
        if canonical != lease:
            raise ExecutionError('Stale mutable workspace revision')
        work = self.checkout(lease)
        if git(work, 'rev-parse', 'HEAD') != lease['current_revision']:
            raise ExecutionError('Workspace revision was changed outside its host checkpoint')
        rows = self.verify_files(work)
        unchanged_index = git(work, 'diff', '--quiet', 'HEAD', '--', check=False).returncode == 0
        if not lease.get('conflicts') and unchanged_index and not (work / '.git/MERGE_HEAD').exists():
            candidates = []
            for path in (self.scope / 'artifacts').glob('*/receipt.json'):
                try:
                    receipt = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                if (receipt.get('lease_id') == lease['id'] and receipt.get('producer') == lease['instance_id']
                        and receipt.get('revision') == lease['current_revision'] and receipt.get('sha256') == digest(rows)):
                    candidates.append(receipt)
            if candidates:
                # Keep the most recently attributed canonical producer receipt.
                receipt = max(candidates, key=lambda item: (item.get('created_at', 0), item['id']))
                self.artifact(receipt, granted={receipt['id']})
                return self.public(lease), receipt, True
        public, artifact = self.snapshot(lease, provenance=provenance)
        return public, artifact, False

    def command(self, identity):
        found = next((r for r in self.policy['commands'] if r['id'] == identity), None)
        if not found:
            raise ExecutionError('Unknown administrator-approved workspace command')
        return found

    async def evaluate(self, receipt, command_ids, *, granted, record=None):
        stored, repository, entries = await asyncio.to_thread(self._artifact, receipt, granted=granted)
        if not command_ids:
            raise ExecutionError('Evaluation requires an authoritative command')
        commands = [self.command(identity) for identity in command_ids]
        if any(not c['authoritative_paths'] for c in commands):
            raise ExecutionError('Evaluation commands require pinned authoritative check paths')
        identity = uuid.uuid4().hex
        root = self.scope / 'evaluations' / identity
        root.mkdir(mode=0o700)
        work = root / 'work'
        work.mkdir(mode=0o755)
        await asyncio.to_thread(self._export_entries, entries, work)
        checks = []
        check_root = root / 'checks'
        check_root.mkdir(mode=0o755)
        check_mounts = {}
        for command in commands:
            for name in command['authoritative_paths']:
                if name in check_mounts:
                    continue
                destination = safe_file(work, name)
                if destination.is_dir():
                    shutil.rmtree(destination)
                elif destination.exists():
                    destination.unlink()
                # Restore exact baseline check directory/file and descendants.
                baseline_rows = git(self.baseline, 'ls-tree', '-rz', '--full-tree', 'HEAD', '--', name, binary=True).split(b'\0')
                if not any(baseline_rows):
                    raise ExecutionError('Authoritative check path is unavailable in pinned baseline')
                for row in filter(None, baseline_rows):
                    header, raw_name = row.split(b'\t', 1)
                    mode, kind, blob = header.decode().split()
                    if mode not in {'100644', '100755'} or kind != 'blob':
                        raise ExecutionError('Unsafe authoritative check entry')
                    target = safe_file(work, raw_name.decode())
                    target.parent.mkdir(parents=True, exist_ok=True)
                    data = git(self.baseline, 'cat-file', 'blob', blob, binary=True)
                    target.write_bytes(data)
                    os.chmod(target, 0o755 if mode == '100755' else 0o644)
                    immutable = safe_file(check_root, raw_name.decode())
                    immutable.parent.mkdir(parents=True, exist_ok=True)
                    if not immutable.exists():
                        immutable.write_bytes(data)
                        os.chmod(immutable, 0o555 if mode == '100755' else 0o444)
                    checks.append({'path': raw_name.decode(), 'sha256': hashlib.sha256(data).hexdigest()})
                check_mounts[name] = str(safe_file(check_root, name, exists=True))
        # Docker's non-root process may write only this disposable checkout, never host receipts.
        for parent, dirs, names in os.walk(work):
            os.chmod(parent, 0o777)
            for name in names:
                file = Path(parent) / name
                os.chmod(file, 0o777 if file.stat().st_mode & 0o111 else 0o666)
        checks = sorted({r['path']: r for r in checks}.values(), key=lambda row: row['path'])
        check_mounts = {target: source for target, source in check_mounts.items()
                        if not any(target.startswith(parent + '/') for parent in check_mounts if parent != target)}
        result = {'kind': 'workspace-evaluation', 'id': identity, 'artifact_id': stored['id'],
            'candidate_revision': stored['revision'], 'candidate_sha256': stored['sha256'],
            'checks_sha256': digest(checks), 'checks': checks, 'commands': [], 'status': 'failed'}
        try:
            for command in commands:
                await execute_command(work, command, backend=self.policy['command_backend'],
                    image=self.policy['docker_image'], runtime=result['commands'],
                    read_only_mounts=[(source, target) for target, source in check_mounts.items()])
            result['status'] = 'passed' if all(c['status'] == 'passed' for c in result['commands']) else 'failed'
        except asyncio.CancelledError:
            result['status'] = 'cancelled'
            raise
        finally:
            result['result_sha256'] = digest(self.verify_files(work))
            self._write(root / 'receipt.json', result)
            if record is not None:
                record.append(copy.deepcopy(result))
        return result

    def integrate(self, instance, selected, strategy, *, previous=None, granted):
        if strategy not in {'select', 'merge'} or not selected or strategy == 'select' and len(selected) != 1:
            raise ExecutionError('Select one alternative, or explicitly merge selected compatible changes')
        validated = [self.artifact(r, granted=granted) for r in selected]
        lease, forked = self.acquire(instance, previous)
        with self.lock(lease['id']):
            work = self.checkout(lease)
            if lease.get('conflicts') or git(work, 'status', '--porcelain'):
                raise ExecutionError('Resolve or publish the current integration before applying another artifact')
            selection = [receipt['id'] for receipt, repository in validated]
            if not set(lease.get('pending_artifacts', [])) <= set(selection):
                raise ExecutionError('Every pending contributor must remain explicitly selected when resuming integration')
            retained, remaining = [], []
            for receipt, repository in validated:
                if receipt['id'] in lease['consumed_artifacts']:
                    present = git(work, 'merge-base', '--is-ancestor', receipt['revision'], 'HEAD', check=False)
                    if present.returncode:
                        raise ExecutionError('Previously applied artifact is missing from integration ancestry')
                    retained.append(receipt['id'])
                else:
                    remaining.append((receipt, repository))
            lease['integration_selection'] = selection
            lease['pending_artifacts'] = [receipt['id'] for receipt, repository in remaining]
            self._write(self._path(lease['id']) / 'lease.json', lease)
            for index, (receipt, repository) in enumerate(remaining):
                git(work, 'fetch', '--quiet', str(repository), receipt['revision'])
                before = lease['current_revision']
                result = git(work, 'merge', '--no-edit', '--no-ff', receipt['revision'], check=False)
                conflicts = git(work, 'diff', '--name-only', '--diff-filter=U').splitlines()
                lease['consumed_artifacts'].append(receipt['id'])
                lease['pending_artifacts'] = [row['id'] for row, repo in remaining[index + 1:]]
                if result.returncode:
                    if not conflicts:
                        raise ExecutionError('Git integration failed without a resolvable conflict')
                    lease.update(status='conflict', conflicts=conflicts,
                        merge_evidence={'incoming_artifact': receipt['id'], 'ours': before, 'theirs': receipt['revision']})
                    self._write(self._path(lease['id']) / 'lease.json', lease)
                    return {'status': 'conflict', '__workspace': self.public(lease), '__artifacts': [],
                            'conflicts': conflicts, 'pending_artifacts': list(lease['pending_artifacts']), 'retained_artifacts': retained,
                            'report': 'Git merge conflict requires resolution, remaining selected contributors, and retesting.'}
                lease['current_revision'] = git(work, 'rev-parse', 'HEAD')
                lease['generation'] += 1
                self._write(self._path(lease['id']) / 'lease.json', lease)
            provenance = {'action': 'artifact.integrate', 'strategy': strategy,
                          'selected_artifacts': selection, 'retained_artifacts': retained}
            if remaining:
                public, artifact = self.snapshot(lease, provenance=provenance)
                unchanged = False
            else:
                public, artifact, unchanged = self.publish(lease, provenance=provenance)
            return {'status': 'integrated', '__workspace': public, '__artifacts': [artifact], 'conflicts': [],
                    'retained': unchanged, 'retained_artifacts': retained, 'pending_artifacts': [],
                    'report': 'Selected immutable revisions integrated; previously committed exact receipts retained idempotently.'}
