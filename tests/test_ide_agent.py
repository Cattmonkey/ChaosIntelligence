import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from chaos import Framework
from chaos.background import BackgroundManager
from chaos.ide_agent import IDEAgentService, LANGUAGE_REFERENCE_TOPICS
from chaos.settings import Settings
from chaos.store import Conflict


class FakeClient:
    def __init__(self, owner, config, script):
        self.owner, self.config, self.script = owner, config, iter(script)
        self.chat = NS(completions=self); self.closed = False
    def create(self, **kwargs):
        self.owner.requests.append(kwargs)
        action = next(self.script)
        if callable(action): action = action(kwargs)
        if isinstance(action, str): message = NS(content=action, tool_calls=[])
        else:
            aliases = {t['function']['description'].split(':', 1)[0]: t['function']['name'] for t in kwargs['tools']}
            calls = [NS(id='call'+str(i), function=NS(name=aliases[name], arguments=json.dumps(value)))
                     for i, (name, value) in enumerate(action)]
            message = NS(content=None, tool_calls=calls)
        return NS(choices=[NS(message=message)])
    def close(self): self.closed = True


class IDEAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.scripts, self.clients, self.requests = [], [], []
        def factory(config):
            client = FakeClient(self, config, self.scripts.pop(0)); self.clients.append(client); return client
        self.settings = Settings(':memory:', client_factory=factory)
        self.settings.update({'providers': {'text': {'baseURL': 'https://model.invalid/v1', 'apiKey': 'IDE_fixture_secret'}},
                              'runtime': {'timeout': 5}}, 1)
        self.app = Framework(settings=self.settings)
        self.project = self.app.create_project('project', source='input')
        self.service = IDEAgentService(self.app, self.root/'state')
    def tearDown(self):
        self.service.close(); self.app.close(); self.temp.cleanup()
    def wait(self, job):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            result = self.service.get('project', job['id'])
            if result['status'] not in ('queued', 'running'): return result
            time.sleep(.01)
        self.fail('IDE task did not terminate')
    def start(self, script, prompt='Improve this program', session=None):
        self.scripts.append(script)
        return self.service.start('project', self.app.get_project('project')['version'], prompt, session)

    def test_language_reference_tool_uses_installed_versioned_docs(self):
        def check_reference(request):
            messages=request['messages']
            tool_messages=[m for m in messages if m.get('role')=='tool']
            evidence=json.loads(tool_messages[-1]['content'])
            self.assertEqual(evidence['topic'],'effects')
            self.assertIn('async fn',evidence['markdown'])
            self.assertIn('fn*',evidence['markdown'])
            return [('draft.write',{'path':'main.chaos','source':'async fn answer(){42};await answer()'})]
        job=self.start([[('language.reference',{'topic':'effects'})],check_reference,[('draft.validate',{}),('draft.evaluate',{})],'Used current language reference.'])
        result=self.wait(job)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['checks'][0]['output'],42)
        self.assertTrue(result['validation']['ok'])

    def test_all_declared_reference_topics_reach_installed_docs_through_harness(self):
        from chaos.documentation import Documentation
        from chaos import __version__
        pages = {page['id']: page for page in Documentation().snapshot()['pages']}
        self.assertEqual(len(LANGUAGE_REFERENCE_TOPICS), len(set(LANGUAGE_REFERENCE_TOPICS)))
        self.assertEqual(LANGUAGE_REFERENCE_TOPICS, tuple(entry['id'] for entry in Documentation().entries()))
        self.assertTrue({'contracts','hash-maps','generic-functions','iterable-composition'}.issubset(LANGUAGE_REFERENCE_TOPICS))
        self.assertTrue({'channels', 'waits', 'select', 'iterable-composition',
                         'comprehensions', 'pattern-bindings', 'language-roadmap',
                         'semantics'}.issubset(LANGUAGE_REFERENCE_TOPICS))
        for topic in LANGUAGE_REFERENCE_TOPICS:
            with self.subTest(topic=topic):
                def check_reference(request):
                    declaration = next(tool['function'] for tool in request['tools']
                                       if tool['function']['description'].startswith('language.reference:'))
                    self.assertEqual(declaration['parameters']['properties']['topic']['enum'],
                                     list(LANGUAGE_REFERENCE_TOPICS))
                    messages = [message for message in request['messages'] if message.get('role') == 'tool']
                    self.assertEqual(len(messages), 1)
                    evidence = json.loads(messages[0]['content'])
                    self.assertEqual(evidence, {'topic': topic, 'version': __version__,
                                                'markdown': pages[topic]['markdown']})
                    return 'Read the installed guide.'
                result = self.wait(self.start([[('language.reference', {'topic': topic})], check_reference]))
                self.assertEqual(result['status'], 'completed', result.get('error'))
                self.assertEqual(result['changes'], [])
        self.assertEqual(self.app.get_project('project')['version'], 1)
        self.assertTrue(all(client.closed for client in self.clients))

    def test_reference_rejects_unknown_and_path_topics_without_reading_docs(self):
        def check_rejections(request):
            messages = [message for message in request['messages'] if message.get('role') == 'tool']
            self.assertEqual(len(messages), 3)
            for message in messages:
                evidence = json.loads(message['content'])
                self.assertEqual(evidence['error']['type'], 'ValueError')
                self.assertIn('Unknown language documentation topic', evidence['error']['message'])
            return 'Unknown topics were rejected.'
        with patch('chaos.documentation.Documentation.page', side_effect=AssertionError('No document read allowed')):
            result = self.wait(self.start([[
                ('language.reference', {'topic': value})
                for value in ('not-a-guide', '../settings', 'function-composition')
            ], check_rejections]))
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual(result['changes'], [])
        self.assertEqual(self.app.get_project('project')['version'], 1)

    def test_agent_definition_reference_guides_pure_draft(self):
        source='let a=agent_from({kind:"agent",name:"role",config:{provider:"model"}});[type_of(a),agent_info(a).name]'
        def use_reference(request):
            messages=[m for m in request['messages'] if m.get('role')=='tool']
            reference=json.loads(messages[-1]['content'])
            self.assertEqual(reference['topic'],'agent-values')
            self.assertIn('agent_source',reference['markdown'])
            return [('draft.write',{'path':'main.chaos','source':source})]
        job=self.start([[('language.reference',{'topic':'agent-values'})],use_reference,
            [('draft.validate',{}),('draft.evaluate',{})],'Constructed a definition without effects.'])
        result=self.wait(job)
        self.assertEqual(result['status'],'completed')
        self.assertTrue(result['validation']['ok'])
        self.assertEqual(result['checks'][0]['output'],['Function','role'])
        self.assertEqual(self.app.get_project('project')['source'],'input')

    def test_team_reference_guides_pure_draft(self):
        source='let t=team_from({kind:"team",name:"example",entry:"worker",roles:[{id:"worker",parent:null,capabilities:[],agent:{kind:"agent",name:"worker",config:{provider:"model"}}}]});[type_of(t),type_of(team_role(t,"worker"))]'
        def use_reference(request):
            messages=[m for m in request['messages'] if m.get('role')=='tool']
            reference=json.loads(messages[-1]['content'])
            self.assertEqual(reference['topic'],'teams')
            self.assertIn('team_bindings',reference['markdown'])
            return [('draft.write',{'path':'main.chaos','source':source})]
        job=self.start([[('language.reference',{'topic':'teams'})],use_reference,
            [('draft.validate',{}),('draft.evaluate',{})],'Constructed language Team without effects.'])
        result=self.wait(job)
        self.assertEqual(result['status'],'completed')
        self.assertTrue(result['validation']['ok'])
        self.assertEqual(result['checks'][0]['output'],['Team','Function'])
        self.assertEqual(self.app.get_project('project')['source'],'input')

    def test_program_history_reference_guides_cold_revision_draft(self):
        source='[type_of(program_history()),type_of(program_revision(1))]'
        def use_reference(request):
            messages=[m for m in request['messages'] if m.get('role')=='tool']
            reference=json.loads(messages[-1]['content'])
            self.assertEqual(reference['topic'],'program-history')
            self.assertIn('firstAvailableVersion',reference['markdown'])
            self.assertIn('program_revision',reference['markdown'])
            return [('draft.write',{'path':'main.chaos','source':source})]
        job=self.start([[('language.reference',{'topic':'program-history'})],use_reference,
            [('draft.validate',{}),('draft.evaluate',{})],'Constructed cold history Tasks; no database history was read.'])
        result=self.wait(job)
        self.assertEqual(result['status'],'completed',result.get('error'))
        self.assertEqual(result['checks'][0]['output'],['Task','Task'])
        self.assertEqual(self.app.get_project('project')['source'],'input')

    def test_process_reference_guides_pure_persistent_definition(self):
        source='let p=process_from({kind:"process",name:"dot",module:"dot.chaos",export:"step",intervalSeconds:30,maxTurns:null});[type_of(p),process_info(p).module,type_of(p("dot",{}))]'
        def use_reference(request):
            messages=[m for m in request['messages'] if m.get('role')=='tool']
            reference=json.loads(messages[-1]['content'])
            self.assertEqual(reference['topic'],'processes')
            self.assertIn('needs_reconciliation',reference['markdown'])
            return [('draft.write',{'path':'main.chaos','source':source})]
        job=self.start([[('language.reference',{'topic':'processes'})],use_reference,
            [('draft.validate',{}),('draft.evaluate',{})],'Defined a cold persistent Process without starting it.'])
        result=self.wait(job)
        self.assertEqual(result['status'],'completed',result.get('error'))
        self.assertTrue(result['validation']['ok'])
        self.assertEqual(result['checks'][0]['output'],['Process','dot.chaos','Task'])
        self.assertEqual(self.app.get_project('project')['source'],'input')

    def test_session_reference_guides_cold_native_role(self):
        source='agent worker {provider:"model",tools:[]};[type_of(session_open("chat",worker)),type_of(session_status("chat"))]'
        def use_reference(request):
            messages=[m for m in request['messages'] if m.get('role')=='tool']
            reference=json.loads(messages[-1]['content'])
            self.assertEqual(reference['topic'],'sessions');self.assertIn('session_compact',reference['markdown'])
            return [('draft.write',{'path':'main.chaos','source':source})]
        result=self.wait(self.start([[('language.reference',{'topic':'sessions'})],use_reference,[('draft.validate',{}),('draft.evaluate',{})],'Created cold native Session operations.']))
        self.assertEqual(result['status'],'completed',result.get('error'));self.assertTrue(result['validation']['ok'])
        self.assertEqual(result['checks'][0]['output'],['Task','Task']);self.assertEqual(self.app.get_project('project')['source'],'input')

    def test_computer_reference_guides_cold_language_resources(self):
        source='[type_of(computer_open()),type_of(computer_open("https://example.com"))]'
        def use_reference(request):
            messages=[m for m in request['messages'] if m.get('role')=='tool']
            reference=json.loads(messages[-1]['content'])
            self.assertEqual(reference['topic'],'computers');self.assertIn('observation_input',reference['markdown'])
            return [('draft.write',{'path':'main.chaos','source':source})]
        result=self.wait(self.start([[('language.reference',{'topic':'computers'})],use_reference,[('draft.validate',{}),('draft.evaluate',{})],'Created cold Computer tasks.']))
        self.assertEqual(result['status'],'completed',result.get('error'));self.assertTrue(result['validation']['ok'])
        self.assertEqual(result['checks'][0]['output'],['Task','Task']);self.assertEqual(self.app.get_project('project')['source'],'input')

    def test_program_reference_guides_pure_self_reflection(self):
        source='let p=current_program();[type_of(p),program_info(p).entry,program_metadata(p).origin]'
        def use_reference(request):
            messages=[m for m in request['messages'] if m.get('role')=='tool']
            reference=json.loads(messages[-1]['content'])
            self.assertEqual(reference['topic'],'self-programming')
            self.assertIn('program_adopt',reference['markdown'])
            return [('draft.write',{'path':'main.chaos','source':source})]
        job=self.start([[('language.reference',{'topic':'self-programming'})],use_reference,
            [('draft.validate',{}),('draft.evaluate',{})],'Reflected an actual Program without effects.'])
        result=self.wait(job)
        self.assertEqual(result['status'],'completed',result.get('error'))
        self.assertTrue(result['validation']['ok'])
        self.assertEqual(result['checks'][0]['output'],['Program','main.chaos',None])
        self.assertEqual(self.app.get_project('project')['source'],'input')

    def test_object_reference_guides_real_draft_tools(self):
        source='class Adder{var n:Int=40;fn __call__(self,x){self.n+x}};Adder()(2)'
        def use_reference(request):
            messages=[m for m in request['messages'] if m.get('role')=='tool']
            reference=json.loads(messages[-1]['content'])
            self.assertEqual(reference['topic'],'objects')
            self.assertIn('__call__',reference['markdown'])
            self.assertIn('C3',reference['markdown'])
            return [('draft.write',{'path':'main.chaos','source':source})]
        job=self.start([[('language.reference',{'topic':'objects'})],use_reference,
            [('draft.validate',{}),('draft.evaluate',{})],'Implemented callable component.'])
        result=self.wait(job)
        self.assertEqual(result['status'],'completed')
        self.assertTrue(result['validation']['ok'])
        self.assertEqual(result['checks'][0]['output'],42)
        self.assertEqual(self.app.get_project('project')['source'],'input')

    def test_decorator_reference_guides_validated_draft(self):
        source='fn increment(f){fn(x){f(x)+1}};\n@increment\nfn answer(x){x};answer(41)'
        def use_reference(request):
            messages=[m for m in request['messages'] if m.get('role')=='tool']
            reference=json.loads(messages[-1]['content'])
            self.assertEqual(reference['topic'],'decorators')
            self.assertIn('staticmethod',reference['markdown'])
            self.assertIn('property',reference['markdown'])
            return [('draft.write',{'path':'main.chaos','source':source})]
        job=self.start([[('language.reference',{'topic':'decorators'})],use_reference,
            [('draft.validate',{}),('draft.evaluate',{})],'Implemented decorator.'])
        result=self.wait(job)
        self.assertEqual(result['status'],'completed')
        self.assertTrue(result['validation']['ok'])
        self.assertEqual(result['checks'][0]['output'],42)
        self.assertEqual(self.app.get_project('project')['source'],'input')

    def test_indexing_reference_guides_validated_draft(self):
        source='class C{fn __getitem__(self,key){[40,41,42][key]}};C()[::2]'
        def use_reference(request):
            messages=[m for m in request['messages'] if m.get('role')=='tool']
            reference=json.loads(messages[-1]['content'])
            self.assertEqual(reference['topic'],'indexing')
            self.assertIn('__getitem__',reference['markdown'])
            self.assertIn('slice_key',reference['markdown'])
            return [('draft.write',{'path':'main.chaos','source':source})]
        result=self.wait(self.start([[('language.reference',{'topic':'indexing'})],use_reference,
            [('draft.validate',{}),('draft.evaluate',{})],'Indexing draft validated.']))
        self.assertEqual(result['status'],'completed')
        self.assertTrue(result['validation']['ok'])
        self.assertEqual(result['checks'][0]['output'],[40,42])
        self.assertEqual(self.app.get_project('project')['source'],'input')

    def test_iterator_reference_guides_validated_draft(self):
        source='class C{fn* __iter__(self){yield 40;yield 42}};collect(C())'
        def use_reference(request):
            messages=[m for m in request['messages'] if m.get('role')=='tool']
            reference=json.loads(messages[-1]['content'])
            self.assertEqual(reference['topic'],'iterators')
            self.assertIn('__anext__',reference['markdown'])
            self.assertIn('done',reference['markdown'])
            return [('draft.write',{'path':'main.chaos','source':source})]
        result=self.wait(self.start([[('language.reference',{'topic':'iterators'})],use_reference,
            [('draft.validate',{}),('draft.evaluate',{})],'Iterator draft validated.']))
        self.assertEqual(result['status'],'completed')
        self.assertTrue(result['validation']['ok'])
        self.assertEqual(result['checks'][0]['output'],[40,42])
        self.assertEqual(self.app.get_project('project')['source'],'input')

    def test_operator_reference_guides_validated_draft(self):
        source='class Amount{fn __add__(self,x:Int)->Int{x+40}};Amount()+2'
        def use_reference(request):
            messages=request['messages']
            reference=json.loads(messages[-1]['content'])
            self.assertEqual(reference['topic'],'operators')
            self.assertIn('__radd__',reference['markdown'])
            self.assertIn('NotImplemented',reference['markdown'])
            return [('draft.write',{'path':'main.chaos','source':source})]
        job=self.start([[('language.reference',{'topic':'operators'})],use_reference,
                       [('draft.validate',{}),('draft.evaluate',{})],'Operator draft validated.'])
        result=self.wait(job)
        self.assertEqual(result['status'],'completed')
        self.assertTrue(result['validation']['ok'])
        self.assertEqual(result['checks'][0]['output'],42)
        self.assertEqual(self.app.get_project(self.project['id'])['source'],self.project['source'])

    def test_user_descriptor_reference_guides_validated_draft(self):
        source='class D{fn __get__(self,object,owner)->Int{42}};class C{let x=D()};let c=C();c.x=40;delattr(c,"x");c.x'
        def use_reference(request):
            messages=[m for m in request['messages'] if m.get('role')=='tool']
            reference=json.loads(messages[-1]['content'])
            self.assertEqual(reference['topic'],'descriptor-protocol')
            self.assertIn('__set_name__',reference['markdown'])
            self.assertIn('delattr',reference['markdown'])
            return [('draft.write',{'path':'main.chaos','source':source})]
        job=self.start([[('language.reference',{'topic':'descriptor-protocol'})],use_reference,
            [('draft.validate',{}),('draft.evaluate',{})],'Implemented descriptor protocol.'])
        result=self.wait(job)
        self.assertEqual(result['status'],'completed')
        self.assertTrue(result['validation']['ok'])
        self.assertEqual(result['checks'][0]['output'],42)
        self.assertEqual(self.app.get_project('project')['source'],'input')

    def test_real_tool_proposal_validate_evaluate_apply_and_ir(self):
        job = self.start([[('draft.read', {'path': 'main.chaos'})],
            [('draft.write', {'path': 'main.chaos', 'source': 'fn twice(x){x*2};twice(input)'})],
            [('draft.validate', {}), ('draft.evaluate', {'input': 21})], 'Implemented and tested.'])
        result = self.wait(job)
        self.assertEqual(result['status'], 'completed'); self.assertTrue(result['validation']['ok'])
        self.assertEqual(result['checks'][0]['output'], 42)
        self.assertEqual(self.app.get_project('project')['source'], 'input')
        self.assertEqual(self.clients[0].config['baseURL'], 'https://model.invalid/v1')
        self.assertEqual(self.clients[0].config['apiKey'], 'IDE_fixture_secret')
        self.assertEqual(self.clients[0].config['backend'], 'native')
        saved = self.service.apply('project', job['id'], 1)
        self.assertEqual(saved['version'], 2); self.assertEqual(self.app.start('project', 2, 21).result(), 42)
        self.assertEqual(self.app.program('project')['version'], 2)
        self.assertEqual(self.service.apply('project', job['id'], 1)['version'], 2)
        self.assertTrue(self.clients[0].closed)

    def test_answer_code_fences_never_become_edits(self):
        result = self.wait(self.start(['```chaos\n42\n```']))
        self.assertEqual(result['changes'], [])
        with self.assertRaises(ValueError): self.service.apply('project', result['id'], 1)
        self.assertEqual(self.app.get_project('project')['source'], 'input')

    def test_invalid_proposal_remains_inspectable_but_cannot_apply(self):
        result = self.wait(self.start([[('draft.write', {'path': 'main.chaos', 'source': 'fn ('})], 'Draft needs repair']))
        self.assertEqual(result['status'], 'completed'); self.assertFalse(result['validation']['ok'])
        self.assertEqual(result['changes'][0]['after'], 'fn (')
        with self.assertRaises(ValueError): self.service.apply('project', result['id'], 1)

    def test_stale_version_rejects_start_and_apply_without_overwrite(self):
        result = self.wait(self.start([[('draft.write', {'path': 'main.chaos', 'source': '42'})], 'done']))
        self.app.edit_program('project', 1, source='99')
        with self.assertRaises(Conflict): self.service.start('project', 1, 'stale')
        with self.assertRaises(Conflict): self.service.apply('project', result['id'], 1)
        self.assertEqual(self.app.get_project('project')['source'], '99')

    def test_session_history_is_persistent_and_project_bound(self):
        first = self.wait(self.start(['First response'], prompt='First request'))
        second = self.wait(self.start(['Second response'], prompt='Second request', session=first['sessionId']))
        context = json.loads(self.requests[-1]['messages'][1]['content'])
        self.assertEqual(context['history'], [{'user': 'First request', 'assistant': 'First response'}])
        self.app.create_project('other', source='0')
        for operation in [lambda: self.service.get('other', first['id']),
                          lambda: self.service.history('other', first['sessionId']),
                          lambda: self.service.cancel('other', first['id']),
                          lambda: self.service.events('other', first['id']),
                          lambda: self.service.apply('other', first['id'], 1),
                          lambda: self.service.start('other', 1, 'bad', first['sessionId'])]:
            with self.assertRaises(KeyError): operation()
        self.assertTrue(self.service.close())
        self.service = IDEAgentService(self.app, self.root/'state')
        history = self.service.history('project', first['sessionId'])
        self.assertEqual([t['reply'] for t in history], ['First response', 'Second response'])
        self.assertEqual(second['sessionId'], first['sessionId'])

    def test_followup_inherits_unapplied_proposal_but_keeps_original_cas_base(self):
        first = self.wait(self.start([[('draft.write', {'path': 'main.chaos', 'source': '41'})], 'first draft']))
        def inspect_then_edit(kwargs):
            observation = json.loads([m for m in kwargs['messages'] if m['role'] == 'tool'][-1]['content'])
            self.assertEqual(observation['source'], '41')
            return [('draft.write', {'path': 'main.chaos', 'source': '42'})]
        second = self.wait(self.start([[('draft.read', {'path': 'main.chaos'})], inspect_then_edit, 'refined'], session=first['sessionId']))
        self.assertEqual(second['baseVersion'], 1)
        self.assertEqual(second['changes'], [{'path': 'main.chaos', 'before': 'input', 'after': '42'}])
        self.assertEqual(self.service.apply('project', second['id'], 1)['version'], 2)

    def test_slow_worker_close_automatically_reaps_service_database(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set(); release.wait(4); args[3](); return None
        self.service.manager.worker = slow
        self.service.start('project', 1, 'slow')
        self.assertTrue(entered.wait(1)); self.assertFalse(self.service.close())
        self.assertFalse(self.service._closed); release.set()
        deadline = time.monotonic() + 2
        while not self.service._closed and time.monotonic() < deadline: time.sleep(.01)
        self.assertTrue(self.service._closed)
        with self.assertRaises(sqlite3.ProgrammingError): self.service._db.execute('SELECT 1')

    def test_failed_background_constructor_closes_new_service_database(self):
        connections = []; connect = sqlite3.connect
        def tracked(*args, **kwargs):
            connection = connect(*args, **kwargs); connections.append(connection); return connection
        with patch('chaos.ide_agent.sqlite3.connect', side_effect=tracked), \
             patch('chaos.ide_agent.BackgroundManager', side_effect=SystemExit('fixture startup failure')):
            with self.assertRaises(SystemExit): IDEAgentService(self.app, self.root/'failed')
        self.assertEqual(len(connections), 1)
        with self.assertRaises(sqlite3.ProgrammingError): connections[0].execute('SELECT 1')

    def test_cancelled_turn_does_not_erase_last_completed_proposal(self):
        first = self.wait(self.start([[('draft.write', {'path': 'main.chaos', 'source': '41'})], 'candidate']))
        self.service.manager.pause_dispatch(True)
        cancelled = self.service.start('project', 1, 'cancel this followup', first['sessionId'])
        self.service.cancel('project', cancelled['id']); self.service.manager.pause_dispatch(False)
        final = self.wait(self.start(['Keep that draft'], session=first['sessionId']))
        self.assertEqual(final['changes'], [{'path': 'main.chaos', 'before': 'input', 'after': '41'}])

    def test_async_cancel_discards_late_calls_and_active_session_conflicts(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(kwargs):
            entered.set(); release.wait(3)
            return [('draft.write', {'path': 'main.chaos', 'source': '42'})]
        started = time.monotonic(); job = self.start([delayed, 'done'])
        self.assertLess(time.monotonic()-started, .5); self.assertTrue(entered.wait(1))
        with self.assertRaises(Conflict): self.service.start('project', 1, 'concurrent', job['sessionId'])
        self.service.cancel('project', job['id']); release.set()
        result = self.wait(job); self.assertEqual(result['status'], 'cancelled'); self.assertEqual(result['changes'], [])
        with self.assertRaises(ValueError): self.service.apply('project', job['id'], 1)

    def test_native_plugin_switch_is_enforced_even_when_runtime_codex(self):
        current = self.settings.public()
        self.settings.update({'runtime': {'backend': 'codex'}}, current['version'])
        result = self.wait(self.start(['native works']))
        self.assertEqual(result['reply'], 'native works'); self.assertEqual(self.clients[0].config['backend'], 'native')
        current = self.settings.public()
        self.settings.update({'plugins': [{'id': 'native', 'enabled': False}]}, current['version'])
        with self.assertRaises(RuntimeError): self.service.start('project', 1, 'disabled')

    def test_paths_are_validated_and_model_can_repair_tool_failure(self):
        result = self.wait(self.start([[('draft.write', {'path': '../outside.chaos', 'source': '42'})],
            [('draft.write', {'path': 'main.chaos', 'source': '42'})], 'repaired']))
        self.assertTrue(result['validation']['ok']); self.assertEqual(len(result['changes']), 1)
        observations = [m for r in self.requests for m in r['messages'] if m['role'] == 'tool']
        self.assertTrue(any('error' in m['content'] for m in observations))
        self.assertFalse((self.root/'outside.chaos').exists())

    def test_credentials_not_written_to_proposals_reply_or_event_storage(self):
        result = self.wait(self.start([[('draft.write', {'path': 'main.chaos', 'source': '"IDE_fixture_secret"'})],
            'prefix ' + 'x'*990 + 'IDE_fixture_secret'], prompt='Do not reveal IDE_fixture_secret'))
        self.assertEqual(result['changes'], []); self.assertNotIn('IDE_fixture_secret', json.dumps(result))
        self.assertNotIn('IDE_fixture_secret', json.dumps(self.service.events('project', result['id'])))
        self.service.close()
        for path in (self.root/'state').glob('*.sqlite3'):
            self.assertNotIn(b'IDE_fixture_secret', path.read_bytes())

    def test_pure_evaluation_budget_and_effect_boundary(self):
        result = self.wait(self.start([[('draft.write', {'path': 'main.chaos', 'source': 'while true {}'})],
            [('draft.evaluate', {'steps': 100})],
            [('draft.write', {'path': 'main.chaos', 'source': 'tool("memory.put",{key:"x",value:1})'})],
            [('draft.evaluate', {})], 'tests failed as expected']))
        self.assertFalse(result['checks'][0]['ok']); self.assertEqual(result['checks'][0]['code'], 'resource_limit')
        self.assertFalse(result['checks'][1]['ok'])

    def test_multifile_directory_move_and_entry_change(self):
        result = self.wait(self.start([
            [('draft.write', {'path': 'lib/math.chaos', 'source': 'let answer=42;export{answer}'})],
            [('draft.move', {'path': 'lib', 'destination': 'src'})],
            [('draft.write', {'path': 'main.chaos', 'source': 'import "src/math.chaos" as M;M.answer'})],
            [('draft.evaluate', {})], 'added module']))
        self.assertTrue(result['validation']['ok']); self.assertEqual(result['checks'][0]['output'], 42)
        saved = self.service.apply('project', result['id'], 1)
        self.assertIn('src/math.chaos', saved['files']); self.assertNotIn('lib/math.chaos', saved['files'])


class BackgroundCloseTests(unittest.TestCase):
    def test_terminal_event_observer_is_part_of_worker_lifecycle(self):
        with tempfile.TemporaryDirectory() as folder:
            entered, release = threading.Event(), threading.Event()
            def observer(event):
                if event['type'] == 'task.completed': entered.set(); release.wait(3)
            manager = BackgroundManager(Path(folder)/'jobs.sqlite3', lambda *args: 'done', event=observer)
            job = manager.start('task', iterations=1)
            self.assertTrue(entered.wait(1))
            # close may wait for the event's condition lock, so call it on a
            # separate thread while the final observer is deliberately held.
            close_request = threading.Thread(target=manager.close)
            close_request.start(); time.sleep(.03)
            self.assertFalse(manager._db_closed); release.set(); close_request.join(1)
            self.assertTrue(manager.close(wait=True))
            with closing(sqlite3.connect(Path(folder)/'jobs.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM background_events WHERE task_id=? AND type=?',
                                            (job['id'], 'task.completed')).fetchone()[0], 1)

    def test_close_waits_for_worker_finalizer_then_closes_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            entered = threading.Event()
            def work(task, agent, previous, checkpoint, event):
                entered.set()
                while True:
                    checkpoint(); time.sleep(.005)
            manager = BackgroundManager(Path(folder)/'jobs.sqlite3', work)
            job = manager.start('task', iterations=1); self.assertTrue(entered.wait(1))
            self.assertTrue(manager.close(wait=True)); self.assertTrue(manager.close(wait=True))
            with self.assertRaises(sqlite3.ProgrammingError): manager._db.execute('SELECT 1')
            replacement = BackgroundManager(Path(folder)/'jobs.sqlite3', lambda *args: 'done')
            try: self.assertEqual(replacement.get(job['id'])['status'], 'interrupted')
            finally: replacement.close(wait=True)

    def test_idle_nonblocking_close_eventually_releases_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = BackgroundManager(Path(folder)/'jobs.sqlite3', lambda *args: None)
            manager.close(); self.assertTrue(manager.close(wait=True)); self.assertTrue(manager._db_closed)


if __name__ == '__main__': unittest.main()
