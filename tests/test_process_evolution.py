"""A Process returns an owned decision; cleanup, probes and atomic commit follow."""
import json
from dataclasses import replace
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from chaos import Framework
from chaos.language import LanguageError
from chaos.runtime import Context
from chaos.settings import Settings
from tests.test_process_integration import definition,wait_for

NEXT='fn step(c){process_step(c.state+10,c.programVersion,true)};export {step}'


def source(*,before='',after='',result='proposal',expect=42,candidate=NEXT,asynchronous=False,cases=None):
    cases=cases if cases is not None else [{'input':None,'expect':expect}]
    return ('async ' if asynchronous else '')+'fn step(c){'+before+'''
let base=current_program();let d=program_info(base);
let candidate=program_from(set(d,"files",set(d.files,"dot.chaos",'''+json.dumps(candidate)+''')));
let proposal=process_evolve(base,candidate,'''+json.dumps(cases)+''',c.state+1,"evolved",false,"verified next turn");
'''+after+result+'};export {step}'


class ProcessEvolutionTests(unittest.TestCase):
    def app(self,**kwargs):
        app=Framework(settings=Settings(':memory:'),**kwargs);self.addCleanup(app.close);return app

    def create(self,app,code=None,*,caps=None,files=None):
        return app.create_project('p',source='42',files={'dot.chaos':code or source(),**(files or {})},
                                  capabilities=caps if caps is not None else ['process.manage','program.adopt'])

    def start(self,app):
        app.create_process('p',1,'dot',definition(),state=0)
        return wait_for(app,predicate=lambda p:p['turn']==1 or p['status']=='needs_reconciliation')

    def unchanged(self,app,p):
        self.assertEqual(app.get_project('p')['version'],1)
        self.assertEqual((p['state'],p['turn'],p['status']),(0,0,'needs_reconciliation'))
        self.assertNotIn('lastEvolution',p)
        self.assertFalse(any(e['type']=='program.adopted' for e in app.events('p',limit=300)['items']))

    def test_success_and_next_turn_use_new_source_with_same_state_mailbox_and_receipt(self):
        app=self.app();self.create(app);first=self.start(app)
        self.assertEqual((first['state'],first['output'],first['lastVersion']),(1,'evolved',1))
        self.assertEqual(first['lastEvolution']['version'],2)
        self.assertEqual(app.get_run(first['lastRunId'])['output'],{'state':1,'output':'evolved','done':False})
        app.send_process('p','dot',None,message_id='next')
        second=wait_for(app,predicate=lambda p:p['status']=='completed')
        self.assertEqual((second['state'],second['output'],second['lastVersion']),(11,2,2))
        self.assertEqual(second['lastEvolution'],first['lastEvolution'])
        self.assertEqual(app.send_process('p','dot',None,message_id='next')['status'],'consumed')

    def test_info_and_nominal_type_preserve_immutable_snapshots(self):
        code=source(after='var info=process_evolution_info(proposal);info=set(info,"state",999);let checked:ProcessEvolution=proposal;',
                    result='process_step(c.state,[type_of(checked),process_evolution_info(checked),type_of(parse_json(json_stringify(checked)))],true)')
        app=self.app();self.create(app,code);p=self.start(app)
        self.assertEqual(p['output'][0],'ProcessEvolution');self.assertEqual(p['output'][2],'Record')
        info=p['output'][1]
        self.assertEqual(set(info),{'base','definitionHash','casesHash','testCount','state','output','done','reason'})
        self.assertEqual(info['state'],1);self.assertEqual(app.get_project('p')['version'],1)

    def test_constructor_does_not_probe_or_commit_when_decision_discarded(self):
        app=self.app();self.create(app,source(expect=99,result='process_step(7,null,true)'))
        with patch.object(Context,'program_operation',side_effect=AssertionError('discarded proposal was probed')):
            p=self.start(app)
        self.assertEqual(p['state'],7);self.assertEqual(app.get_project('p')['version'],1)

    def test_untrusted_program_base_and_same_candidate_are_rejected(self):
        for replacement in ('let base=program_from(program_info(current_program()));','let candidate=base;'):
            with self.subTest(replacement=replacement):
                app=self.app();code=source()
                if replacement.startswith('let base'):code=code.replace('let base=current_program();',replacement)
                else:
                    begin=code.index('let candidate=');end=code.index('\nlet proposal=',begin)
                    code=code[:begin]+replacement+code[end:]
                self.create(app,code);self.unchanged(app,self.start(app))

    def test_pure_evaluator_and_nested_program_cannot_create_process_authority(self):
        with self.assertRaises(LanguageError) as error:Framework.evaluate('process_evolve(null,null,[],null)')
        self.assertEqual(error.exception.code,'capability_error')
        nested={'kind':'program','entry':'main.chaos','files':{'main.chaos':'process_evolve(null,null,[],null)'},'languageVersions':{'main.chaos':2}}
        app=self.app();code='async fn step(c){await program_run(program_from('+json.dumps(nested)+'),null);process_step(99)};export {step}'
        self.create(app,code);self.unchanged(app,self.start(app))

    def test_forged_nested_and_wrong_type_results_do_not_commit(self):
        for result in ('parse_json(json_stringify(proposal))','{state:proposal,output:null,done:false}','[proposal]','let f:Function=proposal;f()'):
            with self.subTest(result=result):
                app=self.app();self.create(app,source(result=result));self.unchanged(app,self.start(app))

    def test_wrong_cases_or_missing_next_binding_do_not_commit(self):
        for opts in ({'expect':43},{'candidate':'42'},{'candidate':'fn other(c){process_step(42)};export {other}'}):
            with self.subTest(opts=opts):
                app=self.app();self.create(app,source(**opts));self.unchanged(app,self.start(app))

    def test_async_export_can_return_decision(self):
        app=self.app();self.create(app,source(asynchronous=True,before='await sleep(0);'))
        self.assertEqual(self.start(app)['lastEvolution']['version'],2)

    def test_ordinary_or_hard_body_failure_after_decision_has_no_commit(self):
        for after in ('throw "late failure";','while true {null};'):
            with self.subTest(after=after):
                app=self.app();self.create(app,source(after=after));self.unchanged(app,self.start(app))

    def test_all_native_cleanup_finishes_before_probe_and_commit(self):
        entered=threading.Event();release=threading.Event();original=Context.close_services;observed=[]
        app=self.app();self.create(app)
        def cleanup(ctx,outcome):
            original(ctx,outcome);entered.set();release.wait(3)
        original_probe=Context.program_operation
        def probe(ctx,*args,**kwargs):
            observed.append(release.is_set());return original_probe(ctx,*args,**kwargs)
        with patch.object(Context,'close_services',cleanup),patch.object(Context,'program_operation',probe):
            app.create_process('p',1,'dot',definition(),state=0)
            self.assertTrue(entered.wait(2));self.assertEqual(app.get_project('p')['version'],1)
            self.assertEqual(app.get_process('p','dot')['state'],0);release.set()
            p=wait_for(app)
        self.assertEqual(observed,[True]);self.assertEqual(p['state'],1)

    def test_native_cleanup_failure_does_not_even_probe(self):
        app=self.app();self.create(app)
        with patch.object(Context,'close_services',side_effect=LanguageError('native cleanup failed')), \
             patch.object(Context,'program_operation',side_effect=AssertionError('probed after failed cleanup')):
            self.unchanged(app,self.start(app))

    def test_fixture_cases_execute_real_bound_handler_without_network(self):
        agent={'kind':'agent','name':'a','config':{'provider':'model','tools':[]}}
        fixture={'agents':[{'definition':agent,'input':21,'actions':[{'tool':'double','input':21,'expect':42},{'final':42}]}]}
        test='agent a{provider:"model",tools:[]};fn twice(x){x*2};await agent_bind(a,[agent_tool("double",twice)])(input)'
        cases=[{'input':21,'expect':42,'entry':'test.chaos','fixtures':fixture}]
        app=self.app();self.create(app,source(cases=cases),files={'test.chaos':test})
        with patch.object(Settings,'resolve',side_effect=AssertionError('model settings requested')):
            p=self.start(app)
        self.assertEqual(p['state'],1);self.assertEqual(p['lastEvolution']['testCount'],1)

    def test_caught_fixture_violation_still_prevents_adoption(self):
        test='try{tool("text.normalize","undeclared")}catch e{42}'
        cases=[{'input':None,'expect':42,'entry':'test.chaos','fixtures':{}}]
        app=self.app();self.create(app,source(cases=cases),files={'test.chaos':test},
                                   caps=['process.manage','program.adopt','text.normalize'])
        self.unchanged(app,self.start(app))

    def test_expired_deadline_rolls_back_every_success_write(self):
        for phase in ('before','after'):
            with self.subTest(phase=phase):
                app=self.app();self.create(app);prepare=app._engine._prepare_process_evolution;event=app._store._event
                def short_deadline(*args):
                    result=prepare(*args)
                    return replace(result,deadline=time.monotonic()+(.1 if phase=='after' else -1))
                def slow_terminal(project,kind,*args,**kwargs):
                    result=event(project,kind,*args,**kwargs)
                    if kind=='run.completed':time.sleep(.15)
                    return result
                with patch.object(app._engine,'_prepare_process_evolution',short_deadline),patch.object(app._store,'_event',slow_terminal):
                    p=self.start(app)
                self.unchanged(app,p)
                self.assertIn('deadline',p['error'])
                self.assertFalse(any(e['type']=='run.completed' for e in app.events('p',limit=300)['items']))

    def test_source_cas_after_successful_probe_does_not_commit_state(self):
        app=self.app();self.create(app);original=app._engine._prepare_process_evolution
        def prepare(*args):
            result=original(*args);p=app.get_project('p');p['source']='43';p['files']['main.chaos']='43';app.save_project(p)
            return result
        with patch.object(app._engine,'_prepare_process_evolution',prepare):p=self.start(app)
        self.assertEqual(app.get_project('p')['version'],2);self.assertEqual(app.get_project('p')['source'],'43')
        self.assertEqual((p['state'],p['turn'],p['status']),(0,0,'needs_reconciliation'))
        self.assertNotIn('lastEvolution',p)

    def test_atomic_receipt_and_source_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database=Path(directory)/'state.sqlite3'
            with Framework(database,settings=Settings(':memory:')) as app:
                self.create(app);p=self.start(app);receipt=p['lastEvolution']
            with Framework(database,settings=Settings(':memory:')) as app:
                self.assertEqual(app.get_process('p','dot')['lastEvolution'],receipt)
                self.assertEqual(app.get_project('p')['version'],2)


if __name__=='__main__':unittest.main()
