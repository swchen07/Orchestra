import asyncio
import copy
import unittest

from open_webui.execution.claude import ExecutionError
from open_webui.execution.modules import AgentModule, ChatAgentModule, ModuleInput, ModuleScope
from open_webui.execution.workflows import WorkflowEngine


def response(text, usage=None):
    return {'choices': [{'message': {'content': text}}], 'usage': usage or {}}


class ModuleContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_backend_mutation_cannot_modify_retained_input_or_binding(self):
        binding = {'id': 'B', 'model_id': 'shared', 'config': {'preset': 'original'}}
        messages = [{'role': 'user', 'content': 'original'}]
        scope = ModuleScope('run', 'call', 'B', 'B-context', 'shared')
        events = []

        async def emit(kind, payload):
            events.append((kind, payload))

        async def invoke(selected, context, native):
            self.assertEqual(selected['_module_scope']['invocation_id'], 'call')
            selected['config']['preset'] = 'changed'
            context[0]['content'] = 'changed'
            return response('report', {'prompt_tokens': 0, 'completion_tokens': 2})

        result = await ChatAgentModule(invoke).run(ModuleInput(scope, binding, messages), emit)
        self.assertIsInstance(ChatAgentModule(invoke), AgentModule)
        self.assertEqual(binding['config']['preset'], 'original')
        self.assertEqual(messages[0]['content'], 'original')
        self.assertEqual(result.scope.instance_id, 'B')
        self.assertEqual(result.usage['prompt_tokens'], 0)
        self.assertEqual(events[-1][1]['status'], 'completed')
        self.assertGreaterEqual(result.timings['invocation_ms'], 0)
        self.assertNotIn('report', str(events))

    async def test_failed_and_cancelled_invocations_keep_their_own_terminal_metrics(self):
        for cancelled in (False, True):
            events = []

            async def emit(kind, payload):
                events.append(payload)

            async def invoke(*args):
                if cancelled:
                    raise asyncio.CancelledError()
                return response('')

            with self.assertRaises(asyncio.CancelledError if cancelled else ExecutionError):
                await ChatAgentModule(invoke).run(
                    ModuleInput(ModuleScope('r', 'i', 'A', 'ctx', 'team'), {}, []), emit,
                )
            self.assertEqual(events[-1]['status'], 'cancelled' if cancelled else 'failed')
            self.assertEqual(events[-1]['invocation_id'], 'i')
            self.assertGreaterEqual(events[-1]['timings']['invocation_ms'], 0)

    async def test_module_owns_partial_usage_snapshots_and_ignores_late_events(self):
        events, saved_emit = [], []

        async def emit(kind, payload):
            events.append((kind, payload))

        async def invoke(binding, messages, native):
            saved_emit.append(binding['_module_emit'])
            await binding['_module_emit']('usage', {'prompt_tokens': 7, 'completion_tokens': 1})
            await binding['_module_emit']('usage', {'completion_tokens': 3})
            return response('done')  # Native final result omitted previously streamed counters.

        result = await ChatAgentModule(invoke).run(
            ModuleInput(ModuleScope('r', 'i', 'A', 'ctx', 'team'), {}, []), emit,
        )
        self.assertEqual(result.usage, {'prompt_tokens': 7, 'completion_tokens': 3})
        self.assertEqual(events[-1][1]['usage'], result.usage)
        count = len(events)
        await saved_emit[0]('usage', {'prompt_tokens': 999})
        self.assertEqual(len(events), count)
        self.assertEqual(result.usage['prompt_tokens'], 7)

    async def test_a_b_c_two_cycles_share_backend_definition_but_never_private_context(self):
        template = {'id': 'minimal-loop', 'name': 'Coordinator loop', 'lead_id': 'A'}
        bindings = [
            {'id': role, 'name': role, 'framework_id': 'dsh', 'model_id': 'same-model', 'binding_id': 'same-team'}
            for role in ('A', 'B', 'C')
        ]
        original = copy.deepcopy(bindings)
        observed = []
        counts = dict.fromkeys(('A', 'B', 'C'), 0)

        async def publish(value):
            pass

        async def invoke(binding, messages, native):
            role = binding['id']
            counts[role] += 1
            scope = binding['_module_scope']
            observed.append((role, copy.deepcopy(messages), scope))
            if role != 'A':
                peer = 'C' if role == 'B' else 'B'
                self.assertNotIn(peer + '-private', str(messages))
                if counts[role] == 2:
                    self.assertIn(role + '-private-1', str(messages))
            await asyncio.sleep(0)
            return response(role + '-private-' + str(counts[role]),
                            {'prompt_tokens': 10, 'completion_tokens': 3})

        engine = WorkflowEngine(template, bindings, 'task', invoke, publish)
        lead = await engine.turn('A', 'Divide the task between B and C')
        for cycle in (1, 2):
            reports = await asyncio.gather(*[
                engine.turn(role, f'Execute your assigned task {cycle}', depends_on=[lead['id']])
                for role in ('B', 'C')
            ])
            lead = await engine.turn('A', engine.outputs(reports), depends_on=[r['id'] for r in reports])
            self.assertIn(reports[0]['report_id'], lead['input'])
            self.assertIn(reports[1]['report_id'], lead['input'])
        self.assertEqual(bindings, original)
        self.assertEqual(len(engine.record['calls']), 7)
        self.assertEqual(len({s['invocation_id'] for _, _, s in observed}), 7)
        contexts = {role: {s['context_id'] for r, _, s in observed if r == role} for role in counts}
        self.assertTrue(all(len(ids) == 1 for ids in contexts.values()))
        self.assertEqual(len(set.union(*contexts.values())), 3)
        self.assertEqual(sum(c['usage']['prompt_tokens'] for c in engine.record['calls']), 70)
        for call in engine.record['calls']:
            self.assertEqual(call['module']['invocation_id'], call['id'])
            self.assertEqual(call['module']['status'], 'completed')
            self.assertGreaterEqual(call['queue_ms'], 0)
        # An old invocation's delayed telemetry must not attach to A's next call.
        old, latest = engine.record['calls'][0], engine.record['calls'][-1]
        latest['status'] = 'running'
        await engine.observe('A', 'usage', {'prompt_tokens': 999}, call_id=old['id'])
        self.assertEqual(latest['usage']['prompt_tokens'], 10)

        # Rebinding C changes its backend, not the graph or B/A instances.
        engine.bindings['C']['framework_id'] = 'hermes'
        engine.bindings['C']['model_id'] = 'other-model'
        engine.signatures['C'] = 'new-binding-signature'
        previous = contexts['C'].copy()
        rebound = await engine.turn('C', 'Continue your task with the new backend')
        self.assertEqual(rebound['binding']['framework_id'], 'hermes')
        self.assertNotIn(rebound['module']['context_id'], previous)
        self.assertIn('C-private-2', str(engine.contexts['C']))
