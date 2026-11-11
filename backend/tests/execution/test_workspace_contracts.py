"""Portable workspace contracts: real files/Git/processes, no model or app data.

Set WORKSPACE_DOCKER_TEST_IMAGE to an already installed trusted Python image
for the separately opted-in Docker confinement probe.
"""

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from open_webui.execution import bridge
from open_webui.execution.claude import ExecutionError
from open_webui.execution.hermes_workspace import HermesWorkspaceAdapter
from open_webui.execution.module_environment import narrow_requests, resolve_environment
from open_webui.execution.workspaces import WorkspaceService, git, safe_file
from open_webui.execution.workspace_process import execute_command


class WorkspaceFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'code.py').write_text('value = 1\n\n# unchanged\n# unchanged\n# unchanged\n\nother = 1\n')
        (self.source / 'check.py').write_text('from code import value\nassert value == 2\n')
        (self.source / 'check.sh').write_text('#!/bin/sh\nexit 0\n')
        (self.source / 'check.sh').chmod(0o755)
        git(self.source, 'init', '--quiet')
        git(self.source, 'add', '--all')
        git(self.source, 'commit', '--quiet', '-m', 'Test baseline')
        self.project = {'id': 'project', 'name': 'Test Project', 'directory': str(self.source), 'workspace': {
            'enabled': True, 'baseline_revision': git(self.source, 'rev-parse', 'HEAD'), 'commands': [
                {'id': 'check', 'argv': [sys.executable, 'check.py'], 'authoritative_paths': ['check.py']},
                {'id': 'shell_one', 'argv': ['./check.sh'], 'authoritative_paths': ['check.sh']},
                {'id': 'shell_two', 'argv': ['./check.sh'], 'authoritative_paths': ['check.sh']},
            ]}}
        self.owner = {'user_id': 'owner', 'project_id': 'project', 'chat_id': 'chat'}
        self.service = WorkspaceService(self.project, self.owner, root=self.root / 'managed')

    def tearDown(self):
        self.temporary.cleanup()

    def candidate(self, instance, value):
        lease, _ = self.service.acquire(instance)
        (self.service.checkout(lease) / 'code.py').write_text(f'value = {value}\n')
        public, artifact = self.service.snapshot(lease, provenance={'invocation_id': instance})
        return lease, public, artifact


class WorkspaceContracts(WorkspaceFixture):
    def test_private_git_trees_and_immutable_bytes_survive_continuation_and_fork(self):
        lease, pinned, artifact = self.candidate('first', 2)
        peer, _, peer_artifact = self.candidate('second', 3)
        self.assertNotEqual(lease['id'], peer['id'])
        self.assertEqual((self.source / 'code.py').read_text().splitlines()[0], 'value = 1')
        (self.service.checkout(lease) / 'code.py').write_text('value = 4\n')
        self.service.snapshot(lease)
        fork, forked = self.service.acquire('first', pinned)
        self.assertTrue(forked)
        self.assertEqual((self.service.checkout(fork) / 'code.py').read_text(), 'value = 2\n')
        cold = WorkspaceService(self.project, self.owner, root=self.root / 'managed')
        continued, forked = cold.acquire('second', self.service.public(peer))
        self.assertFalse(forked)
        self.assertEqual(continued['id'], peer['id'])
        for receipt in (artifact, peer_artifact):
            stored, repository = self.service.artifact(receipt, granted={receipt['id']})
            self.assertEqual(git(repository, 'show', stored['revision'] + ':code.py'),
                             'value = ' + ('2' if stored['producer'] == 'first' else '3'))

    def test_forged_hash_wrong_owner_and_unsafe_paths_are_rejected(self):
        lease, _, artifact = self.candidate('writer', 2)
        forged = copy.deepcopy(artifact)
        forged['sha256'] = '0' * 64
        with self.assertRaises(ExecutionError):
            self.service.artifact(forged, granted={artifact['id']})
        with self.assertRaises(ExecutionError):
            self.service.artifact(artifact, granted=set())
        peer = WorkspaceService(self.project, {**self.owner, 'chat_id': 'other'}, root=self.root / 'managed')
        with self.assertRaises(ExecutionError):
            peer.artifact(artifact, granted={artifact['id']})
        for path in ('../escape', '/etc/passwd', 'a/../../escape', '.git/config', 'a\\b'):
            with self.subTest(path=path), self.assertRaises(ExecutionError):
                safe_file(self.service.checkout(lease), path)
        (self.service.checkout(lease) / 'escape').symlink_to(self.source / 'code.py')
        with self.assertRaises(ExecutionError):
            self.service.snapshot(lease)

    def test_noop_publication_retains_receipt_and_ignored_outputs_are_real(self):
        lease, _, artifact = self.candidate('writer', 2)
        receipt_path = self.service.scope / 'artifacts' / artifact['id'] / 'receipt.json'
        original = receipt_path.read_bytes()
        _, retained, unchanged = self.service.publish(lease, provenance={'node_id': 'publish'})
        self.assertTrue(unchanged)
        self.assertEqual(retained, artifact)
        self.assertEqual(receipt_path.read_bytes(), original)
        work = self.service.checkout(lease)
        (work / '.gitignore').write_text('result.out\n')
        (work / 'result.out').write_bytes(b'real ignored output')
        (work / '__pycache__').mkdir()
        (work / '__pycache__' / 'code.pyc').write_bytes(b'generated bytecode')
        _, fresh, unchanged = self.service.publish(lease)
        self.assertFalse(unchanged)
        self.assertNotEqual(fresh['id'], artifact['id'])
        self.assertIn('result.out', [row['path'] for row in fresh['files']])
        self.assertNotIn('__pycache__/code.pyc', [row['path'] for row in fresh['files']])
        stored, repository = self.service.artifact(fresh, granted={fresh['id']})
        self.assertEqual(git(repository, 'show', stored['revision'] + ':result.out'), 'real ignored output')
        self.service.artifact(artifact, granted={artifact['id']})
        self.assertEqual(receipt_path.read_bytes(), original)

    def test_staging_conflict_markers_does_not_resolve_a_merge(self):
        _, _, first = self.candidate('first', 2)
        _, _, second = self.candidate('second', 3)
        result = self.service.integrate('integration', [first, second], 'merge',
                                        granted={first['id'], second['id']})
        self.assertEqual(result['status'], 'conflict')
        lease = self.service.load(result['__workspace']['id'])
        git(self.service.checkout(lease), 'add', '--all')
        with self.assertRaises(ExecutionError):
            self.service.snapshot(lease)
        (self.service.checkout(lease) / 'code.py').write_text('value = 2\n')
        _, resolved = self.service.snapshot(lease)
        self.assertEqual(lease['conflicts'], [])
        self.service.artifact(resolved, granted={resolved['id']})
        repeated = self.service.integrate('integration', [first], 'merge', previous=self.service.public(lease),
                                          granted={first['id']})
        self.assertEqual(repeated['retained_artifacts'], [first['id']])
        self.assertTrue(repeated['retained'])
        self.assertEqual(repeated['__workspace']['current_revision'], lease['current_revision'])

    def test_authoritative_checks_replace_candidate_edits_and_shared_executables_work(self):
        lease, _, _ = self.candidate('writer', 2)
        (self.service.checkout(lease) / 'check.py').write_text('raise RuntimeError("candidate check")\n')
        _, artifact = self.service.snapshot(lease)
        result = asyncio.run(self.service.evaluate(artifact, ['check', 'shell_one', 'shell_two'],
                                                   granted={artifact['id']}))
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(len(result['commands']), 3)
        self.assertEqual(len(result['checks']), 2)
        self.assertEqual(result['candidate_revision'], artifact['revision'])
        self.assertEqual(result['checks_sha256'], hashlib.sha256(json.dumps(result['checks'], sort_keys=True,
                         separators=(',', ':'), ensure_ascii=False).encode()).hexdigest())

    def test_materialized_input_is_revalidated_on_each_admission(self):
        _, _, artifact = self.candidate('producer', 2)
        consumer, _ = self.service.acquire('consumer')
        exposed = self.service.materialize_inputs(consumer, [artifact])
        copied = Path(exposed[0]['directory'])
        (copied / 'code.py').chmod(0o644)
        (copied / 'code.py').write_text('tampered')
        with self.assertRaises(ExecutionError):
            self.service.materialize_inputs(consumer, [artifact])
        # A different private consumer still gets canonical bytes.
        other, _ = self.service.acquire('other')
        exposed = self.service.materialize_inputs(other, [artifact])
        copied = Path(exposed[0]['directory'])
        moved = copied.with_name('retained-copy')
        copied.rename(moved)
        copied.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ExecutionError):
            self.service.materialize_inputs(other, [artifact])

    def test_conflict_preserves_pending_selected_contributors_until_resumed(self):
        _, _, first = self.candidate('first', 2)
        _, _, second = self.candidate('second', 3)
        third, _ = self.service.acquire('third')
        (self.service.checkout(third) / 'third.txt').write_text('third contributor')
        _, last = self.service.snapshot(third)
        selected = [first, second, last]
        grants = {r['id'] for r in selected}
        conflict = self.service.integrate('integration', selected, 'merge', granted=grants)
        self.assertEqual(conflict['__workspace']['pending_artifacts'], [last['id']])
        lease = self.service.load(conflict['__workspace']['id'])
        (self.service.checkout(lease) / 'code.py').write_text('value = 2\n')
        resolved, _ = self.service.snapshot(lease)
        self.assertEqual(resolved['pending_artifacts'], [last['id']])
        complete = self.service.integrate('integration', selected, 'merge', previous=resolved, granted=grants)
        self.assertEqual(complete['__workspace']['pending_artifacts'], [])
        self.assertEqual((self.service.checkout(lease) / 'third.txt').read_text(), 'third contributor')

    def test_rework_can_replace_one_producer_while_retaining_applied_and_pending_peers(self):
        _, _, first = self.candidate('first', 2)
        writer, _, old = self.candidate('second', 3)
        third, _ = self.service.acquire('third')
        (self.service.checkout(third) / 'third.txt').write_text('pending contributor')
        _, pending = self.service.snapshot(third)
        original = [first, old, pending]
        conflict = self.service.integrate('integration', original, 'merge', granted={r['id'] for r in original})
        lease = self.service.load(conflict['__workspace']['id'])
        (self.service.checkout(lease) / 'code.py').write_text('value = 3\n')
        resolved, _ = self.service.snapshot(lease)
        (self.service.checkout(writer) / 'code.py').write_text('value = 4\n')
        _, replacement = self.service.snapshot(writer)
        desired = [first, replacement, pending]
        result = self.service.integrate('integration', desired, 'merge', previous=resolved,
                                        granted={r['id'] for r in desired})
        self.assertEqual(result['status'], 'integrated')
        self.assertEqual(result['retained_artifacts'], [first['id']])
        self.assertEqual(result['pending_artifacts'], [])
        self.assertEqual((self.service.checkout(lease) / 'code.py').read_text(), 'value = 4\n')
        self.assertEqual((self.service.checkout(lease) / 'third.txt').read_text(), 'pending contributor')

    def test_one_writer_and_strict_capability_admission(self):
        lease, _ = self.service.acquire('writer')
        with self.service.lock(lease['id']):
            with self.assertRaises(ExecutionError):
                with self.service.lock(lease['id']):
                    pass
        with self.assertRaises(ValueError):
            narrow_requests({'filesystem': 'read-only'}, {'filesystem': 'workspace-write'})
        for request in ({'isolation': 'sandbox'}, {'filesystem': 'read-only'}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                resolve_environment({'framework_id': 'hermes', '_environment_base': {
                    'workspace': self.project['workspace']}, '_environment_request': request}, 'writer')


class WorkspaceProcesses(unittest.IsolatedAsyncioTestCase):
    async def test_successful_parent_does_not_leave_a_background_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            child = "import time; time.sleep(.6); open('orphan.txt','w').write('escaped')"
            parent = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c",' + repr(child) + \
                     '],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)'
            receipt = await execute_command(work, {'id': 'cleanup', 'argv': [sys.executable, '-c', parent],
                                                   'timeout_seconds': 3})
            self.assertEqual(receipt['status'], 'passed')
            await asyncio.sleep(.7)
            self.assertFalse((work / 'orphan.txt').exists())

    async def test_failed_hermes_bridge_keeps_observed_usage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('source', 'profile', 'runtime', 'work'):
                (root / name).mkdir()
            (root / 'source/run_agent.py').write_text(
                "class AIAgent:\n"
                " def __init__(self, **kw):\n"
                "  self.model=kw['model']; self.session_id=kw['session_id']\n"
                "  self.session_input_tokens=9; self.session_cache_read_tokens=4\n"
                "  self.session_cache_write_tokens=0; self.session_output_tokens=5\n"
                " def run_conversation(self,*args,**kw):\n"
                "  return {'final_response':None,'error':'failed','api_calls':2}\n")
            adapter = HermesWorkspaceAdapter(root / 'work', root / 'runtime', {
                'python': sys.executable, 'source': str(root / 'source'), 'profile': str(root / 'profile'),
                'model': 'stub', 'provider': 'stub'})
            events = []
            with self.assertRaises(ExecutionError):
                async for event in adapter.stream([{'role': 'user', 'content': 'attempt'}]):
                    events.append(event)
            usage = [payload for kind, payload in events if kind == 'usage'][-1]
            self.assertEqual((usage['prompt_tokens'], usage['completion_tokens'], usage['cache_read_tokens']), (13, 5, 4))
            tree = [payload['native_usage'] for kind, payload in events
                    if kind == 'metadata' and payload.get('native_usage')][-1]
            self.assertFalse(tree['complete'])
            self.assertEqual(tree['agents'][0]['status'], 'failed')

    @unittest.skipUnless(os.environ.get('WORKSPACE_DOCKER_TEST_IMAGE'), 'Docker confinement is separately opt-in')
    async def test_docker_only_grants_its_scratch_and_readonly_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / 'work'
            work.mkdir(mode=0o777)
            work.chmod(0o777)
            (root / 'peer.txt').write_text('private')
            check = root / 'check.py'
            check.write_text('authoritative')
            (work / 'check.py').write_text('candidate')
            program = "from pathlib import Path\nPath('own.txt').write_text('owned')\n" + \
                f"assert not Path({str(root / 'peer.txt')!r}).exists()\n" + \
                "for name in ['/etc/escape','/workspace/check.py']:\n" + \
                " try: Path(name).write_text('escape')\n except OSError: pass\n else: raise AssertionError(name)\n"
            result = await execute_command(work, {'id': 'confinement', 'argv': ['python3', '-c', program],
                'timeout_seconds': 30}, backend='docker', image=os.environ['WORKSPACE_DOCKER_TEST_IMAGE'],
                read_only_mounts=[(str(check), 'check.py')])
            self.assertEqual(result['status'], 'passed', result['stderr'])
            self.assertEqual((work / 'own.txt').read_text(), 'owned')
            self.assertEqual(check.read_text(), 'authoritative')


class WorkspaceBridge(WorkspaceFixture, unittest.IsolatedAsyncioTestCase):
    async def test_native_adapter_receives_allocated_cwd_and_source_is_unchanged(self):
        manager = self.service
        lease, _ = manager.acquire('writer')
        project = {**self.project, 'workspace': manager.policy}
        captured = []

        class Adapter:
            def __init__(self, executable, directory, *args, **kwargs):
                self.directory = directory
                captured.append(self)

            async def stream(self, messages):
                (Path(self.directory) / 'written.py').write_text('actual private file')
                yield 'metadata', {'actual_model': 'stub'}
                yield 'text', {'text': 'complete'}

        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/chat/completions',
                           'headers': [(b'authorization', b'Bearer test')]})
        request.state.execution_workspace_service = manager
        messages = [{'role': 'user', 'content': 'write'}]
        form = {'stream': True, 'messages': messages, 'metadata': {'chat_id': 'chat',
            'execution_project': project, 'execution_module_namespace': 'writer',
            'execution_workspace_lease': manager.public(lease)}}
        fake_modules = {
            'open_webui.models.config': SimpleNamespace(Config=SimpleNamespace()),
            'open_webui.models.chats': SimpleNamespace(Chats=SimpleNamespace(get_chat_by_id=AsyncMock(return_value=None))),
        }
        with patch.dict(sys.modules, fake_modules), \
             patch.object(bridge, 'settings', AsyncMock(return_value={'frameworks': {}})), \
             patch.object(bridge, 'prepare_turn', AsyncMock(return_value=(messages, {}))), \
             patch.object(bridge, 'runtime_path', return_value=sys.executable), \
             patch.object(bridge, 'ClaudeAdapter', Adapter):
            response = await bridge.generate_chat(request, form, SimpleNamespace(id='owner', role='admin'), {
                'id': 'stub', 'execution': {'framework_id': 'claude-code', 'framework_name': 'Claude Code', 'model_id': 'stub'}})
            wire = ''.join([chunk async for chunk in response.body_iterator])
        self.assertTrue(captured[0].coding)
        self.assertEqual(captured[0].directory, str(manager.checkout(lease)))
        self.assertFalse((self.source / 'written.py').exists())
        self.assertTrue((manager.checkout(lease) / 'written.py').is_file())
        self.assertIn('workspace-write', wire)


class WorkspaceHostAdmission(unittest.IsolatedAsyncioTestCase):
    async def test_host_pool_is_separate_bounded_and_narrowed_by_children(self):
        from open_webui.execution import workspace_runtime
        root = SimpleNamespace(parent=None, host_slots=asyncio.Semaphore(2), local_host_slots=asyncio.Semaphore(2),
                               slots=asyncio.Semaphore(0))
        root.root = root
        root.runner = SimpleNamespace(graph={'limits': {'max_seconds': 5}}, run_started=time.monotonic())
        child = SimpleNamespace(parent=root, root=root, host_slots=root.host_slots,
                                local_host_slots=asyncio.Semaphore(1), runner=root.runner)
        active = maximum = 0
        async def command(runner, node, inputs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(.02)
                return {'report': 'host command completed'}
            finally:
                active -= 1
        with patch.object(workspace_runtime, '_host_action', side_effect=command):
            runner = SimpleNamespace(engine=root)
            await asyncio.gather(*[workspace_runtime.host_action(runner, {}, {}) for _ in range(3)])
            self.assertEqual(maximum, 2)
            maximum = 0
            runner = SimpleNamespace(engine=child)
            await asyncio.gather(*[workspace_runtime.host_action(runner, {}, {}) for _ in range(3)])
            self.assertEqual(maximum, 1)


class WorkspaceReadonlyNativeState(unittest.IsolatedAsyncioTestCase):
    async def test_readonly_dsh_rotates_missing_private_handle_and_replays_own_history(self):
        from open_webui.execution import dsh
        import uuid
        import yaml
        captures = {}
        selected = json.dumps(['provider', 'model'], separators=(',', ':'))

        class Rpc:
            def __init__(self, argv, cwd, env, *args):
                captures['env'] = env
                captures['patch'] = yaml.safe_load(Path(argv[argv.index('--patch') + 1]).read_text())
                self.events = asyncio.Queue()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def request(self, method, params=None):
                captures.setdefault('methods', []).append(method)
                if method == 'session/new':
                    return {'sessionId': str(uuid.uuid4()), 'configOptions': [{'id': 'model', 'currentValue': selected}]}
                if method == 'session/prompt':
                    captures['prompt'] = params['prompt'][0]['text']
                    return {'stopReason': 'end_turn'}
                return {}

            async def next_event(self):
                return await self.events.get()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / 'runtime'
            runtime.mkdir()
            adapter = dsh.DshAdapter(str(root), 'model', 'provider', native_session_id=str(uuid.uuid4()))
            adapter.coding = False
            adapter.runtime_directory = str(runtime)
            reader = unittest.mock.Mock(return_value=None)
            with patch.object(dsh, 'NativeRpc', Rpc), patch.object(dsh, 'runtime_path', return_value='stub'), \
                 patch.object(dsh, 'read_native_tree', reader):
                events = [event async for event in adapter.stream([
                    {'role': 'user', 'content': 'remember my own earlier fact'},
                    {'role': 'assistant', 'content': 'retained in my private dialogue'},
                    {'role': 'user', 'content': 'continue inspecting read-only files'},
                ])]
            self.assertIn('session/new', captures['methods'])
            self.assertNotIn('session/resume', captures['methods'])
            self.assertEqual(captures['env']['DSH_PERMISSION_MODE'], 'read-only')
            row = next(r for r in captures['patch'] if r['id'] == 'session-persistence-jsonl')
            self.assertEqual(row['config']['root'], str(runtime / '.dsh/sessions'))
            self.assertIn('remember my own earlier fact', captures['prompt'])
            self.assertEqual(reader.call_args.args[-1], str(runtime))
            self.assertTrue(any(payload.get('context_mode') == 'workspace-history-replay'
                                for kind, payload in events if kind == 'metadata'))
