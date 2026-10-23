"""Live Team roles compose real callables and preserve authority/provenance."""
import json
import unittest
from unittest.mock import patch
from chaos import Framework,Registry,ProviderSpec,ToolSpec,ToolCall,Final,Settings
from chaos.language import Evaluator,LanguageError
from tests.test_harness_runtime import FakeClient,message


def definition(capabilities=()):
    def role(name,parent):return {'id':name,'parent':parent,'capabilities':list(capabilities),
        'agent':{'kind':'agent','name':name,'config':{'provider':'local','tools':[]}}}
    return {'kind':'team','name':'live','entry':'worker','roles':[role('lead',None),role('worker','lead')]}


class TeamFunctionTests(unittest.TestCase):
    def evaluate(self,source,caps=(),**kwargs):
        return Evaluator(**kwargs).run('let t=team_from(input);'+source,{'input':definition(caps)})

    def runtime(self,source,capabilities=(),*,registry=None,settings=None):
        app=Framework(registry=registry,settings=settings or Settings(':memory:'));self.addCleanup(app.close)
        app.create_project('p',source='let t=team_from('+json.dumps(definition(capabilities))+');'+source,capabilities=list(capabilities))
        run=app.start('p',1,None,mode='source');return app,run,run.result(10)

    def test_real_multiarity_keywords_contracts_and_cold_roles(self):
        result=self.evaluate('var seen=0;fn add(x:Int,y:Int=2)->Int{seen=seen+1;x+y};let b=team_bind(t,{worker:add});let pending=b(40);[seen,type_of(b),type_of(pending),await pending,await team_role(b,"worker")(y=3,x=39),seen]')
        self.assertEqual(result,[0,'Function','Task',42,42,2])
        self.assertEqual(self.evaluate('let b=team_bind(t,{worker:fn(x:Int){x}});try{await b("bad")}catch e{e.code}'),'runtime_error')

    def test_metadata_fallback_and_portable_export_guard(self):
        result=self.evaluate('let b=team_bind(t,{worker:fn(x){x}});[team_binding_info(b),keys(team_bindings(b)),type_of(parse_json(json_stringify(b))),try{team_info(b)}catch e{e.code},try{team_source(b)}catch e{e.code}]')
        self.assertEqual(result[0]['team'],definition());self.assertEqual(result[0]['bindings'],{'worker':{'kind':'Function'}})
        self.assertEqual(result[2:],['Record','team_error','team_error'])
        self.assertEqual(self.evaluate('await team_bind(t,{})(42)',agent_handler=lambda n,c,v:v),42)

    def test_binding_identity_epoch_and_invalid_bindings(self):
        ev=Evaluator();bound=ev.run('team_bind(team_from(input),{})',{'input':definition()})
        for evaluator in (ev,Evaluator()):
            with self.assertRaises(LanguageError):evaluator.run('team_binding_info(input)',{'input':bound})
        for value in ('[]','{missing:fn(x){x}}','{worker:42}'):
            with self.subTest(value=value),self.assertRaises(LanguageError):self.evaluate('team_bind(t,'+value+')')

    def test_async_result_is_awaited_once_and_owned_returns_remain_values(self):
        result=self.evaluate('async fn work(x){async fn later(){x};later()};let b=team_bind(t,{worker:work});let pending=await b(42);[type_of(pending),await pending]')
        self.assertEqual(result,['Task',42])
        self.assertEqual(self.evaluate('let b=team_bind(t,{worker:fn(x){fn(y){x+y}}});let f=await b(40);f(2)'),42)

    def test_restricted_returned_stored_and_native_closures_cannot_escalate(self):
        for expression in ('fn(){tool("test.secret",null)}','partial(wide)','compose(wide,fn(){null})'):
            with self.subTest(expression=expression):
                calls=[]
                result=self.evaluate('fn wide(x=null){tool("test.secret",null)};var saved=null;let b=team_bind(t,{worker:fn(x){saved='+expression+';saved}});let f=await b(0);[try{f()}catch e{e.code},try{saved()}catch e{e.code},wide()]',tool_handler=lambda n,v:calls.append(n) or 42)
                self.assertEqual(result,['capability_error','capability_error',42]);self.assertEqual(calls,['test.secret'])

    def test_tail_calls_keep_restriction_and_compose_restores_continuation_authority(self):
        calls=[]
        code='fn wide(x){tool("test.secret",x)};let b=team_bind(t,{worker:fn(x){fn loop(n){if n==0{wide(42)}else{loop(n-1)}};loop}});let f=await b(0);[try{f(500)}catch e{e.code},compose(wide,fn(x){x})(42)]'
        self.assertEqual(self.evaluate(code,tool_handler=lambda n,v:calls.append(v) or v),['capability_error',42]);self.assertEqual(calls,[42])
        code='fn wide(x){tool("test.secret",x)};let b=team_bind(t,{worker:fn(x){fn(y){y}}});let narrow=await b(0);compose(wide,narrow)(42)'
        self.assertEqual(self.evaluate(code,tool_handler=lambda n,v:v),42)

    def test_fresh_method_class_and_descriptor_wrappers_keep_creation_authority(self):
        setups=[('class C{fn work(self){tool("test.secret",null)}};let c=C();','c.work','saved()'),
                ('class C{fn __call__(self){tool("test.secret",null)}};','C()','saved()'),
                ('class C{var value=null};fn wide(){tool("test.secret",null)};','staticmethod(wide)','C.value=saved;C.value()'),
                ('class C{var value=null};fn wide(cls){tool("test.secret",null)};','classmethod(wide)','C.value=saved;C.value()'),
                ('class C{var value=null};let c=C();fn wide(self){tool("test.secret",null)};','property(wide)','C.value=saved;c.value')]
        for setup,expression,consume in setups:
            with self.subTest(expression=expression):
                seen=[];code=setup+'let b=team_bind(t,{worker:fn(x){'+expression+'}});let saved=await b(0);try{'+consume+'}catch e{e.code}'
                self.assertEqual(self.evaluate(code,tool_handler=lambda n,v:seen.append(v)),'capability_error');self.assertEqual(seen,[])

    def test_owned_cyclic_instance_is_not_projected_at_role_return(self):
        code='class Box{var value=null};let b=team_bind(t,{worker:fn(x){let item=Box();item.value=item;item}});let result=await b(0);result.value==result'
        self.assertTrue(self.evaluate(code))
        code='class Box{var value=null;fn __call__(self){42}};let b=team_bind(t,{worker:fn(x){let item=Box();item.value=item;item}});let result=await b(0);[result.value==result,result.value()]'
        self.assertEqual(self.evaluate(code),[True,42])
        code='let b=team_bind(t,{worker:fn(x){class Base{};{base:Base}}});let result=await b(0);class Derived(result.base){};[type_of(result.base),issubclass(Derived,result.base)]'
        self.assertEqual(self.evaluate(code),['Class',True])

    def test_inherited_member_projection_cannot_shed_receiver_authority(self):
        variants=[('let f=staticmethod(wide)','Narrow','escaped.f(42)'),
                  ('let f=staticmethod(wide)','Wide()','escaped.f(42)'),
                  ('let f=classmethod(fn(cls,x){wide(x)})','Narrow','escaped.f(42)'),
                  ('let f=property(fn(self){wide(42)})','Narrow()','escaped.f'),
                  ('let f=property(fn(self){wide(42)})','Narrow','escaped.f.fget(null)'),
                  ('fn f(self,x){wide(x)}','Narrow','escaped.f(null,42)'),
                  ('fn f(self,x){wide(x)}','Narrow()','escaped.f(42)'),
                  ('let f=property(fn(self){wide})','Narrow()','escaped.f(42)'),
                  ('let f=wide','Narrow','escaped.f(42)')]
        for member,returned,consume in variants:
            with self.subTest(member=member,returned=returned,consume=consume):
                seen=[];code='fn wide(x){tool("test.secret",x)};class Wide{'+member+'};let b=team_bind(t,{worker:fn(x){class Narrow(Wide){};'+returned+'}});let escaped=await b(0);try{'+consume+'}catch e{e.code}'
                self.assertEqual(self.evaluate(code,tool_handler=lambda n,v:seen.append(v)),'capability_error');self.assertEqual(seen,[])
        self.assertTrue(self.evaluate('fn f(x){x};class C{let work=staticmethod(f)};C.work==f'))

    def test_created_binding_keeps_restricted_authority_after_escape(self):
        calls=[]
        code='var saved=null;let outer=team_bind(t,{worker:fn(x){saved=team_bind(t,{worker:fn(y){tool("test.secret",y)}});0}});await outer(0);try{await saved(1)}catch e{e.code}'
        self.assertEqual(self.evaluate(code,tool_handler=lambda n,v:calls.append(v)),'capability_error');self.assertEqual(calls,[])

    def test_broader_borrowed_task_remains_unstarted_and_owned_by_its_caller(self):
        code='async fn wide(){42};let borrowed=wide();let b=team_bind(t,{worker:fn(x){borrowed}});let error=try{await b(0)}catch e{e.code};[error,task_info(borrowed).state,task_info(borrowed).cancellationRequested,await borrowed]'
        self.assertEqual(self.evaluate(code),['capability_error','created',False,42])

    def test_argument_errors_and_caught_body_errors_restore_caller_authority(self):
        code='let b=team_bind(t,{worker:fn(x){fn(y:Int){throw "stop"}}});let f=await b(0);[try{f("wrong")}catch e{e.code},try{f(1)}catch e{e.code},tool("test.secret",42)]'
        self.assertEqual(self.evaluate(code,tool_handler=lambda n,v:v),['runtime_error','user_throw',42])

    def test_sync_generator_iterator_and_stack_cleanup_preserve_role_provenance(self):
        seen=[];registry=Registry();registry.register_tool(ToolSpec('test.probe',lambda x,c:seen.append(x) or x))
        code='''let b=team_bind(t,{worker:fn(x){
          fn* values(){try{tool("test.probe","generator");yield 42}finally{tool("test.probe","generator.close")}};
          class Values{fn __iter__(self){self}fn __next__(self){tool("test.probe","iterator");{done:false,value:42}}fn __close__(self){tool("test.probe","iterator.close");null}}
          let stack=exit_stack();stack.callback(fn(){tool("test.probe","stack.close")});[values(),iter(Values()),stack.pop_all()]}});
          let handles=await b(0);next(handles[0]);next(handles[1]);close(handles[0]);close(handles[1]);handles[2].close();42'''
        app,run,result=self.runtime(code,['test.probe'],registry=registry);self.assertEqual(result,42)
        self.assertEqual(set(seen),{'generator','iterator','generator.close','iterator.close','stack.close'})
        from tests.test_team_integration import events
        scopes=[row['payload']['team']for row in events(app,'p',run.id)if row['type']=='tool.started']
        self.assertEqual({c['roleId']for c in scopes},{'worker'});self.assertEqual(len({c['invocationId']for c in scopes}),1)

    def test_escaped_deferred_handles_retain_capability_ceiling(self):
        calls=[]
        for handler,consume in [('fn(x){async fn later(){tool("test.secret",null)};later()}','await h'),
                                ('fn(x){fn* values(){tool("test.secret",null);yield 1};values()}','next(h)'),
                                ('fn(x){let s=exit_stack();s.callback(fn(){tool("test.secret",null)});s}','h.close()')]:
            with self.subTest(handler=handler):
                code='let b=team_bind(t,{worker:'+handler+'});try{let h=await b(0);'+consume+'}catch e{e.code}'
                self.assertEqual(self.evaluate(code,tool_handler=lambda n,v:calls.append(v)),'capability_error')
        self.assertEqual(calls,[])

    def test_nested_sibling_roles_restore_actual_invocation_provenance(self):
        seen=[]
        code='var b=null;b=team_bind(t,{lead:async fn(x){tool("test.probe","before");await team_role(b,"worker")(x);tool("test.probe","after")},worker:fn(x){tool("test.probe","inside")}});await team_role(b,"lead")(1);tool("test.probe","outside")'
        # Outside uses no role, so record its separate shape without assuming one.
        registry=Registry();registry.register_tool(ToolSpec('test.probe',lambda x,c:seen.append(x) or x))
        app,run,result=self.runtime(code,['test.probe'],registry=registry)
        from tests.test_team_integration import events
        scopes=[row['payload'].get('team')for row in events(app,'p',run.id)if row['type']=='tool.started']
        self.assertEqual([c['roleId']if c else None for c in scopes],['lead','worker','lead',None])
        self.assertEqual(scopes[0]['invocationId'],scopes[2]['invocationId']);self.assertNotEqual(scopes[0]['invocationId'],scopes[1]['invocationId'])

    def test_real_bound_agent_handler_effects_keep_role_scope(self):
        seen=[];registry=Registry()
        registry.register_provider(ProviderSpec('choose',lambda r,c:ToolCall('work',r.input)if not r.history else Final(r.history[-1]['output'])))
        registry.register_tool(ToolSpec('test.probe',lambda x,c:seen.append(x) or x+1))
        code='agent a{provider:"choose",tools:["test.probe"]};let impl=agent_bind(a,[agent_tool("work",fn(x){tool("test.probe",x)},capabilities=["test.probe"])]);let b=team_bind(t,{worker:impl});await b(41)'
        app,run,result=self.runtime(code,['test.probe'],registry=registry);self.assertEqual(result,42)
        from tests.test_team_integration import events
        scopes=[row['payload']['team']for row in events(app,'p',run.id)if row['type']=='tool.started']
        self.assertEqual(scopes[0]['implementationKind'],'BoundAgent');self.assertEqual(scopes[0]['roleId'],'worker')

    def test_prebuilt_bound_session_and_plain_session_are_direct_role_implementations(self):
        for bound in (False,True):
            with self.subTest(bound=bound):
                settings=Settings(':memory:');settings.update({'providers':{'text':{'baseURL':'https://fixture.invalid/v1','apiKey':'fixture-only'}}},1)
                client=FakeClient([message('done')]);source='agent a{provider:"model",tools:[]};let s=await session_open("chat",a);let impl='+('session_bind(s,[])'if bound else's')+';let b=team_bind(t,{worker:impl});[await b(1,message_id="once"),await b(1,message_id="once")]'
                with patch.object(settings,'client',return_value=client):app,run,result=self.runtime(source,['session.manage'],settings=settings)
                self.assertEqual([r['replayed']for r in result],[False,True]);self.assertEqual(len(client.requests),1)


if __name__=='__main__':unittest.main()
