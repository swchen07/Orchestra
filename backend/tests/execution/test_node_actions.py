import copy
import json
import unittest
from pathlib import Path

from open_webui.execution.node_actions import BY_ID, BY_OPERATION, ACTION_HANDLERS, resolve_action
from open_webui.execution.graph_runtime import GraphRunner
from open_webui.execution.graph_spec import validate_graph
from open_webui.execution.workflow_presets import starter_templates
from open_webui.execution.workflow_templates import compile_template, normalize_graph_template
from open_webui.execution.workflows import WorkflowEngine


class NodeActionTests(unittest.IsolatedAsyncioTestCase):
    def test_public_editor_catalog_is_an_exact_export_of_backend_definitions(self):
        from open_webui.execution.node_actions import action_catalog
        artifact = Path(__file__).resolve().parents[3] / 'src/lib/components/execution/nodeActions.generated.json'
        self.assertEqual(json.loads(artifact.read_text()), action_catalog())

    def test_every_selectable_action_has_an_implementation_and_context_contract(self):
        self.assertEqual(set(BY_OPERATION), set(ACTION_HANDLERS))
        self.assertEqual(len(BY_ID), len(BY_OPERATION))
        for operation, method in ACTION_HANDLERS.items():
            if method:
                self.assertTrue(callable(getattr(GraphRunner, method)))
            else:
                self.assertEqual(operation, 'plan')
            self.assertTrue(BY_OPERATION[operation]['context_rule'])
            self.assertTrue(BY_OPERATION[operation]['control_effect'])
        with self.assertRaises(ValueError):
            resolve_action({'action': 'native.handoff', 'op': 'call'})

    def test_action_only_nodes_compile_to_a_checked_legacy_alias(self):
        graph = starter_templates()[2]['graph']
        for node in graph['nodes']:
            node.pop('op')
        compiled = validate_graph(graph)
        self.assertTrue(all(n['op'] == BY_ID[n['action']]['operation'] for n in compiled['nodes']))
        self.assertTrue(all('op' not in n for n in graph['nodes']))
        bad = copy.deepcopy(compiled)
        bad['nodes'][1]['action'] = 'results.collect'
        with self.assertRaisesRegex(ValueError, 'disagree'):
            validate_graph(bad)

    def test_three_examples_are_editable_graphs_not_new_modes(self):
        examples = starter_templates()
        self.assertEqual([e['name'] for e in examples], ['Adaptive Modules','Compare','Distribute & Review'])
        for definition in examples:
            normalized = normalize_graph_template(definition)
            self.assertNotIn('mode', normalized)
            self.assertTrue(normalized['example']['task'])
            self.assertTrue(normalized['example']['expected'])
            self.assertTrue(all('action' in n for n in normalized['graph']['nodes']))
        assigned = next(n for n in examples[2]['graph']['nodes'] if n['id']=='assign')
        self.assertIn('self-contained assignment', assigned['output_schema']['properties']['task_b']['description'])

    async def test_field_meanings_reach_producer_and_consumer_without_sending_example_text(self):
        definition = {'name': 'Port meanings', 'roles': [{'id':'writer','name':'Writer'},{'id':'reader','name':'Reader'}],
            'lead_role_id':'writer','example':{'purpose':'Only help text','task':'DO_NOT_SEND_THIS_EXAMPLE','expected':'Only documentation'},
            'graph':{'schema_version':'2.0','input_policy':'scoped','nodes':[
                {'id':'produce','op':'call','binding':'writer','context_key':'writer','inputs':{'question':{'source':'input','pointer':'/task'}},
                 'output_schema':{'type':'object','properties':{'amount':{'type':'integer','description':'Amount in cents, not whole currency units.'}},'required':['amount']}},
                {'id':'consume','op':'call','binding':'reader','context_key':'reader','inputs':{'value':{'source':'node','node':'produce','pointer':'/amount'}},
                 'report_description':'Return the cents value and explain its unit.'}],
                'edges':[],'outputs':{'answer':{'source':'node','node':'consume','pointer':'/report'}}}}
        participant={'id':'flash','name':'Flash','model_id':'flash'}
        template,bindings=compile_template(definition,{'writer':'flash','reader':'flash'},[participant])
        seen=[]
        async def invoke(binding,messages,native):
            prompt=messages[-1]['content']
            self.assertNotIn('DO_NOT_SEND_THIS_EXAMPLE',prompt)
            self.assertIn('Amount in cents, not whole currency units.',prompt)
            if binding['id']=='writer':
                self.assertIn('Return ONLY JSON matching this output schema',prompt)
                result='{"amount":42}'
            else:
                self.assertIn('Input field meanings:',prompt)
                self.assertIn('Return the cents value and explain its unit.',prompt)
                result='42 cents'
            seen.append(binding['id'])
            return {'choices':[{'message':{'content':result}}],'usage':{'prompt_tokens':10,'completion_tokens':2}}
        async def publish(record):
            pass
        engine=WorkflowEngine(template,bindings,'Compute the amount',invoke,publish)
        self.assertEqual(await engine.execute(),'42 cents')
        self.assertEqual(seen,['writer','reader'])
        self.assertEqual(engine.record['control']['budget']['reported_tokens'],24)
