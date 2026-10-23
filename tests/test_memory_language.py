"""Versioned memory through real language effects, Bot scopes and HTTP SDK."""
from pathlib import Path
import tempfile
import threading
import time
import unittest

from chaos import Client, Final, Framework, ProviderSpec, Registry, RunFailed, Settings, ToolCall, ToolSpec
from chaos.modules import STDLIB
from chaos.program import format_program, parse_program
from chaos.server import make_server

CAPS = ['memory.read', 'memory.compare_exchange', 'memory.get', 'memory.put']
SOURCE = '''import "std/memory" as M;
let before=M.read("counter");
let first=M.compare_exchange("counter",before.revision,null);
let stale=M.compare_exchange("counter",before.revision,999);
tool("memory.put",{key:"counter",value:41});
let newer=M.read("counter");
let final=M.compare_exchange("counter",newer.revision,newer.value+1);
[before,first,stale,newer,final,tool("memory.get","counter")]'''


class MemoryLanguage(unittest.TestCase):
    def app(self, registry=None):
        app=Framework(registry=registry, settings=Settings(':memory:'))
        self.addCleanup(app.close)
        return app

    def test_std_module_real_functions_legacy_write_invalidates_and_null_is_present(self):
        app=self.app();app.create_project('p', source=SOURCE, capabilities=CAPS)
        values=app.start('p',1,None,mode='source').result(8)
        self.assertEqual(values, [
            {'key':'counter','exists':False,'value':None,'revision':0},
            {'key':'counter','exists':True,'value':None,'revision':1,'swapped':True},
            {'key':'counter','exists':True,'value':None,'revision':1,'swapped':False},
            {'key':'counter','exists':True,'value':41,'revision':2},
            {'key':'counter','exists':True,'value':42,'revision':3,'swapped':True},42])
        self.assertEqual(Framework.evaluate('import "std/memory" as M;[type_of(M.read),type_of(M.compare_exchange)]'), ['Function','Function'])
        self.assertIn('std/memory',STDLIB)

    def test_denied_capabilities_and_closed_inputs_do_not_write(self):
        app=self.app()
        source='''import "std/memory" as M;
let denied=try{M.compare_exchange("secret",0,42);false}catch e{true};
[denied,tool("memory.get","secret")]'''
        app.create_project('deny',source=source,capabilities=['memory.get'])
        self.assertEqual(app.start('deny',1,None,mode='source').result(6),[True,None])
        source='''[try{tool("memory.compare_exchange",input);false}catch e{true},tool("memory.read","safe")]'''
        app.create_project('invalid',source=source,capabilities=CAPS)
        for value in ({'key':'safe','expectedRevision':True,'value':1},
                      {'key':'safe','expectedRevision':0,'value':1,'retry':True},
                      {'key':'safe','expectedRevision':-1,'value':1}):
            self.assertEqual(app.start('invalid',1,value,mode='source').result(6),
                             [True,{'key':'safe','exists':False,'value':None,'revision':0}])

    def test_registered_agent_can_use_actual_tool_and_cannot_invent_extra_authority(self):
        registry=Registry()
        registry.register_provider(ProviderSpec('choose',lambda r,c:
            ToolCall('memory.compare_exchange',{'key':'agent','expectedRevision':0,'value':42})
            if r.step==1 else Final(r.history[-1]['output'])))
        app=self.app(registry)
        app.create_project('agent',source='agent worker{provider:"choose",tools:["memory.compare_exchange"]};worker(null)',capabilities=CAPS)
        self.assertEqual(app.start('agent',1,None,mode='source').result(6)['value'],42)
        tools=app.capabilities()['tools']
        self.assertEqual(tools['memory.read']['effect'],'read')
        self.assertEqual(tools['memory.compare_exchange']['effect'],'write')
        self.assertTrue(tools['memory.compare_exchange']['callable'])

    def test_agent_tool_closure_uses_caller_capability_and_shared_ordinary_function(self):
        registry=Registry()
        registry.register_provider(ProviderSpec('choose',lambda r,c:
            ToolCall('save',r.input) if r.step==1 else Final(r.history[-1]['output'])))
        app=self.app(registry)
        source='''import "std/memory" as M;
agent worker{provider:"choose",tools:["memory.compare_exchange"]};
let save=agent_tool("save",fn(v){M.compare_exchange("closure",0,v)},capabilities=["memory.compare_exchange"]);
await agent_bind(worker,[save])(input)'''
        app.create_project('closure',source=source,capabilities=['memory.compare_exchange'])
        self.assertEqual(app.start('closure',1,42,mode='source').result(8),
                         {'key':'closure','exists':True,'value':42,'revision':1,'swapped':True})
        app.create_project('closure-denied',source=source.replace('tools:["memory.compare_exchange"]','tools:[]'),capabilities=['memory.compare_exchange'])
        with self.assertRaises(RunFailed):app.start('closure-denied',1,42,mode='source').result(8)
        self.assertIsNone(app._store.memory_get('closure-denied','closure'))

    def test_parallel_durable_bot_roles_have_one_winner_and_real_completed_effects(self):
        barrier=threading.Barrier(2);registry=Registry();calls=[]
        def rendezvous(value,context):
            calls.append(value);barrier.wait(timeout=5);return None
        registry.register_tool(ToolSpec('fixture.barrier',rendezvous))
        app=self.app(registry)
        code='''import "std/memory" as M;
async fn step(c){
 fn make(name){bot(name,fn(s){M.read("counter")},fn(p,s){
  tool("fixture.barrier",p.revision);M.compare_exchange("counter",p.revision,p.value+1)
 },fn(s){{done:true,context:null,output:s.evidence[0]}},role=name)};
 let left=make("left");let right=make("right");
 let root=bot("root",fn(s){null},async fn(p,s){
  let answers=await gather([left(),right()]);[answers[0].output,answers[1].output,M.read("counter")]
 },fn(s){{done:true,context:null,output:s.evidence[0]}});
 let answer=await root();process_step(answer.state,answer.output,answer.done)
};export {step}'''
        app.create_project('p',source='42',files={'dot.chaos':code},capabilities=CAPS+['fixture.barrier','process.manage'])
        app._store.memory_put('p','counter',0)
        app.create_process('p',1,'dot',{'kind':'process','name':'memory','module':'dot.chaos','export':'step',
            'intervalSeconds':None,'maxTurns':None,'roles':['left','right']})
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            row=app.get_process('p','dot')
            if row['status']in ('completed','needs_reconciliation'):break
            time.sleep(.005)
        self.assertEqual(row['status'],'completed',row.get('error'))
        left,right,current=row['output']
        self.assertCountEqual([left['swapped'],right['swapped']],[True,False])
        self.assertEqual(current,{'key':'counter','exists':True,'value':1,'revision':2})
        self.assertEqual(calls,[1,1])
        events=app.events('p',limit=300)['items']
        self.assertEqual(sum(e['type']=='tool.completed'and e['payload'].get('tool')=='memory.compare_exchange'for e in events),2)

    def test_http_sdk_program_ir_and_formatting_use_same_memory_effects(self):
        app=self.app()
        with tempfile.TemporaryDirectory()as folder:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                client.create_project('http',source=SOURCE,capabilities=CAPS)
                result=client.start('http',1,None,mode='source').result(8)
                self.assertEqual(result[-1],42);self.assertFalse(result[2]['swapped'])
                ir=client.program('http')['program']
                self.assertEqual(parse_program(format_program(ir))['semanticHash'],parse_program(SOURCE)['semanticHash'])
                draft='import "std/memory" as M; M.'
                analysis=client.analyze(draft,position=len(draft))
                self.assertTrue({'read','compare_exchange'}<= {x['label']for x in analysis['completions']})
                self.assertEqual(client.format_source(SOURCE),Framework.format_source(SOURCE))
            finally:
                server.shutdown();worker.join(3);server.server_close()

    def test_committed_memory_is_not_rolled_back_or_replayed_with_process_failure(self):
        app=self.app()
        app.create_project('failed',source='42',files={'step.chaos':
            'import "std/memory" as M;fn step(c){M.compare_exchange("saved",0,42);throw "after commit"};export {step}'},
            capabilities=CAPS+['process.manage'])
        app.create_process('failed',1,'job',{'kind':'process','name':'memory','module':'step.chaos','export':'step',
            'intervalSeconds':None,'maxTurns':None})
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            row=app.get_process('failed','job')
            if row['status']=='needs_reconciliation':break
            time.sleep(.005)
        self.assertEqual(row['status'],'needs_reconciliation')
        self.assertEqual(app._store.memory_read('failed','saved'),{'key':'saved','exists':True,'value':42,'revision':1})
        self.assertFalse(app._store.memory_compare_exchange('failed','saved',0,43)['swapped'])
        self.assertEqual(row['turn'],0)
