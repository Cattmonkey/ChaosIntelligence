"""Actual cooperative interpreter pause/step tests, including HTTP and shutdown."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from chaos import Framework, Client, Settings
from chaos.debugger import options, preview
from chaos.runtime import Budget, CombinedCancel, TaskBudget, Context
from unittest.mock import patch
from chaos.server import make_server
from chaos.store import Conflict

ROOT = Path(__file__).resolve().parent.parent


class DebuggerTests(unittest.TestCase):
    def setUp(self):
        self.app = Framework(':memory:', settings=Settings(':memory:'))
        self.addCleanup(self.app.close)

    def start(self, source, **kwargs):
        project = self.app.create_project('debug-'+str(time.time_ns()), source=source)
        return project, self.app.start_debug(project['id'], project['version'], **kwargs)

    def paused(self, run, after=0):
        end = time.monotonic()+3
        while time.monotonic()<end:
            state=run.debugger()
            if state['status']=='paused' and state['pauseId']>after:return state
            if state['status'] not in ('paused','running'):self.fail(str(run.get()))
            time.sleep(.005)
        self.fail('Interpreter did not pause')

    def resume(self, run, state, command='continue'):
        run.debug_command(command,pause_id=state['pauseId'])

    def test_entry_is_real_pause_and_snapshot_survives_project_edit(self):
        project,run=self.start('let a=40;\nlet b=a+2;\nb')
        state=self.paused(run);time.sleep(.03)
        self.assertEqual(run.get()['status'],'running')
        self.assertNotIn('a',[v['name'] for v in state['frames'][0]['locals']])
        project.update(source='99',files={'main.chaos':'99'})
        saved=self.app.save_project(project,from_view='source')
        self.assertEqual(saved['version'],2);self.assertEqual(run.debugger()['version'],1)
        self.resume(run,state);self.assertEqual(run.result(3),42)
        self.assertEqual(run.get()['snapshot']['source'],'let a=40;\nlet b=a+2;\nb')

    def test_breakpoints_locals_stale_commands_and_repeat_same_line(self):
        _,run=self.start('var i=0;\nwhile i<3 { i=i+1; };\ni',pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':2}])
        state=self.paused(run);seen=[]
        for _ in range(8):
            seen.append(next(v['value'] for v in state['frames'][0]['locals'] if v['name']=='i'))
            old=state['pauseId'];self.resume(run,state)
            try:state=self.paused(run,old)
            except AssertionError:
                if run.get()['status']=='completed':break
                raise
        self.assertEqual(run.result(3),3)
        self.assertIn(0,seen);self.assertIn(1,seen);self.assertIn(2,seen)
        # Consecutive children on the line must not trap Continue forever.
        self.assertLessEqual(len(seen),5)

    def test_step_into_over_out_and_recursive_frame_identity(self):
        source='fn inner(x){\n let y=x+1;\n y\n}\nfn outer(x){\n let y=inner(x);\n y+1\n}\nlet answer=outer(40);\nanswer'
        _,run=self.start(source,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':9}])
        state=self.paused(run);self.resume(run,state,'step_into');state=self.paused(run,state['pauseId'])
        self.assertEqual(state['location']['line'],6);self.assertEqual(state['frames'][0]['name'],'outer')
        with self.assertRaises(Conflict):run.debug_command('continue',pause_id=state['pauseId']-1)
        self.resume(run,state,'step_into');state=self.paused(run,state['pauseId'])
        self.assertEqual(state['location']['line'],2);self.assertEqual(state['frames'][0]['name'],'inner')
        self.assertEqual(len(set(f['id'] for f in state['frames'])),len(state['frames']))
        self.resume(run,state,'step_out');state=self.paused(run,state['pauseId'])
        self.assertEqual(state['location']['line'],7);self.assertEqual(state['frames'][0]['name'],'outer')
        self.resume(run,state,'step_over');state=self.paused(run,state['pauseId'])
        self.assertEqual(state['location']['line'],10)
        self.resume(run,state);self.assertEqual(run.result(3),42)

    def test_step_over_skips_nested_function_and_recursion_out(self):
        source='fn f(n){\n if n==0 {0} else {\n let r=f(n-1);\n r+1\n }\n}\nlet r=f(2);\nr'
        _,run=self.start(source,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':3}])
        s=self.paused(run);first=s['frames'][0]['id'];run.debug_command('breakpoints',breakpoints=[])
        self.resume(run,s,'step_into');s=self.paused(run,s['pauseId'])
        self.assertNotEqual(s['frames'][0]['id'],first)
        self.resume(run,s,'step_out');s=self.paused(run,s['pauseId'])
        self.assertEqual(s['frames'][0]['id'],first);self.assertEqual(s['location']['line'],4)
        self.resume(run,s);self.assertEqual(run.result(3),2)
        _,run=self.start('fn f(x){\n x+1\n}\nlet v=f(41);\nv',pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':4}])
        s=self.paused(run);self.resume(run,s,'step_over');s=self.paused(run,s['pauseId'])
        self.assertEqual(s['location']['line'],5);self.resume(run,s);self.assertEqual(run.result(3),42)

    def test_cancel_timeout_and_close_wake_pause(self):
        _,run=self.start('1');self.paused(run);run.cancel();self.assertEqual(run.wait(3)['status'],'cancelled')
        _,run=self.start('1',pause_timeout=.1);self.paused(run);self.assertEqual(run.wait(3)['status'],'failed')
        self.assertIn('暂停超时',run.get()['error'])
        _,run=self.start('1');self.paused(run);start=time.monotonic();self.app.close();self.assertLess(time.monotonic()-start,1)

    def test_effect_budget_observes_live_pause_and_task_cancel_isolated(self):
        _,run=self.start('1');self.paused(run)
        session=self.app._engine.debug_sessions[run.id]
        budget=Budget(threading.Event(),seconds=.02);budget.pause_clock=session.paused_seconds
        time.sleep(.04);budget.tick()
        a,b=threading.Event(),threading.Event();parent=threading.Event()
        first=TaskBudget(budget,CombinedCancel(parent,a));second=TaskBudget(budget,CombinedCancel(parent,b))
        a.set()
        with self.assertRaises(BaseException):first.tick()
        second.tick();self.assertFalse(parent.is_set());self.resume(run,run.debugger());self.assertEqual(run.result(3),1)

    def test_object_method_pause_and_safe_identity_previews(self):
        source='class Counter {\n var value:Int=40;\n fn add(self,amount:Int){\n  self.value=self.value+amount;\n  self.value\n }\n}\nlet item=Counter();\nitem.add(2)'
        _,run=self.start(source,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':4}])
        state=self.paused(run)
        receiver=next(v['value']for v in state['frames'][0]['locals']if v['name']=='self')
        self.assertEqual(receiver['class'],'Counter');self.assertEqual(receiver['fields']['value'],40)
        self.resume(run,state);self.assertEqual(run.result(3),42)
        from chaos.objects import ChaosClass,ChaosInstance,Member,BoundMethod,SuperProxy
        from chaos.language import Cell,Function
        klass=ChaosClass('Example',(),None);klass.mro=(klass,);klass.building=False
        instance=ChaosInstance(klass,None);instance.fields={'self':Cell(instance,'Any'),'token':Cell('in-memory-secret','Any')}
        snapshot=preview(instance)
        self.assertTrue(snapshot['fields']['self']['circular']);self.assertEqual(snapshot['fields']['token'],'[redacted]')
        function=Function([], 'Any',None,None,'call')
        self.assertEqual(preview(BoundMethod(function,instance,klass,None))['receiverClass'],'Example')
        self.assertEqual(preview(SuperProxy(klass,instance,None))['after'],'Example')

    def test_bounded_safe_previews_and_validation(self):
        class Unsafe:
            def __repr__(self):raise AssertionError('repr must not run')
            @property
            def name(self):raise AssertionError('property must not run')
        self.assertEqual(preview(Unsafe()),{'type':'Unsafe'})
        huge=[['x'*1000 for _ in range(30)] for _ in range(30)]
        huge=[huge]*30
        self.assertLess(len(json.dumps(preview(huge))),750000)
        cyclic=[];cyclic.append(cyclic);self.assertTrue(preview(cyclic)[0]['circular'])
        for value in ({'expression':'x'},{'pauseTimeout':float('nan')},{'pauseOnEntry':1},{'breakpoints':[{'file':'../a','line':1}]}):
            with self.subTest(value=value),self.assertRaises(ValueError):options(value,{'main.chaos':'1'})
        p=self.app.create_project('bad-debug',source='1')
        with self.assertRaises(ValueError):self.app.start(p['id'],1,None,mode='graph',debug=True)
        with self.assertRaises(ValueError):self.app.start(p['id'],1,None,mode='source',auto_optimize=True,debug=True)

    def test_http_sdk_cli_share_real_pause(self):
        with tempfile.TemporaryDirectory() as folder:
            server=make_server('127.0.0.1',0,framework=self.app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_address[1]));p=client.create_project('http-debug',source='let a=40;\na+2')
                run=client.start_debug(p['id'],p['version']);state=self.paused(run)
                result=subprocess.run([sys.executable,'-m','chaos','debug','inspect',run.id,'--server',client.base_url.rsplit('/api',1)[0]],capture_output=True,text=True,timeout=5,cwd=ROOT)
                self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout)['pauseId'],state['pauseId'])
                self.resume(run,state);self.assertEqual(run.result(3),42)
            finally:server.shutdown();server.server_close();worker.join(2)

    def test_async_tasks_debugger_has_task_frames_and_durable_events(self):
        source='async fn f(x){\n let y=x+1;\n y\n}\nawait gather([f(40),f(41)])'
        p,run=self.start(source,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':2}])
        state=self.paused(run);self.assertTrue(state['frames'][0]['taskId'])
        run.debug_command('breakpoints',breakpoints=[]);self.resume(run,state)
        self.assertEqual(run.result(3),[41,42])
        events=self.app.events(p['id'],run_id=run.id,limit=300)['items']
        lifecycle=[event for event in events if event['type'].startswith('program.task.')]
        self.assertTrue(lifecycle);self.assertTrue(all(event['payload']['taskId'] for event in lifecycle))
        self.assertFalse(any(event['type']=='program.step' for event in events))

    def test_generator_debug_frame_and_sensitive_local_preview(self):
        source='fn* values(x){\n let password="do-not-display";\n yield x;\n return x+1\n}\nlet g=values(41);\n[next(g),next(g)]'
        _,run=self.start(source,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':3}])
        state=self.paused(run);values={v['name']:v['value'] for v in state['frames'][0]['locals']}
        self.assertEqual(values['password'],'[redacted]');self.assertEqual(values['x'],41)
        self.assertNotIn('do-not-display',json.dumps(state))
        self.resume(run,state);self.assertEqual(run.result(3),[{'done':False,'value':41},{'done':True,'value':42}])

    def test_effect_tool_environment_cache_is_task_specific(self):
        created=[]
        class Environment:
            def __init__(self,settings,config,checkpoint,event):self.checkpoint=checkpoint;created.append(self)
            def call(self,name,value):self.checkpoint();return value
            def close(self):pass
        p=self.app.create_project('task-cache',source='null',capabilities=['code.execute'])
        context=Context(self.app._engine,'test-only',p,threading.Event())
        context.event=lambda *args:None
        # Avoid durable tool events using a nonexistent run, while exercising the real child path.
        with patch('chaos.control.ToolEnvironment',Environment),patch.object(Context,'event',lambda *args:None):
            one,two=threading.Event(),threading.Event()
            self.assertEqual(context.effect('tool',{'name':'code.execute','value':1},one,{'code.execute'},'same-node',True),1)
            one.set()
            self.assertEqual(context.effect('tool',{'name':'code.execute','value':42},two,{'code.execute'},'same-node',True),42)
        self.assertEqual(len(created),2);self.assertFalse(context.cancel.is_set())

    def test_async_effect_and_graph_tools_close_on_creating_thread(self):
        made,closed=[],[]
        class Environment:
            def __init__(self,settings,config,checkpoint,event):
                self.owner=threading.get_ident();self.checkpoint=checkpoint;made.append(self)
            def call(self,name,value):self.checkpoint();return value
            def close(self):
                self.assertion = self.owner == threading.get_ident()
                if not self.assertion:raise AssertionError('thread-affine session closed on another thread')
                closed.append(self)
        with patch('chaos.control.ToolEnvironment',Environment):
            p=self.app.create_project('effect-cleanup',source='async fn f(x){tool("code.execute",x)}await gather([f(20),f(22)])',capabilities=['code.execute'])
            self.assertEqual(self.app.start(p['id'],1,None,mode='source').result(3),[20,22])
            self.assertEqual(len(made),2);self.assertEqual(len(closed),2)
            context=Context(self.app._engine,'test-only',p,threading.Event());failures=[]
            def worker():
                try:
                    with patch.object(Context,'event',lambda *args:None):
                        self.assertEqual(self.app._engine.execute_node(context,{'id':'node','kind':'tool','config':{'tool':'code.execute'}},42),42)
                except BaseException as error:failures.append(error)
            thread=threading.Thread(target=worker);thread.start();thread.join(2)
            self.assertFalse(thread.is_alive());self.assertEqual(failures,[])
            self.assertEqual(len(made),3);self.assertEqual(len(closed),3);self.assertEqual(context.tool_environments,{})

    def test_preview_redacts_before_string_key_and_name_truncation(self):
        fake_key='fake-key-for-redaction-boundary-123456789'
        redact=lambda value:value.replace(fake_key,'[redacted]')
        raw='a'*490+fake_key+'z'*50
        shown=preview(raw,redact=redact)
        self.assertNotIn('fake-key',shown)
        self.assertNotIn('fake-key',json.dumps(preview({'a'*150+fake_key:raw},redact=redact)))
        # This is a test-only in-memory credential. No real configuration is read.
        self.app._engine.settings._data['providers']['test-redaction']={'apiKey':fake_key}
        source='let message='+json.dumps(raw)+';\nmessage'
        _,run=self.start(source,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':2}])
        state=self.paused(run);self.assertNotIn('fake-key',json.dumps(state));self.resume(run,state)
        run.wait(3)
        class Evil:
            @property
            def __dict__(self):raise AssertionError('custom property must never execute')
        self.assertEqual(preview(Evil()),{'type':'Evil'})

    def test_async_generator_pause_send_value_and_safe_handle_summary(self):
        source='async fn* stream(x){\n let sent=yield x;\n yield sent;\n return 9\n}\nlet g=stream(40);\nlet first=await anext(g);\nlet second=await asend(g,42);\n[first,second,await anext(g)]'
        _,run=self.start(source,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':3}])
        state=self.paused(run)
        self.assertTrue(state['frames'][0]['taskId'])
        values={v['name']:v['value'] for v in state['frames'][0]['locals']};self.assertEqual(values['sent'],42)
        module=next(f for f in state['frames'] if f['name']=='<module>')
        handle=next(v['value']for v in module['locals']if v['name']=='g')
        self.assertEqual(handle['type'],'AsyncGenerator');self.assertIn('state',handle)
        self.assertNotIn('iterator',json.dumps(state));self.resume(run,state)
        self.assertEqual(run.result(3),[{'done':False,'value':40},{'done':False,'value':42},{'done':True,'value':9}])

    def test_cancel_while_async_generator_is_paused_terminates_run(self):
        _,run=self.start('async fn* values(){\n yield 42\n}\nawait acollect(values())',pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':2}])
        self.paused(run);self.assertTrue(run.cancel());self.assertEqual(run.wait(3)['status'],'cancelled')
        self.assertEqual(run.debugger()['frames'],[])

    def test_descriptor_preview_is_inert_and_class_bound_receiver_is_safe(self):
        from chaos.language import Evaluator
        evaluator=Evaluator()
        values=evaluator.run('class C{@property\nfn value(self){throw "getter must not run"};@classmethod\nfn owner(cls){cls};@staticmethod\nfn plain(x){x}};let c=C();[C,C.value,C.owner,C.plain,c,super(C,C)]')
        result=preview(values)
        self.assertEqual(result[1],{'type':'Descriptor','kind':'Property','getter':True,'setter':False,'deleter':False})
        self.assertEqual(result[2]['receiverClass'],'C')
        self.assertEqual(result[4]['class'],'C');self.assertEqual(result[5]['receiverClass'],'C')
        self.assertNotIn('owner',json.dumps(result[1]))

    def test_classmethod_callable_instance_preview_has_safe_name(self):
        from chaos.language import Evaluator
        method=Evaluator().run('class Factory{fn __call__(self,cls){42}};class C{let build=classmethod(Factory())};C.build')
        result=preview(method)
        self.assertEqual(result['type'],'BoundMethod');self.assertEqual(result['receiverClass'],'C')
        self.assertEqual(result['name'],'Factory')

    def test_real_property_getter_pause_locals_and_continue(self):
        source='class C{var raw=42;\n@property\nfn value(self){\n self.raw\n}};let c=C();c.value'
        _,run=self.start(source,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':4}])
        state=self.paused(run)
        self.assertEqual(state['frames'][0]['name'],'value')
        receiver=next(v['value'] for v in state['frames'][0]['locals'] if v['name']=='self')
        self.assertEqual(receiver['class'],'C');self.assertEqual(receiver['fields']['raw'],42)
        self.resume(run,state);self.assertEqual(run.result(3),42)

    def test_user_descriptor_preview_protocols_and_property_deleter_are_inert(self):
        from chaos.language import Evaluator
        effects=[]
        evaluator=Evaluator(tool_handler=lambda *args:effects.append(args))
        values=evaluator.run('class D{fn __get__(self,obj,owner){tool("forbidden",1)};fn __delete__(self,obj){null}}class C{let x=D()};[D(),C(),property(fn(self){42},null,fn(self){null})]')
        result=preview(values)
        self.assertEqual(result[0]['type'],'Instance');self.assertEqual(result[0]['protocols'],['__delete__','__get__'])
        self.assertEqual(result[1]['fields']['x']['class'],'D');self.assertTrue(result[2]['deleter'])
        self.assertEqual(effects,[])

    def test_real_user_descriptor_getter_and_deleter_pause(self):
        source='class D{\nfn __get__(self,obj,owner){\n obj.raw\n}\nfn __delete__(self,obj){\n obj.raw=0;null\n}}class C{var raw=42;let value=D()};let c=C();let first=c.value;delattr(c,"value");[first,c.raw]'
        _,run=self.start(source,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':3},{'file':'main.chaos','line':6}])
        state=self.paused(run);self.assertEqual(state['frames'][0]['name'],'__get__')
        locals={v['name']:v['value'] for v in state['frames'][0]['locals']};self.assertEqual(locals['obj']['class'],'C');self.assertEqual(locals['owner']['name'],'C')
        self.resume(run,state);state=self.paused(run,state['pauseId']);self.assertEqual(state['frames'][0]['name'],'__delete__')
        self.resume(run,state);self.assertEqual(run.result(3),[42,0])

    def test_cancel_paused_user_descriptor_deleter(self):
        source='class D{fn __delete__(self,obj){\n null\n}}class C{let x=D()};delattr(C(),"x")'
        _,run=self.start(source,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':2}])
        self.paused(run);run.cancel();self.assertEqual(run.wait(3)['status'],'cancelled');self.assertEqual(run.debugger()['frames'],[])

    @unittest.skipUnless(shutil.which('node'),'Node.js is unavailable')
    def test_debugger_ui_contract(self):
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/debugger_controls.cjs')],capture_output=True,text=True,timeout=20,cwd=ROOT)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
