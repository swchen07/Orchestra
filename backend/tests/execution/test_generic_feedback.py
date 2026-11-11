"""Feedback is tested by graph shape/action, never by a template name or role label."""
import asyncio
import copy
import json
import unittest
from collections import Counter
from unittest.mock import patch

from open_webui.execution.adaptive_workflow import adaptive_definition
from open_webui.execution.workflow_presets import starter_templates
from open_webui.execution.workflow_templates import normalize_graph_template, compile_template
from open_webui.execution.workflows import WorkflowEngine

MODEL={'id':'model_flash','name':'Flash','model_id':'flash','framework_id':'direct'}


def result(value):
    return {'choices':[{'message':{'content':value if isinstance(value,str) else json.dumps(value)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':2}}


def verdict(engine,approved,answer='VERIFIED'):
    current={key for state in engine.runner.states.values() if state['status']=='completed'
             for key in state.get('attempt_call_ids',state.get('call_ids',[])[-1:])}
    refs=[c['report_id'] for c in engine.record['calls'] if c['id'] in current and c['kind']!='decision']
    return {'verdict':'approve' if approved else 'revise','summary':'Current attempt checked.',
            'issues':[] if approved else ['Correct the current work using the same private instance.'],
            'checked_report_refs':refs,'answer':answer if approved else ''}


def definition(nodes,roles,repair,review_id='audit',answer_source=None):
    task={'source':'input','pointer':'/task'}
    return {'name':'User composition','roles':[{'id':r,'name':r} for r in [*roles,review_id]],
        'lead_role_id':roles[0],'graph':{'schema_version':'2.0','input_policy':'scoped','nodes':[
            *nodes,{'id':review_id,'op':'review','binding':review_id,'inputs':{
                'goal':task,'candidate':{'source':'node','node':answer_source or nodes[-1]['id'],'pointer':'/report'}},
                'review':{'repair_node':repair,'max_repairs':2}}],
            'edges':[],'outputs':{'answer':{'source':'node','node':review_id,'pointer':'/answer'}}}}


class GenericFeedbackTests(unittest.IsolatedAsyncioTestCase):
    def compile(self,value):
        # These fixtures intentionally exercise shared role histories.
        for node in value['graph']['nodes']:
            if node.get('binding'): node.setdefault('context_key',node['binding'])
        value=normalize_graph_template(value)
        return compile_template(value,{r['id']:MODEL['id'] for r in value['roles']},[MODEL])

    async def run_engine(self,engine):
        with patch('open_webui.execution.workflows.claim_native_context',return_value=False):
            return await asyncio.wait_for(engine.execute(),4)

    async def test_compare_with_optional_review_replays_one_answer_and_host_collector_across_two_turns(self):
        value=copy.deepcopy(starter_templates()[1]);value['name']='Unrelated comparison title'
        value['roles'].append({'id':'critic','name':'Independent quality check'})
        graph=value['graph'];graph['nodes'].insert(-1,{'id':'quality','op':'review','binding':'critic',
            'inputs':{'goal':{'source':'node','node':'workflow_input','pointer':'/task'},
                      'candidate':{'source':'node','node':'answers','pointer':'/report'}},
            'review':{'repair_node':'compare_1','max_repairs':1}})
        next(n for n in graph['nodes'] if n['op']=='output')['inputs']['answer']={'source':'node','node':'quality','pointer':'/answer'}
        template,bindings=self.compile(value);previous=[]
        for turn in (1,2):
            counts=Counter()
            async def invoke(binding,messages,native):
                key=binding['id'];counts[key]+=1
                self.assertIn('Current invocation (host-owned)',messages[-1]['content'])
                self.assertIn('"module_instance_id": "'+key+'"',messages[-1]['content'])
                if key=='critic': return result(verdict(engine,counts[key]>1))
                if counts[key]>1:
                    self.assertIn('Review feedback control input',messages[-1]['content'])
                if turn==2: self.assertTrue(any(m['role']=='assistant' for m in messages[:-1]))
                return result(key+('_fixed' if counts[key]>1 else '_first'))
            async def publish(record): pass
            engine=WorkflowEngine(template,bindings,'Same complete task',invoke,publish,previous=previous)
            self.assertEqual(await self.run_engine(engine),'VERIFIED')
            self.assertEqual(counts,{'executor_01':2,'executor_02':1,'executor_03':1,'critic':2})
            self.assertEqual(engine.record['control']['budget']['reported_tokens'],72)
            self.assertEqual(len(engine.record['node_states']['answers']['attempts']),1)
            previous.append(copy.deepcopy(engine.record))

    async def test_serial_dependency_chain_is_invalidated_without_erasing_old_reports(self):
        value=definition([
            {'id':'research','op':'call','binding':'author','inputs':{}},
            {'id':'edit','op':'call','binding':'editor','inputs':{'draft':{'source':'node','node':'research','pointer':'/report'}}},
        ],['author','editor'],'research')
        template,bindings=self.compile(value);counts=Counter()
        async def invoke(binding,messages,native):
            key=binding['id'];counts[key]+=1
            if key=='audit': return result(verdict(engine,counts[key]>1))
            if counts[key]>1: self.assertTrue(any(m['role']=='assistant' for m in messages[:-1]))
            if key=='editor': self.assertIn('author_'+str(counts[key]),messages[-1]['content'])
            return result(key+'_'+str(counts[key]))
        async def publish(record): pass
        engine=WorkflowEngine(template,bindings,'Goal',invoke,publish)
        self.assertEqual(await self.run_engine(engine),'VERIFIED')
        self.assertEqual(counts,{'author':2,'editor':2,'audit':2})
        self.assertEqual([c['output'] for c in engine.record['calls'] if c['node_id']=='research'],['author_1','author_2'])

    async def test_batch_replay_reviews_all_current_item_calls_and_retains_item_contexts(self):
        value=definition([{'id':'batch','op':'map','binding':'processor','context_key':'batch',
            'items':{'source':'literal','value':[1,2]},'inputs':{}}],['processor'],'batch')
        template,bindings=self.compile(value);counts=Counter();review_count=0
        async def invoke(binding,messages,native):
            nonlocal review_count
            key=binding['id'];counts[key]+=1
            if key=='audit':
                review_count+=1
                reply=verdict(engine,review_count>1)
                self.assertEqual(len(reply['checked_report_refs']),2)
                return result(reply)
            if counts[key]>1: self.assertTrue(any(m['role']=='assistant' for m in messages[:-1]))
            return result(key+'_item_'+str(counts[key]))
        async def publish(record): pass
        engine=WorkflowEngine(template,bindings,'Batch task',invoke,publish)
        self.assertEqual(await self.run_engine(engine),'VERIFIED')
        self.assertEqual(sorted(counts.values()),[2,2,2])
        self.assertEqual(engine.record['control']['budget']['reported_tokens'],72)
        first,last=engine.record['review_attempts']
        self.assertTrue(set(first['required_report_refs']).isdisjoint(last['required_report_refs']))

    async def test_review_chain_can_return_feedback_to_an_upstream_review_action(self):
        value=definition([
            {'id':'draft','op':'call','binding':'writer','inputs':{}},
            {'id':'check','op':'review','binding':'check','inputs':{'draft':{'source':'node','node':'draft','pointer':'/report'}},'review':{'max_repairs':0}},
        ],['writer','check'],'check')
        template,bindings=self.compile(value);counts=Counter()
        async def invoke(binding,messages,native):
            key=binding['id'];counts[key]+=1
            if key=='writer': return result('Written result')
            if key=='check':
                if counts[key]>1: self.assertIn('Review feedback control input',messages[-1]['content'])
                return result(verdict(engine,True,'Checked_'+str(counts[key])))
            return result(verdict(engine,counts[key]>1))
        async def publish(record): pass
        engine=WorkflowEngine(template,bindings,'Goal',invoke,publish)
        self.assertEqual(await self.run_engine(engine),'VERIFIED')
        self.assertEqual(counts,{'writer':1,'check':2,'audit':2})

    async def test_generated_graph_retries_existing_node_via_the_same_invalidation_service(self):
        template,bindings=self.compile(adaptive_definition())
        next(n for n in template['graph']['nodes'] if n['op']=='plan')['planning']['on_events']=False
        counts=Counter()
        async def invoke(binding,messages,native):
            key=binding['id'];counts[key]+=1
            if key=='controller':
                if counts[key]==1:
                    return result({'action':'propose_plan','graph':{'nodes':[
                        {'id':'analysis','op':'call','binding':MODEL['id'],'context_key':'retained_worker','inputs':{}}],
                        'edges':[],'result_node':'analysis'}})
                self.assertIn('Review feedback control input',messages[-1]['content'])
                return result({'action':'retry_nodes','base_revision':engine.runner.graph['revision'],'node_ids':['analysis']})
            if key=='reviewer': return result(verdict(engine,counts[key]>1))
            if counts[key]>1:
                self.assertIn('Review feedback control input',messages[-1]['content'])
                self.assertTrue(any(m['role']=='assistant' for m in messages[:-1]))
            return result('Worker_'+str(counts[key]))
        async def publish(record): pass
        engine=WorkflowEngine(template,bindings,'Goal',invoke,publish,registry=[MODEL])
        self.assertEqual(await self.run_engine(engine),'VERIFIED')
        self.assertEqual(counts,{'controller':2,'retained_worker':2,'reviewer':2})
        self.assertEqual(len([n for n in engine.record['plan']['nodes'] if n['id']=='analysis']),1)

    def test_plain_compare_does_not_silently_gain_review_or_feedback_nodes(self):
        graph=starter_templates()[1]['graph']
        self.assertFalse(any(n['op'] in {'plan','review','dispatch'} for n in graph['nodes']))
        self.assertFalse(any(e['on']=='revise' for e in graph['edges']))

    async def test_event_driven_planning_and_review_feedback_share_the_same_scheduler(self):
        template,bindings=self.compile(adaptive_definition())
        self.assertTrue(next(n for n in template['graph']['nodes'] if n['op']=='plan')['planning']['on_events'])
        counts=Counter();retried=False
        async def invoke(binding,messages,native):
            nonlocal retried
            key=binding['id'];counts[key]+=1
            if key=='controller':
                if counts[key]==1:
                    return result({'action':'propose_plan','graph':{'nodes':[
                        {'id':'work','op':'call','binding':MODEL['id'],'context_key':'worker','inputs':{}}],
                        'edges':[],'result_node':'work'}})
                if 'Review feedback control input' in messages[-1]['content']:
                    retried=True
                    return result({'action':'retry_nodes','base_revision':engine.runner.graph['revision'],'node_ids':['work']})
                return result({'action':'wait','reason':'Observe remaining work and review.'})
            if key=='reviewer': return result(verdict(engine,counts[key]>1))
            return result('Current report '+str(counts[key]))
        async def publish(record): pass
        engine=WorkflowEngine(template,bindings,'Goal',invoke,publish,registry=[MODEL])
        self.assertEqual(await self.run_engine(engine),'VERIFIED')
        self.assertTrue(retried)
        self.assertEqual(counts['worker'],2)
        self.assertEqual(counts['reviewer'],2)
        self.assertLessEqual(counts['controller'],5)

    async def test_generated_repair_appends_work_and_reopens_review_for_new_candidate_alias(self):
        template,bindings=self.compile(adaptive_definition())
        next(n for n in template['graph']['nodes'] if n['op']=='plan')['planning']['on_events']=False
        counts=Counter()
        async def invoke(binding,messages,native):
            key=binding['id'];counts[key]+=1
            if key=='controller':
                if counts[key]==1:
                    return result({'action':'propose_plan','graph':{'nodes':[
                        {'id':'subtotal','op':'call','binding':MODEL['id'],'context_key':'subtotal','inputs':{}}],
                        'edges':[],'result_node':'subtotal'}})
                self.assertIn('Review feedback control input',messages[-1]['content'])
                return result({'action':'patch_plan','base_revision':engine.runner.graph['revision'],'changes':[
                    {'op':'add_node','node':{'id':'invoice','op':'call','binding':MODEL['id'],'context_key':'invoice',
                        'inputs':{'subtotal':{'source':'node','node':'subtotal','pointer':'/report'},
                                  'missing_amount':3 if counts[key]==2 else {'source':'literal','value':3}}}},
                    {'op':'set_output','port':'candidate','ref':{'source':'node','node':'invoice','pointer':'/report'}}]})
            if key=='reviewer': return result(verdict(engine,counts[key]>1))
            return result('Subtotal 13' if key=='subtotal' else 'Invoice 15')
        async def publish(record): pass
        engine=WorkflowEngine(template,bindings,'Invoice includes fee',invoke,publish,registry=[MODEL])
        self.assertEqual(await self.run_engine(engine),'VERIFIED')
        self.assertEqual(counts,{'controller':3,'subtotal':1,'invoice':1,'reviewer':2})
        self.assertTrue(any('typed source reference' in error for error in engine.runner.control_errors))
        self.assertEqual(engine.record['node_states']['subtotal']['output']['report'],'Subtotal 13')
        self.assertEqual(len(engine.record['node_states']['review_result']['attempts']),1)
        self.assertEqual(engine.record['review_attempts'][-1]['status'],'approved')

    async def test_revised_finish_candidate_can_receive_another_bounded_review(self):
        template,bindings=self.compile(adaptive_definition());counts=Counter()
        next(n for n in template['graph']['nodes'] if n['op']=='plan')['planning']['on_events']=False
        next(n for n in template['graph']['nodes'] if n['op']=='review')['review']['max_repairs']=2
        async def invoke(binding,messages,native):
            key=binding['id'];counts[key]+=1
            if key=='controller':
                if counts[key]==1:
                    return result({'action':'propose_plan','graph':{'nodes':[
                        {'id':'work','op':'call','binding':MODEL['id'],'context_key':'worker','inputs':{}}],
                        'edges':[],'result_node':'work'}})
                return result({'action':'final','content':'Candidate revision '+str(counts[key])})
            if key=='reviewer': return result(verdict(engine,counts[key]==3))
            return result('Evidence report')
        async def publish(record): pass
        engine=WorkflowEngine(template,bindings,'Goal',invoke,publish,registry=[MODEL])
        self.assertEqual(await self.run_engine(engine),'VERIFIED')
        self.assertEqual(counts,{'controller':3,'worker':1,'reviewer':3})
        self.assertFalse(engine.runner.feedback_jobs)
        self.assertEqual(engine.runner.review_repairs,2)

    async def test_affected_active_cut_waits_while_an_independent_branch_advances(self):
        independent_done=asyncio.Event(); first_slow_done=asyncio.Event(); answered=False
        nodes=[{'id':'root','op':'call','binding':'root','inputs':{}},
            {'id':'fast','op':'call','binding':'fast','inputs':{'source':{'source':'node','node':'root','pointer':'/report'}}},
            {'id':'slow','op':'call','binding':'slow','inputs':{'source':{'source':'node','node':'root','pointer':'/report'}}},
            {'id':'unrelated','op':'checkpoint','question':'Independent input','inputs':{}},
            {'id':'next_unrelated','op':'call','binding':'next_unrelated','inputs':{'value':{'source':'node','node':'unrelated','pointer':'/report'}}}]
        value=definition(nodes,['root','fast','slow','unrelated','next_unrelated'],'root',answer_source='fast')
        template,bindings=self.compile(value);counts=Counter()
        async def invoke(binding,messages,native):
            key=binding['id'];counts[key]+=1
            if key=='root' and counts[key]>1: self.assertTrue(first_slow_done.is_set())
            if key=='audit':
                return result(verdict(engine,counts[key]>1))
            if key=='slow' and counts[key]==1:
                await asyncio.wait_for(independent_done.wait(),2);first_slow_done.set()
            if key=='next_unrelated':
                self.assertEqual(counts['root'],1)
                self.assertEqual(engine.runner.feedback_jobs['root']['stage'],'queued')
                independent_done.set()
            return result(key+'_'+str(counts[key]))
        async def publish(record):
            nonlocal answered
            if not answered and any(e['type']=='review.feedback' for e in record.get('events',[])):
                answered=True
                asyncio.create_task(engine.runner.control({'action':'answer','node_id':'unrelated','value':'Independent response'}))
        engine=WorkflowEngine(template,bindings,'Goal',invoke,publish)
        self.assertEqual(await self.run_engine(engine),'VERIFIED')
        self.assertEqual(counts,{'root':2,'fast':2,'slow':2,'audit':2,'next_unrelated':1})
        self.assertEqual(engine.record['control']['budget']['reported_tokens'],108)
