"""Declared evaluator capability must be available before paid native admission."""

import asyncio
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from open_webui.execution.claude import ExecutionError
from open_webui.execution.graph_runtime import GraphRunner
from open_webui.execution.workflows import WorkflowEngine
from open_webui.execution.workspaces import git
from open_webui.execution.workspace_runtime import preflight_host_actions


class WorkspacePreflight(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        source = self.root / 'source'
        source.mkdir()
        (source / 'code.py').write_text('value = 1\n')
        (source / 'check.py').write_text('from code import value\nassert value > 0\n')
        git(source, 'init', '--quiet')
        git(source, 'add', '--all')
        git(source, 'commit', '--quiet', '-m', 'Baseline')
        self.base = {'directory': str(source), '_workspace_root': str(self.root / 'managed'), 'workspace': {
            'enabled': True, 'baseline_revision': git(source, 'rev-parse', 'HEAD'),
            'command_backend': 'docker', 'docker_image': 'trusted-test-image',
            'commands': [{'id': 'check', 'argv': ['python3', 'check.py'], 'authoritative_paths': ['check.py']}]}}
        self.binding = {'id': 'worker', 'name': 'Worker', 'framework_id': 'dsh', 'model_id': 'stub',
                        '_environment_base': self.base}
        self.entries = []

    def tearDown(self):
        self.temporary.cleanup()

    def graph(self, command='check', enabled=True):
        return {'schema_version': '2.0', 'nodes': [
            {'id': 'work', 'op': 'call', 'binding': 'worker', 'inputs': {}},
            {'id': 'evaluate', 'op': 'evaluate', 'enabled': enabled,
             'workspace': {'command_ids': [command]},
             'inputs': {'artifact': {'source': 'node', 'node': 'work', 'pointer': '/__artifacts'}}},
        ], 'edges': [], 'outputs': {'answer': {'source': 'node', 'node': 'evaluate' if enabled else 'work', 'pointer': '/report'}}}

    def engine(self, graph, bindings=None, registry=()):
        async def invoke(binding, messages, native):
            self.entries.append(binding['id'])
            return {'choices': [{'message': {'content': 'complete'}}]}
        async def publish(record):
            pass
        return WorkflowEngine({'id': 'preflight', 'name': 'Preflight', 'lead_id': (bindings or [self.binding])[0]['id'],
                               'graph': graph}, bindings or [self.binding], 'task', invoke, publish,
            record={'id': 'run', 'user_id': 'owner', 'project_id': 'project', 'chat_id': 'chat',
                    'calls': [], 'messages': [], 'status': 'queued'}, registry=registry)

    async def test_unavailable_daemon_or_image_prevents_all_native_entries(self):
        for error in ('Docker engine is unavailable', 'The approved Docker image is unavailable locally'):
            with self.subTest(error=error):
                engine = self.engine(self.graph())
                with patch('open_webui.execution.workspace_process.docker_capability', side_effect=ExecutionError(error)) as probe:
                    with self.assertRaisesRegex(ExecutionError, 'unavailable'):
                        await engine.execute()
                self.assertEqual(self.entries, [])
                self.assertEqual(engine.record['calls'], [])
                self.assertEqual(probe.call_count, 1)

    async def test_unknown_command_prevents_native_without_even_probing_docker(self):
        engine = self.engine(self.graph(command='missing'))
        with patch('open_webui.execution.workspace_process.docker_capability') as probe:
            with self.assertRaisesRegex(ExecutionError, 'Unknown administrator-approved'):
                await engine.execute()
        self.assertEqual(self.entries, [])
        self.assertEqual(engine.record['calls'], [])
        probe.assert_not_called()

    async def test_unused_or_disabled_evaluator_does_not_probe(self):
        engine = self.engine(self.graph(enabled=False))
        with patch('open_webui.execution.workspace_process.docker_capability') as probe:
            self.assertEqual(await engine.execute(), 'complete')
        probe.assert_not_called()
        self.assertEqual(self.entries, ['work'])

    async def test_root_singleflight_caches_success_and_failure_across_runners(self):
        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable):
                engine = self.engine(self.graph())
                runners = [GraphRunner(engine, self.graph()) for _ in range(3)]
                def probe(image):
                    import time
                    time.sleep(.01)
                    if unavailable:
                        raise ExecutionError('Docker engine is unavailable')
                    return {'backend': 'docker', 'image': image, 'image_id': 'sha256:' + 'a' * 64}
                with patch('open_webui.execution.workspace_process.docker_capability', side_effect=probe) as checked:
                    results = await asyncio.gather(*(preflight_host_actions(r) for r in runners), return_exceptions=True)
                self.assertEqual(checked.call_count, 1)
                self.assertTrue(all(isinstance(r, ExecutionError) for r in results) if unavailable else all(r is None for r in results))
                self.assertEqual(self.entries, [])

    async def test_new_required_action_is_checked_before_a_graph_revision_is_admitted(self):
        engine = self.engine(self.graph(enabled=False))
        runner = GraphRunner(engine, self.graph(enabled=False))
        await preflight_host_actions(runner)
        candidate = copy.deepcopy(runner.graph)
        candidate['revision'] += 1
        next(n for n in candidate['nodes'] if n['id'] == 'evaluate')['enabled'] = True
        with patch('open_webui.execution.workspace_process.docker_capability', side_effect=ExecutionError('missing image')):
            with self.assertRaisesRegex(ExecutionError, 'missing image'):
                await runner.revise(candidate, 'Enable required evaluation')
        self.assertFalse(next(n for n in runner.graph['nodes'] if n['id'] == 'evaluate')['enabled'])
        self.assertEqual(self.entries, [])

    async def test_known_child_evaluator_is_checked_before_unrelated_root_work(self):
        from open_webui.execution.workflow_templates import normalize_graph_template, compile_template
        backend = {**self.binding, 'id': 'backend'}
        child = normalize_graph_template({'name': 'Child', 'roles': [{'id': 'worker', 'name': 'Worker'}],
            'lead_role_id': 'worker', 'graph': self.graph()})
        template, bindings = compile_template(child, {'worker': 'backend'}, [backend])
        bound = {'id': 'child', 'name': 'Child', 'framework_id': 'workflow', 'module_type': 'workflow',
                 '_workflow_template': template, '_workflow_bindings': bindings,
                 'input_schema': template['graph']['input_schema'], 'output_schema': template['graph']['output_schema']}
        graph = {'schema_version': '2.0', 'nodes': [
            {'id': 'unrelated', 'op': 'call', 'binding': 'worker', 'inputs': {}},
            {'id': 'nested', 'op': 'call', 'binding': 'child', 'inputs': {'task': {'source': 'literal', 'value': 'task'}}},
        ], 'edges': [], 'outputs': {'answer': {'source': 'node', 'node': 'nested', 'pointer': '/answer'}}}
        engine = self.engine(graph, [self.binding, bound], [backend])
        with patch('open_webui.execution.workspace_process.docker_capability', side_effect=ExecutionError('missing child image')):
            with self.assertRaisesRegex(ExecutionError, 'missing child image'):
                await engine.execute()
        self.assertEqual(self.entries, [])
        self.assertEqual(engine.record['calls'], [])
