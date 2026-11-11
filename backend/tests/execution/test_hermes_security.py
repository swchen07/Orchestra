import json
import unittest
from types import SimpleNamespace
import httpx
from fastapi import HTTPException
from starlette.requests import Request
from open_webui.execution.hermes import HermesAdapter
from open_webui.execution.bridge import authorize
from open_webui.execution.claude import ExecutionError
from open_webui.execution.history_provenance import dialogue_digest


class HermesTests(unittest.IsolatedAsyncioTestCase):
    async def test_workflow_context_scope_is_forwarded_without_enabling_native_history_replay(self):
        def server(request):
            self.assertEqual(request.headers['X-Hermes-Session-Key'], 'context-a')
            self.assertNotIn('X-Hermes-Session-Id', request.headers)
            return httpx.Response(200, headers={'X-Hermes-Session-Id': 'native-a'}, text='data: [DONE]\n\n')

        adapter = HermesAdapter(
            'http://localhost:8642/v1',
            'private',
            client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(server), **kw),
        )
        adapter.context_key = 'context-a'
        self.assertEqual(
            [event async for event in adapter.stream([{'role': 'user', 'content': 'hi'}])],
            [('metadata', {'native_session_id': 'native-a', 'context_mode': 'text-replay',
                           'submitted_history_messages': 0, 'submitted_history_sha256': dialogue_digest([])})],
        )

    async def test_chat_history_and_tool_events_are_preserved(self):
        messages = [
            {'role': 'user', 'content': 'remember blue'},
            {'role': 'assistant', 'content': 'ok'},
            {'role': 'user', 'content': 'what color?'},
        ]

        def server(request):
            self.assertEqual(json.loads(request.content)['messages'], messages)
            return httpx.Response(
                200,
                text='event: hermes.tool.progress\ndata: {"tool":"read_file","status":"completed"}\n\ndata: {"choices":[{"delta":{"content":"blue"}}]}\n\ndata: [DONE]\n\n',
            )

        adapter = HermesAdapter(
            'http://localhost:8642/v1',
            'private',
            client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(server), **kw),
        )
        events = [e async for e in adapter.stream(messages)]
        self.assertEqual(events[0][0], 'metadata')
        self.assertEqual(events[0][1]['submitted_history_sha256'], dialogue_digest(messages[:-1]))
        self.assertEqual(events[0][1]['submitted_history_messages'], 2)
        self.assertEqual(events[1][0], 'tool')
        self.assertEqual(events[1][1]['name'], 'read_file')
        self.assertTrue(events[1][1]['done'])
        self.assertEqual(events[1][1]['status'], 'completed')
        self.assertEqual(events[2], ('text', {'text': 'blue'}))

    async def test_invalid_upstream_never_looks_successful(self):
        for code, body in [(401, 'private'), (200, 'data: {}\n\n'), (200, 'data: broken\n\n')]:

            def server(request):
                return httpx.Response(code, text=body)

            adapter = HermesAdapter(
                'http://localhost:8642/v1',
                'private',
                client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(server), **kw),
            )
            with self.subTest(code=code, body=body), self.assertRaises(ExecutionError):
                _ = [e async for e in adapter.stream([{'role': 'user', 'content': 'hi'}])]


class SecurityTests(unittest.TestCase):
    def request(self, headers):
        return Request(
            {
                'type': 'http',
                'method': 'POST',
                'path': '/api/chat/completions',
                'headers': [(k.encode(), v.encode()) for k, v in headers.items()],
            }
        )

    def test_admin_and_origin_both_required(self):
        with self.assertRaises(HTTPException):
            authorize(self.request({'authorization': 'Bearer test'}), SimpleNamespace(role='user'))
        with self.assertRaises(HTTPException):
            authorize(
                self.request({'authorization': 'Bearer test', 'origin': 'https://other.invalid'}),
                SimpleNamespace(role='admin'),
            )
        authorize(
            self.request({'authorization': 'Bearer test', 'origin': 'http://localhost:5173'}),
            SimpleNamespace(role='admin'),
        )
