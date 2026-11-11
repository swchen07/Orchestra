import asyncio
import json
import tempfile
import sys
import unittest
from pathlib import Path
from open_webui.execution.claude import ClaudeAdapter, ExecutionError


class ClaudeTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, code):
        f = Path(self.tmp.name) / 'fake-claude'
        f.write_text('#!' + sys.executable + '\n' + code)
        f.chmod(0o755)
        return ClaudeAdapter(str(f), self.tmp.name, 'real-configured-model', False, timeout=3)

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_partial_text_result_and_actual_model(self):
        rows = [
            {'type': 'system', 'subtype': 'init', 'model': 'actual-model'},
            {
                'type': 'stream_event',
                'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'hello'}},
            },
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'hello'}]}},
            {
                'type': 'result',
                'is_error': False,
                'result': 'hello',
                'usage': {'input_tokens': 2, 'output_tokens': 1},
                'modelUsage': {'actual-model': {}},
            },
        ]
        adapter = self.adapter(
            'import json,sys\nsys.stdin.read()\n'
            + ''.join('print(' + repr(json.dumps(r)) + ',flush=True)\n' for r in rows)
        )
        events = [e async for e in adapter.stream([{'role': 'user', 'content': 'a; touch /tmp/should-not-run'}])]
        self.assertEqual(''.join(e[1].get('text', '') for e in events if e[0] == 'text'), 'hello')
        self.assertEqual([e[1]['actual_model'] for e in events if e[0] == 'metadata'][-1], 'actual-model')

    async def test_nonzero_bad_json_and_missing_result(self):
        for code in ['import sys\nsys.exit(3)', 'print("broken")', "print('{}')"]:
            with self.subTest(code=code), self.assertRaises(ExecutionError):
                _ = [e async for e in self.adapter(code).stream([{'role': 'user', 'content': 'hi'}])]

    async def test_provider_file_is_private_not_in_argv_and_removed_on_success_and_error(self):
        marker = Path(self.tmp.name) / 'settings-path'
        code = (
            'import sys,json,os;from pathlib import Path\nsys.stdin.read()\np=Path(sys.argv[sys.argv.index("--settings")+1]);Path('
            + repr(str(marker))
            + ').write_text(str(p))\nassert p.stat().st_mode & 0o777 == 0o600\nassert "private-test-key" not in " ".join(sys.argv)\nassert json.loads(p.read_text())["env"]["ANTHROPIC_API_KEY"]=="private-test-key"\n'
        )
        for suffix in ['print(json.dumps({"type":"result","subtype":"success","result":"ok"}))', 'print("broken")']:
            adapter = self.adapter(code + suffix)
            adapter.provider_settings = {'env': {'ANTHROPIC_API_KEY': 'private-test-key'}}
            try:
                _ = [event async for event in adapter.stream([{'role': 'user', 'content': 'hi'}])]
            except ExecutionError:
                self.assertIn('broken', suffix)
            self.assertFalse(Path(marker.read_text()).exists())

    async def test_timeout_reaps_process_group(self):
        marker = Path(self.tmp.name) / 'child-finished'
        child = 'import time;from pathlib import Path;time.sleep(1);Path(' + repr(str(marker)) + ').write_text("alive")'
        code = (
            'import subprocess,time,sys\nsys.stdin.read()\nsubprocess.Popen([sys.executable,"-c",'
            + repr(child)
            + '])\ntime.sleep(60)'
        )
        with self.assertRaises(ExecutionError):
            adapter = self.adapter(code)
            adapter.timeout = 0.5
            _ = [e async for e in adapter.stream([{'role': 'user', 'content': 'hi'}])]
        await asyncio.sleep(0.8)
        self.assertFalse(marker.exists(), 'timed-out descendant kept running')

    async def test_cancellation_cleans_provider_file(self):
        marker = Path(self.tmp.name) / 'private-settings-path'
        code = (
            'import sys,time;from pathlib import Path\nsys.stdin.read()\nPath('
            + repr(str(marker))
            + ').write_text(sys.argv[sys.argv.index("--settings")+1])\ntime.sleep(60)'
        )
        adapter = self.adapter(code)
        adapter.timeout = 10
        adapter.provider_settings = {'env': {'ANTHROPIC_API_KEY': 'test-private-key'}}

        async def collect():
            return [event async for event in adapter.stream([{'role': 'user', 'content': 'hi'}])]

        task = asyncio.create_task(collect())
        async with asyncio.timeout(2):
            while not marker.exists():
                await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(Path(marker.read_text()).exists())
