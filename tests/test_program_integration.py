"""Real Program effects share project snapshots, authority and Store CAS."""
import copy
import json
import threading
import unittest
from unittest.mock import patch

from chaos import Framework, Client, Registry, ProviderSpec, ToolSpec, Final
from chaos.extensions import DEFAULT_CAPABILITIES
from chaos.language import LanguageError, ResourceLimit
from chaos.program_values import canonical_hash
from chaos.runtime import Context, Cancelled
from chaos.settings import Settings
from chaos.store import Conflict


def definition(source='input*2', *, files=None, entry='main.chaos', versions=None):
    files={**(files or {}),entry:source}
    return {'kind':'program','entry':entry,'files':files,'languageVersions':versions or {f:2 for f in files}}


class ProgramIntegration(unittest.TestCase):
    def app(self, **kwargs):
        app=Framework(settings=Settings(':memory:'),**kwargs)
        self.addCleanup(app.close)
        return app

    def events(self, app, project):
        result=[];after=0
        while True:
            page=app.events(project,after=after,limit=300);result.extend(page['items'])
            if page['nextCursor'] is None:return result
            after=page['nextCursor']

    def context(self, app, project):
        return Context(app._engine,None,app.get_project(project),threading.Event())

    def test_pure_sdk_schema_source_snapshot_and_client_contract(self):
        app=self.app();app.create_project('sdk',source='input',capabilities=[])
        d=definition();checked=app.validate_program(d,name='saved')
        self.assertEqual(checked['definitionHash'],canonical_hash(d))
        self.assertEqual(Framework.evaluate('import "saved.chaos" as S;program_info(S.saved)',files={'saved.chaos':checked['source']}),d)
        self.assertEqual(checked['summary']['files'],['main.chaos'])
        checked['definition']['files']['main.chaos']='null'
        self.assertEqual(app.validate_program(d)['definition'],d)
        self.assertFalse(app.program_schema()['additionalProperties'])
        snap=app.program_snapshot('sdk');self.assertEqual(snap['version'],1)
        self.assertEqual(snap['definitionHash'],canonical_hash(snap['definition']))
        client=Client('http://127.0.0.1:1')
        with patch.object(client,'_request',return_value={}) as request:
            client.validate_program(d);request.assert_called_with('POST','/programs/validate',{'definition':d,'name':'saved_program'})
            client.program_schema();request.assert_called_with('GET','/programs/schema')
            client.program_snapshot('sdk');request.assert_called_with('GET','/projects/sdk/program-snapshot')
            client.adopt_program('sdk',1,d,expected_hash='h',cases=[{'input':21,'expect':42}])
            request.assert_called_with('POST','/projects/sdk/program-adopt',{'version':1,'definition':d,'expectedHash':'h','cases':[{'input':21,'expect':42}],'reason':''})

    def test_valid_large_definition_does_not_require_exportable_single_module(self):
        app=self.app();d=definition('input',files={'big.chaos':'//'+('x'*99000)+'\nnull'})
        d['files']['other.chaos']='//'+('y'*99000)+'\nnull';d['languageVersions']['other.chaos']=2
        result=app.validate_program(d)
        self.assertIsNone(result['source']);self.assertIn('sourceError',result)
        self.assertEqual(result['definition'],d)
        with self.assertRaises(LanguageError):app.validate_program(d,name='not-a-name')

    def test_large_adoption_returns_compact_language_receipt_after_real_commit(self):
        app=self.app()
        source='''let base=current_program();let d=program_info(base);
let candidate=program_from({kind:"program",entry:"next.chaos",files:d.files,languageVersions:d.languageVersions});
let receipt=await program_adopt(base,candidate,[{input:null,expect:42}]);receipt.version'''
        files={'next.chaos':'42',**{f'comment{i}.chaos':'//'+('x'*99000)+'\nnull' for i in range(3)}}
        app.create_project('large',source=source,files=files,capabilities=['program.adopt'])
        self.assertEqual(app.start('large',1,None,mode='source').result(),2)
        self.assertEqual(app.get_project('large')['entry'],'next.chaos')
        self.assertEqual(app.start('large',2,None,mode='source').result(),42)

    def test_real_provider_proposal_checks_adopts_and_old_run_stays_fixed(self):
        proposed=definition('input*2')
        seen=[];registry=Registry()
        registry.register_provider(ProviderSpec('app.designer',lambda request,ctx:seen.append(request.input) or Final(proposed)))
        app=self.app(registry=registry)
        source='''let base=current_program();
agent designer {provider:"app.designer"};
let candidate=program_from(designer({goal:"double"}));
let report=await program_probe(candidate,[{input:21,expect:42}]);
let accepted=await program_adopt(base,candidate,[{input:21,expect:42}],"verified double");
[report.passed,accepted.version,program_metadata(current_program()).origin.version,
 program_info(current_program()).files["main.chaos"],await candidate(21)]'''
        original=app.create_project('self',source=source,capabilities=['program.adopt'])
        run=app.start('self',1,None,mode='source');out=run.result()
        self.assertEqual(out,[True,2,1,source,42]);self.assertEqual(seen,[{'goal':'double'}])
        saved=app.get_project('self');self.assertEqual(saved['name'],original['name'])
        self.assertEqual(saved['capabilities'],original['capabilities'])
        self.assertEqual(saved['files'],proposed['files']);self.assertEqual(saved['program']['sourceVersions'],proposed['languageVersions'])
        self.assertEqual(app.get_run(run.id)['snapshot']['source'],source)
        self.assertEqual(app.start('self',2,21,mode='source').result(),42)
        events=self.events(app,'self');adopted=[e for e in events if e['type']=='program.adopted']
        self.assertEqual(len(adopted),1);self.assertEqual(adopted[0]['run_id'],run.id)
        self.assertNotIn('files',adopted[0]['payload']);self.assertNotIn('checks',adopted[0]['payload'])
        self.assertTrue(any(e['type']=='graph.saved' and e['payload']['version']==2 for e in events))

    def test_probe_entry_is_pure_and_comparison_is_type_strict(self):
        app=self.app();app.create_project('probe',source='await program_probe(program_from(input),[{entry:"test.chaos",input:null,expect:1}])',capabilities=[])
        d=definition('tool("memory.put",{key:"x",value:1})',files={'test.chaos':'true'})
        report=app.start('probe',1,d,mode='source').result()
        self.assertFalse(report['passed']);self.assertIs(report['checks'][0]['actual'],True)
        ctx=self.context(app,'probe')
        report=ctx.program_operation('probe',{'definition':d,'cases':[{'input':None,'expect':1}]})
        self.assertFalse(report['passed']);self.assertIn('error',report['checks'][0])
        self.assertFalse(any(e['type']=='tool.started' for e in self.events(app,'probe')))

    def test_adoption_requires_explicit_grant_and_marker_is_not_tool(self):
        self.assertNotIn('program.adopt',DEFAULT_CAPABILITIES)
        app=self.app();app.create_project('deny',source='input',capabilities=[])
        snap=app.program_snapshot('deny')
        with self.assertRaises(LanguageError):app.adopt_program('deny',1,definition(),expected_hash=snap['definitionHash'],cases=[{'input':21,'expect':42}])
        app.create_project('tool',source='tool("program.adopt",input)',capabilities=['program.adopt'])
        self.assertEqual(app.start('tool',1,{},mode='source').wait()['status'],'failed')
        seen=[];registry=Registry();registry.register_provider(ProviderSpec('app.worker',lambda r,c:seen.append(r) or Final(42)))
        other=self.app(registry=registry)
        other.create_project('agent',source='agent a{provider:"app.worker",tools:["program.adopt"]};a(null)',capabilities=['program.adopt'])
        self.assertEqual(other.start('agent',1,None,mode='source').wait()['status'],'failed');self.assertEqual(seen,[])

    def test_nested_execution_inherits_authority_and_team_scope(self):
        app=self.app();app.create_project('nested',source='await program_from(input)(null)',capabilities=['memory.put','program.adopt'])
        ctx=self.context(app,'nested');ctx.team_scope={'name':'n','definitionHash':'a'*64,'roleId':'r','rolePath':['r'],'capabilities':[]}
        with self.assertRaises(LanguageError):
            ctx.program_operation('run',{'definition':definition('tool("memory.put",{key:"x",value:1})'),'input':None})
        with self.assertRaises(LanguageError):
            ctx.program_operation('adopt',{'definition':definition(),'cases':[{'input':21,'expect':42}],'base':ctx.program_origin(),'reason':''})
        self.assertEqual(app.get_project('nested')['version'],1)
        self.assertFalse(any(e['type']=='tool.started' for e in self.events(app,'nested')))

    def test_nested_program_cannot_restore_disabled_agent_handler(self):
        calls=[];registry=Registry();registry.register_provider(ProviderSpec('app.worker',lambda r,c:calls.append(r) or Final(42)))
        app=self.app(registry=registry);app.create_project('restricted',source='input',capabilities=[])
        ctx=self.context(app,'restricted')
        with self.assertRaises(LanguageError):
            ctx.evaluator(allow_agents=False).run('await program_from(input)(null)',{'input':definition('agent worker{provider:"app.worker"};worker(null)')})
        self.assertEqual(calls,[])

    def test_snapshot_hash_foreign_origin_stale_and_noop_reject(self):
        app=self.app();app.create_project('cas',source='input',capabilities=['program.adopt'])
        snap=app.program_snapshot('cas');cases=[{'input':21,'expect':42}]
        with self.assertRaises(Conflict):app.adopt_program('cas',1,definition(),expected_hash='0'*64,cases=cases)
        ctx=self.context(app,'cas')
        with self.assertRaises(LanguageError):ctx.program_operation('adopt',{'definition':definition(),'cases':cases,'base':{**ctx.program_origin(),'projectId':'foreign'},'reason':''})
        with self.assertRaises(ValueError):app.adopt_program('cas',1,snap['definition'],expected_hash=snap['definitionHash'],cases=[{'input':21,'expect':21}])
        result=app.adopt_program('cas',1,definition(),expected_hash=snap['definitionHash'],cases=cases)
        self.assertEqual(result['receipt']['version'],2)
        with self.assertRaises(Conflict):app.adopt_program('cas',1,definition('input*3'),expected_hash=snap['definitionHash'],cases=[{'input':14,'expect':42}])
        self.assertEqual(len([e for e in self.events(app,'cas') if e['type']=='program.adopted']),1)

    def test_failed_cases_and_compile_errors_never_save(self):
        app=self.app();app.create_project('fail',source='input',capabilities=['program.adopt']);snap=app.program_snapshot('fail')
        for d,cases in [(definition(),[{'input':2,'expect':5}]),(definition('let ='),[{'input':2,'expect':4}]),
                        (definition(files={'orphan.chaos':'import "missing.chaos" as M;null'}),[{'input':2,'expect':4}])]:
            with self.subTest(d=d):
                with self.assertRaises((LanguageError,ValueError)):
                    app.adopt_program('fail',1,d,expected_hash=snap['definitionHash'],cases=cases)
        self.assertEqual(app.program_snapshot('fail'),snap)
        self.assertFalse(any(e['type']=='program.adopted' for e in self.events(app,'fail')))

    def test_program_depth_invocation_and_shared_probe_budget(self):
        app=self.app();app.create_project('bounds',source='input',capabilities=[])
        ctx=self.context(app,'bounds')
        recursive=definition('await current_program()(input)')
        with self.assertRaises(ResourceLimit):ctx.program_operation('run',{'definition':recursive,'input':None})
        ctx=self.context(app,'bounds');ctx.services['programCalls']=64
        with self.assertRaises(ResourceLimit):ctx.program_operation('run',{'definition':definition(),'input':21})
        ctx=self.context(app,'bounds');ctx.budget.limit=150
        with self.assertRaises(ResourceLimit):ctx.program_operation('probe',{'definition':definition('while true {null}'),'cases':[{'input':None,'expect':None}]})

    def test_recording_program_attempt_poison_cannot_be_caught_and_sealed(self):
        app=self.app()
        source='''let p=program_from(input);let r=await record_skill(async fn(x){try {await p(null)}catch e{null};42});r'''
        app.create_project('record',source=source,capabilities=[])
        failed=app.start('record',1,definition('42'),mode='source').wait()
        self.assertEqual(failed['status'],'failed')
        artifacts=app.modes('record')['items'];self.assertTrue(artifacts)
        self.assertTrue(all(a['status']!='ready' for a in artifacts))
        self.assertFalse(any(e['type']=='program.execution.started' for e in self.events(app,'record')))

    def test_sdk_concurrent_adoptions_have_exactly_one_cas_winner(self):
        app=self.app();app.create_project('race',source='input',capabilities=['program.adopt']);snap=app.program_snapshot('race')
        barrier=threading.Barrier(2);results=[]
        # The contender can reach the transaction only after each operation has
        # captured the same project baseline and actually checked its candidate.
        original_probe=Context.program_operation
        def coordinated(ctx,op,payload,*args):
            barrier.wait(timeout=5)
            return original_probe(ctx,op,payload,*args)
        def work(mult):
            try:results.append(app.adopt_program('race',1,definition('input*'+str(mult)),expected_hash=snap['definitionHash'],cases=[{'input':1,'expect':mult}]))
            except BaseException as error:results.append(error)
        with patch.object(Context,'program_operation',coordinated):
            threads=[threading.Thread(target=work,args=(n,)) for n in (2,3)]
            for thread in threads:thread.start()
            for thread in threads:thread.join(10);self.assertFalse(thread.is_alive())
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        self.assertEqual(sum(isinstance(r,Conflict) for r in results),1)
        self.assertEqual(app.get_project('race')['version'],2)

    def test_cancel_before_commit_and_after_commit_evidence(self):
        app=self.app();app.create_project('cancel',source='input',capabilities=['program.adopt']);ctx=self.context(app,'cancel')
        payload={'definition':definition(),'cases':[{'input':21,'expect':42}],'base':ctx.program_origin(),'reason':''}
        original=app._store.adopt_program
        def before(*args,**kwargs):ctx.cancel.set();return original(*args,**kwargs)
        with patch.object(app._store,'adopt_program',before):
            with self.assertRaises(Cancelled):ctx.program_operation('adopt',payload)
        self.assertEqual(app.get_project('cancel')['version'],1)
        ctx=self.context(app,'cancel')
        def after(*args,**kwargs):
            result=original(*args,**kwargs);ctx.cancel.set();return result
        with patch.object(app._store,'adopt_program',after):result=ctx.program_operation('adopt',payload)
        self.assertEqual(result['receipt']['version'],2);self.assertTrue(ctx.cancel.is_set())
        self.assertEqual(len([e for e in self.events(app,'cancel') if e['type']=='program.adopted']),1)

    def test_atomic_event_failure_rolls_back_source_and_graph_saved(self):
        app=self.app();app.create_project('atomic',source='input',capabilities=['program.adopt']);snap=app.program_snapshot('atomic')
        original=app._store._event
        def event(project,kind,*args):
            if kind=='program.adopted':raise RuntimeError('event insert failure')
            return original(project,kind,*args)
        with patch.object(app._store,'_event',event):
            with self.assertRaisesRegex(RuntimeError,'event insert'):
                app.adopt_program('atomic',1,definition(),expected_hash=snap['definitionHash'],cases=[{'input':21,'expect':42}])
        self.assertEqual(app.program_snapshot('atomic'),snap)
        self.assertFalse(any(e['type']=='graph.saved' for e in self.events(app,'atomic')))

    def test_framework_close_tracks_inflight_adoption(self):
        app=self.app();app.create_project('close',source='input',capabilities=['program.adopt']);snap=app.program_snapshot('close')
        entered=threading.Event();release=threading.Event();errors=[];original=Context.program_operation
        def wait(ctx,*args):
            entered.set();release.wait(5);return original(ctx,*args)
        def adopt():
            try:app.adopt_program('close',1,definition(),expected_hash=snap['definitionHash'],cases=[{'input':21,'expect':42}])
            except BaseException as error:errors.append(error)
        with patch.object(Context,'program_operation',wait):
            thread=threading.Thread(target=adopt);thread.start();self.assertTrue(entered.wait(5))
            try:
                with self.assertRaises(TimeoutError):app.close(timeout=.01)
                self.assertEqual(app._store.project('close')['version'],1)
            finally:release.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertTrue(any(isinstance(e,Cancelled) for e in errors));self.assertEqual(app._engine.operations,{})
        app.close()

    def test_nested_program_real_tool_cancellation_drains_before_terminal(self):
        entered=threading.Event();finished=threading.Event();registry=Registry()
        def wait(value,context):
            entered.set()
            try:
                while True:context.checkpoint();threading.Event().wait(.005)
            finally:finished.set()
        registry.register_tool(ToolSpec('app.wait',wait,effect='read'))
        app=self.app(registry=registry)
        app.create_project('drain',source='await program_from(input)(null)',capabilities=['app.wait'])
        run=app.start('drain',1,definition('tool("app.wait",null)'),mode='source')
        self.assertTrue(entered.wait(5));run.cancel();self.assertEqual(run.wait()['status'],'cancelled')
        self.assertTrue(finished.is_set())
        self.assertTrue(any(e['type']=='program.execution.failed' for e in self.events(app,'drain')))

    def test_adoption_preserves_exact_dialects_and_child_traces_are_namespaced(self):
        app=self.app();app.create_project('dialect',source='input',capabilities=['program.adopt'])
        d=definition('import "legacy.chaos" as L;L.answer',files={'legacy.chaos':'let answer=[42][0];export{answer}'},
                     versions={'main.chaos':2,'legacy.chaos':1})
        snap=app.program_snapshot('dialect')
        saved=app.adopt_program('dialect',1,d,expected_hash=snap['definitionHash'],cases=[{'input':None,'expect':42}])['project']
        self.assertEqual(saved['program']['sourceVersions'],d['languageVersions'])
        self.assertEqual(app.program_snapshot('dialect')['definition'],d)
        self.assertEqual(app.program('dialect',file='legacy.chaos')['program']['languageVersion'],1)
        app.create_project('trace',source='await program_from(input)(21)',capabilities=[])
        run=app.start('trace',1,definition(),mode='source',trace=True);self.assertEqual(run.result(),42)
        events=self.events(app,'trace')
        children=[e for e in events if e['type']=='program.child.trace']
        self.assertTrue(children);self.assertTrue(all(e['payload'].get('callId') for e in children))
        self.assertFalse(any(e['type']=='program.step' and e['payload'].get('callId') for e in events))
