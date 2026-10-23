import copy
import json
import threading
import time
import unittest
from chaos.language import Evaluator,LanguageError,serializable,check_type
from chaos.computer_values import Computer,Observation,ComputerAction,ObservedInput
from chaos.modules import ProgramBundle


class ComputerFixture:
    def __init__(self):self.calls=[];self.ids=0;self.records=0;self.agents=[]
    def handle(self,op,payload):
        self.calls.append((op,copy.deepcopy(payload)))
        if op=='agent':self.agents.append(payload);return 'visual sync'
        if op=='open':self.ids+=1;return {'id':'computer-'+str(self.ids),'backend':'browser','status':'open'}
        if op=='close':return {'id':payload['id'],'backend':'browser','status':'closed'}
        self.records+=1
        info={'mimeType':'image/png','sha256':'a'*64,'width':20,'height':10,'scale':1}
        return {'id':'observation-'+str(self.records),'runId':'run-1','sourceVersion':1,'computerId':payload['id'],'sessionId':'session-'+payload['id'],'controlEpoch':1,'snapshotId':self.records,
                'url':'https://example.invalid','title':'fixture','text':'observed','targets':[{'ref':'e1','text':'Button','tag':'button','locator':{'kind':'role','role':'button','name':'Button'},'bbox':{'x':1,'y':2,'width':3,'height':4}}],
                'viewport':{'width':20,'height':10},'cursor':{'x':0,'y':0},'image':{'preview':info,'model':info}if payload.get('image',True)else None,
                'truncated':{'text':False,'targets':False},'receipt':None}
    def evaluator(self,**kwargs):return Evaluator(computer_handler=self.handle,**kwargs)


class ComputerValuesTests(unittest.TestCase):
    def setUp(self):self.host=ComputerFixture()
    def run_code(self,source,**kw):return self.host.evaluator(**kw).run(source)
    def test_cold_open_and_pure_rejection(self):
        self.assertEqual(self.run_code('type_of(computer_open())'),'Task');self.assertEqual(self.host.calls,[])
        with self.assertRaises(LanguageError):Evaluator(effect_handler=lambda*a:{}).run('await computer_open()')
    def test_owned_types_callable_and_explicit_information(self):
        result=self.run_code('let c=await computer_open();let o=await computer_observe(c);let a=computer_action(o,{action:"click",ref:"e1"});let i=observation_input(o,42);fn pass(f:Function){f};let next=await pass(c)(a);[type_of(c),type_of(o),type_of(a),type_of(i),observation_info(next).id,action_info(a).used]')
        self.assertEqual(result,['Computer','Observation','ComputerAction','ObservedInput','observation-2',True])
    def test_deepcopy_information_and_descriptors_do_not_revive(self):
        e=self.host.evaluator();o=e.run('let c=await computer_open();await computer_observe(c)');doc=e.env.get('observation_info').call(o);doc['targets'][0]['ref']='edited'
        self.assertEqual(o.document['targets'][0]['ref'],'e1');self.assertNotIn('text',serializable(o));self.assertIn('$observation',serializable(o))
        with self.assertRaises(LanguageError):e.env.get('observation_info').call(serializable(o))
        with self.assertRaises(LanguageError):Evaluator().env.get('observation_info').call(o)
    def test_stale_wrong_computer_ref_and_single_use_before_effect(self):
        for body in ['let o2=await computer_observe(c);await c(a)','let other=await computer_open();await other(a)','await c(a);await c(a)','await computer_close(c);await c(a)']:
            host=ComputerFixture();e=host.evaluator()
            with self.assertRaises(LanguageError):e.run('let c=await computer_open();let o=await computer_observe(c);let a=computer_action(o,{action:"click",ref:"e1"});'+body)
            self.assertLessEqual(sum(op=='act'for op,p in host.calls),1)
        with self.assertRaises(LanguageError):self.run_code('let c=await computer_open();let o=await computer_observe(c);computer_action(o,{action:"click",ref:"missing"})')
    def test_action_failure_consumed_and_busy_released(self):
        original=self.host.handle
        def handle(op,p):
            if op=='act':raise LanguageError('fixture failure')
            return original(op,p)
        e=Evaluator(computer_handler=handle)
        result=e.run('let c=await computer_open();let o=await computer_observe(c);let a=computer_action(o,{action:"click",ref:"e1"});try{await c(a)}catch e{null};[action_info(a).used,computer_info(c).status]')
        self.assertEqual(result,[True,'open'])
    def test_close_idempotent(self):
        self.assertEqual(self.run_code('let c=await computer_open();await computer_close(c);await computer_close(c)'),{'id':'computer-1','backend':'browser','status':'closed'})
        self.assertEqual([x[0]for x in self.host.calls],['open','close'])
    def test_epoch_cannot_reuse_resource(self):
        e=self.host.evaluator();c=e.run('await computer_open()')
        with self.assertRaisesRegex(LanguageError,'epoch'):e.run('await computer_observe(input)',{'input':c})
    def test_image_required_and_action_is_strict(self):
        with self.assertRaisesRegex(LanguageError,'captured image'):self.run_code('let c=await computer_open();observation_input(await computer_observe(c,false))')
        for spec in ['{action:"click",ref:"e1",snapshotId:9}','{action:"navigate",url:"file:///tmp/x"}','{action:"scroll",deltaX:true,deltaY:1}']:
            with self.assertRaises((LanguageError,ValueError)):self.run_code('let c=await computer_open();let o=await computer_observe(c);computer_action(o,'+spec+')')
    def test_source_attribution_is_captured_when_task_constructed(self):
        source='fn job(){computer_open()};let t=job();await t'
        self.host.evaluator().run(ProgramBundle(source,entry='entry.chaos'))
        location=self.host.calls[0][1]['location'];self.assertEqual(location['file'],'entry.chaos');self.assertIn('line',location)
    def test_visual_sync_and_task_agent_do_not_pass_live_values(self):
        e=self.host.evaluator(agent_handler=lambda*a:'ordinary');self.assertEqual(e.run('agent a{provider:"model"};let c=await computer_open();let o=await computer_observe(c);a(observation_input(o,42))'),'visual sync')
        self.assertEqual(self.host.agents[0]['_observed'],{'recordId':'observation-1','input':42});self.assertIsNone(self.host.agents[0]['value'])
        captured=[]
        def effect(kind,p,cancel):
            if kind=='computer':return self.host.handle(p['operation'],p['payload'])
            captured.append(copy.deepcopy(p));return 'visual task'
        e=self.host.evaluator(agent_handler=lambda*a:None,effect_handler=effect)
        self.assertEqual(e.run('agent a{provider:"model"};let c=await computer_open();let o=await computer_observe(c);async fn f(v){a(v)};await f(observation_input(o,{ask:42}))'),'visual task')
        self.assertEqual(captured[0]['_observed']['input'],{'ask':42});self.assertIsNone(captured[0]['value'])
    def test_plain_json_cannot_opt_in(self):
        seen=[];e=self.host.evaluator(agent_handler=lambda n,c,v:seen.append(v)or 42)
        e.run('agent a{provider:"model"};a({_observed:{recordId:"fake",input:42}})')
        self.assertEqual(seen,[{'_observed':{'recordId':'fake','input':42}}]);self.assertEqual(self.host.agents,[])
    def test_visual_task_fallback_preserves_image_and_team_envelope(self):
        ordinary=[]
        e=self.host.evaluator(agent_handler=lambda *args:ordinary.append(args)or'wrong')
        result=e.run('agent a{provider:"model"};let c=await computer_open();let o=await computer_observe(c);async fn f(){a(observation_input(o,42))};await f()')
        self.assertEqual(result,'visual sync');self.assertEqual(ordinary,[])
        self.assertEqual(self.host.agents[-1]['_observed'],{'recordId':'observation-1','input':42})
        e=self.host.evaluator(agent_handler=lambda *args:ordinary.append(args)or'wrong')
        result=e.run('let t=team_from({kind:"team",name:"roles",entry:"reader",roles:[{id:"reader",parent:null,capabilities:[],agent:{kind:"agent",name:"reader",config:{provider:"model",tools:[]}}}]});let c=await computer_open();let o=await computer_observe(c);await t(observation_input(o,43))')
        self.assertEqual(result,'visual sync');self.assertEqual(ordinary,[])
        self.assertEqual(self.host.agents[-1]['_observed']['input'],43)
        self.assertEqual(self.host.agents[-1]['_team']['capabilities'],[])
        self.assertEqual(self.host.agents[-1]['_team']['roleId'],'reader')
    def test_computer_descriptor_staticmethod_and_setter_rules(self):
        self.assertEqual(self.run_code('let c=await computer_open();let o=await computer_observe(c);class C{let call=staticmethod(c)};type_of(await C.call(computer_action(o,{action:"click",ref:"e1"})))'),'Observation')
        with self.assertRaises(LanguageError):self.run_code('let c=await computer_open();property(null,c)')
    def test_same_computer_busy_and_independent_gather(self):
        base=self.host.handle;entered=threading.Event();release=threading.Event()
        def effect(kind,p,cancel):
            if p['operation']=='observe':entered.set();release.wait(.05)
            return base(p['operation'],p['payload'])
        e=self.host.evaluator(effect_handler=effect)
        with self.assertRaisesRegex(LanguageError,'already executing'):e.run('let c=await computer_open();await gather([computer_observe(c),computer_observe(c)])')
        self.assertTrue(entered.is_set())
        e=self.host.evaluator();result=e.run('let a=await computer_open();let b=await computer_open();map(type_of,await gather([computer_observe(a),computer_observe(b)]))');self.assertEqual(result,['Observation','Observation'])
    def test_visual_team_preserves_capability_scope(self):
        captured=[]
        def effect(kind,p,cancel):
            if kind=='computer':return self.host.handle(p['operation'],p['payload'])
            captured.append(copy.deepcopy(p));return 'seen'
        e=self.host.evaluator(agent_handler=lambda*a:None,effect_handler=effect)
        result=e.run('let t=team_from({kind:"team",name:"roles",entry:"reader",roles:[{id:"reader",parent:null,capabilities:[],agent:{kind:"agent",name:"reader",config:{provider:"model",tools:[]}}}]});let c=await computer_open();let o=await computer_observe(c);await t(observation_input(o,42))')
        self.assertEqual(result,'seen');self.assertEqual(captured[0]['_team']['capabilities'],[]);self.assertEqual(captured[0]['_observed']['input'],42)
    def test_session_visual_payload_is_private_and_metadata_unchanged(self):
        from chaos.agent_values import definition_hash
        seen=[];agent={'kind':'agent','name':'worker','config':{'provider':'model','tools':[]}}
        row={'key':'chat','revision':1,'status':'idle','turn':0,'agent':agent,'definitionHash':definition_hash(agent),'contextLimit':100000}
        def session(op,p):
            seen.append((op,copy.deepcopy(p)))
            if op=='open':return copy.deepcopy(row)
            newer={**row,'revision':3,'turn':1}
            return {'receipt':{'key':'chat','messageId':'one','revision':3,'status':'completed','output':'seen','replayed':False},'session':newer}
        e=self.host.evaluator(session_handler=session)
        value=e.run('agent worker{provider:"model",tools:[]};let c=await computer_open();let o=await computer_observe(c);let s=await session_open("chat",worker);let r=await s(observation_input(o,{question:"where"}),"one");[r.output,session_info(s).turn]')
        self.assertEqual(value,['seen',1]);self.assertEqual(seen[1][1],{'key':'chat','revision':1,'input':None,'messageId':'one','_observed':{'recordId':'observation-1','input':{'question':'where'}}})
    def test_foreign_visual_owner_rejected_before_agent(self):
        first=self.host.evaluator();value=first.run('let c=await computer_open();observation_input(await computer_observe(c))')
        calls=[];second=self.host.evaluator(agent_handler=lambda*a:calls.append(a))
        with self.assertRaisesRegex(LanguageError,'ownership'):second.run('agent a{provider:"model"};a(input)',{'input':value})
        self.assertEqual(calls,[])
    def test_module_source_and_task_origin_captured_before_later_await(self):
        e=self.host.evaluator();bundle=ProgramBundle('import "resource.chaos" as R;let job=R.prepare();await job',files={'resource.chaos':'async fn prepare(){let job=computer_open();await job}export{prepare}'})
        e.run(bundle);place=self.host.calls[0][1]['location'];self.assertEqual(place['file'],'resource.chaos');self.assertEqual(place['taskId'],'task-1')
    def test_host_cannot_return_another_computer_observation(self):
        original=self.host.handle
        def wrong(op,p):
            result=original(op,p)
            if op=='observe':result['computerId']='another'
            return result
        with self.assertRaisesRegex(LanguageError,'another computer'):Evaluator(computer_handler=wrong).run('let c=await computer_open();await computer_observe(c)')
    def test_cancel_releases_busy_after_actual_worker_drains(self):
        from chaos.suspension import TaskCancelled
        original=self.host.handle;started=threading.Event();released=threading.Event()
        def effect(kind,p,cancel):
            if p['operation']=='observe':
                started.set()
                try:
                    while not cancel.wait(.005):pass
                    raise TaskCancelled('cancelled observation')
                finally:released.set()
            return original(p['operation'],p['payload'])
        e=self.host.evaluator(effect_handler=effect)
        # The independent tool waits for worker admission, then cancels that Task.
        def wait(name,value):
            self.assertTrue(started.wait(1));return None
        def dispatch(kind,p,cancel):
            if kind=='tool':return wait(p['name'],p['value'])
            return effect(kind,p,cancel)
        e.tool_handler=wait;e.effect_handler=dispatch
        with self.assertRaises(TaskCancelled):e.run('let c=await computer_open();let task=spawn(computer_observe(c));tool("wait",null);cancel(task);await task')
        self.assertTrue(released.is_set());self.assertIsNone(e.suspension.pool);self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0)
    def test_alias_descriptions_do_not_decode_large_snapshot_repeatedly(self):
        from unittest.mock import patch
        e=self.host.evaluator();values=e.run('let c=await computer_open();let o=await computer_observe(c);[o,computer_action(o,{action:"type",ref:"e1",text:"x"}),observation_input(o)]')
        with patch('chaos.computer_values.json.loads',side_effect=AssertionError('descriptor must use cached identities')):
            described=serializable([values]*100)
        self.assertEqual(described[0][0]['$observation'],'observation-1')
