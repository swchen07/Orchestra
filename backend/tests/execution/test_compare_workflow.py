import asyncio
import copy
import unittest
from unittest.mock import patch

from open_webui.execution.compare_workflow import normalize_compare
from open_webui.execution.module_protocol import usage_budget
from open_webui.execution.workflow_presets import complete_presets
from open_webui.execution.workflow_templates import compile_template
from open_webui.execution.workflows import WorkflowEngine


class CompareWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def definition(self, count=2):
        row = next(r for r in complete_presets([]) if r['name'] == 'Compare')
        row['roles'] = row['roles'][:count]
        return normalize_compare(row)

    def compile(self, definition):
        choices = [{'id': 'same-backend', 'name': 'Flash', 'framework_id': 'direct', 'model_id': 'flash'}]
        return compile_template(definition, {r['id']: 'same-backend' for r in definition['roles']}, choices)

    async def test_parallel_same_task_isolated_histories_and_invocation_only_usage_across_turns(self):
        definition = self.definition()
        template, bindings = self.compile(definition)
        started = {b['id']: asyncio.Event() for b in bindings}
        prompts = []

        async def invoke(binding, context, native):
            identity = binding['id']
            started[identity].set()
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 1)
            prompts.append(context[-1]['content'])
            # Deliberately mutate the adapter's copy; it must not leak out.
            context.append({'role': 'assistant', 'content': 'PRIVATE_MUTATION_' + identity})
            return {'choices': [{'message': {'content': 'PRIVATE_ANSWER_' + identity}}],
                    'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'cache_read_input_tokens': 5}}

        async def publish(record):
            pass

        engine = WorkflowEngine(template, bindings, 'Same task for every module', invoke, publish)
        result = await asyncio.wait_for(engine.execute(), 2)
        # The comparison task is identical; each call still knows its own
        # host-owned identity instead of simulating all comparison modules.
        self.assertEqual(prompts[0].split('Current invocation (host-owned):')[0],
                         prompts[1].split('Current invocation (host-owned):')[0])
        for identity in ('executor_01','executor_02'):
            self.assertTrue(any('"module_instance_id": "'+identity+'"' in prompt for prompt in prompts))
        from open_webui.execution.workflow_templates import module_system_prompt
        self.assertEqual(module_system_prompt(template, bindings[0]), module_system_prompt(template, bindings[1]))
        self.assertEqual(len(engine.record['calls']), 2)  # no controller / synthesis calls
        self.assertEqual(usage_budget(engine.record['calls'])['reported_tokens'], 36)
        self.assertIn('PRIVATE_ANSWER_executor_01', result)
        self.assertIn('PRIVATE_ANSWER_executor_02', result)
        for role, messages in engine.contexts.items():
            text = str(messages)
            peer = 'executor_02' if role == 'executor_01' else 'executor_01'
            self.assertNotIn('PRIVATE_MUTATION', text)
            self.assertNotIn('PRIVATE_ANSWER_' + peer, text)

        async def followup(binding, context, native):
            answers = [m['content'] for m in context if m['role'] == 'assistant']
            self.assertEqual(answers, ['PRIVATE_ANSWER_' + binding['id']])
            return {'choices': [{'message': {'content': 'continued'}}]}

        next_turn = WorkflowEngine(template, bindings, 'Recall your own answer', followup, publish,
                                   previous=[copy.deepcopy(engine.record)])
        with patch('open_webui.execution.workflows.claim_native_context', return_value=False):
            await asyncio.wait_for(next_turn.execute(), 2)
        self.assertEqual(len(next_turn.record['calls']), 2)

    async def test_failed_module_is_visible_and_successful_peer_is_retained(self):
        from open_webui.execution.claude import ExecutionError
        template, bindings = self.compile(self.definition())

        async def invoke(binding, context, native):
            if binding['id'] == 'executor_02':
                raise ExecutionError('native backend failed')
            return {'choices': [{'message': {'content': 'usable result'}}]}

        async def publish(record):
            pass

        engine = WorkflowEngine(template, bindings, 'task', invoke, publish)
        answer = await engine.execute()
        self.assertIn('usable result', answer)
        self.assertIn('Execution failed', answer)
        self.assertEqual(set(engine.record['node_states']['answers']['output']['outcomes'].values()),
                         {'completed', 'failed'})

    def test_slots_recompile_to_graph_without_perspective_or_extra_model_calls(self):
        definition = self.definition(3)
        definition['roles'].append({'id': 'another', 'name': 'Another', 'instructions': 'Act as critic'})
        normalized = normalize_compare(definition)
        self.assertEqual(len([n for n in normalized['graph']['nodes'] if n['op'] == 'call']), 4)
        self.assertNotIn('controller', normalized['graph'])
        self.assertNotIn('reviewer', normalized['graph'])
        self.assertEqual(len({r['instructions'] for r in normalized['roles']}), 1)
        with self.assertRaises(ValueError):
            normalize_compare({**definition, 'roles': definition['roles'][:1]})
