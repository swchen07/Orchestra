"""Hermes callback receipts follow the installed runtime's actual call sites."""

import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from open_webui.execution.hermes_workspace import HermesWorkspaceAdapter
from open_webui.execution.hermes_workspace_bridge import tool_callbacks


INSTALLED_EXECUTOR = Path.home() / '.hermes/hermes-agent/agent/tool_executor.py'


class HermesToolCallbackContracts(unittest.TestCase):
    @unittest.skipUnless(INSTALLED_EXECUTOR.is_file(), 'Installed Hermes call-site compatibility is optional')
    def test_actual_installed_sequential_and_concurrent_callback_calls_match(self):
        events = []
        # Execute the installed pure classifier's terminal branch without importing
        # unrelated native configuration or starting AIAgent/model work.
        display = ast.parse((INSTALLED_EXECUTOR.parent / 'display.py').read_text())
        definition = next(n for n in display.body if isinstance(n, ast.FunctionDef) and n.name == '_detect_tool_failure')
        scope = {'safe_json_loads': json.loads, 'file_mutation_result_landed': lambda *args: False, '_trim_error': str}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[definition], type_ignores=[])), str(INSTALLED_EXECUTOR), 'exec'), scope)
        start, complete = tool_callbacks(lambda kind, payload: events.append((kind, payload)), scope['_detect_tool_failure'])
        agent = SimpleNamespace(tool_start_callback=start, tool_complete_callback=complete)
        result = subprocess.run([sys.executable, '-c', "print('actual command output');raise SystemExit(7)"],
                                capture_output=True, text=True)
        arguments = {'command': 'bounded local command'}
        namespace = {'agent': agent, 'tc': SimpleNamespace(id='native-id'),
            'tool_call': SimpleNamespace(id='native-id'), 'name': 'terminal', 'function_name': 'terminal',
            'args': arguments, 'function_args': arguments,
            'function_result': json.dumps({'output': result.stdout, 'exit_code': result.returncode, 'error': None})}
        matched = set()
        source = ast.parse(INSTALLED_EXECUTOR.read_text())
        for node in ast.walk(source):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {'tool_start_callback', 'tool_complete_callback'}):
                self.assertEqual(len(node.args), 3 if node.func.attr == 'tool_start_callback' else 4)
                expression = ast.fix_missing_locations(ast.Expression(body=node))
                eval(compile(expression, str(INSTALLED_EXECUTOR), 'eval'), namespace)
                matched.add(node.func.attr)
        self.assertEqual(matched, {'tool_start_callback', 'tool_complete_callback'})
        completed = [payload for kind, payload in events if payload['done']]
        self.assertTrue(completed)
        for payload in completed:
            self.assertEqual((payload['id'], payload['name'], payload['input']), ('native-id', 'terminal', arguments))
            self.assertEqual(payload['status'], 'failed')
            self.assertEqual(payload['exit_code'], 7)
            self.assertEqual(payload['output']['output'], result.stdout)

    def test_actual_error_and_blocked_results_are_preserved_and_output_is_bounded(self):
        events = []
        start, complete = tool_callbacks(lambda kind, payload: events.append(payload))
        start('tool-one', 'read_file', {'path': 'missing.py'})
        complete('tool-one', 'read_file', {'path': 'missing.py'}, json.dumps({'error': 'file unavailable'}))
        complete('tool-two', 'terminal', {'command': 'restricted'}, json.dumps({'status': 'blocked', 'exit_code': -1}))
        complete('tool-three', 'terminal', {}, json.dumps({'output': 'x' * 20000, 'exit_code': 0, 'error': None}))
        self.assertEqual(events[0]['id'], events[1]['id'])
        self.assertEqual(events[1]['status'], 'failed')
        self.assertEqual(events[1]['output']['error'], 'file unavailable')
        self.assertEqual(events[2]['status'], 'denied')
        self.assertEqual(events[3]['status'], 'unknown')
        self.assertLess(len(events[3]['output']['output']), 17000)
        self.assertTrue(events[3]['output_truncated'])


class HermesToolBridge(unittest.IsolatedAsyncioTestCase):
    async def test_real_bridge_subprocess_retains_tool_ids_inputs_and_actual_exit_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('source', 'profile', 'runtime', 'work'):
                (root / name).mkdir()
            (root / 'source/run_agent.py').write_text('''import json, subprocess, sys
class AIAgent:
 def __init__(self, **kw):
  self.model=kw['model'];self.session_id=kw['session_id'];self.start=kw['tool_start_callback'];self.complete=kw['tool_complete_callback']
  self.session_input_tokens=3;self.session_cache_read_tokens=0;self.session_cache_write_tokens=0;self.session_output_tokens=2
 def run_conversation(self, *args, **kw):
  for identity, program in [('write-call',"from pathlib import Path;Path('written.py').write_text('value=1');print('written')"),('failed-call',"print('actual failure');raise SystemExit(4)")]:
   arguments={'command':program};self.start(identity,'terminal',arguments)
   result=subprocess.run([sys.executable,'-c',program],capture_output=True,text=True)
   self.complete(identity,'terminal',arguments,json.dumps({'output':result.stdout,'exit_code':result.returncode,'error':None}))
  return {'final_response':'finished','api_calls':1}
''')
            adapter = HermesWorkspaceAdapter(root / 'work', root / 'runtime', {
                'python': sys.executable, 'source': str(root / 'source'), 'profile': str(root / 'profile'),
                'model': 'stub', 'provider': 'stub'})
            events = [event async for event in adapter.stream([{'role': 'user', 'content': 'bounded task'}])]
            tools = [payload for kind, payload in events if kind == 'tool']
            self.assertEqual([row['id'] for row in tools], ['write-call', 'write-call', 'failed-call', 'failed-call'])
            self.assertTrue(all(row['name'] == 'terminal' and isinstance(row['input'], dict) for row in tools))
            self.assertEqual((tools[1]['status'], tools[1]['exit_code']), ('unknown', 0))
            self.assertEqual((tools[3]['status'], tools[3]['exit_code']), ('unknown', 4))
            self.assertEqual(tools[3]['output']['output'].strip(), 'actual failure')
            self.assertEqual((root / 'work/written.py').read_text(), 'value=1')
