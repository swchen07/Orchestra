import asyncio
import copy
import json
import unittest
from unittest.mock import patch

from open_webui.execution.claude import ExecutionError
from open_webui.execution.composed_workflow import distribute_review_definition
from open_webui.execution.graph_spec import validate_graph
from open_webui.execution.workflows import WorkflowEngine
from open_webui.execution.workflow_templates import compile_template, normalize_graph_template


BACKEND={'id':'flash','name':'Flash','framework_id':'direct','model_id':'flash'}


class ReviewFeedbackTests(unittest.IsolatedAsyncioTestCase):
    def compile(self,limit=2):
        definition=distribute_review_definition()
        next(n for n in definition['graph']['nodes'] if n['op']=='review')['review']['max_repairs']=limit
        definition=normalize_graph_template(definition)
        return compile_template(definition,{r['id']:'flash' for r in definition['roles']},[BACKEND])

    def verdict(self,engine,approve):
        current={s['call_ids'][-1] for s in engine.runner.states.values() if s.get('call_ids') and s['status']=='completed'}
        refs=[c['report_id'] for c in engine.record['calls'] if c['id'] in current and c['kind']=='work']
        return {'verdict':'approve' if approve else 'revise','summary':'Checked current reports.',
                'issues':[] if approve else ['Correct the selected branch arithmetic.'],
                'checked_report_refs':refs,'answer':'VERIFIED' if approve else ''}

    async def execute_case(self,limit=2,always_reject=False,previous=None,selected='execute_c',policy='automatic'):
        template,bindings=self.compile(limit)
        counts={key:0 for key in ['controller','executor_01','executor_02','reviewer']}
        snapshots=[]
        approvals=[]
        self.approved_revisions=set()
        async def invoke(binding,messages,native):
            key=binding['id'];counts[key]+=1
            body=messages[-1]['content']
            if key=='controller':
                repair=counts[key]>1
                if repair:
                    self.assertIn('review_feedback',body)
                    self.assertIn('Correct the selected branch arithmetic.',body)
                result={'task_b':'B_ASSIGNMENT' if not repair else 'PROPOSED_B',
                        'task_c':'C_ASSIGNMENT' if not repair else 'PROPOSED_C',
                        'selected_nodes':[selected] if repair else ['execute_b','execute_c']}
            elif key=='reviewer':
                if counts[key]>1:
                    packet=json.loads(body.split('Evidence JSON:\n')[1].split('\n')[0])
                    current=json.dumps([r for r in packet['reports'] if r['current']])
                    self.assertNotIn('BAD_REPORT',current)
                    self.assertIn('CORRECTED_REPORT',current)
                result=self.verdict(engine,counts[key]>1 and not always_reject)
            else:
                if counts[key]>1:
                    self.assertIn('BAD_REPORT',str(messages[:-1]))
                    result='CORRECTED_REPORT'
                elif key==('executor_02' if selected=='execute_c' else 'executor_01'):
                    result='BAD_REPORT'
                else:
                    result='RETAINED_GOOD_REPORT'
            return {'choices':[{'message':{'content':result if isinstance(result,str) else json.dumps(result)}}],
                    'usage':{'prompt_tokens':10,'completion_tokens':2}}
        async def publish(record):
            if policy=='review' and record.get('control',{}).get('paused'):
                revision=record['plan']['revision']
                if revision not in self.approved_revisions:
                    self.approved_revisions.add(revision)
                    approvals.append(asyncio.create_task(engine.runner.control({'action':'approve','revision':revision})))
            if record.get('node_states',{}).get('workflow_output',{}).get('status')=='completed':
                snapshots.append(copy.deepcopy(record))
        engine=WorkflowEngine(template,bindings,'Goal',invoke,publish,previous=previous or [],policy=policy)
        with patch('open_webui.execution.workflows.claim_native_context',return_value=False):
            if always_reject or limit==0:
                with self.assertRaises(ExecutionError):
                    await asyncio.wait_for(engine.execute(),3)
                self.assertFalse(snapshots)
            else:
                self.assertEqual(await asyncio.wait_for(engine.execute(),3),'VERIFIED')
        await asyncio.gather(*approvals)
        return engine,counts

    async def test_selective_rework_preserves_good_report_and_counts_all_calls_once(self):
        engine,counts=await self.execute_case()
        self.assertEqual(counts,{'controller':2,'executor_01':1,'executor_02':2,'reviewer':2})
        r=engine.record
        self.assertEqual([x['status'] for x in r['review_attempts']],['changes_requested','approved'])
        self.assertEqual(r['control']['budget']['reported_tokens'],84)
        self.assertEqual(r['control']['review_repairs'],1)
        verdict_events=[e for e in r['events'] if e['type'] in {'review.approved','review.changes_requested'}]
        self.assertEqual(len({e['call_id'] for e in verdict_events}),2)
        self.assertTrue(all(e['role_id']=='reviewer' for e in verdict_events))
        self.assertEqual(r['node_states']['assign']['output']['task_b'],'B_ASSIGNMENT')
        self.assertEqual(r['node_states']['assign']['output']['task_c'],'PROPOSED_C')
        c=[c for c in r['calls'] if c['node_id']=='execute_c']
        self.assertEqual([v['output'] for v in c],['BAD_REPORT','CORRECTED_REPORT'])
        self.assertNotIn(c[0]['report_id'],r['review']['required_report_refs'])
        self.assertIn(c[1]['report_id'],r['review']['required_report_refs'])
        self.assertTrue(r['node_states']['execute_b']['reused_in_revision'])

    async def test_rework_on_next_chat_turn_keeps_private_histories_and_can_select_other_branch(self):
        first,_=await self.execute_case()
        second,counts=await self.execute_case(previous=[copy.deepcopy(first.record)],selected='execute_b')
        self.assertEqual(counts['executor_01'],2)
        self.assertEqual(counts['executor_02'],1)
        self.assertTrue(all(c['history']['history_messages']>=2 for c in second.record['calls']))
        self.assertEqual(second.record['control']['budget']['reported_tokens'],84)

    async def test_exhausted_budget_never_delivers(self):
        engine,counts=await self.execute_case(limit=1,always_reject=True)
        self.assertEqual(counts['controller'],2)
        self.assertEqual(counts['reviewer'],2)
        self.assertEqual(engine.record['review']['status'],'changes_requested')
        self.assertNotEqual(engine.record['node_states']['workflow_output']['status'],'completed')

    async def test_zero_corrections_stops_after_initial_rejection(self):
        _,counts=await self.execute_case(limit=0)
        self.assertEqual(counts,{'controller':1,'executor_01':1,'executor_02':1,'reviewer':1})

    async def test_existing_plan_approval_policy_is_respected_on_correction(self):
        _,counts=await self.execute_case(policy='review')
        self.assertEqual(self.approved_revisions,{1,2})
        self.assertEqual(counts['reviewer'],2)

    async def test_dispatcher_cannot_select_outside_declared_targets(self):
        template,bindings=self.compile()
        async def invoke(binding,messages,native):
            self.assertEqual(binding['id'],'controller')
            return {'choices':[{'message':{'content':json.dumps({'task_b':'B','task_c':'C','selected_nodes':['workflow_output']})}}],
                    'usage':{'prompt_tokens':10,'completion_tokens':2}}
        async def publish(record):
            pass
        engine=WorkflowEngine(template,bindings,'Goal',invoke,publish)
        with self.assertRaises(ExecutionError):
            await asyncio.wait_for(engine.execute(),3)
        self.assertEqual(len(engine.record['calls']),1)
        self.assertNotEqual(engine.record['node_states']['workflow_output']['status'],'completed')
        self.assertEqual(engine.record['control']['budget']['reported_tokens'],12)

    async def test_feedback_uses_node_actions_and_bindings_after_renaming_the_entire_graph(self):
        template,bindings=self.compile()
        template['name']='Custom reusable composition'
        graph=template['graph']; names={n['id']:'custom_'+str(i) for i,n in enumerate(graph['nodes'])}
        for n in graph['nodes']:
            n['id']=names[n['id']]
            for ref in n['inputs'].values():
                if ref.get('source')=='node': ref['node']=names.get(ref['node'],ref['node'])
            if n.get('dispatch'):
                n['dispatch']['targets']=[names[i] for i in n['dispatch']['targets']]
                n['output_schema']['properties']['selected_nodes']['items']['enum']=n['dispatch']['targets']
            if n.get('review'): n['review']['repair_node']=names[n['review']['repair_node']]
        for ref in graph['outputs'].values(): ref['node']=names[ref['node']]
        graph['result_node']=names[graph['result_node']]
        graph['edges']=[]
        counts={}; reviews=0
        async def invoke(binding,messages,native):
            nonlocal reviews
            key=binding['id'];counts[key]=counts.get(key,0)+1
            if key=='controller':
                result={'task_b':'B','task_c':'C','selected_nodes':[names['execute_c']] if counts[key]>1 else [names['execute_b'],names['execute_c']]}
            elif key=='reviewer':
                reviews+=1;result=self.verdict(engine,reviews>1)
            else: result='Report from '+key
            return {'choices':[{'message':{'content':result if isinstance(result,str) else json.dumps(result)}}],
                    'usage':{'prompt_tokens':10,'completion_tokens':2}}
        async def publish(record): pass
        engine=WorkflowEngine(template,bindings,'Goal',invoke,publish)
        self.assertEqual(await asyncio.wait_for(engine.execute(),3),'VERIFIED')
        self.assertEqual(counts,{'controller':2,'executor_01':1,'executor_02':2,'reviewer':2})

    def test_feedback_edges_are_bounded_control_not_forward_readiness_dependencies(self):
        template,_=self.compile()
        graph=template['graph']
        self.assertIn({'from':'review','to':'assign','on':'revise','generated':True},graph['edges'])
        self.assertEqual(validate_graph(graph),graph)
        bad=copy.deepcopy(graph);bad['nodes'][0]['inputs']['bad']={'source':'node','node':'review','pointer':'/answer'}
        with self.assertRaisesRegex(ValueError,'cycles'):
            validate_graph(bad)
        bad=copy.deepcopy(graph);next(n for n in bad['nodes'] if n['op']=='review')['review']['repair_node']='workflow_output'
        with self.assertRaisesRegex(ValueError,'feedback-capable'):
            validate_graph(bad)
