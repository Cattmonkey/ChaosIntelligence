"""Proposal: process_events remains an ordinary cold native read and shared contract."""
import inspect
import json
import threading
import unittest
from unittest.mock import patch

from chaos import Framework, Settings, Workflow
from chaos.ide_language import analyze
from chaos.language import Evaluator, LanguageError, Native
from chaos.modules import STDLIB
from chaos.native_specs import SHAPES, all_specs, spec_for
from chaos.program import format_program, parse_program
from chaos.suspension import Scheduler
from tests.test_process_integration import definition


PAGE = {'items':[], 'nextCursor':None, 'lastEventId':42,
        'historyStartEventId':None, 'legacyHistoryIncluded':False}


class ProcessStreamNative(unittest.TestCase):
    def test_construction_is_cold_await_uses_exact_request_worker_and_json_copy(self):
        main = threading.get_ident()
        calls = []
        shared = {**PAGE, 'items':[{'id':43,'payload':{'nested':[1]}}]}
        def host(operation,payload):
            calls.append((operation,payload,threading.get_ident()))
            return shared
        self.assertEqual(Evaluator(process_handler=host).run('type_of(process_events("alpha",after=42,limit=3))'),'Task')
        self.assertEqual(calls,[])
        result = Evaluator(process_handler=host).run('await process_events("alpha",after=42,limit=3)')
        self.assertEqual([(op,payload) for op,payload,_ in calls],
            [('events',{'key':'alpha','after':42,'limit':3})])
        self.assertNotEqual(calls[0][2],main)
        result['items'][0]['payload']['nested'].append(2)
        self.assertEqual(shared['items'][0]['payload']['nested'],[1])
        calls.clear()
        Evaluator(process_handler=host).run('await process_events("alpha")')
        self.assertEqual(calls[0][:2],('events',{'key':'alpha','after':0,'limit':100}))
        effects = []
        self.assertEqual(Evaluator(effect_handler=lambda *a:effects.append(a)).run('type_of(process_events("alpha"))'),'Task')
        with self.assertRaises(LanguageError):
            Evaluator(effect_handler=lambda *a:effects.append(a),tool_handler=lambda *a:effects.append(a)).run('await process_events("alpha")')
        self.assertEqual(effects,[])

    def test_invalid_native_arguments_fail_before_host_and_results_are_bounded(self):
        calls = []
        expressions = ['process_events("alpha",after=true)', 'process_events("alpha",after=-1)',
                       'process_events("alpha",after=1.0)', 'process_events("alpha",after="1")',
                       'process_events("alpha",limit=true)', 'process_events("alpha",limit=0)',
                       'process_events("alpha",limit=301)', 'process_events("alpha",limit=1.0)',
                       'process_events("bad key")']
        for source in expressions:
            with self.subTest(source=source), self.assertRaises(LanguageError):
                Evaluator(process_handler=lambda *a:calls.append(a)).run(source)
        self.assertEqual(calls,[])
        live = Evaluator().run('fn(x){x}')
        with self.assertRaises(LanguageError):
            Evaluator(process_handler=lambda *a:{**PAGE,'items':[live]}).run('await process_events("alpha")')
        with self.assertRaises(LanguageError):
            Evaluator(process_handler=lambda *a:{**PAGE,'items':[{'payload':'x'*250001}]}).run('await process_events("alpha")')
        small = {**PAGE,'items':[{'payload':'x'*1000}]}
        self.assertEqual(Evaluator(process_handler=lambda *a:small).run('await process_events("alpha")'),small)

    def test_recording_abort_poison_survives_a_user_catch_without_observation(self):
        operations,reads = [],[]
        def recording(operation,payload):
            operations.append(operation)
            if operation=='recording.begin': return {'scopeId':'scope'}
            if operation=='recording.abort': return None
            if operation=='recording.finish': raise LanguageError('recording poisoned')
            raise AssertionError(operation)
        source = ('async fn action(x){try{await process_events("alpha")}catch error{null};42};'
                  'await record_skill(action)')
        with self.assertRaises(LanguageError):
            Evaluator(mode_handler=recording,process_handler=lambda *a:reads.append(a)).run(source)
        self.assertIn('recording.abort',operations)
        self.assertEqual(reads,[])

    def test_exact_shared_native_shape_aliases_macro_code_and_compile_paths(self):
        env = Evaluator().env.values
        native = env['process_events']
        spec = spec_for('process_events')
        actual = list(inspect.signature(native.call).parameters.values())
        self.assertEqual([p.name for p in actual],['key','after','limit'])
        self.assertEqual([p.name for p in spec.parameters],['key','after','limit'])
        self.assertEqual([p.optional for p in spec.parameters],[False,True,True])
        self.assertEqual([p.default_display for p in spec.parameters[1:]],['0','100'])
        self.assertEqual((spec.result_type,spec.awaited_type,spec.awaited_shape),('Task','Record','ProcessEventPage'))
        self.assertEqual({field.name for field in SHAPES['ProcessEventPage']},set(PAGE))
        self.assertEqual(len([value for value in env.values() if type(value) is Native]),335)
        self.assertEqual(len(all_specs()),381)
        self.assertEqual(len(STDLIB),21)
        self.assertEqual(Framework.evaluate('import "std/bot" as B;[B.process_events==process_events,type_of(B.process_events("alpha"))]'),[True,'Task'])
        sources = ['type_of(process_events("alpha",after=42,limit=3))',
            'let f=process_events;type_of(apply(partial(f,"alpha"),[42,3]))',
            'fn caller(f){f("alpha",42,3)};type_of(caller(process_events))',
            'let f=compose(fn(t){t},partial(process_events,"alpha"));type_of(f(42,3))',
            'macro events(f,k){quote{unquote(f)(unquote(k))}};type_of(events!(process_events,"alpha"))',
            'let f=function_from(code_from("fn(k){process_events(k)}"));type_of(f("alpha"))']
        sources += ['let f=compile_function(fn(k){process_events(k)},target='+json.dumps(target)+');type_of(f("alpha"))'
                    for target in ('interpreter','auto','auto-flow')]
        for source in sources:
            with self.subTest(source=source):
                parsed = parse_program(source)
                formatted = format_program(parsed)
                self.assertEqual(parse_program(formatted)['semanticHash'],parsed['semanticHash'])
                self.assertEqual(Framework.evaluate(formatted),'Task')
        self.assertEqual(Framework.evaluate('fn process_events(key)->Int{7};process_events("alpha")'),7)
        prefix = 'import "std/bot" as B;let get=B.process_events;let cold=get("alpha",after=42,limit=3);let transcript=await cold;'
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed native source')), \
             patch.object(Scheduler,'_s_resume',side_effect=AssertionError('analysis resumed cold request')):
            info = analyze(prefix+'transcript.items',position=len(prefix+'transcript.'))
        self.assertEqual(info['diagnostics'],[])
        self.assertEqual({entry['label'] for entry in info['completions']},set(PAGE))
        symbols = {entry['name']:entry['type'] for entry in info['symbols']}
        self.assertEqual((symbols['cold'],symbols['transcript']),('Task','Record'))

    def test_engine_read_returns_early_exact_fields_current_project_and_redaction(self):
        settings = Settings(':memory:')
        secret = 'process-stream-fixture-secret'
        settings.update({'providers':{'text':{'apiKey':secret}}},version=1)
        app = Framework(settings=settings)
        self.addCleanup(app.close)
        app._engine.pause_dispatch(True)
        for project in ('p','q'):
            app.create_project(project,source='42',files={'dot.chaos':'fn step(c){process_step(c.state)};export {step}'},capabilities=['process.manage'])
        app.create_process('p',1,'alpha',definition(),state=0)
        claim = app._store.processes.claim('p','alpha','actual')
        app._store.event('p','bot.progress',{'text':'prefix '+secret,'apiKey':'key-value','authorization':'header-value'},'actual')
        before = app.get_process('p','alpha')
        with patch.object(app._engine,'_ensure_dispatcher',side_effect=AssertionError('observation scheduled dispatcher')), \
             patch.object(app._engine.idle,'notify_all',side_effect=AssertionError('observation notified dispatcher')):
            selected = app._engine.process_operation('p','events',{'key':'alpha','after':0,'limit':100},allowed=set())
            self.assertEqual(app.get_process('p','alpha'),before)
            for payload in ({'key':'alpha','after':0}, {'key':'alpha','after':0,'limit':100,'projectId':'q'}):
                with self.assertRaises(LanguageError):
                    app._engine.process_operation('p','events',payload)
            with self.assertRaises(KeyError):
                app._engine.process_operation('q','events',{'key':'alpha','after':0,'limit':100})
        public = next(row for row in selected['items'] if row['type']=='bot.progress')['payload']
        self.assertEqual(public,{'text':'prefix [redacted]','apiKey':'[redacted]','authorization':'[redacted]'})
        self.assertEqual([row['id'] for row in selected['items']],
                         [row['id'] for row in app.events('p',process_key='alpha')['items']])

    def test_function_node_can_read_without_manage_authority_and_cannot_schedule(self):
        app = Framework(settings=Settings(':memory:'))
        self.addCleanup(app.close)
        app._engine.pause_dispatch(True)
        project = app.create_project('p',source='42',files={'dot.chaos':'fn step(c){process_step(c.state)};export {step}'},capabilities=['process.manage'])
        app.create_process('p',1,'alpha',definition(),state=0)
        graph = Workflow().input().function('observe','await process_events("alpha")').output().connect('input','observe').connect('observe','output')
        project['graph'] = graph.build()
        project['capabilities'] = []
        project = app.save_project(project,from_view='graph')
        result = app.start('p',project['version'],None).result(3)
        self.assertEqual([row['id'] for row in result['items']],
            [row['id'] for row in app.events('p',process_key='alpha')['items']])
        self.assertEqual(app.get_process('p','alpha')['turn'],0)
        project['graph'] = Workflow().input().function('observe','await process_send("alpha",42)').output().connect('input','observe').connect('observe','output').build()
        project = app.save_project(project,from_view='graph')
        with self.assertRaisesRegex(Exception,'Function-node contexts cannot schedule or manage Processes'):
            app.start('p',project['version'],None).result(3)
        self.assertEqual(app.get_process('p','alpha')['turn'],0)


if __name__ == '__main__':
    unittest.main()
