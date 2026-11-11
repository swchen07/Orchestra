"""Implementation regressions for the public module invocation boundary."""
import asyncio
import copy
import json
import unittest
import uuid

from open_webui.execution.claude import ExecutionError
from open_webui.execution.workflow_templates import normalize_graph_template, compile_template, compile_chat_template
from open_webui.execution.workflow_module import workflow_catalog
from open_webui.execution.workflows import WorkflowEngine

BACKEND = {'id': 'flash', 'name': 'Flash', 'model_id': 'flash', 'framework_id': 'direct'}
EMPTY = {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}

async def publish(record):
    pass

def definition(nodes, outputs, **fields):
    return normalize_graph_template({'id': str(uuid.uuid4()), 'name': 'Reusable',
        'roles': [{'id': 'worker', 'name': 'Worker'}], 'lead_role_id': 'worker',
        'graph': {'schema_version': '2.0', 'nodes': nodes, 'edges': [], 'outputs': outputs, **fields}})

def ref(node, field):
    return {'source': 'node', 'node': node, 'pointer': '/' + field}

def response(value):
    return {'choices': [{'message': {'content': value if isinstance(value, str) else json.dumps(value)}}],
            'usage': {'prompt_tokens': 2, 'completion_tokens': 1}}

class ComposableInvocationTests(unittest.IsolatedAsyncioTestCase):
    def compile(self, value, catalog=(), selected='flash'):
        return compile_template(value, {'worker': selected}, [BACKEND, *catalog])

    async def test_role_substitution_preserves_action_and_custom_falsy_public_outputs(self):
        child = definition([{'id': 'data', 'op': 'finish', 'inputs': {'report': {'source': 'literal', 'value': False}}}],
                           {'flag': ref('data', 'report'), 'count': {'source': 'literal', 'value': 0},
                            'absent': {'source': 'literal', 'value': None}}, input_schema=EMPTY)
        catalog = workflow_catalog([child], {child['id']: {'worker': 'flash'}}, [BACKEND])
        self.assertEqual(len(catalog), 1)
        parent = definition([{'id': 'delegate', 'action': 'module.call', 'binding': 'worker', 'inputs': {}}],
                            {'answer': ref('delegate', 'flag')}, input_schema=EMPTY)
        template, bindings = self.compile(parent, catalog, catalog[0]['id'])
        engine = WorkflowEngine(template, bindings, '', None, publish, registry=catalog, input_values={})
        self.assertEqual(await engine.execute(), 'false')
        state = engine.record['node_states']['delegate']
        self.assertEqual(state['action'], 'module.call')
        self.assertEqual({k: state['output'][k] for k in ('flag', 'count', 'absent')}, {'flag': False, 'count': 0, 'absent': None})
        self.assertEqual(engine.record['calls'], [])
        self.assertEqual(state['last_invocation']['id'], state['output']['__report']['invocation_id'])

    async def test_custom_root_delivery_rejects_before_leaf_work(self):
        value = definition([{'id': 'work', 'op': 'call', 'binding': 'worker', 'inputs': {}}],
                           {'custom': ref('work', 'report')})
        template, bindings = self.compile(value)
        engine = WorkflowEngine(template, bindings, 'Task', None, publish)
        with self.assertRaisesRegex(ExecutionError, 'delivery_port'):
            await engine.execute()
        self.assertEqual(engine.record['calls'], [])
        template['graph']['delivery_port'] = 'custom'
        engine = WorkflowEngine(template, bindings, 'Task', lambda *a: asyncio.sleep(0, result=response('ok')), publish)
        self.assertEqual(await engine.execute(), 'ok')

    async def test_invocation_input_is_explicit_and_root_cannot_fabricate_it(self):
        child = definition([
            {'id': 'entry', 'op': 'input', 'inputs': {'responsibilities': {'source': 'invocation', 'pointer': '/instruction'}},
             'output_schema': {'type': 'object', 'properties': {'responsibilities': {'type': 'string'}}, 'required': ['responsibilities']}},
            {'id': 'exit', 'op': 'output', 'inputs': {'result': ref('entry', 'responsibilities')},
             'output_schema': {'type': 'object', 'properties': {'result': {'type': 'string'}}, 'required': ['result']}}],
            {'result': ref('exit', 'result')}, input_schema=EMPTY)
        catalog = workflow_catalog([child], {child['id']: {'worker': 'flash'}}, [BACKEND])
        parent = definition([{'id': 'team', 'op': 'call', 'binding': catalog[0]['id'], 'instruction': 'bounded work', 'inputs': {}}],
                            {'answer': ref('team', 'result')}, input_schema=EMPTY)
        template, bindings = self.compile(parent, catalog)
        engine = WorkflowEngine(template, bindings, '', None, publish, registry=catalog, input_values={})
        self.assertEqual(await engine.execute(), 'bounded work')
        template, bindings = self.compile(child)
        template['graph']['delivery_port'] = 'result'
        engine = WorkflowEngine(template, bindings, '', None, publish, input_values={})
        with self.assertRaisesRegex(ExecutionError, 'invocation'):
            await engine.execute()
        self.assertEqual(engine.record['calls'], [])

    async def test_same_role_without_explicit_key_has_separate_private_history(self):
        value = definition([
            {'id': 'left', 'op': 'call', 'binding': 'worker', 'inputs': {}},
            {'id': 'right', 'op': 'call', 'binding': 'worker', 'inputs': {}}], {'answer': ref('right', 'report')})
        template, bindings = self.compile(value)
        async def invoke(binding, messages, native):
            self.assertEqual([m for m in messages if m['role'] == 'assistant'], [])
            return response(binding['id'] + '-private')
        engine = WorkflowEngine(template, bindings, 'Task', invoke, publish)
        self.assertEqual(await engine.execute(), 'right-private')
        self.assertEqual({c['participant_id'] for c in engine.record['calls']}, {'left', 'right'})

    def test_saved_implicit_legacy_context_keys_are_preserved_on_copy(self):
        old = definition([{'id': 'work', 'op': 'call', 'binding': 'worker', 'inputs': {}}], {'answer': ref('work', 'report')})
        node = next(n for n in old['graph']['nodes'] if n['id'] == 'work')
        node.pop('context_key')
        old['version'] = 1
        original = copy.deepcopy(old)
        template, _ = self.compile(old)
        self.assertEqual(next(n for n in template['graph']['nodes'] if n['id'] == 'work')['context_key'], 'worker')
        self.assertEqual(old, original)

    def test_native_checkpoint_cannot_be_claimed_again_under_a_different_role(self):
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch
        from open_webui.execution.workflow_memory import claim_native_context
        for field in ('native_session_id', 'scope_id'):
            with tempfile.TemporaryDirectory() as directory, patch.dict('sys.modules', {'open_webui.env': SimpleNamespace(DATA_DIR=directory)}):
                state = {'source_run_id': str(uuid.uuid4()), field: 'same-checkpoint'}
                self.assertTrue(claim_native_context(state, 'left', 'run-a'))
                self.assertFalse(claim_native_context(state, 'right', 'run-b'))
        with tempfile.TemporaryDirectory() as directory, patch.dict('sys.modules', {'open_webui.env': SimpleNamespace(DATA_DIR=directory)}):
            state = {'source_run_id': str(uuid.uuid4())}
            self.assertTrue(claim_native_context(state, 'legacy', 'run-a'))
            self.assertFalse(claim_native_context({**state, 'native_session_id': 'old'}, 'migrated', 'run-b'))

    async def test_composite_review_revise_empty_answer_reaches_host_correction_gate(self):
        from open_webui.execution.node_program import capability_schema
        schema = capability_schema('review')
        fields = schema['properties']
        child = definition([
            {'id': 'entry', 'op': 'input', 'inputs': {'packet': {'source': 'invocation', 'pointer': '/context/prompt'}},
             'output_schema': {'type': 'object', 'properties': {'packet': {'type': 'string'}}, 'required': ['packet']}},
            {'id': 'inspect', 'op': 'call', 'binding': 'worker', 'inputs': {'evidence': ref('entry', 'packet')}, 'output_schema': schema},
            {'id': 'exit', 'op': 'output', 'inputs': {name: ref('inspect', name) for name in fields},
             'output_schema': {'type': 'object', 'properties': fields, 'required': list(fields)}}],
            {name: ref('exit', name) for name in fields}, input_schema={'type': 'object'})
        catalog = workflow_catalog([child], {child['id']: {'worker': 'flash'}}, [BACKEND])
        parent = normalize_graph_template({'name': 'Review correction', 'roles': [{'id': key, 'name': key} for key in ('writer', 'critic')],
            'lead_role_id': 'writer', 'graph': {'schema_version': '2.0', 'nodes': [
                {'id': 'draft', 'op': 'call', 'binding': 'writer', 'inputs': {}},
                {'id': 'review', 'op': 'review', 'binding': 'critic', 'inputs': {'candidate': ref('draft', 'report')},
                 'review': {'repair_node': 'draft', 'max_repairs': 1}}], 'edges': [], 'outputs': {'answer': ref('review', 'answer')}}})
        template, bindings = compile_template(parent, {'writer': 'flash', 'critic': catalog[0]['id']}, [BACKEND, *catalog])
        reviews = 0
        async def invoke(binding, messages, native):
            nonlocal reviews
            if binding['id'] == 'inspect':
                reviews += 1
                self.assertIn('Evidence packet', messages[-1]['content'])
                refs = [engine.runner.states['draft']['output']['__report']['id']]
                return response({'verdict': 'revise' if reviews == 1 else 'approve', 'summary': 'Checked actual evidence',
                    'issues': ['Correct the draft'] if reviews == 1 else [], 'checked_report_refs': refs,
                    'answer': '' if reviews == 1 else 'Final'})
            return response('Draft evidence')
        engine = WorkflowEngine(template, bindings, 'Task', invoke, publish, registry=catalog)
        from unittest.mock import patch
        with patch('open_webui.execution.workflows.claim_native_context', return_value=False):
            self.assertEqual(await engine.execute(), 'Final')
        self.assertEqual(reviews, 2)
        self.assertEqual([row['status'] for row in engine.record['review_attempts']], ['changes_requested', 'approved'])
        self.assertEqual(len(engine.record['calls']), 4)
        self.assertTrue(all(row['call_id'] not in {c['id'] for c in engine.record['calls']} for row in engine.record['review_attempts']))

    async def test_map_and_repeat_inject_their_own_invocation_inputs(self):
        for operation, field, data in [('map', 'item', 0), ('loop', 'previous', None)]:
            child = definition([
                {'id': 'entry', 'op': 'input', 'inputs': {'payload': {'source': 'input', 'pointer': '/' + field}},
                 'output_schema': {'type': 'object', 'properties': {'payload': {}}, 'required': ['payload']}},
                {'id': 'exit', 'op': 'output', 'inputs': {'value': ref('entry', 'payload'), 'done': {'source': 'literal', 'value': True}},
                 'output_schema': {'type': 'object', 'properties': {'value': {}, 'done': {'type': 'boolean'}}, 'required': ['value', 'done']}}],
                {'value': ref('exit', 'value'), 'done': ref('exit', 'done')},
                input_schema={'type': 'object', 'properties': {field: {}}, 'required': [field], 'additionalProperties': False})
            catalog = workflow_catalog([child], {child['id']: {'worker': 'flash'}}, [BACKEND])
            node = {'id': 'batch', 'op': operation, 'binding': catalog[0]['id'], 'inputs': {}}
            node.update({'items': {'source': 'literal', 'value': [data]}} if operation == 'map' else
                        {'max_iterations': 2, 'stop_when': {'pointer': '/done', 'equals': True}})
            parent = definition([node], {'answer': ref('batch', 'report')}, input_schema=EMPTY)
            template, bindings = self.compile(parent, catalog)
            engine = WorkflowEngine(template, bindings, '', None, publish, registry=catalog, input_values={})
            await engine.execute()
            output = engine.record['node_states']['batch']['output']
            actual = output['items'][0]['output'] if operation == 'map' else output['last']
            self.assertIs(actual['value'], data)
            self.assertEqual(engine.record['calls'], [])

    async def test_legacy_saved_context_retains_report_lineage_across_turns(self):
        from unittest.mock import patch
        value = definition([{'id': 'work', 'op': 'call', 'binding': 'worker', 'inputs': {}}], {'answer': ref('work', 'report')})
        next(n for n in value['graph']['nodes'] if n['id'] == 'work').pop('context_key')
        value['version'] = 1
        template, bindings = self.compile(value)
        first = WorkflowEngine(template, bindings, 'First', lambda *a: asyncio.sleep(0, result=response('old own report')), publish)
        await first.execute()
        previous = copy.deepcopy(first.record)
        seen = []
        async def invoke(binding, messages, native):
            self.assertEqual(binding['id'], 'worker')
            self.assertIn('old own report', str(messages))
            seen.append(binding['id'])
            return response('continued')
        engine = WorkflowEngine(template, bindings, 'Second', invoke, publish, previous=[previous])
        with patch('open_webui.execution.workflows.claim_native_context', return_value=False):
            self.assertEqual(await engine.execute(), 'continued')
        history = engine.record['calls'][0]['history']
        self.assertEqual(history['attributed_reports'], 1)
        self.assertEqual(history['unattributed_assistant_messages'], 0)
        self.assertEqual(history['first_retained_report_ref'], previous['calls'][0]['report_id'])
        self.assertEqual(previous, first.record)

    async def test_removed_control_node_does_not_make_its_private_history_public(self):
        value = definition([{'id': 'work', 'op': 'call', 'binding': 'worker', 'context_key': 'prior_controller', 'inputs': {}}], {'answer': ref('work', 'report')})
        template, bindings = self.compile(value)
        previous = {'id': str(uuid.uuid4()), 'calls': [], 'plan': {'nodes': [
            {'id': 'old_plan', 'op': 'plan', 'binding': 'worker', 'context_key': 'prior_controller'}]}}
        engine = WorkflowEngine(template, bindings, 'Task', None, publish, previous=[previous])
        with self.assertRaisesRegex(ExecutionError, 'private history'):
            await engine.execute()
        self.assertEqual(engine.record['calls'], [])

    async def test_intermediate_local_concurrency_limits_all_descendant_leaves(self):
        leaf = definition([{'id': key, 'op': 'call', 'binding': 'worker', 'inputs': {}} for key in ('a', 'b')], {'answer': ref('b', 'report')})
        leaf_catalog = workflow_catalog([leaf], {leaf['id']: {'worker': 'flash'}}, [BACKEND])
        middle = definition([{'id': 'leaves', 'op': 'call', 'binding': leaf_catalog[0]['id'], 'inputs': {'task': {'source': 'input', 'pointer': '/task'}}}], {'answer': ref('leaves', 'answer')}, limits={'max_concurrency': 1})
        catalog = workflow_catalog([leaf, middle], {leaf['id']: {'worker': 'flash'}, middle['id']: {'worker': 'flash'}}, [BACKEND])
        root = definition([{'id': 'middle', 'op': 'call', 'binding': 'workflow:' + middle['id'], 'inputs': {'task': {'source': 'input', 'pointer': '/task'}}}], {'answer': ref('middle', 'answer')})
        template, bindings = self.compile(root, catalog)
        active = peak = 0
        async def invoke(binding, messages, native):
            nonlocal active, peak
            active += 1; peak = max(peak, active)
            await asyncio.sleep(.01)
            active -= 1
            return response('done')
        engine = WorkflowEngine(template, bindings, 'Task', invoke, publish, registry=catalog)
        self.assertEqual(await engine.execute(), 'done')
        self.assertEqual(peak, 1)
