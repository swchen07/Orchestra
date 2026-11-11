import copy
import json
import unittest

from open_webui.execution.claude import ExecutionError
from open_webui.execution.composed_workflow import distribute_review_definition
from open_webui.execution.graph_spec import validate_graph
from open_webui.execution.node_program import upgrade_graph
from open_webui.execution.workflow_templates import compile_template
from open_webui.execution.workflows import WorkflowEngine
from open_webui.execution.graph_boundaries import bind_input_references


class GraphBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def definition(self):
        return {'name': 'Typed ports', 'roles': [{'id': 'worker', 'name': 'Executor'}],
                'lead_role_id': 'worker', 'instructions': '', 'graph': {
            'schema_version': '2.0', 'input_policy': 'scoped', 'nodes': [
                {'id': 'entry', 'op': 'input', 'inputs': {'question': {'source': 'input', 'pointer': '/task'}},
                 'output_schema': {'type': 'object', 'properties': {'question': {'type': 'string'}}, 'required': ['question']}},
                {'id': 'compute', 'op': 'call', 'binding': 'worker',
                 'inputs': {'request': {'source': 'node', 'node': 'entry', 'pointer': '/question'}},
                 'output_schema': {'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value']}},
                {'id': 'exit', 'op': 'output', 'inputs': {'answer': {'source': 'node', 'node': 'compute', 'pointer': '/value'}},
                 'output_schema': {'type': 'object', 'properties': {'answer': {'type': 'integer'}}, 'required': ['answer']}},
            ], 'edges': [], 'outputs': {'answer': {'source': 'node', 'node': 'exit', 'pointer': '/answer'}}}}

    def test_import_rebinds_all_original_inputs_and_preserves_assignment_fields(self):
        before = distribute_review_definition()['graph']
        graph = validate_graph(upgrade_graph(before))
        self.assertEqual(upgrade_graph(graph), graph)
        self.assertEqual([n['op'] for n in graph['nodes']].count('input'), 1)
        self.assertEqual([n['op'] for n in graph['nodes']].count('output'), 1)
        self.assertEqual(next(n for n in graph['nodes'] if n['id']=='assign')['inputs']['goal'],
                         {'source': 'node', 'node': 'workflow_input', 'pointer': '/task'})
        self.assertEqual(next(n for n in graph['nodes'] if n['id']=='execute_b')['inputs']['assignment']['pointer'], '/task_b')
        self.assertEqual(graph['outputs']['answer']['node'], 'workflow_output')
        self.assertEqual(before['nodes'][0]['inputs']['goal']['source'], 'input')

    def test_bad_source_fields_and_boundary_bypasses_fail_before_execution(self):
        graph = self.definition()['graph']
        for ref in ({'source': 'node', 'node': 'entry', 'pointer': '/missing'},
                    {'source': 'input', 'pointer': '/task'}):
            bad = copy.deepcopy(graph)
            bad['nodes'][1]['inputs']['request'] = ref
            with self.assertRaisesRegex(ValueError, 'not declared|Workflow input'):
                validate_graph(bad)
        bad = copy.deepcopy(graph)
        bad['nodes'][0]['inputs']['question']['pointer'] = '/history'
        with self.assertRaisesRegex(ValueError, 'not declared'):
            validate_graph(bad)
        bad = copy.deepcopy(graph)
        bad['outputs']['answer'] = {'source': 'node', 'node': 'compute', 'pointer': '/value'}
        with self.assertRaisesRegex(ValueError, 'Workflow output'):
            validate_graph(bad)

    def test_planner_compatibility_references_respect_renamed_input_fields(self):
        graph = self.definition()['graph']
        graph['nodes'][1]['inputs']['request'] = {'source': 'input', 'pointer': '/task'}
        compiled = validate_graph(bind_input_references(graph))
        self.assertEqual(compiled['nodes'][1]['inputs']['request'], {'source': 'node', 'node': 'entry', 'pointer': '/question'})

    def test_malformed_planner_inputs_are_recoverable_contract_errors(self):
        for bad in ({'missing_amount':3},[],{'request':None}):
            graph=self.definition()['graph'];graph['nodes'][1]['inputs']=bad
            with self.subTest(inputs=bad),self.assertRaisesRegex(ValueError,'typed source reference'):
                bind_input_references(graph)

    async def test_typed_boundaries_execute_without_extra_invocations_or_tokens(self):
        participant = {'id': 'flash', 'name': 'Flash', 'model_id': 'flash', 'framework_id': 'direct'}
        template, bindings = compile_template(self.definition(), {'worker': 'flash'}, [participant])
        async def invoke(binding, messages, native):
            self.assertIn('"request": "Calculate 6 times 7"', messages[-1]['content'])
            return {'choices': [{'message': {'content': '{"value":42}'}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 3}}
        async def publish(record):
            pass
        engine = WorkflowEngine(template, bindings, 'Calculate 6 times 7', invoke, publish)
        self.assertEqual(await engine.execute(), '42')
        self.assertEqual(len(engine.record['calls']), 1)
        self.assertEqual(engine.record['control']['budget']['reported_tokens'], 13)
        self.assertEqual(engine.record['node_states']['entry']['output'], {'question': 'Calculate 6 times 7'})
        self.assertEqual(engine.record['node_states']['exit']['output'], {'answer': 42})

    def test_input_type_failure_prevents_any_paid_call(self):
        definition = self.definition()
        definition['graph']['nodes'][0]['output_schema']['properties']['question']['type'] = 'integer'
        participant = {'id': 'flash', 'name': 'Flash', 'model_id': 'flash'}
        # A host boundary copies its input and cannot cast a known string to an
        # integer. The unified compiler now rejects this before a run exists.
        with self.assertRaisesRegex(ValueError, 'host boundaries copy inputs'):
            compile_template(definition, {'worker': 'flash'}, [participant])

    async def test_order_only_dependency_does_not_copy_upstream_output(self):
        definition = self.definition()
        definition['graph']['nodes'].insert(2, {'id': 'second', 'op': 'call', 'binding': 'worker',
            'context_key': 'separate', 'inputs': {}, 'instruction': 'Use only your assignment.'})
        definition['graph']['edges'] = [{'from': 'compute', 'to': 'second', 'on': 'success'}]
        definition['graph']['nodes'][-1]['inputs']['answer'] = {'source': 'node', 'node': 'second', 'pointer': '/report'}
        definition['graph']['nodes'][-1]['output_schema']['properties']['answer'] = {'type': 'string'}
        participant = {'id': 'flash', 'name': 'Flash', 'model_id': 'flash'}
        template, bindings = compile_template(definition, {'worker': 'flash'}, [participant])
        calls = []
        async def invoke(binding, messages, native):
            if not calls:
                result = '{"value":913721}'
            else:
                self.assertNotIn('913721', json.dumps(messages))
                result = 'Done without peer data'
            calls.append(binding['id'])
            return {'choices': [{'message': {'content': result}}], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}
        async def publish(record):
            pass
        engine = WorkflowEngine(template, bindings, 'Task', invoke, publish)
        self.assertEqual(await engine.execute(), 'Done without peer data')
        self.assertEqual(calls, ['compute', 'separate'])
