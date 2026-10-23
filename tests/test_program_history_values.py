"""Cold history reads return owned snapshots, without widening generic output."""
import copy
import json
import threading
import unittest
from unittest.mock import patch
from chaos.language import Evaluator, LanguageError, ResourceLimit, serializable
from chaos.modules import ProgramBundle
from chaos.program_values import Program, builtins, canonical_hash, _RevisionPayload
from chaos.suspension import TaskCancelled


def definition(source='input+1', files=None, versions=None):
    files={**(files or {}),'main.chaos':source}
    return {'kind':'program','entry':'main.chaos','files':files,'languageVersions':versions or {k:2 for k in files}}


def response(d=None, version=1, project='p'):
    d=definition() if d is None else d
    return {'definition':d,'origin':{'projectId':project,'version':version,'definitionHash':canonical_hash(d)}}


def metadata(version=1, kind='created'):
    return {'version':version,'definitionHash':canonical_hash(definition()),'entry':'main.chaos','created':1.5,'kind':kind,'runId':None,'reason':''}


class ProgramHistoryValueTests(unittest.TestCase):
    def test_cold_values_no_host_and_dedicated_handler_required(self):
        seen=[];e=Evaluator(program_handler=lambda *a:seen.append(a))
        self.assertEqual(e.run('[type_of(program_history()),type_of(program_revision(1))]'),['Task','Task']);self.assertEqual(seen,[])
        for call in ('program_history()','program_revision(1)'):
            seen=[]
            with self.assertRaisesRegex(LanguageError,'不允许'):Evaluator(effect_handler=lambda*a:seen.append(a)).run('await '+call)
            self.assertEqual(seen,[])

    def test_strict_arguments_reject_before_host(self):
        for call in ('program_revision(0)','program_revision(true)','program_revision(-1)','program_revision(9223372036854775808)','program_history(after=true)','program_history(after=-1)','program_history(limit=0)','program_history(limit=101)','program_history(limit=true)','program_revision(version="1")'):
            seen=[]
            with self.subTest(call=call),self.assertRaises(LanguageError):Evaluator(program_handler=lambda*a:seen.append(a)).run('await '+call)
            self.assertEqual(seen,[])

    def test_metadata_page_and_builtin_aliases(self):
        page={'items':[metadata(2,'saved')],'nextCursor':2,'currentVersion':3,'firstAvailableVersion':1};calls=[]
        def host(op,p):calls.append((op,p));return page
        result=Evaluator(program_handler=host).run('let f=program_history;await apply(partial(f,limit=1),[],{after:1})')
        self.assertEqual(result,page);self.assertIsNot(result,page);self.assertIsNot(result['items'],page['items'])
        self.assertEqual(calls,[('history',{'after':1,'limit':1})])

    def test_empty_page_can_follow_current_head(self):
        page={'items':[],'nextCursor':None,'currentVersion':2,'firstAvailableVersion':2}
        self.assertEqual(Evaluator(program_handler=lambda *_:page).run('await program_history(after=9)'),page)

    def test_owned_program_and_immutable_historical_origin(self):
        payload=response();e=Evaluator(program_handler=lambda *_:payload,program_origin={'projectId':'p','version':3,'definitionHash':'a'*64})
        p=e.run('await program_revision(1)');self.assertIs(type(p),Program);self.assertIs(p.owner,e)
        payload['definition']['files']['main.chaos']='0';self.assertEqual(p.definition['files']['main.chaos'],'input+1')
        info=builtins(e)['program_metadata'](p);info['origin']['version']=9;self.assertEqual(p.origin['version'],1)
        with self.assertRaisesRegex(LanguageError,'跨执行'):builtins(Evaluator())['program_info'](p)
        with self.assertRaises(LanguageError):Evaluator().run('program_info(input)',{'input':serializable(p)})

    def test_snapshot_constructed_on_ast_thread_without_module_execution(self):
        main=threading.get_ident();worker=[];compiles=[];seen=[];d=definition('tool("should-not-run",null)',{'unused.chaos':'agent unused{provider:"model"};null'})
        def host(op,p):worker.append(threading.get_ident());return response(d)
        from chaos import program_values
        original=program_values.validate_definition
        def validate(*args,**kwargs):compiles.append(threading.get_ident());return original(*args,**kwargs)
        with patch.object(program_values,'validate_definition',validate):
            result=Evaluator(program_handler=host,tool_handler=lambda*a:seen.append(a),agent_handler=lambda*a:seen.append(a)).run('type_of(await program_revision(1))')
        self.assertEqual(result,'Program');self.assertEqual(seen,[]);self.assertTrue(all(x!=main for x in worker));self.assertEqual(compiles,[main])

    def test_history_response_rejects_malformed_metadata(self):
        page={'items':[metadata()],'nextCursor':None,'currentVersion':1,'firstAvailableVersion':1}
        bad=[{},dict(page,extra=1),dict(page,nextCursor=True),dict(page,nextCursor=1),dict(page,firstAvailableVersion=2),dict(page,currentVersion=True)]
        for key,value in [('created',True),('created',-1),('created',2**1000),('kind','unknown'),('entry','../bad.chaos'),('definitionHash','fake'),('runId',{}),('reason',None),('version',True)]:
            item=metadata();item[key]=value;bad.append(dict(page,items=[item]))
        bad.append(dict(page,currentVersion=2,items=[metadata(2),metadata(1)]))
        for result in bad:
            with self.subTest(result=result),self.assertRaises(LanguageError):Evaluator(program_handler=lambda *_:result).run('await program_history()')

    def test_revision_response_exact_request_hash_origin_and_compile(self):
        base=response();bad=[None,dict(base,metadata={}),dict(base,origin=None),dict(base,origin={**base['origin'],'version':2}),dict(base,origin={**base['origin'],'projectId':'other'}),dict(base,origin={**base['origin'],'definitionHash':'0'*64}),response(definition('let ='))]
        for result in bad:
            with self.subTest(result=str(result)[:100]),self.assertRaises(LanguageError):Evaluator(program_handler=lambda *_:result,program_origin={'projectId':'p','version':3,'definitionHash':'a'*64}).run('await program_revision(1)')

    def test_large_snapshot_opaque_crossing_and_ordinary_output_budget(self):
        d=definition('42',{name+'.chaos':'//'+('x'*90000)+'\nnull'for name in ('a','b','c')});payload=response(d)
        e=Evaluator(program_handler=lambda *_:payload)
        self.assertEqual(e.run('let p=await program_revision(1);[type_of(p),length(keys(program_info(p).files)),program_metadata(p).origin.version]'),['Program',4,1])
        with self.assertRaises(ResourceLimit):serializable(e.run('program_info(await program_revision(1))'))
        with self.assertRaises(ResourceLimit):Evaluator(program_handler=lambda *_:d).run('await program_from({kind:"program",entry:"main.chaos",files:{"main.chaos":"null"},languageVersions:{"main.chaos":2}})(null)')
        with self.assertRaises(LanguageError):Evaluator(program_handler=lambda *_:response(definition('1',{'huge.chaos':'x'*1200000}))).run('await program_revision(1)')

    def test_private_envelope_cannot_escape_other_effects(self):
        payload=_RevisionPayload.capture(response())
        with self.assertRaises(LanguageError):Evaluator(program_handler=lambda *_:payload).run('await program_from({kind:"program",entry:"main.chaos",files:{"main.chaos":"null"},languageVersions:{"main.chaos":2}})(null)')
        with self.assertRaises(LanguageError):Evaluator(tool_handler=lambda *_:payload).run('async fn f(){tool("bad",null)}await f()')

    def test_historical_dialect_and_macro_shape_preserved(self):
        d=definition('class C{let x=40;fn __getitem__(self,k){42}};C()["x"]',versions={'main.chaos':1})
        calls=[]
        def host(op,p):
            calls.append(op)
            if op=='revision':return response(d)
            value=p['definition'];return Evaluator().run(ProgramBundle(value['files'][value['entry']],value['files'],value['entry'],language_versions=value['languageVersions']))
        self.assertEqual(Evaluator(program_handler=host).run('let p=await program_revision(1);[program_info(p).languageVersions["main.chaos"],await p(null)]'),[1,40]);self.assertEqual(calls,['revision','run'])

    def test_same_snapshot_new_owned_values_and_current_reflection_unchanged(self):
        source='let old=await program_revision(1);[program_metadata(current_program()).origin.version,program_metadata(old).origin.version,old==await program_revision(1)]'
        current={'projectId':'p','version':3,'definitionHash':canonical_hash(definition(source))}
        self.assertEqual(Evaluator(program_origin=current,program_handler=lambda *_:response()).run(source),[3,1,False])

    def test_hard_errors_and_compile_budget_propagate(self):
        for error in (TaskCancelled('stop'),ResourceLimit('budget')):
            def host(*_):raise error
            with self.subTest(error=type(error).__name__),self.assertRaises(type(error)):Evaluator(program_handler=host).run('try{await program_revision(1)}catch e{42}')
        d=definition('['+','.join('1'for _ in range(1000))+']')
        with self.assertRaises(ResourceLimit):Evaluator(step_limit=150,program_handler=lambda *_:response(d)).run('await program_revision(1)')

    def test_recording_rejects_reads_before_dispatch_and_poison_survives_catch(self):
        for call in ('program_revision(1)','program_history()'):
            modes=[];calls=[]
            def mode(op,p):
                modes.append(op)
                if op=='recording.begin':return {'scopeId':'scope'}
                if op=='recording.finish':raise LanguageError('incomplete')
            with self.subTest(call=call),self.assertRaises(LanguageError):Evaluator(mode_handler=mode,program_handler=lambda *a:calls.append(a)).run('await record_skill(async fn(x){try{await '+call+'}catch e{null};42})')
            self.assertEqual(calls,[]);self.assertIn('recording.abort',modes)

    def test_generic_effect_dispatch_and_payload_exactness(self):
        seen=[]
        def effect(kind,p,c):seen.append((kind,p));return response(version=2)
        e=Evaluator(program_handler=lambda *_:self.fail('fallback not expected'),effect_handler=effect)
        self.assertEqual(e.run('program_metadata(await program_revision(version=2)).origin.version'),2)
        self.assertEqual(seen,[('program',{'operation':'revision','payload':{'version':2}})])

    def test_actual_language_restore_keeps_current_functions_and_origin(self):
        from chaos import Framework,Settings
        source='''import "behavior.chaos" as B;
if input==null {B.answer()} else {
let base=current_program();let old=await program_revision(input);
let receipt=await program_adopt(base,old,[{entry:"tests.chaos",input:null,expect:40}]);
[receipt.version,B.answer(),program_metadata(current_program()).origin.version]}'''
        files={'behavior.chaos':'fn answer(){40};export{answer}','tests.chaos':'import "behavior.chaos" as B;B.answer()'}
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('history',source=source,files=files,capabilities=['program.adopt'])
            app.edit_program('history',1,file='behavior.chaos',source='fn answer(){41};export{answer}')
            self.assertEqual(app.start('history',2,1,mode='source').result(5),[3,41,2])
            self.assertEqual(app.start('history',3,None,mode='source').result(5),40)
            rows=app.program_history('history')['items'];self.assertEqual([row['version']for row in rows],[1,2,3])
            self.assertEqual(rows[-1]['kind'],'adopted');self.assertEqual(rows[0]['definitionHash'],rows[-1]['definitionHash'])
            self.assertEqual(app.get_project('history')['capabilities'],['program.adopt'])

    def test_actual_historical_origin_cannot_replace_current_base(self):
        from chaos import Framework,Settings
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('history',source='40',capabilities=['program.adopt'])
            app.edit_program('history',1,source='let old=await program_revision(1);await program_adopt(old,old,[{input:null,expect:40}])')
            with self.assertRaises(Exception):app.start('history',2,None,mode='source').result(5)
            self.assertEqual(app.get_project('history')['version'],2)
            self.assertEqual(len(app.program_history('history')['items']),2)

    def test_actual_pure_probe_cannot_read_project_history(self):
        from chaos import Framework,Settings
        with Framework(settings=Settings(':memory:')) as app:
            candidate=definition('await program_revision(1)')
            app.create_project('history',source='await program_probe(program_from(input),[{input:null,expect:40}])',capabilities=[])
            report=app.start('history',1,candidate,mode='source').result(5)
            self.assertFalse(report['passed']);self.assertIn('error',report['checks'][0])

    def test_actual_historical_program_uses_current_authority(self):
        from chaos import Framework,Settings,Registry,ToolSpec
        seen=[];registry=Registry();registry.register_tool(ToolSpec('app.effect',lambda v,c:seen.append(v)or v))
        with Framework(settings=Settings(':memory:'),registry=registry) as app:
            app.create_project('history',source='tool("app.effect",42)',capabilities=['app.effect'])
            project=app.get_project('history');project.update(source='let old=await program_revision(1);await old(null)',capabilities=[])
            app.save_project(project,from_view='source')
            with self.assertRaises(Exception):app.start('history',2,None,mode='source').result(5)
            self.assertEqual(seen,[])

    def test_actual_large_snapshot_is_an_owned_callable_after_loading(self):
        from chaos import Framework,Settings
        d=definition('42',{name+'.chaos':'//'+('x'*90000)+'\nnull'for name in ('a','b','c')})
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('large',source=d['files']['main.chaos'],files=d['files'],capabilities=[])
            app.edit_program('large',1,source='let old=await program_revision(1);[type_of(old),await old(null),program_metadata(old).origin.version]')
            self.assertEqual(app.start('large',2,None,mode='source').result(5),['Program',42,1])

if __name__=='__main__':unittest.main()
