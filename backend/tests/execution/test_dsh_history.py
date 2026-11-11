import json
from pathlib import Path
import tempfile
import unittest

from open_webui.execution.dsh_history import metadata, preview

try:
    from compression import zstd
except ImportError:
    zstd = None


@unittest.skipIf(zstd is None, 'zstd is unavailable')
class DshHistoryTests(unittest.TestCase):
    def test_native_transcript_filters_runtime_context_and_private_blocks(self):
        with tempfile.TemporaryDirectory() as home:
            path = Path(home) / '.dsh/sessions/project/session/session.v4.jsonl.zstd'
            path.parent.mkdir(parents=True)
            rows = [
                {'type': 'session', 'version': 4, 'id': 'session', 'cwd': '/project'},
                {
                    'type': 'user/message',
                    'data': {'source': {'kind': 'user'}, 'content': [{'type': 'text', 'text': 'real question'}]},
                },
                {
                    'type': 'user/message',
                    'data': {
                        'source': {'kind': 'runtime-context'},
                        'content': [{'type': 'text', 'text': 'internal snapshot'}],
                    },
                },
                {'type': 'request/context', 'data': {'model': 'exact-native-model'}},
                {
                    'type': 'assistant/message',
                    'data': {
                        'message': {
                            'content': [
                                {'type': 'thinking', 'thinking': 'private reasoning'},
                                {'type': 'text', 'text': 'public answer'},
                            ]
                        }
                    },
                },
            ]
            with zstd.open(path, 'wt') as stream:
                for row in rows:
                    stream.write(json.dumps(row) + '\n')
            row = metadata(path, home)
            result = preview(row)
            self.assertEqual([m['content'] for m in result['messages']], ['real question', 'public answer'])
            self.assertEqual(result['messages'][-1]['model'], 'exact-native-model')
            self.assertNotIn('private reasoning', str(result))
            self.assertNotIn('_path', result['session'])

    def test_child_sessions_and_paths_outside_the_native_root_are_rejected(self):
        with tempfile.TemporaryDirectory() as home:
            path = Path(home) / '.dsh/sessions/project/child/session.v4.jsonl.zstd'
            path.parent.mkdir(parents=True)
            with zstd.open(path, 'wt') as stream:
                stream.write(json.dumps({'type': 'session', 'version': 4, 'id': 'child', 'delegationDepth': 1}) + '\n')
            self.assertIsNone(metadata(path, home))
            self.assertIsNone(metadata(path, Path(home) / 'other-user'))

    def test_incomplete_native_writer_does_not_break_history_listing(self):
        with tempfile.TemporaryDirectory() as home:
            path = Path(home) / '.dsh/sessions/project/session/session.v4.jsonl.zstd'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'not-a-complete-zstd-frame')
            self.assertIsNone(metadata(path, home))
