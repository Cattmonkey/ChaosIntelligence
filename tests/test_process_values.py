"""Controlled Process values and fresh exported-turn evaluator lifecycles."""
import copy
import json
import threading
import unittest
from dataclasses import FrozenInstanceError

from chaos.language import Evaluator, LanguageError, ResourceLimit, serializable
from chaos.modules import ProgramBundle
from chaos.program_values import canonical_hash, definition_from_bundle
from chaos.process_values import (Process,builtins,configuration_schema,definition_hash,source_definition,
    validate_definition,validate_binding,validate_step)
from chaos.suspension import TaskCancelled


def definition(**changes):
    return dict({'kind':'process','module':'dot.chaos','export':'step','intervalSeconds':None,'maxTurns':100,'name':'dot'},**changes)


def bundle(source, main='throw "entry initializer must not run"', extra=None):
    files={'main.chaos':main,'dot.chaos':source};files.update(extra or {})
    return ProgramBundle(main,files)


def context(state=None,input=None):
    return {'state':state,'input':input,'turn':1,'process':{'key':'dot','kind':'start','messageId':None},'programVersion':1}


class ProcessValueTests(unittest.TestCase):
    def evaluate(self,source,value=None,**options):
        return Evaluator(**options).run(source,{'input':definition() if value is None else value})

    def test_owned_nominal_callable_and_no_marker_revival(self):
        source='let p:Process=process_from(input);let f:Function=p;[type_of(f),p==p,p==process_from(input),type_of(p("dot")),type_of(parse_json(json_stringify(p)))]'
        self.assertEqual(self.evaluate(source),['Process',True,False,'Task','Record'])
        for source in ['let p:Process=input;p','await input("dot")']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.evaluate(source,serializable(builtins(Evaluator())['process_from'](definition())))

    def test_frozen_definition_info_and_schema_are_fresh(self):
        e=Evaluator();api=builtins(e);d=definition();p=api['process_from'](d);d['name']='changed'
        public=api['process_info'](p);public['maxTurns']=None
        self.assertEqual(p.definition,definition())
        self.assertEqual(p.definition_hash,definition_hash(definition()))
        with self.assertRaises(FrozenInstanceError):p.owner=None
        schema=configuration_schema();schema['properties']['kind']['const']='bad'
        self.assertEqual(configuration_schema()['properties']['kind']['const'],'process')

    def test_strict_public_fields_no_grants_and_all_required(self):
        bad=[None,[],{},definition(kind='Program'),definition(capabilities=[]),definition(projectId='other'),definition(name=''),definition(name='x'*201)]
        for key in definition():
            d=definition();del d[key];bad.append(d)
        class Sub(dict):pass
        bad.append(Sub(definition()))
        for d in bad:
            with self.subTest(value=d),self.assertRaises(LanguageError):validate_definition(d)

    def test_paths_exports_intervals_and_turn_limits(self):
        for path in ['../dot.chaos','/dot.chaos','a/../dot.chaos','std/x.chaos','dot.txt','a\\dot.chaos',1]:
            with self.subTest(path=path),self.assertRaises(LanguageError):validate_definition(definition(module=path))
        for export in ['','x.y','fn','x;tool("x",1)',1]:
            with self.subTest(export=export),self.assertRaises(LanguageError):validate_definition(definition(export=export))
        for interval in [0,0.9,86401,True,'1',float('nan'),float('inf'),1<<1000]:
            with self.subTest(interval=str(interval)[:30]),self.assertRaises(LanguageError):validate_definition(definition(intervalSeconds=interval))
        for maximum in [0,-1,1000001,True,1.5,'1']:
            with self.subTest(maximum=maximum),self.assertRaises(LanguageError):validate_definition(definition(maxTurns=maximum))
        for interval in [None,1,1.5,86400]:self.assertEqual(validate_definition(definition(intervalSeconds=interval))['intervalSeconds'],interval)
        self.assertIsNone(validate_definition(definition(maxTurns=None))['maxTurns'])
        self.assertEqual(validate_definition(definition(export='推进'))['export'],'推进')

    def test_source_roundtrip_and_no_constructor_shadow(self):
        d=definition(intervalSeconds=1.5,maxTurns=None)
        self.assertEqual(Evaluator().run(source_definition(d,'角色')+'process_info(角色)'),d)
        for name in ['process_from','fn','','x;tool("x",1)']:
            with self.subTest(name=name),self.assertRaises(LanguageError):source_definition(d,name)

    def test_all_effects_cold_and_require_dedicated_host(self):
        prefix='let p=process_from(input);'
        calls=['p("a")','process_start("a",p,1)','process_send("a",2,"message")','process_status("a")','process_control("a",1,"pause")','process_configure("a",1,input)','process_reconcile("a",1,{},"checked",true)']
        for call in calls:
            with self.subTest(call=call):
                self.assertEqual(self.evaluate(prefix+'type_of('+call+')'),'Task')
                seen=[]
                with self.assertRaises(LanguageError):self.evaluate(prefix+'await '+call,effect_handler=lambda *a:seen.append(a))
                self.assertEqual(seen,[])

    def test_public_effect_payloads_snapshot_and_argument_labels(self):
        seen=[]
        def host(op,payload):seen.append((op,payload));return {'operation':op}
        source='let p=process_from(input);await p(key="k",state={n:1});await process_send("k",{x:2},message_id="m");await process_status("k");await process_control("k",2,"pause");await process_configure("k",3,input);await process_reconcile("k",4,{n:5},evidence="verified",resume=true)'
        self.assertEqual(self.evaluate(source,process_handler=host),{'operation':'reconcile'})
        self.assertEqual([x[0]for x in seen],['start','send','status','control','configure','reconcile'])
        self.assertEqual(seen[0][1],{'key':'k','definition':definition(),'state':{'n':1}})
        self.assertEqual(seen[1][1],{'key':'k','input':{'x':2},'messageId':'m'})
        self.assertEqual(seen[-1][1],{'key':'k','revision':4,'state':{'n':5},'evidence':'verified','resume':True})

    def test_combinators_macros_static_descriptors_and_owner(self):
        source='macro make(ctor,d){quote{unquote(ctor)(unquote(d))}};let p=make!(process_from,input);class C{let start=staticmethod(p)};let f=compose(fn(t){t},partial(C.start,"key"));await apply(f,[{n:42}])'
        self.assertEqual(self.evaluate(source,process_handler=lambda op,p: p['state']),{'n':42})
        p=builtins(Evaluator())['process_from'](definition())
        for source in ['process_info(input)','await input("x")','staticmethod(input)','property(null,input)','tool("capture",{x:input})','type_of(input)']:
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator(process_handler=lambda *a:None,tool_handler=lambda *a:None).run(source,{'input':p})
        with self.assertRaises(LanguageError):self.evaluate('let p=process_from(input);property(null,staticmethod(p))')

    def test_key_revision_message_control_and_evidence_validation(self):
        calls=['process_send("../bad",1)','process_send("x",1,"")','process_control("x",true,"pause")','process_control("x",0,"pause")','process_control("x",1,"restart")','process_reconcile("x",1,{},evidence={})','process_reconcile("x",1,{},resume=1)']
        for call in calls:
            with self.subTest(call=call),self.assertRaises(LanguageError):self.evaluate(call)
        with self.assertRaises(LanguageError):self.evaluate('process_send("x",input)', 'x'*40001)
        with self.assertRaises(LanguageError):self.evaluate('process_reconcile("x",1,{},input)', 'x'*1001)

    def test_process_step_is_exact_plain_bounded_json(self):
        self.assertEqual(self.evaluate('process_step({n:42},done=true)'),{'state':{'n':42},'output':None,'done':True})
        for value in [None,{}, {'state':1,'output':2,'done':1},{'state':1,'output':2,'done':False,'extra':0}, {'state':'x'*40001,'output':None,'done':False}]:
            with self.subTest(value=str(value)[:100]),self.assertRaises(LanguageError):validate_step(value)
        for source in ['process_step(fn(x){x})','process_step(process_from(input))','async fn f(){1};process_step(f())','class C{};process_step(C())']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.evaluate(source)
        self.assertEqual(len(validate_step({'state':'a'*39998,'output':'b'*39998,'done':False})['state']),39998)
        with self.assertRaises(LanguageError):validate_step({'state':[999999]*6000,'output':None,'done':False})
        with self.assertRaises(LanguageError):validate_step({'state':'\n'*20000,'output':None,'done':False})

    def test_validate_binding_is_pure_and_runtime_export_only(self):
        b=bundle('tool("initializer",null);fn step(ctx){process_step(ctx.state)};export {step}')
        self.assertEqual(validate_binding(b,definition()),definition())
        for d in [definition(module='missing.chaos'),definition(export='missing')]:
            with self.assertRaises(LanguageError):validate_binding(b,d)
        with self.assertRaises(LanguageError):validate_binding(bundle('macro step(x){x};export {step}'),definition())

    def test_run_export_loads_selected_module_and_preserves_bundle_entry_origin(self):
        b=bundle('import "lib.chaos" as L;fn step(ctx){process_step({old:ctx.state,value:L.n},program_metadata(current_program()),true)};export {step}',extra={'lib.chaos':'let n=42;export {n}','unused.chaos':'tool("unused",null)'})
        origin={'projectId':'p','version':7,'definitionHash':canonical_hash(definition_from_bundle(b))}
        seen=[];e=Evaluator(program_origin=origin,tool_handler=lambda n,v:seen.append(n))
        result=e.run_export(b,'dot.chaos','step',context(1))
        self.assertEqual(result['state'],{'old':1,'value':42});self.assertEqual(result['output']['origin'],origin)
        self.assertIs(e.bundle,b);self.assertEqual(e.bundle.entry,'main.chaos');self.assertEqual(seen,[])
        self.assertEqual(set(e.module_cache),{'dot.chaos','lib.chaos'});self.assertFalse(e.suspension.active)

    def test_missing_export_fails_before_initializer(self):
        seen=[];b=bundle('tool("should-not-run",null);let private=42')
        with self.assertRaises(LanguageError):Evaluator(tool_handler=lambda n,v:seen.append(n)).run_export(b,'dot.chaos','private',context())
        self.assertEqual(seen,[])

    def test_sync_and_async_exports_and_exactly_one_await(self):
        sources=['fn step(ctx){process_step(ctx.state,42,true)};export {step}', 'async fn step(ctx){process_step(ctx.state,42,true)};export {step}', 'async fn inner(ctx){process_step(ctx.state,42,true)};fn step(ctx){inner(ctx)};export {step}']
        for source in sources:
            with self.subTest(source=source):self.assertEqual(Evaluator().run_export(bundle(source),'dot.chaos','step',context({'n':1})),{'state':{'n':1},'output':42,'done':True})
        b=bundle('async fn inner(){process_step(null)};async fn step(ctx){inner()};export {step}')
        with self.assertRaises(LanguageError):Evaluator().run_export(b,'dot.chaos','step',context())

    def test_export_callable_validation_and_generator_results(self):
        for source in ['let step=42;export {step}','fn step(){process_step(null)};export {step}','fn* step(ctx){yield process_step(null)};export {step}','async fn* step(ctx){yield process_step(null)};export {step}','fn step(ctx){fn(x){x}};export {step}']:
            e=Evaluator()
            with self.subTest(source=source),self.assertRaises(LanguageError):e.run_export(bundle(source),'dot.chaos','step',context())
            self.assertFalse(e.suspension.active);self.assertIsNone(e.suspension.pool);self.assertEqual(e.frames,[])

    def test_export_agent_and_async_effect_use_existing_host(self):
        seen=[]
        b=bundle('agent step{provider:"fixture"};export {step}')
        def agent(name,config,value):seen.append(value);return {'state':value['state'],'output':42,'done':False}
        self.assertEqual(Evaluator(agent_handler=agent).run_export(b,'dot.chaos','step',context(7))['output'],42)
        self.assertEqual(seen,[context(7)])
        b=bundle('async fn step(ctx){process_step(ctx.state,tool("echo",ctx.input))};export {step}')
        self.assertEqual(Evaluator(tool_handler=lambda n,v:v).run_export(b,'dot.chaos','step',context(1,42))['output'],42)

    def test_export_epoch_drains_cleanup_and_rejects_failed_descendant(self):
        seen=[]
        b=bundle('fn* values(){try{yield 1}finally{tool("cleanup",null)}};fn step(ctx){let stream=values();next(stream);process_step(null,42)};export {step}')
        e=Evaluator(tool_handler=lambda n,v:seen.append(n))
        self.assertEqual(e.run_export(b,'dot.chaos','step',context())['output'],42)
        self.assertEqual(seen,['cleanup']);self.assertEqual(e.frames,[]);self.assertFalse(e.suspension.active)
        b=bundle('async fn bad(){throw "failed"};async fn pause(){1};async fn step(ctx){spawn(bad());await pause();process_step(null)};export {step}')
        with self.assertRaises(LanguageError):Evaluator().run_export(b,'dot.chaos','step',context())

    def test_export_cleanup_failure_and_hard_cancel_are_not_success(self):
        b=bundle('fn* values(){try{yield 1}finally{tool("cleanup",null)}};fn step(ctx){let g=values();next(g);process_step(null)};export {step}')
        for error in [LanguageError('cleanup failed'),TaskCancelled('cancel')]:
            def tool(*args):raise error
            e=Evaluator(tool_handler=tool)
            with self.subTest(error=type(error).__name__),self.assertRaises(type(error)):e.run_export(b,'dot.chaos','step',context())
            self.assertFalse(e.suspension.active);self.assertEqual(e.frames,[])
        b=bundle('fn step(ctx){while true {null};process_step(null)};export {step}')
        with self.assertRaises(ResourceLimit):Evaluator(step_limit=100).run_export(b,'dot.chaos','step',context())

    def test_repeated_export_turns_rebuild_lexical_state(self):
        b=bundle('var local=0;fn step(ctx){local=local+1;process_step(ctx.state+1,local)};export {step}')
        e=Evaluator();first=e.run_export(b,'dot.chaos','step',context(0));second=e.run_export(b,'dot.chaos','step',context(first['state']))
        self.assertEqual([first,second],[{'state':1,'output':1,'done':False},{'state':2,'output':1,'done':False}])

    def test_recording_refusal_poison_survives_user_catch(self):
        seen=[];effects=[]
        def host(op,payload):
            seen.append(op)
            if op=='recording.begin':return {'scopeId':'scope'}
            if op=='recording.abort':return None
            if op=='recording.finish':raise LanguageError('incomplete')
            raise AssertionError(op)
        source='async fn action(x){try{await process_status("key")}catch e{null};42};await record_skill(action)'
        with self.assertRaises(LanguageError):self.evaluate(source,mode_handler=host,process_handler=lambda *args:effects.append(args))
        self.assertIn('recording.abort',seen);self.assertEqual(effects,[])

    def test_effect_payload_thread_ownership_and_no_live_output(self):
        main=threading.get_ident();seen=[]
        def host(op,payload):seen.append(threading.get_ident());return {'key':payload['key']}
        self.assertEqual(self.evaluate('await process_status("key")',process_handler=host),{'key':'key'})
        self.assertNotEqual(seen[0],main)
        with self.assertRaises(LanguageError):self.evaluate('await process_status("key")',process_handler=lambda *_:Evaluator().run('fn(x){x}'))


if __name__=='__main__':unittest.main()
