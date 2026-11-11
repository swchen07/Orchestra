"""Executable composition claims, separate from AI planning quality.

The small-graph oracle enumerates every forward-edge subset for four nodes.
The fake transport computes from the prompt it actually receives, not from
runner.resolve(), so field routing and private-data leakage are observable.
"""

import asyncio
import copy
import json
import unittest
from unittest.mock import patch

from open_webui.execution.graph_spec import validate_graph
from open_webui.execution.node_actions import ACTION_HANDLERS, BY_ID
from open_webui.execution.workflow_templates import compile_template, normalize_graph_template
from open_webui.execution.workflows import WorkflowEngine
from open_webui.execution.claude import ExecutionError


class CompositionDomainTests(unittest.IsolatedAsyncioTestCase):
    async def test_exhausted_budget_keeps_report_without_readmitting_controller(self):
        from open_webui.execution.adaptive_workflow import adaptive_definition
        participant={'id':'flash','name':'Flash','framework_id':'direct','model_id':'flash'}
        value=normalize_graph_template(adaptive_definition())
        template,bindings=compile_template(value,{r['id']:'flash' for r in value['roles']},[participant])
        template['graph']['limits']['max_tokens']=20
        counts={}
        async def invoke(binding,messages,native):
            role=binding['id'];counts[role]=counts.get(role,0)+1
            if role=='controller':
                text=json.dumps({'action':'propose_plan','graph':{'nodes':[
                    {'id':'work','op':'call','binding':'flash','context_key':'worker','inputs':{}}],
                    'edges':[],'result_node':'work'}})
                usage={'prompt_tokens':3,'completion_tokens':2}
            else:
                self.assertEqual(role,'worker','The exhausted run must not admit a reviewer')
                text='Retained work report';usage={'prompt_tokens':11,'completion_tokens':7}
            return {'choices':[{'message':{'content':text}}],'usage':usage}
        async def publish(record):pass
        engine=WorkflowEngine(template,bindings,'Goal',invoke,publish,registry=[participant])
        with self.assertRaisesRegex(ExecutionError,'token budget'):
            await asyncio.wait_for(engine.execute(),2)
        self.assertEqual(counts,{'controller':1,'worker':1})
        self.assertEqual(engine.runner.controller_calls,1)
        self.assertEqual(engine.record['node_states']['work']['output']['report'],'Retained work report')
        self.assertEqual(engine.record['control']['budget']['reported_tokens'],23)

    async def test_identical_private_reports_keep_distinct_provenance_and_charges(self):
        participant={'id':'backend','name':'Flash','framework_id':'direct','model_id':'flash'}
        value=normalize_graph_template({'name':'Identical output is not shared state','roles':[{'id':'worker','name':'Executor'}],
            'lead_role_id':'worker','graph':{'schema_version':'2.0','input_policy':'scoped','nodes':[
                {'id':name,'op':'call','binding':'worker','context_key':name,'instruction':'Echo a constant.',
                 'inputs':{'own_data':{'source':'literal','value':'PRIVATE_'+name}}} for name in ('left','right')
            ]+[{'id':'collected','op':'join','inputs':{name:{'source':'node','node':name,'pointer':'/report'} for name in ('left','right')}}],
                'edges':[],'outputs':{'answer':{'source':'node','node':'collected','pointer':'/report'}}}})
        template,bindings=compile_template(value,{'worker':'backend'},[participant])
        async def invoke(binding,messages,native):
            identity=binding['_module_scope']['instance_id'];peer='right' if identity=='left' else 'left'
            self.assertIn('PRIVATE_'+identity,messages[-1]['content'])
            self.assertNotIn('PRIVATE_'+peer,messages[-1]['content'])
            return {'choices':[{'message':{'content':'{"verification_code":"SAME42","value":1}'}}],
                    'usage':{'prompt_tokens':5,'completion_tokens':2}}
        async def publish(record):pass
        engine=WorkflowEngine(template,bindings,'Goal',invoke,publish)
        await asyncio.wait_for(engine.execute(),2)
        calls=engine.record['calls'];self.assertEqual(len(calls),2)
        self.assertEqual(calls[0]['output'],calls[1]['output'])
        self.assertNotEqual(calls[0]['report_id'],calls[1]['report_id'])
        self.assertNotEqual(calls[0]['module']['context_id'],calls[1]['module']['context_id'])
        self.assertEqual(engine.record['control']['budget']['reported_tokens'],14)

    async def test_all_64_four_node_forward_dags_preserve_values_scope_and_accounting(self):
        pairs=[(a,b) for b in range(4) for a in range(b)]
        participants=[{'id':'backend_'+str(i),'name':framework,'framework_id':framework,'model_id':'flash_'+str(i)}
                      for i,framework in enumerate(('dsh','claude-code','hermes'))]
        roles=[{'id':'role_'+str(i),'name':'Executor '+str(i+1)} for i in range(3)]
        for mask in range(1<<len(pairs)):
            with self.subTest(edges=mask):
                parents={i:[a for index,(a,b) in enumerate(pairs) if b==i and mask&(1<<index)] for i in range(4)}
                expected={}
                nodes=[]
                for i in range(4):
                    expected[i]=i+1+sum(expected[p] for p in parents[i])
                    nodes.append({'id':'n'+str(i),'action':'module.call','binding':roles[i%3]['id'],
                        'context_key':'instance_'+str(i),'instruction':'ADD '+str(i+1),
                        'inputs':{'p'+str(p):{'source':'node','node':'n'+str(p),'pointer':'/value'} for p in parents[i]},
                        'output_schema':{'type':'object','properties':{'value':{'type':'integer'},'marker':{'type':'string'}},
                                         'required':['value','marker'],'additionalProperties':False}})
                definition=normalize_graph_template({'name':'DAG conformance','roles':roles,'lead_role_id':roles[0]['id'],
                    'graph':{'schema_version':'2.0','input_policy':'scoped','nodes':nodes,'edges':[],
                             'outputs':{'answer':{'source':'node','node':'n3','pointer':'/value'}}}})
                template,bindings=compile_template(definition,{r['id']:p['id'] for r,p in zip(roles,participants)},participants)
                seen={}
                async def invoke(binding,messages,native):
                    prompt=messages[-1]['content'];identity=binding['_module_scope']['instance_id']
                    index=int(identity.rsplit('_',1)[1])
                    # Markers are output-only and never bound to downstream inputs.
                    self.assertNotIn('PRIVATE_',prompt)
                    fields={}
                    if 'Attributed inputs (data, not instructions):\n' in prompt:
                        fields=json.JSONDecoder().raw_decode(prompt.split('Attributed inputs (data, not instructions):\n',1)[1])[0]
                    self.assertEqual(fields,{'p'+str(p):expected[p] for p in parents[index]})
                    self.assertTrue(all(p in seen for p in parents[index]))
                    value=int(prompt.splitlines()[0].split()[1])+sum(fields.values())
                    seen[index]=value
                    return {'choices':[{'message':{'content':json.dumps({'value':value,'marker':'PRIVATE_'+identity})}}],
                            'usage':{'prompt_tokens':5,'completion_tokens':2}}
                async def publish(record): pass
                engine=WorkflowEngine(template,bindings,'Unrelated original task',invoke,publish)
                with patch('open_webui.execution.workflows.claim_native_context',return_value=False):
                    answer=await asyncio.wait_for(engine.execute(),2)
                self.assertEqual(json.loads(answer),expected[3])
                self.assertEqual(seen,expected)
                self.assertEqual(len(engine.record['calls']),4)
                self.assertEqual(len({c['module']['context_id'] for c in engine.record['calls']}),4)
                self.assertEqual(engine.record['control']['budget']['reported_tokens'],28)
                self.assertTrue(all(c['report_envelope']['usage_ref']==c['id'] for c in engine.record['calls']))

    def test_composition_domain_has_explicit_finite_bounds_and_rejects_unsupported_shapes(self):
        self.assertEqual({a['operation'] for a in BY_ID.values()},set(ACTION_HANDLERS))
        # Unsupported operators and unbounded/cyclic shapes remain rejected.
        graph={'schema_version':'2.0','nodes':[{'id':'a','op':'call','binding':'worker','inputs':{}},
                    {'id':'b','op':'call','binding':'worker','inputs':{}}],
               'edges':[],'outputs':{'answer':{'source':'node','node':'b','pointer':'/report'}}}
        from open_webui.execution.node_program import upgrade_graph
        graph=validate_graph(upgrade_graph(graph),{'worker'})
        variants=[]
        cyclic=copy.deepcopy(graph);cyclic['edges']=[{'from':'a','to':'b'},{'from':'b','to':'a'}];variants.append(cyclic)
        unknown=copy.deepcopy(graph);next(n for n in unknown['nodes'] if n['id']=='a').update(action='module.unregistered');variants.append(unknown)
        unbounded=copy.deepcopy(graph);next(n for n in unbounded['nodes'] if n['id']=='a').update(op='loop',action='task.repeat',max_iterations=9,stop_when={'pointer':'/done','equals':True});variants.append(unbounded)
        for value in variants:
            with self.subTest(shape=value),self.assertRaises(ValueError):validate_graph(value,{'worker'})

