import copy
import json
import unittest
import httpx

from open_webui.execution.claude import ExecutionError
from open_webui.execution.direct import DirectAdapter
from open_webui.execution.workflow_templates import (
    validate_template,
    compile_template,
    effective_bindings,
    compile_chat_template,
)
from open_webui.execution.workflows import WorkflowEngine


class DirectTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, protocol, server):
        return DirectAdapter(
            {
                'protocol': protocol,
                'endpoint': 'https://api.example.test/anthropic'
                if protocol == 'anthropic'
                else 'https://api.example.test/v1',
                'key': 'private-api-key',
            },
            'exact-model',
            client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(server), **kw),
        )

    async def test_anthropic_direct_stream_keeps_system_context_and_reports_actual_usage(self):
        def server(request):
            body = json.loads(request.content)
            self.assertEqual(str(request.url), 'https://api.example.test/anthropic/v1/messages')
            self.assertEqual(request.headers['x-api-key'], 'private-api-key')
            self.assertEqual(body['system'], 'Project rules')
            self.assertEqual(body['messages'], [{'role': 'user', 'content': 'task'}])
            self.assertNotIn('tools', body)
            events = [
                {
                    'type': 'message_start',
                    'message': {'model': 'observed-model', 'usage': {'input_tokens': 7, 'cache_read_input_tokens': 3}},
                },
                {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'answer'}},
                {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 2}},
                {'type': 'message_stop'},
            ]
            return httpx.Response(200, text=''.join('data: ' + json.dumps(e) + '\n\n' for e in events))

        events = [
            e
            async for e in self.adapter('anthropic', server).stream(
                [{'role': 'system', 'content': 'Project rules'}, {'role': 'user', 'content': 'task'}]
            )
        ]
        self.assertIn(('text', {'text': 'answer'}), events)
        usage = [v for t, v in events if t == 'usage'][-1]
        self.assertEqual(
            (
                usage['prompt_tokens'],
                usage['fresh_input_tokens'],
                usage['cache_read_tokens'],
                usage['completion_tokens'],
            ),
            (10, 7, 3, 2),
        )
        self.assertNotIn('private-api-key', str(events))

    async def test_responses_and_chat_have_distinct_wire_contracts(self):
        for protocol in ('responses', 'chat'):

            def server(request):
                body = json.loads(request.content)
                self.assertIn('input' if protocol == 'responses' else 'messages', body)
                events = (
                    [
                        {'type': 'response.output_text.delta', 'delta': 'OK'},
                        {
                            'type': 'response.completed',
                            'response': {
                                'model': 'observed',
                                'usage': {
                                    'input_tokens': 10,
                                    'output_tokens': 2,
                                    'input_tokens_details': {'cached_tokens': 4},
                                },
                            },
                        },
                    ]
                    if protocol == 'responses'
                    else [
                        {'model': 'observed', 'choices': [{'delta': {'content': 'OK'}, 'finish_reason': None}]},
                        {
                            'choices': [{'delta': {}, 'finish_reason': 'stop'}],
                            'usage': {'prompt_tokens': 10, 'completion_tokens': 2},
                        },
                    ]
                )
                return httpx.Response(
                    200,
                    text=''.join('data: ' + json.dumps(e) + '\n\n' for e in events)
                    + ('data: [DONE]\n\n' if protocol == 'chat' else ''),
                )

            events = [e async for e in self.adapter(protocol, server).stream([{'role': 'user', 'content': 'task'}])]
            self.assertIn(('text', {'text': 'OK'}), events)
            self.assertTrue(any(t == 'usage' for t, _ in events))

    async def test_auth_errors_truncation_and_unexpected_stop_never_report_success_or_leak_keys(self):
        for status, body in [
            (401, 'private-api-key'),
            (200, 'data: {"type":"message_stop"}\n\n'),
            (200, 'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"}}\n\n'),
        ]:
            with self.subTest(status=status, body=body), self.assertRaises(ExecutionError) as error:
                _ = [
                    e
                    async for e in self.adapter('anthropic', lambda r: httpx.Response(status, text=body)).stream(
                        [{'role': 'user', 'content': 'task'}]
                    )
                ]
            self.assertNotIn('private-api-key', str(error.exception))


class TemplateTests(unittest.IsolatedAsyncioTestCase):
    def chat_choices(self):
        return [
            {'id': 'a', 'name': 'Codex low', 'model_id': 'codex', 'effort': 'low', 'profile_id': None},
            {'id': 'b', 'name': 'DSH minimal', 'model_id': 'dsh', 'profile_id': 'minimal'},
        ], [
            {'id': 'codex', 'execution': {'effort_options': ['low', 'high']}},
            {'id': 'dsh', 'execution': {}},
        ]

    async def test_chat_override_changes_one_role_without_mutating_project_or_template(self):
        definition = self.template()
        defaults = {definition['id']: {'research': 'a', 'review': 'a'}}
        original = copy.deepcopy((definition, defaults))
        choices, models = self.chat_choices()
        override = {'workflow_id': definition['id'], 'roles': {'review': {'participant_id': 'b'}}}
        _, resolved = compile_chat_template(definition, defaults, override, choices, models)
        self.assertEqual([p['binding_id'] for p in resolved], ['a', 'b'])
        self.assertEqual(resolved[1]['profile_id'], 'minimal')
        self.assertEqual([p['binding_source'] for p in resolved], ['project', 'chat'])
        self.assertEqual((definition, defaults), original)
        _, reset = compile_chat_template(definition, defaults, None, choices, models)
        self.assertEqual([p['binding_id'] for p in reset], ['a', 'a'])

    async def test_chat_effort_override_and_explicit_default_are_distinct_from_project_effort(self):
        definition = self.template()
        choices, models = self.chat_choices()
        defaults = {definition['id']: {'research': 'a', 'review': 'b'}}
        for value in ('high', None):
            override = {
                'workflow_id': definition['id'],
                'roles': {'research': {'participant_id': 'a', 'effort': value}},
            }
            _, resolved = compile_chat_template(definition, defaults, override, choices, models)
            self.assertEqual(resolved[0]['effort'], value)
            self.assertEqual(choices[0]['effort'], 'low')

    async def test_chat_overrides_reject_scope_escalation_stale_roles_and_unsupported_effort(self):
        definition = self.template()
        choices, models = self.chat_choices()
        defaults = {definition['id']: {'research': 'a', 'review': 'b'}}
        base = {'workflow_id': definition['id'], 'roles': {}}
        bad = [
            {**base, 'workflow_id': 'a-different-template'},
            {**base, 'roles': {'unknown_role': {'participant_id': 'a'}}},
            {**base, 'roles': {'research': {'participant_id': 'outside-project'}}},
            {**base, 'roles': {'research': {'participant_id': 'b', 'effort': 'high'}}},
            {**base, 'roles': {'research': {'participant_id': 'a', 'effort': 'invalid'}}},
            {**base, 'roles': {'research': {'participant_id': 'a', 'model_id': 'unbound-model'}}},
            {**base, 'roles': {'research': {'participant_id': ['a']}}},
        ]
        for override in bad:
            with self.subTest(override=override), self.assertRaises(ValueError):
                compile_chat_template(definition, defaults, override, choices, models)

    def template(self):
        return validate_template(
            {
                'name': 'Peer review',
                'mode': 'parallel',
                'roles': [
                    {'id': 'research', 'name': 'Researcher', 'instructions': 'Collect evidence'},
                    {'id': 'review', 'name': 'Reviewer', 'instructions': 'Critique evidence'},
                ],
                'lead_role_id': 'research',
                'instructions': '',
                'max_rounds': 2,
            }
        )

    async def test_one_template_binds_different_project_models_without_changing_template(self):
        definition = self.template()
        original = copy.deepcopy(definition)
        available = [
            {'id': 'a', 'name': 'Model A', 'model_id': 'a', 'framework_id': 'direct'},
            {'id': 'b', 'name': 'Model B', 'model_id': 'b', 'framework_id': 'codex'},
        ]
        _, first = compile_template(definition, {'research': 'a', 'review': 'b'}, available)
        _, second = compile_template(definition, {'research': 'b', 'review': 'a'}, available)
        self.assertEqual([p['model_id'] for p in first], ['a', 'b'])
        self.assertEqual([p['model_id'] for p in second], ['b', 'a'])
        self.assertEqual(definition, original)
        self.assertEqual(first[1]['role_instructions'], 'Critique evidence')

    async def test_same_model_in_two_roles_keeps_independent_call_contexts(self):
        template, bindings = compile_template(
            self.template(),
            {'research': 'a', 'review': 'a'},
            [{'id': 'a', 'name': 'Model A', 'model_id': 'a', 'framework_id': 'codex'}],
        )
        seen = []

        async def invoke(binding, context, native):
            seen.append((binding['id'], copy.deepcopy(context), native))
            return {
                'choices': [{'message': {'content': binding['id']}}],
                'execution': {'native_session_id': binding['id']},
            }

        async def publish(value):
            pass

        engine = WorkflowEngine(template, bindings, 'same task', invoke, publish)
        await engine.execute()
        await engine.turn('research', 'follow up')
        self.assertEqual(set(engine.contexts), {'research', 'review'})
        self.assertEqual(seen[-1][2], 'research')
        self.assertIn({'role': 'assistant', 'content': 'research'}, seen[-1][1])
        self.assertNotIn({'role': 'assistant', 'content': 'review'}, seen[-1][1])

    async def test_scope_and_role_changes_require_valid_project_rebinding(self):
        definition = self.template()
        available = [{'id': 'a', 'name': 'A', 'model_id': 'a'}]
        for bindings in (
            {},
            {'research': 'a'},
            {'research': 'a', 'review': 'not-in-project'},
            {'research': 'a', 'review': 'a', 'extra': 'a'},
        ):
            with self.assertRaises(ValueError):
                compile_template(definition, bindings, available)
        self.assertEqual(effective_bindings(definition, {}), {})
        legacy = {'id': definition['id'], 'participant_ids': ['a', 'b']}
        self.assertEqual(effective_bindings(legacy, {}), {'role_1': 'a', 'role_2': 'b'})
        self.assertEqual(effective_bindings(legacy, {definition['id']: {'role_1': 'b'}}), {'role_1': 'b'})
