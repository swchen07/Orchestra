"""An owned ACP subprocess retains committed usage when interrupted."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from open_webui.execution.claude import ExecutionError
from open_webui.execution.dsh import DshAdapter

try:
    from compression import zstd
except ImportError:
    try:
        import zstandard as zstd
    except ImportError:
        zstd = None


STUB = r'''
import json, os, sys, time, uuid
from pathlib import Path
import yaml
try:
 from compression import zstd
except ImportError:
 import zstandard as zstd
rows = yaml.safe_load(Path(sys.argv[sys.argv.index('--patch')+1]).read_text())
selection = next(r['config'] for r in rows if r['id']=='acp')
root = Path(next(r['config']['root'] for r in rows if r['id']=='session-persistence-jsonl'))
session = str(uuid.uuid4())
def reply(event, result):
 print(json.dumps({'jsonrpc':'2.0','id':event['id'],'result':result}),flush=True)
for line in sys.stdin:
 event=json.loads(line); method=event.get('method')
 if method=='initialize': reply(event,{})
 elif method=='session/new': reply(event,{'sessionId':session,'configOptions':[{'id':'model','currentValue':json.dumps([selection['provider'],selection['model']],separators=(',',':'))}]})
 elif method=='session/prompt':
  reference=event['params']['prompt'][0]['text'].rsplit('Execution usage reference: ',1)[1].strip()
  path=root/'test'/session/'session.v4.jsonl.zstd';path.parent.mkdir(parents=True)
  records=[{'type':'session','version':4,'id':session,'cwd':os.getcwd()},
   {'seq':1,'type':'turn/start','time':100,'data':{'turn':1}},
   {'seq':2,'type':'user/message','time':101,'data':{'content':[{'type':'text','text':'Task\nExecution usage reference: '+reference}]}},
   {'seq':3,'type':'step/start','time':102,'data':{'turn':1,'step':1}},
   {'seq':4,'type':'assistant/message','time':130,'data':{'turn':1,'step':1,'usage':{'inputTokens':9,'outputTokens':5,'cacheReadTokens':4,'cacheWriteTokens':0}}}]
  with zstd.open(path,'wt') as log:
   for row in records:log.write(json.dumps(row)+'\n')
  (root.parent.parent/'stub.pid').write_text(str(os.getpid()))
  print(json.dumps({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':session,'update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'committed usage is ready'}}}}),flush=True)
  if os.environ.get('OWUI_STUB_FAIL')=='1':
   print(json.dumps({'jsonrpc':'2.0','id':event['id'],'error':{'code':-32000,'message':'stub failure'}}),flush=True)
 elif method=='session/close':reply(event,{})
'''


@unittest.skipIf(zstd is None, 'zstd decoder is unavailable')
class DshInterruption(unittest.IsolatedAsyncioTestCase):
    async def run_interrupted(self, mode):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / 'runtime'
            runtime.mkdir()
            executable = root / 'owned-acp-stub'
            executable.write_text('#!' + sys.executable + '\n' + STUB)
            executable.chmod(0o755)
            adapter = DshAdapter(str(root), 'model', 'provider', timeout=.4 if mode == 'timeout' else 5)
            adapter.coding = False
            adapter.runtime_directory = str(runtime)
            ready = asyncio.Event()
            events = []
            async def collect():
                async for kind, payload in adapter.stream([{'role': 'user', 'content': 'bounded task'}]):
                    events.append((kind, payload))
                    if kind == 'text':
                        ready.set()
            with patch('open_webui.execution.dsh.runtime_path', return_value=str(executable)), \
                 patch.dict(os.environ, {'OWUI_STUB_FAIL': '1' if mode == 'failure' else '0'}):
                task = asyncio.create_task(collect())
                await asyncio.wait_for(ready.wait(), 2)
                if mode == 'cancel':
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    with self.assertRaises(ExecutionError):
                        await task
            usage = [payload for kind, payload in events if kind == 'usage'][-1]
            self.assertEqual((usage['prompt_tokens'], usage['completion_tokens'], usage['cache_read_tokens']), (13, 5, 4))
            self.assertFalse(usage['native_tree_total_complete'])
            self.assertEqual(len(usage['source_event_ids']), 1)
            tree = [payload['native_usage'] for kind, payload in events if kind == 'metadata' and payload.get('native_usage')][-1]
            self.assertFalse(tree['complete'])
            self.assertEqual(tree['agents'][0]['status'], 'cancelled' if mode == 'cancel' else 'failed')
            pid = int((runtime / 'stub.pid').read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            self.assertEqual(len(list((runtime / '.dsh/sessions').glob('*/' + tree['agents'][0]['native_session_id'] + '/session.v4.jsonl.zstd'))), 1)

    async def test_cancel_retains_known_private_usage_and_stops_the_process(self):
        await self.run_interrupted('cancel')

    async def test_timeout_retains_known_private_usage_and_stops_the_process(self):
        await self.run_interrupted('timeout')

    async def test_native_error_retains_known_private_usage_and_stops_the_process(self):
        await self.run_interrupted('failure')
