import asyncio
import copy
import json
import unittest
from unittest.mock import patch

from open_webui.execution.adaptive_workflow import adaptive_definition, normalize_adaptive
from open_webui.execution.workflow_presets import complete_presets, reconcile_catalog, archive_template, restore_template
from open_webui.execution.workflow_templates import compile_chat_template, compile_template
from open_webui.execution.graph_spec import validate_graph
from open_webui.execution.module_protocol import usage_budget
from open_webui.execution.workflows import WorkflowEngine


class DynamicTemplateTests(unittest.TestCase):
    def test_custom_library_and_delete_all_survive_catalog_reads_and_can_restore(self):
        rows = complete_presets([])
        custom = {**copy.deepcopy(rows[0]), 'id': 'custom', 'name': 'My planning rules', 'builtin_key': None}
        rows.append(custom)
        self.assertEqual(reconcile_catalog(rows)[0], rows)
        archive = []
        for row in list(rows):
            rows, archive = archive_template(rows, archive, row['id'], row['version'])
        self.assertEqual(reconcile_catalog(rows, archive), ([], archive))  # no reseeding
        rows, archive = restore_template(rows, archive, 'custom')
        self.assertEqual(rows[0]['name'], 'My planning rules')
        self.assertEqual(rows[0]['version'], custom['version'] + 1)
        self.assertEqual(reconcile_catalog(rows, archive), (rows, archive))
        with self.assertRaisesRegex(ValueError, 'changed'):
            archive_template(rows, archive, 'custom', custom['version'])

    def test_only_core_roles_and_authorized_project_backends_can_be_used(self):
        definition = adaptive_definition() | {'id': '12345678-1234-4234-8234-123456789abc', 'preset_key': 'adaptive_modules'}
        self.assertEqual({r['id'] for r in normalize_adaptive(definition)['roles']}, {'controller', 'reviewer'})
        invalid = copy.deepcopy(definition)
        invalid['roles'].append({'id': 'fixed_worker', 'name': 'Fixed worker', 'instructions': ''})
        with self.assertRaisesRegex(ValueError, 'only Controller'):
            normalize_adaptive(invalid)
        available = [{'id': 'backend', 'model_id': 'flash', 'name': 'Flash', 'framework_id': 'direct'}]
        overrides = {'workflow_id': '12345678-1234-4234-8234-123456789abc', 'roles': {}, 'executor_pool': ['foreign']}
        with self.assertRaisesRegex(ValueError, 'Executor pool'):
            compile_chat_template(definition, {'12345678-1234-4234-8234-123456789abc': {'controller': 'backend', 'reviewer': 'backend'}}, overrides, available, [])
        forbidden = copy.deepcopy(definition['graph'])
        forbidden['nodes'] = [{'id': 'work', 'op': 'call', 'binding': 'controller', 'inputs': {}}]
        forbidden['result_node'] = 'work'
        with self.assertRaisesRegex(ValueError, 'Controller plans work'):
            validate_graph(forbidden)


class DynamicExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_controller_creates_parallel_instances_from_project_catalog_and_continues_each_context(self):
        definition = adaptive_definition() | {'id': '12345678-1234-4234-8234-123456789abc', 'preset_key': 'adaptive_modules'}
        definition['graph']['controller']['on_events'] = False  # one generated graph per test turn
        backend = {'id': 'catalog:flash', 'name': 'Flash', 'framework_id': 'direct', 'model_id': 'flash'}
        template, core = compile_template(definition, {'controller': backend['id'], 'reviewer': backend['id']}, [backend])
        self.assertEqual({p['id'] for p in core}, {'controller', 'reviewer'})
        previous = []
        for turn in (1, 2):
            starts = {k: asyncio.Event() for k in ('north', 'south')}
            async def publish(record):
                pass
            async def invoke(binding, messages, native):
                identity = binding['id']
                if identity == 'controller':
                    catalog = json.loads(messages[-1]['content'].split('AVAILABLE MODULES:\n')[1].split('\nCURRENT PLAN:')[0])
                    self.assertEqual([p['id'] for p in catalog], [backend['id']])
                    nodes = [{'id': k, 'context_key': k, 'binding': backend['id'], 'op': 'call',
                              'instruction': k, 'inputs': {}} for k in starts]
                    nodes.append({'id': 'answer', 'op': 'finish', 'inputs': {
                        k: {'source': 'node', 'node': k, 'pointer': '/report'} for k in starts}})
                    output = {'action': 'propose_plan', 'graph': {'nodes': nodes, 'edges': [], 'result_node': 'answer'}}
                elif identity == 'reviewer':
                    output = {'verdict': 'approve', 'summary': 'Verified both reports.', 'issues': [],
                              'checked_report_refs': [c['report_id'] for c in engine.record['calls'] if c['kind'] == 'work'],
                              'answer': 'Verified both private worker reports.'}
                else:
                    starts[identity].set()
                    await asyncio.wait_for(asyncio.gather(*(e.wait() for e in starts.values())), 1)
                    assistant = [m['content'] for m in messages if m['role'] == 'assistant']
                    self.assertEqual(assistant, [] if turn == 1 else [identity + '-private'])
                    self.assertNotIn(('south' if identity == 'north' else 'north') + '-private', str(messages))
                    output = identity + '-private'
                return {'choices': [{'message': {'content': output if isinstance(output, str) else json.dumps(output)}}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
            engine = WorkflowEngine(template, core, 'Independent two-depot task', invoke, publish,
                                    previous=previous, registry=[backend])
            with patch('open_webui.execution.workflows.claim_native_context', return_value=False):
                await asyncio.wait_for(engine.execute(), 3)
            self.assertEqual(engine.record['review']['status'], 'approved')
            self.assertEqual(len(engine.record['calls']), 4)
            self.assertEqual(usage_budget(engine.record['calls'])['reported_tokens'], 48)
            self.assertEqual(engine.record['module_names']['north'], 'Executor 01')
            self.assertEqual(engine.record['module_names']['south'], 'Executor 02')
            self.assertNotEqual(engine.role_states['north']['scope_id'], engine.role_states['south']['scope_id'])
            previous.append(copy.deepcopy(engine.record))


class GraphSourceContractTests(unittest.IsolatedAsyncioTestCase):
    def predefined(self):
        return {'id': '12345678-1234-4234-8234-123456789abc', 'name': 'Saved chain',
                'preset_key': 'adaptive_modules',  # starter provenance is not execution behavior
                'roles': [{'id': 'reader', 'name': 'Reader', 'instructions': 'Extract facts.'},
                          {'id': 'writer', 'name': 'Writer', 'instructions': 'Transform the supplied facts.'}],
                'lead_role_id': 'reader', 'instructions': '', 'max_rounds': 3,
                'execution_policy': 'automatic',
                'graph': {'input_policy': 'scoped', 'nodes': [
                    {'id': 'extract', 'op': 'call', 'binding': 'reader', 'instruction': 'Extract.',
                     'inputs': {'task': {'source': 'input', 'pointer': '/task'}}},
                    {'id': 'transform', 'op': 'call', 'binding': 'writer', 'instruction': 'Transform.',
                     'inputs': {'facts': {'source': 'node', 'node': 'extract', 'pointer': '/report'}}}],
                    'edges': [], 'result_node': 'transform', 'limits': {'max_concurrency': 1}}}

    def test_graph_source_not_starter_name_controls_template_validation_and_prompts(self):
        from open_webui.execution.workflow_templates import normalize_graph_template, module_system_prompt
        from open_webui.execution.compare_workflow import is_comparison_graph
        saved = normalize_graph_template(self.predefined())
        self.assertNotIn('controller', saved['graph'])
        self.assertEqual(next(n for n in saved['graph']['nodes'] if n['id']=='transform')['inputs']['facts']['node'], 'extract')
        self.assertEqual(saved['graph']['limits']['max_concurrency'], 1)
        self.assertEqual(saved['roles'][1]['instructions'], 'Transform the supplied facts.')
        prompt = module_system_prompt(saved, {'id': 'writer', 'name': 'Writer',
                                            'role_instructions': 'Transform the supplied facts.'})
        self.assertIn('isolated callable module', prompt)
        self.assertNotIn('Solve the complete user task', prompt)
        generated = normalize_graph_template(adaptive_definition() | {'preset_key': 'compare_modules'})
        self.assertTrue(next(n for n in generated['graph']['nodes'] if n['op'] == 'plan')['planning']['initial'])
        self.assertEqual({r['id'] for r in generated['roles']}, {'controller', 'reviewer'})
        compare = next(r for r in complete_presets([]) if r['name'] == 'Compare')
        self.assertTrue(is_comparison_graph(compare))
        from open_webui.execution.workflow_templates import template_view
        self.assertTrue(is_comparison_graph(normalize_graph_template(template_view(compare))))
        self.assertFalse(is_comparison_graph(saved))

    async def test_predefined_chain_follows_saved_dependencies_without_planning_or_review_calls(self):
        from open_webui.execution.workflow_templates import normalize_graph_template
        definition = normalize_graph_template(self.predefined())
        participant = {'id': 'flash', 'name': 'Flash', 'model_id': 'flash', 'framework_id': 'direct'}
        template, bindings = compile_template(definition, {'reader': 'flash', 'writer': 'flash'}, [participant])
        seen = []
        async def invoke(binding, messages, native):
            if binding['id'] == 'reader':
                self.assertIn('RAW_TASK', messages[-1]['content'])
                output = 'EXTRACTED_FACTS'
            else:
                self.assertEqual(seen, ['reader'])
                self.assertIn('EXTRACTED_FACTS', messages[-1]['content'])
                self.assertNotIn('RAW_TASK', messages[-1]['content'])
                output = 'FINAL_RESULT'
            seen.append(binding['id'])
            return {'choices': [{'message': {'content': output}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
        async def publish(record):
            pass
        engine = WorkflowEngine(template, bindings, 'RAW_TASK', invoke, publish)
        self.assertEqual(await engine.execute(), 'FINAL_RESULT')
        self.assertEqual(seen, ['reader', 'writer'])
        self.assertEqual([c['kind'] for c in engine.record['calls']], ['work', 'work'])
