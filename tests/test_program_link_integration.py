"""Reusable linked source libraries share actual host, agent and revision semantics."""
import copy
import json
import unittest

from chaos import (Framework, Registry, ProviderSpec, ToolSpec, ToolCall, Final,
                   Settings, ProgramBundle, Evaluator, link_program)
from tests.test_team_functions import definition as team_definition


def definition(source, *, files=None, entry='main.chaos', versions=None):
    files = {**(files or {}), entry: source}
    return {'kind': 'program', 'entry': entry, 'files': files,
            'languageVersions': versions or {name: 2 for name in files}}


class ProgramLinkIntegration(unittest.TestCase):
    def app(self, **kwargs):
        app = Framework(settings=Settings(':memory:'), **kwargs)
        self.addCleanup(app.close)
        return app

    def create_linked(self, app, name, source, libraries, capabilities=()):
        linked = link_program(definition(source), libraries)
        app.create_project(name, source=linked['files'][linked['entry']],
                           files=linked['files'], entry=linked['entry'],
                           capabilities=list(capabilities))
        return linked

    def test_agent_tool_calls_linked_async_function(self):
        requests = []
        def provider(request, context):
            requests.append(request.input)
            return Final(request.history[-1]['output']) if request.history else ToolCall('work', request.input)
        registry = Registry()
        registry.register_provider(ProviderSpec('app.choose', provider))
        app = self.app(registry=registry)
        library = definition('import "helpers.chaos" as H; async fn work(x){await sleep(0);H.add(x)+1};export{work}',
                             files={'helpers.chaos': 'fn add(x){x+1};export{add}'})
        self.create_linked(app, 'agent', '''import "vendor/worker/main.chaos" as W;
            agent solver{provider:"app.choose"};
            let bound=agent_bind(solver,[agent_tool("work",W.work)]);await bound(input)''',
                           {'vendor/worker': library})
        self.assertEqual(app.start('agent', 1, 40, mode='source').result(10), 42)
        self.assertEqual(requests, [40, 40])

    def test_team_role_uses_linked_macro_and_keyword_function(self):
        app = self.app()
        library = definition('import "syntax.chaos" as S;fn work(x,*,offset=0){S.inc!(x)+offset};export{work}',
                             files={'syntax.chaos': 'macro inc(x){quote{unquote(x)+1}};export{inc}'})
        self.create_linked(app, 'team', '''import "vendor/worker/main.chaos" as W;
            let crew=team_bind(team_from(input),{worker:W.work});
            await team_role(crew,"worker")(40,offset=1)''', {'vendor/worker': library})
        run = app.start('team', 1, team_definition(), mode='source')
        self.assertEqual(run.result(10), 42)
        events = app.events('team', run_id=run.id)['items']
        self.assertEqual([row['type'] for row in events if row['type'].startswith('team.role.')],
                         ['team.role.started', 'team.role.completed'])

    def test_link_is_inert_and_import_initializes_library_once(self):
        calls = []
        registry = Registry()
        registry.register_tool(ToolSpec('app.init', lambda value, context: calls.append(value)))
        app = self.app(registry=registry)
        library = definition('tool("app.init","loaded");fn value(){21};export{value}')
        linked = self.create_linked(app, 'imports', '''import "vendor/shared/main.chaos" as A;
            import "vendor/shared/main.chaos" as B;A.value()+B.value()''',
                                    {'vendor/shared': library}, ['app.init'])
        self.assertEqual(calls, [])
        self.assertEqual(linked['files']['vendor/shared/main.chaos'], library['files']['main.chaos'])
        self.assertEqual(app.start('imports', 1, None, mode='source').result(10), 42)
        self.assertEqual(calls, ['loaded'])

    def test_native_link_probe_adopt_preserves_old_running_program(self):
        app = self.app()
        source = '''fn old(x){x-1};let base=current_program();
            let library=program_from(input.library);
            let candidate=program_link(input.root,{"vendor/math":library});
            let report=await program_probe(candidate,[{input:40,expect:42}]);
            let receipt=await program_adopt(base,candidate,[{input:40,expect:42}],"linked library");
            [report.passed,receipt.version,program_metadata(candidate).origin,
             program_metadata(current_program()).origin.version,old(40)]'''
        app.create_project('evolve', source=source, capabilities=['program.adopt'])
        request = {'library': definition('fn add(x){x+2};export{add}'),
                   'root': definition('import "vendor/math/main.chaos" as M;M.add(input)')}
        original = copy.deepcopy(request)
        run = app.start('evolve', 1, request, mode='source')
        self.assertEqual(run.result(10), [True, 2, None, 1, 39])
        self.assertEqual(request, original)
        self.assertEqual(app.get_run(run.id)['snapshot']['source'], source)
        self.assertEqual(app.start('evolve', 2, 40, mode='source').result(10), 42)
        saved = app.get_project('evolve')
        expected = link_program(request['root'], {'vendor/math': request['library']})
        self.assertEqual(saved['files'], expected['files'])
        self.assertEqual(saved['program']['sourceVersions'], expected['languageVersions'])

    def test_public_sdk_preserves_legacy_dialect_in_existing_bundle_runner(self):
        library = definition('class Box{let n=40;fn __getitem__(self,key){0}};fn work(x){let box=Box();box["n"]+2};export{work}',
                             versions={'main.chaos': 1})
        root = definition('import "vendor/math/main.chaos" as M;M.work(40)')
        linked = link_program(root, library_definitions={'vendor/math': library})
        bundle = ProgramBundle(linked['files'][linked['entry']], linked['files'],
                               linked['entry'], language_versions=linked['languageVersions'])
        self.assertEqual(Evaluator().run(bundle), 42)
        self.assertEqual(linked['languageVersions'], {'main.chaos': 2, 'vendor/math/main.chaos': 1})


if __name__ == '__main__':
    unittest.main()
