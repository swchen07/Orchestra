import asyncio
import copy
import json
import unittest
from unittest.mock import patch

from open_webui.execution.claude import ExecutionError
from open_webui.execution.graph_spec import validate_graph
from open_webui.execution.module_environment import resolve_environment, narrow_requests
from open_webui.execution.port_types import compatible, validate_value
from open_webui.execution.workflow_module import workflow_catalog, bind_workflow_ports
from open_webui.execution.workflow_templates import normalize_graph_template, compile_template
from open_webui.execution.workflows import WorkflowEngine


BACKEND = {'id': 'flash', 'name': 'Flash', 'framework_id': 'direct', 'model_id': 'flash'}


def definition(name, nodes, answer, roles=('worker',), **extra):
    return normalize_graph_template({'name': name, 'roles': [{'id': r, 'name': r} for r in roles],
        'lead_role_id': roles[0], 'graph': {'schema_version': '2.0', 'input_policy': 'scoped',
        'nodes': nodes, 'edges': [], 'outputs': {'answer': answer}, **extra}})


def call(identity, inputs=None, **extra):
    return {'id': identity, 'op': 'call', 'binding': 'worker', 'context_key': identity,
            'inputs': inputs or {}, 'instruction': identity, **extra}


async def publish(record):
    pass


def response(value):
    return {'choices': [{'message': {'content': value if isinstance(value, str) else json.dumps(value)}}],
            'usage': {'prompt_tokens': 5, 'completion_tokens': 2}}


class PortContractTests(unittest.IsolatedAsyncioTestCase):
    def test_route_case_is_a_declared_typed_output(self):
        graph = definition('Typed route', [call('choose', op='route', cases=['yes', 'no']),
            {'id': 'selected', 'op': 'finish', 'inputs': {'answer': {'source': 'node', 'node': 'choose', 'pointer': '/case'}}}],
            {'source': 'node', 'node': 'selected', 'pointer': '/report'})['graph']
        route = next(n for n in graph['nodes'] if n['id'] == 'choose')
        self.assertEqual(route['output_schema']['properties']['case']['enum'], ['yes', 'no'])
    async def test_return_supplied_data_preserves_falsy_json_values(self):
        for result in (0, False, None, []):
            value = definition('Return JSON', [{'id': 'value', 'op': 'finish', 'inputs': {
                'answer': {'source': 'literal', 'value': result}}}], {'source': 'node', 'node': 'value', 'pointer': '/report'})
            template, bindings = compile_template(value, {'worker': 'flash'}, [BACKEND])
            engine = WorkflowEngine(template, bindings, 'Task', None, publish)
            self.assertEqual(json.loads(await engine.execute()), result)
            self.assertEqual(engine.record['calls'], [])

    def test_incompatible_objects_arrays_and_scalar_connections_fail_before_admission(self):
        for source, target in [({'type': 'integer'}, {'type': 'string'}),
                               ({'type': 'array', 'items': {'type': 'string'}}, {'type': 'array', 'items': {'type': 'number'}}),
                               ({'type': 'object', 'properties': {}, 'required': []}, {'type': 'object', 'required': ['missing']})]:
            with self.subTest(source=source):
                nodes = [call('producer', output_schema={'type': 'object', 'properties': {'value': source}, 'required': ['value']}),
                         call('consumer', {'data': {'source': 'node', 'node': 'producer', 'pointer': '/value'}},
                              input_schema={'type': 'object', 'properties': {'data': target}, 'required': ['data']})]
                with self.assertRaisesRegex(ValueError, 'incompatible'):
                    definition('Bad types', nodes, {'source': 'node', 'node': 'consumer', 'pointer': '/report'})
        self.assertTrue(compatible({'type': 'integer'}, {'type': 'number'}))
        self.assertFalse(compatible({'enum': ['x', 3]}, {'type': 'string'}))

    def test_schemas_are_local_and_values_are_not_coerced_or_echoed(self):
        for value in ('PRIVATE_VALUE', float('nan')):
            with self.assertRaises(ValueError) as caught:
                validate_value(value, {'type': 'integer'}, 'Typed input')
            self.assertNotIn('PRIVATE_VALUE', str(caught.exception))
        with self.assertRaisesRegex(ValueError, 'local'):
            definition('Remote ref', [call('x', input_schema={'$ref': 'https://example.org/schema'})],
                       {'source': 'node', 'node': 'x', 'pointer': '/report'})

    def test_host_boundaries_preserve_known_types_and_reject_implicit_casts(self):
        graph = definition('Typed result', [call('number', output_schema={'type': 'object',
            'properties': {'value': {'type': 'integer'}}, 'required': ['value']})],
            {'source': 'node', 'node': 'number', 'pointer': '/value'})['graph']
        self.assertEqual(graph['output_schema']['properties']['answer']['type'], 'integer')
        exit = next(n for n in graph['nodes'] if n['op'] == 'output')
        exit['output_schema']['properties']['answer'] = {'type': 'string'}
        with self.assertRaisesRegex(ValueError, 'copy inputs'):
            validate_graph(graph)

    def test_references_cannot_traverse_known_text_reports_or_use_negative_array_indices(self):
        from open_webui.execution.graph_spec import pointer
        for path in ('/report/value', '/value/-1'):
            nodes = [call('producer', output_schema={'type': 'object', 'properties': {'value': {'type': 'array', 'items': {'type': 'integer'}}}}),
                     call('consumer', {'data': {'source': 'node', 'node': 'producer', 'pointer': path}})]
            with self.assertRaisesRegex(ValueError, 'scalar|nonnegative'):
                definition('Bad reference', nodes, {'source': 'node', 'node': 'consumer', 'pointer': '/report'})
        for path in ('/-1', '/01', '/-'):
            with self.assertRaises(ValueError): pointer([10, 20], path)

    async def test_runtime_checks_unknown_upstream_field_before_consumer_call(self):
        value = definition('Runtime types', [call('producer', output_schema={'type': 'object', 'properties': {'value': {}}, 'required': ['value']}),
            call('consumer', {'data': {'source': 'node', 'node': 'producer', 'pointer': '/value'}},
                 input_schema={'type': 'object', 'properties': {'data': {'type': 'integer'}}, 'required': ['data']})],
            {'source': 'node', 'node': 'consumer', 'pointer': '/report'})
        template, bindings = compile_template(value, {'worker': 'flash'}, [BACKEND])
        seen = []
        async def invoke(binding, messages, native):
            seen.append(binding['id'])
            return response({'value': 'not-a-number'})
        engine = WorkflowEngine(template, bindings, 'Task', invoke, publish)
        with self.assertRaises(ExecutionError):
            await engine.execute()
        self.assertEqual(seen, ['producer'])
        self.assertIn('violates type', engine.record['node_states']['consumer']['error'])

    async def test_non_task_workflow_ingress_and_egress_are_checked(self):
        graph = {'schema_version': '2.0', 'nodes': [
            {'id': 'input', 'op': 'input', 'inputs': {'count': {'source': 'input', 'pointer': '/count'}},
             'output_schema': {'type': 'object', 'properties': {'count': {'type': 'integer'}}, 'required': ['count']}},
            {'id': 'output', 'op': 'output', 'inputs': {'answer': {'source': 'node', 'node': 'input', 'pointer': '/count'}},
             'output_schema': {'type': 'object', 'properties': {'answer': {'type': 'integer'}}, 'required': ['answer']}}],
            'outputs': {'answer': {'source': 'node', 'node': 'output', 'pointer': '/answer'}},
            'input_schema': {'type': 'object', 'properties': {'count': {'type': 'integer'}}, 'required': ['count']},
            'output_schema': {'type': 'object', 'properties': {'answer': {'type': 'number'}}, 'required': ['answer']}}
        template = {'id': 'typed', 'name': 'Typed', 'lead_id': 'worker', 'graph': validate_graph(graph)}
        for data, good in [({'count': 3}, True), ({'count': '3'}, False)]:
            engine = WorkflowEngine(template, [BACKEND | {'id': 'worker'}], '', None, publish, input_values=data)
            if good:
                self.assertEqual(await engine.execute(), '3')
            else:
                with self.assertRaisesRegex(ExecutionError, 'Workflow input'):
                    await engine.execute()
            self.assertEqual(engine.record['calls'], [])

    def test_optional_unavailable_outcomes_require_an_explicit_compatible_union(self):
        nodes = [call('a'), call('b', {'data': {'source': 'node', 'node': 'a', 'pointer': '/report', 'optional': True}},
            input_schema={'type': 'object', 'properties': {'data': {'type': 'string'}}, 'required': ['data']})]
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            definition('Optional must be handled', nodes, {'source': 'node', 'node': 'b', 'pointer': '/report'})


class EnvironmentContractTests(unittest.TestCase):
    def test_catalog_and_compiler_use_the_same_environment_capabilities(self):
        from open_webui.execution.module_protocol import module_catalog
        dsh = BACKEND | {'framework_id': 'dsh', 'id': 'dsh'}
        claude = BACKEND | {'framework_id': 'claude-code', 'id': 'claude'}
        rows = {r['id']: r for r in module_catalog({'dsh': dsh, 'claude': claude})}
        self.assertEqual(rows['dsh']['environment_contract']['supported_requests']['tools'], ['inherit'])
        self.assertIn('none', rows['claude']['environment_contract']['supported_requests']['tools'])
        graph = definition('Unsupported before commit', [call('x', environment={'tools': 'none'})],
            {'source': 'node', 'node': 'x', 'pointer': '/report'})['graph']
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            bind_workflow_ports(graph, {'worker': dsh})
        restricted = dsh | {'_environment_request': {'tools': 'none'}}
        self.assertEqual(module_catalog({'dsh': restricted}), [])

    def test_explicit_authorities_narrowing_and_unsupported_restrictions(self):
        for framework in ('direct', 'dsh', 'claude-code', 'hermes', 'codex'):
            env = resolve_environment({'framework_id': framework}, 'path/role')
            self.assertEqual(env.namespace, 'path/role')
            self.assertNotIn('api_key', json.dumps(env.public()))
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                resolve_environment({'framework_id': framework, '_environment_request': {'isolation': 'sandbox'}}, 'role')
        self.assertEqual(resolve_environment({'framework_id': 'claude-code', '_environment_request': {'tools': 'none'}}, 'x').filesystem, 'none')
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            resolve_environment({'framework_id': 'hermes', '_environment_request': {'network': 'provider-only'}}, 'x')
        with self.assertRaisesRegex(ValueError, 'widen'):
            narrow_requests({'filesystem': 'none'}, {'filesystem': 'read-only'})
        with self.assertRaisesRegex(ValueError, 'weaken'):
            narrow_requests({'isolation': 'process'}, {'isolation': 'logical'})
        self.assertEqual(narrow_requests({'tools': 'none'}, {'tools': 'inherit'}), {'tools': 'none'})


class ControllerObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_controller_output_is_metered_then_repaired_with_bounded_admission(self):
        from open_webui.execution.adaptive_workflow import adaptive_definition
        value = normalize_graph_template(adaptive_definition())
        template, bindings = compile_template(value, {'controller': 'flash', 'reviewer': 'flash'}, [BACKEND])
        decisions = 0
        async def invoke(binding, messages, native):
            nonlocal decisions
            if binding['id'] == 'controller':
                decisions += 1
                if decisions == 1:
                    return response('{"action":')
                self.assertIn('Expecting value', messages[-1]['content'])
                return response({'action': 'propose_plan', 'graph': {'nodes': [call('work') | {'binding': 'flash'}], 'result_node': 'work'}})
            if binding['id'] == 'reviewer':
                return response({'verdict': 'approve', 'summary': 'Checked', 'issues': [],
                    'checked_report_refs': [c['report_id'] for c in engine.record['calls'] if c['kind'] == 'work'], 'answer': 'Correct'})
            return response('Evidence')
        engine = WorkflowEngine(template, bindings, 'Task', invoke, publish, registry=[BACKEND])
        self.assertEqual(await asyncio.wait_for(engine.execute(), 2), 'Correct')
        self.assertEqual(decisions, 2)
        self.assertEqual(engine.record['calls'][0]['status'], 'failed')
        self.assertEqual(engine.record['calls'][0]['invalid_output'], '{"action":')
        self.assertEqual(engine.record['control']['budget']['reported_tokens'], 28)

    async def test_one_initial_decision_advances_fast_consumer_without_slow_branch_or_wait_polling(self):
        from open_webui.execution.adaptive_workflow import adaptive_definition
        value = normalize_graph_template(adaptive_definition())
        template, bindings = compile_template(value, {'controller': 'flash', 'reviewer': 'flash'}, [BACKEND])
        consumer_started = asyncio.Event()
        decisions = 0
        async def invoke(binding, messages, native):
            nonlocal decisions
            identity = binding['id']
            if identity == 'controller':
                decisions += 1
                nodes = [call('fast'), call('slow'), call('consumer', {'value': {'source': 'node', 'node': 'fast', 'pointer': '/report'}}),
                    {'id': 'collect', 'op': 'join', 'policy': 'all_success', 'inputs': {
                        k: {'source': 'node', 'node': k, 'pointer': '/report'} for k in ('consumer', 'slow')}}]
                for node in nodes:
                    if node.get('binding') == 'worker': node['binding'] = 'flash'
                return response({'action': 'propose_plan', 'graph': {'nodes': nodes, 'edges': [], 'result_node': 'collect'}})
            if identity == 'slow':
                await asyncio.wait_for(consumer_started.wait(), 1)
            if identity == 'consumer':
                consumer_started.set()
            if identity == 'reviewer':
                return response({'verdict': 'approve', 'summary': 'All evidence checked', 'issues': [],
                    'checked_report_refs': [c['report_id'] for c in engine.record['calls'] if c['kind'] == 'work'], 'answer': 'Correct'})
            return response(identity + ' evidence')
        engine = WorkflowEngine(template, bindings, 'Task', invoke, publish, registry=[BACKEND])
        self.assertEqual(await asyncio.wait_for(engine.execute(), 2), 'Correct')
        self.assertEqual(decisions, 1)
        self.assertEqual(len(engine.record['calls']), 5)
        self.assertGreater(engine.record['control']['observations_suppressed'], 0)


class WorkflowModuleTests(unittest.IsolatedAsyncioTestCase):
    def test_catalog_excludes_a_bound_parent_with_an_unavailable_child(self):
        template, _, catalog = self.catalog()
        child = catalog[0]['_workflow_template']
        rows = workflow_catalog([child, template], {template['id']: {'worker': 'flash'}}, [BACKEND])
        self.assertEqual(rows, [])

    def catalog(self, *, child_limits=None):
        child = definition('Private pair', [call('left'), call('right'),
            {'id': 'collect', 'op': 'join', 'policy': 'all_success', 'inputs': {
                k: {'source': 'node', 'node': k, 'pointer': '/report'} for k in ('left', 'right')}}],
            {'source': 'node', 'node': 'collect', 'pointer': '/report'}, limits=child_limits or {})
        catalog = workflow_catalog([child], {child['id']: {'worker': 'flash'}}, [BACKEND])
        parent = definition('Parent', [{'id': 'team', 'action': 'workflow.call', 'binding': catalog[0]['id'],
                                       'context_key': 'team', 'inputs': {'task': {'source': 'input', 'pointer': '/task'}}}],
                            {'source': 'node', 'node': 'team', 'pointer': '/answer'})
        template, bindings = compile_template(parent, {'worker': 'flash'}, [BACKEND, *catalog])
        return template, bindings, catalog

    async def test_nested_two_turn_histories_are_private_and_leaf_usage_charged_once(self):
        template, bindings, catalog = self.catalog()
        previous = []
        seen = []
        active, peak = 0, 0
        for turn in (1, 2):
            async def invoke(binding, messages, native):
                nonlocal active, peak
                local = binding['id']; peer = 'right' if local == 'left' else 'left'
                text = str(messages)
                self.assertNotIn(peer + '-private', text)
                if turn == 2:
                    self.assertIn(local + '-private', text)
                self.assertEqual(binding['_module_scope']['instance_id'], 'team/' + local)
                seen.append((turn, local))
                active += 1; peak = max(peak, active)
                await asyncio.sleep(.01)
                active -= 1
                return response(local + '-private')
            engine = WorkflowEngine(template, bindings, 'Task', invoke, publish, previous=previous, registry=[BACKEND, *catalog])
            with patch('open_webui.execution.workflows.claim_native_context', return_value=False):
                await asyncio.wait_for(engine.execute(), 3)
            self.assertEqual(engine.record['control']['budget']['reported_tokens'], 14)
            self.assertEqual(len(engine.record['calls']), 2)
            self.assertEqual({c['participant_id'] for c in engine.record['calls']}, {'team/left', 'team/right'})
            rollup = engine.record['node_states']['team']['workflow']
            self.assertEqual(rollup['usage']['prompt_tokens'], 10)
            self.assertEqual(set(rollup['leaf_call_ids']), {c['id'] for c in engine.record['calls']})
            self.assertEqual(len(engine.record['child_workflows']['team']['role_contexts']), 3)
            from open_webui.execution.module_protocol import report_rows
            composite = next(r for r in report_rows(engine.record) if r['kind'] == 'composite')
            self.assertEqual(composite['envelope']['usage_refs'], rollup['leaf_call_ids'])
            self.assertIsNone(composite['envelope']['usage_ref'])
            ref = {'source': 'report', 'report_ref': composite['id'], 'pointer': '/report'}
            self.assertEqual(engine.runner.resolve(ref), composite['output'])
            if turn == 2:
                old = next(r for r in engine.prior_reports if r['kind'] == 'composite')
                self.assertEqual(engine.runner.resolve({**ref, 'report_ref': old['id']}), old['output'])
            previous.append(copy.deepcopy(engine.record))
        self.assertEqual(len(seen), 4)
        self.assertEqual(peak, 2)

    async def test_child_and_parent_call_budgets_share_admission(self):
        for local_limit, root_limit in ((1, 24), (24, 1)):
            template, bindings, catalog = self.catalog(child_limits={'max_calls': local_limit})
            template['graph']['limits']['max_calls'] = root_limit
            engine = WorkflowEngine(template, bindings, 'Task', lambda *a: asyncio.sleep(0, result=response('done')), publish, registry=catalog)
            with self.assertRaises(ExecutionError):
                await asyncio.wait_for(engine.execute(), 3)
            self.assertEqual(len(engine.record['calls']), 1)
            self.assertEqual(engine.record['control']['budget']['reported_tokens'], 7)

    async def test_local_serial_child_does_not_occupy_a_global_slot_while_waiting(self):
        _, _, catalog = self.catalog(child_limits={'max_concurrency': 1})
        nodes = [{'id': k, 'op': 'workflow', 'binding': catalog[0]['id'], 'context_key': k,
                  'inputs': {'task': {'source': 'literal', 'value': k}}} for k in ('alpha', 'beta')]
        nodes.append({'id': 'both', 'op': 'join', 'policy': 'all_success', 'inputs': {
            k: {'source': 'node', 'node': k, 'pointer': '/answer'} for k in ('alpha', 'beta')}})
        value = definition('Two serial teams', nodes, {'source': 'node', 'node': 'both', 'pointer': '/report'})
        template, bindings = compile_template(value, {'worker': 'flash'}, [BACKEND, *catalog])
        starts = {k: asyncio.Event() for k in ('alpha', 'beta')}
        async def invoke(binding, messages, native):
            if binding['id'] == 'left':
                path = binding['_module_scope']['instance_id'].split('/')[0]
                starts[path].set()
                await asyncio.wait_for(asyncio.gather(*(s.wait() for s in starts.values())), 1)
            return response('evidence')
        engine = WorkflowEngine(template, bindings, 'Task', invoke, publish, registry=catalog)
        await asyncio.wait_for(engine.execute(), 2)
        self.assertTrue(all(s.is_set() for s in starts.values()))
        self.assertEqual(len(engine.record['calls']), 4)
        self.assertEqual(engine.record['control']['budget']['reported_tokens'], 28)

    async def test_nested_cancellation_propagates_to_all_children(self):
        template, bindings, catalog = self.catalog()
        started = asyncio.Event()
        async def invoke(*args):
            started.set()
            await asyncio.Event().wait()
        engine = WorkflowEngine(template, bindings, 'Task', invoke, publish, registry=catalog)
        task = asyncio.create_task(engine.execute())
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(all(c['status'] == 'cancelled' for c in engine.record['calls']))
        self.assertEqual(engine.record['child_workflows']['team']['status'], 'cancelled')

    async def test_nested_manual_approval_routes_through_parent_and_common_call_alias(self):
        template, bindings, catalog = self.catalog()
        catalog[0]['_workflow_template']['execution_policy'] = 'review'
        node = next(n for n in template['graph']['nodes'] if n['id'] == 'team')
        node.update(op='call', action='module.call')
        seen = []
        async def invoke(binding, *args):
            seen.append(binding['id'])
            return response('evidence')
        engine = WorkflowEngine(template, bindings, 'Task', invoke, publish, registry=catalog)
        task = asyncio.create_task(engine.execute())
        for _ in range(100):
            child = engine.child_engines.get('team')
            if child and child.runner and child.runner.paused:
                break
            await asyncio.sleep(.001)
        self.assertEqual(seen, [])
        await engine.runner.control({'action': 'approve', 'revision': 1, 'workflow_path': 'team'})
        await asyncio.wait_for(task, 2)
        self.assertEqual(len(seen), 2)
        self.assertEqual(engine.record['node_states']['team']['action'], 'module.call')

    async def test_recursion_fails_without_calling_a_model_and_public_contract_cannot_be_widened(self):
        template, bindings, catalog = self.catalog()
        catalog[0]['_workflow_template']['id'] = template['id']
        engine = WorkflowEngine(template, bindings, 'Task', None, publish, registry=catalog)
        with self.assertRaises(ExecutionError):
            await engine.execute()
        self.assertIn('recursion', engine.record['error'])
        self.assertEqual(engine.record['calls'], [])
        graph = copy.deepcopy(template['graph'])
        next(n for n in graph['nodes'] if n['id'] == 'team')['output_schema'] = {'type': 'object', 'properties': {'impossible': {'type': 'integer'}}, 'required': ['impossible']}
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            bind_workflow_ports(graph, {p['id']: p for p in catalog})
