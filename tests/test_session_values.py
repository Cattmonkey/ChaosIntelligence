import copy
import threading
import time
import unittest
from chaos.language import Evaluator,LanguageError,serializable
from chaos.agent_values import definition_hash
from chaos.session_values import AgentSession,configuration_schema
from chaos.suspension import TaskCancelled

AGENT={'kind':'agent','name':'worker','config':{'provider':'model'}}
def metadata(key='thread',revision=1,**extra):
    return dict({'key':key,'revision':revision,'status':'idle','turn':0,'agent':AGENT,'definitionHash':definition_hash(AGENT),'contextLimit':100000},**extra)
class Host:
    def __init__(self):self.events=[];self.current=1
    def __call__(self,op,p):
        self.events.append((op,copy.deepcopy(p),threading.get_ident()))
        if op in ('open','load','fork'):
            result=metadata(p.get('newKey',p['key']),self.current)
            if p.get('agent') is not None:result.update(agent=p['agent'],definitionHash=definition_hash(p['agent']))
            result['contextLimit']=p.get('contextLimit',100000)
            return result
        if op=='turn':
            assert p['revision']==self.current,(p,self.current)
            self.current+=1
            return {'receipt':{'key':p['key'],'messageId':p['messageId'] or 'generated','status':'completed','output':str(p['input']),'revision':self.current,'replayed':False},'session':metadata(p['key'],self.current)}
        return {'operation':op}

class SessionValueTests(unittest.TestCase):
    def run_source(self,source,host=None,**options):return Evaluator(session_handler=host,**options).run(source)
    def test_open_load_fork_cold_and_owned_function(self):
        h=Host();value=self.run_source('agent a{provider:"model"};let t=session_open("thread",a);[type_of(t),type_of(session_load("thread"))]',h);self.assertEqual(value,['Task','Task']);self.assertEqual(h.events,[])
        self.assertEqual(self.run_source('let s:AgentSession=await session_load("thread");let f:Function=s;[type_of(s),type_of(f(1)),session_info(s).key]',h),['AgentSession','Task','thread'])
        self.assertEqual(len(h.events),1)
    def test_handles_not_forged_or_transferred(self):
        h=Host();e=Evaluator(session_handler=h);handle=e.run('await session_load("thread")');self.assertIs(type(handle),AgentSession)
        for src in ['session_info(input)','await input(1)','type_of(input)','staticmethod(input)','tool("capture",{s:input})']:
            with self.subTest(src=src),self.assertRaises(LanguageError):Evaluator(session_handler=h,tool_handler=lambda*a:None).run(src,{'input':handle})
        for src in ['let s:AgentSession=input;s','await input(1)']:
            with self.assertRaises(LanguageError):Evaluator(session_handler=h).run(src,{'input':serializable(handle)})
    def test_pure_environment_cannot_enable_effect_generic_callback(self):
        seen=[]
        with self.assertRaises(LanguageError):self.run_source('await session_load("thread")',effect_handler=lambda*a:seen.append(a))
        self.assertEqual(seen,[])
    def test_open_requires_explicit_native_agent_and_valid_definition(self):
        for src in ['agent a{};session_open("x",a)','agent a{provider:"local"};session_open("x",a)','session_open("x",{kind:"agent"})','agent a{provider:"model"};session_open("x",a,true)','agent a{provider:"model"};session_open("x",a,999)']:
            with self.subTest(src=src),self.assertRaises(LanguageError):self.run_source(src)
    def test_exact_operation_payloads_and_schema_freshness(self):
        h=Host();self.run_source('agent a{provider:"model"};await session_open("thread",a,context_limit=2000);await session_status("thread");await session_history("thread",after=2,limit=3);await session_fork("thread",1,"new",a);await session_compact("thread",1,0);await session_close("thread",1);await session_reconcile("thread",1,"checked")',h)
        ops=[(o,p)for o,p,_ in h.events];self.assertEqual([o for o,p in ops],['open','status','history','fork','compact','close','reconcile']);self.assertEqual(ops[0][1],{'key':'thread','agent':{'kind':'agent','name':'a','config':{'provider':'model'}},'contextLimit':2000});self.assertEqual(ops[2][1],{'key':'thread','after':2,'limit':3});self.assertEqual(ops[4][1]['keepTurns'],0)
        schema=configuration_schema();schema['properties']['agent']['properties']['config']['properties'].clear();self.assertTrue(configuration_schema()['properties']['agent']['properties']['config']['properties'])
    def test_two_precreated_tasks_read_latest_revision_at_execution(self):
        h=Host();self.assertEqual(self.run_source('let s=await session_load("thread");let a=s(1,message_id="first");let b=s(2,message_id="second");let one=await a;let two=await b;[one.output,two.output,session_info(s).revision]',h),['1','2',3]);self.assertEqual([p['revision']for o,p,_ in h.events if o=='turn'],[1,2])
    def test_same_handle_busy_rejected_no_second_host_effect(self):
        h=Host();started=threading.Event()
        def host(op,p):
            if op=='turn':started.set();time.sleep(.03)
            return h(op,p)
        e=Evaluator(session_handler=host)
        with self.assertRaisesRegex(LanguageError,'另一调用'):e.run('let s=await session_load("thread");await gather([s(1),s(2)])')
        self.assertEqual(sum(o=='turn'for o,p,_ in h.events),1);self.assertFalse(e.suspension.active)
    def test_receipt_replay_never_regresses_snapshot_revision(self):
        def host(op,p):
            if op=='load':return metadata(revision=7)
            return {'receipt':{'key':'thread','messageId':'old','status':'completed','output':'prior','revision':2,'replayed':True},'session':metadata(revision=7)}
        self.assertEqual(self.run_source('let s=await session_load("thread");let r=await s(1,"old");[r.revision,session_info(s).revision]',host),[2,7])
    def test_snapshot_copy_and_host_thread_boundary(self):
        h=Host();main=threading.get_ident();e=Evaluator(session_handler=h);s=e.run('await session_load("thread")');snapshot=e.run('session_info(input)',{'input':s});snapshot['revision']=99;self.assertEqual(s.snapshot['revision'],1);self.assertNotEqual(h.events[0][2],main)
    def test_combinators_macro_descriptors_and_plan_output_json(self):
        h=Host();src='let s=await session_load("thread");class C{let start=staticmethod(s)};let f=compose(fn(x){x},partial(C.start,42));let a=await apply(f,[]);let p=plan([{id:"ask",call:"ask",needs:[]}]);let r=await run_plan(p,{ask:async fn(ctx){await s(ctx.input)}},43);[a.output,r.ask.output]';self.assertEqual(self.run_source(src,h),['42','43'])
        with self.assertRaises(LanguageError):self.run_source('let s=await session_load("thread");property(null,staticmethod(s))',Host())
    def test_invalid_payloads_before_host(self):
        for src in ['session_history("x",true)','session_history("x",0,101)','session_close("x",0)','session_reconcile("x",1,{})','session_compact("x",1,-1)','session_load("../x")']:
            h=Host()
            with self.subTest(src=src),self.assertRaises(LanguageError):self.run_source(src,h)
            self.assertEqual(h.events,[])
    def test_recording_caught_refusal_still_aborts(self):
        calls=[]
        def modes(op,p):
            calls.append(op)
            if op=='recording.begin':return {'scopeId':'scope'}
            if op=='recording.finish':raise LanguageError('incomplete')
            return None
        h=Host()
        with self.assertRaises(LanguageError):self.run_source('async fn action(x){try{await session_load("thread")}catch e{null};42};await record_skill(action)',h,mode_handler=modes)
        self.assertIn('recording.abort',calls);self.assertEqual(h.events,[])
    def test_host_mismatched_identity_rejected(self):
        with self.assertRaises(LanguageError):self.run_source('await session_load("thread")',lambda *a:metadata('other'))
        with self.assertRaises(LanguageError):self.run_source('agent a{provider:"model"};await session_open("thread",a)',lambda *a:metadata())

    def test_host_error_releases_busy_and_rejects_bad_receipt(self):
        for error in [LanguageError('failed'),TaskCancelled('cancel')]:
            def host(op,p):
                if op=='load':return metadata()
                raise error
            e=Evaluator(session_handler=host);handle=e.run('await session_load("thread")')
            with self.assertRaises(type(error)):e.run('await input(1)',{'input':handle})
            self.assertFalse(handle.busy);self.assertEqual(e.frames,[]);self.assertIsNone(e.suspension.pool)
        h=Host()
        def bad(op,p):return h(op,p) if op=='load' else {'output':'forged'}
        with self.assertRaises(LanguageError):self.run_source('let s=await session_load("thread");await s(1)',bad)
    def test_receipt_wrapper_budget_does_not_fail_after_valid_large_output(self):
        h=Host()
        def host(op,p):
            result=h(op,p)
            if op=='turn':result['receipt']['output']='x'*39900;result['receipt']['messageId']='m'*128
            return result
        self.assertEqual(self.run_source('let s=await session_load("thread");length((await s(1)).output)',host),39900)
