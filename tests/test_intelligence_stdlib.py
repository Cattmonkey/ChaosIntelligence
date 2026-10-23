"""Built-in intelligence remains a language value on the existing host boundary."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from chaos import Framework, Settings
from chaos.ide_language import analyze
from chaos.language import LanguageError
from chaos.modules import ProgramBundle, STDLIB
from chaos.program import parse_program
from chaos.source_format import format_source
from tests.test_harness_runtime import FakeClient, call, message


IMPORT = 'import "std/intelligence" as AI;'
ROOT = Path(__file__).resolve().parent.parent


class IntelligenceStdlibTests(unittest.TestCase):
    def settings(self, responses=None):
        self.selected, self.clients = [], []

        def factory(config):
            self.selected.append(dict(config))
            client = FakeClient(responses if responses is not None else [message(config['model'])])
            self.clients.append(client)
            return client

        settings = Settings(':memory:', client_factory=factory)
        settings.update({
            'providers': {'text': {'apiKey': 'fictional-intelligence-test-secret'}},
            'roles': {name: {'model': 'fixture-' + name, 'autoUpgrade': False}
                      for name in ('planner', 'executor', 'reviewer')},
        }, 1)
        return settings

    def events(self, app, run):
        rows, after = [], 0
        while True:
            page = app.events('p', run_id=run.id, after=after, limit=300)
            rows.extend(page['items'])
            if page['nextCursor'] is None:
                return rows
            after = page['nextCursor']

    def test_import_and_construction_are_pure_existing_agent_values(self):
        source = IMPORT + '''let helper=AI.make("explain",role="reviewer",route="my-tutor",tools=["code.execute"],max_steps=3,name="tutor");
        [map(fn(value){[type_of(value),agent_info(value)]},[AI.planner,AI.executor,AI.reviewer,AI.assistant]),agent_info(helper)]'''
        with patch.object(Settings, 'client', side_effect=AssertionError('construction made a request')):
            result = Framework.evaluate(source)
        self.assertEqual([item[0] for item in result[0]], ['Function'] * 4)
        self.assertEqual([item[1]['config']['role'] for item in result[0]],
                         ['planner', 'executor', 'reviewer', 'executor'])
        self.assertEqual([item[1]['config']['route'] for item in result[0]],
                         ['intelligence-planner', 'intelligence-executor', 'intelligence-reviewer', 'intelligence-assistant'])
        self.assertTrue(all(item[1]['config']['tools'] == [] for item in result[0]))
        self.assertEqual(result[1], {'kind': 'agent', 'name': 'tutor', 'config': {
            'provider': 'model', 'prompt': 'explain', 'role': 'reviewer', 'route': 'my-tutor',
            'tools': ['code.execute'], 'maxSteps': 3}})

    def test_construction_reuses_public_agent_validation(self):
        for expression in ('AI.make("help",max_steps=0)', 'AI.make("help",max_steps=13)',
                           'AI.make("help",tools=[42])', 'AI.make("help",name="not-valid")',
                           'AI.make("help",route="")', 'AI.make(42)'):
            with self.subTest(expression=expression), self.assertRaises(LanguageError):
                Framework.evaluate(IMPORT + expression)

    def test_ask_uses_explicit_roles_not_prompt_keywords_and_emits_existing_routes(self):
        settings = self.settings()
        source = IMPORT + '[AI.ask(input,role="planner"),AI.ask(input),AI.ask(input,role="reviewer"),AI.ask(input,role="assistant")]'
        with Framework(settings=settings) as app:
            app.create_project('p', source=source, capabilities=[])
            run = app.start('p', 1, 'review planner executor shell code delete', mode='source')
            self.assertEqual(run.result(10), ['fixture-planner', 'fixture-executor', 'fixture-reviewer', 'fixture-executor'])
            events = self.events(app, run)
        self.assertEqual([item['role'] for item in self.selected], ['planner', 'executor', 'reviewer', 'executor'])
        self.assertEqual([e['payload']['model'] for e in events if e['type'] == 'route.selected'],
                         ['fixture-planner', 'fixture-executor', 'fixture-reviewer', 'fixture-executor'])
        self.assertTrue(all(client.closed for client in self.clients))
        self.assertNotIn('fictional-intelligence-test-secret', json.dumps(events))

    def test_unknown_ask_role_fails_before_request(self):
        settings = self.settings()
        with Framework(settings=settings) as app:
            app.create_project('p', source=IMPORT + 'AI.ask(input,role="guess-from-task")', capabilities=[])
            failed = app.start('p', 1, 'please review', mode='source').wait(10)
            self.assertEqual(failed['status'], 'failed')
            self.assertIn('程序抛出错误', failed['error'])
        self.assertEqual(self.clients, [])

    def test_hot_settings_and_agent_route_override_preserve_other_role_defaults(self):
        settings = self.settings()
        settings.update({'agents': {'intelligence-assistant': {'model': 'fixture-tutor', 'autoUpgrade': False}}},
                        settings.public()['version'])
        with Framework(settings=settings) as app:
            app.create_project('p', source=IMPORT + '[AI.assistant(input),AI.executor(input)]', capabilities=[])
            first = app.start('p', 1, 'question', mode='source')
            self.assertEqual(first.result(10), ['fixture-tutor', 'fixture-executor'])
            settings.update({'roles': {'executor': {'model': 'fixture-next'}}}, settings.public()['version'])
            second = app.start('p', 1, 'question', mode='source')
            self.assertEqual(second.result(10), ['fixture-tutor', 'fixture-next'])
            versions = [next(e['payload']['configVersion'] for e in self.events(app, run)
                             if e['type'] == 'route.selected') for run in (first, second)]
            self.assertLess(versions[0], versions[1])

    def test_function_tools_use_real_language_closure_and_native_protocol(self):
        settings = self.settings([message(calls=[call(args='{"input":21}')]), message('42')])
        source = IMPORT + '''var seen=[];
        fn twice(value:Int)->Int{seen=append(seen,value);value*2}
        let work=agent_bind(AI.executor,[agent_tool("twice",twice,input_type="Int",output_type="Int")]);
        [await work(21),seen]'''
        with Framework(settings=settings) as app:
            app.create_project('p', source=source, capabilities=[])
            run = app.start('p', 1, None, mode='source')
            self.assertEqual(run.result(10), ['42', [21]])
            events = self.events(app, run)
        client = self.clients[0]
        self.assertTrue(client.closed)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(json.loads(client.requests[1]['messages'][-1]['content']), 42)
        self.assertTrue(any(e['type'] == 'agent.tool_completed' for e in events))

    def test_host_tools_require_existing_project_grants(self):
        settings = self.settings()
        source = IMPORT + 'let worker=AI.make("run",tools=["code.execute"]);worker(input)'
        with Framework(settings=settings) as app:
            app.create_project('p', source=source, capabilities=[])
            failed = app.start('p', 1, 'do work', mode='source').wait(10)
            self.assertEqual(failed['status'], 'failed')
            self.assertIn('未授予', failed['error'])
        self.assertEqual(self.clients, [])

    def test_existing_session_accepts_assistant_and_retains_history(self):
        settings = self.settings()
        source = IMPORT + '''let conversation=await session_open("conversation",AI.assistant);
        let first=await conversation("explain function composition");
        let current=await session_load("conversation");
        let second=await current("show another example");
        [first,second]'''
        with Framework(settings=settings) as app:
            app.create_project('p', source=source, capabilities=['session.manage'])
            app.start('p', 1, None, mode='source').result(10)
            self.assertEqual(app.agent_session('p', 'conversation')['turn'], 2)
        self.assertEqual(len(self.clients), 2)
        self.assertTrue(all(client.closed for client in self.clients))
        messages = self.clients[1].requests[0]['messages']
        self.assertTrue(any('explain function composition' in str(item) for item in messages))

    def test_existing_bot_accepts_agent_execution_and_honest_verification(self):
        settings = self.settings()
        source = IMPORT + '''fn prepare(state){state.input}
        fn execute(plan,state){AI.executor(plan)}
        fn verify(state){{done:state.evidence==["fixture-executor"],context:state.context,output:state.evidence}}
        let worker=bot("answer the supplied question",prepare,execute,verify);
        let result=await worker(input);result.output'''
        with Framework(settings=settings) as app:
            app.create_project('p', source=source, capabilities=[])
            self.assertEqual(app.start('p', 1, 'question', mode='source').result(10), ['fixture-executor'])
        self.assertEqual(len(self.clients), 1)

    def test_composition_uses_existing_callable_protocol(self):
        settings = self.settings()
        source = IMPORT + 'compose(AI.reviewer,AI.planner)(input)'
        with Framework(settings=settings) as app:
            app.create_project('p', source=source, capabilities=[])
            self.assertEqual(app.start('p', 1, 'plan task', mode='source').result(10), 'fixture-reviewer')
        self.assertEqual([item['role'] for item in self.selected], ['planner', 'reviewer'])

    def test_standard_module_completion_and_examples_need_no_executed_analysis(self):
        self.assertIn('std/intelligence', STDLIB)
        with patch.object(Settings, 'client', side_effect=AssertionError('analysis made a request')):
            result = analyze(IMPORT + 'AI.')
            self.assertEqual({x['label'] for x in result['completions']},
                             {'make', 'ask', 'planner', 'executor', 'reviewer', 'assistant'})
            self.assertEqual(analyze(IMPORT + 'let helper=AI.make("help");')['diagnostics'], [])
            for path in sorted((ROOT / 'examples/intelligence').glob('*.chaos')):
                with self.subTest(example=path.name):
                    source = path.read_text(encoding='utf-8')
                    ProgramBundle(source)
                    self.assertEqual(analyze(source)['diagnostics'], [])
                    self.assertEqual(parse_program(source)['semanticHash'],
                                     parse_program(format_source(source))['semanticHash'])


if __name__ == '__main__':
    unittest.main()
