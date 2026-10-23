"""Program library composition uses one parser, native contract and execution path."""
import contextlib
import copy
import inspect
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from chaos import Client, Framework, Registry, ProviderSpec, Final, Settings, ToolCall, link_program
from chaos.cli import main
from chaos.code_presentation import render_code
from chaos.ide_language import analyze, builtin_symbols
from chaos.language import Evaluator, Parser
from chaos.lsp_adapters import convert
from chaos.native_specs import spec_for
from chaos.program import decode_program, format_program, parse_program, plain
from chaos.server import make_server
from chaos.source_format import format_source


def definition(source, files=None, entry='main.chaos'):
    files={**(files or {}),entry:source}
    return {'kind':'program','entry':entry,'files':files,'languageVersions':{n:2 for n in files}}


LIBRARY=definition('import "util.chaos" as U;fn apply(x){U.step(x)};export{apply}',
                   {'util.chaos':'fn step(x){x+1};export{step}'})
ROOT=definition('import "vendor/left/main.chaos" as L;import "vendor/right/main.chaos" as R;fn solve(x){R.apply(L.apply(x))};export{solve}')
SOURCE=('// Two libraries retain their original internal filenames.\n'
        'let left=program_from('+json.dumps(LIBRARY)+');\n'
        'let linked=program_link('+json.dumps(ROOT)+',{"vendor/left":left,"vendor/right":left});\n'
        'module_from(linked).solve(40)')


class ProgramLinkSurfaces(unittest.TestCase):
    def test_real_native_signature_result_and_public_python_shape(self):
        native=Evaluator().env.get('program_link');spec=spec_for('program_link')
        self.assertEqual(native.spec_id,spec.id)
        self.assertEqual([(p.name,p.type,p.mode,p.optional)for p in spec.parameters],
                         [('definition','Record','positional',False),('libraries','Record','positional',False)])
        self.assertEqual(list(inspect.signature(native.call).parameters),['definition','libraries'])
        self.assertEqual(spec.result_type,'Program');self.assertEqual(builtin_symbols()['program_link'].returns,'Program')
        signature=inspect.signature(link_program)
        self.assertEqual(list(signature.parameters),['definition','library_definitions','checkpoint'])
        self.assertEqual(signature.parameters['checkpoint'].kind,inspect.Parameter.KEYWORD_ONLY)
        before=copy.deepcopy([ROOT,LIBRARY])
        result=link_program(ROOT,library_definitions={'vendor/left':LIBRARY,'vendor/right':LIBRARY})
        self.assertEqual(set(result),{'kind','entry','files','languageVersions'})
        self.assertEqual(result['files']['vendor/left/util.chaos'],LIBRARY['files']['util.chaos'])
        self.assertEqual([ROOT,LIBRARY],before)
        self.assertEqual(Framework.evaluate('module_from(program_from(input)).solve(40)',{'input':result}),42)

    def test_formatter_ir_and_hygienic_macro_share_ordinary_call(self):
        for source in (SOURCE,format_source(SOURCE,line_width=48),format_program(parse_program(SOURCE))):
            self.assertEqual(Framework.evaluate(source),42)
            self.assertEqual(plain(decode_program(parse_program(source))),plain(Parser(SOURCE).program()))
        formatted=format_source(SOURCE);self.assertEqual(format_source(formatted),formatted)
        macro='macro join(linker,root,libs){quote{unquote(linker)(unquote(root),unquote(libs))}};'
        source=macro+SOURCE.replace('program_link('+json.dumps(ROOT), 'join!(program_link,'+json.dumps(ROOT))
        for text in (source,format_source(source),format_program(parse_program(source))):self.assertEqual(Framework.evaluate(text),42)

    def test_alias_shadow_completion_and_lsp_are_inert(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran source')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('evaluated source')):
            source='let link=program_link;let candidate=link({},{});'
            report=analyze(source);types={x['name']:x['type']for x in report['symbols']}
            self.assertEqual(types['candidate'],'Program')
            self.assertIn('program_link',[x['label']for x in analyze('program_li')['completions']])
            text='program_link({},libraries=';report=analyze(text)
            self.assertEqual([x['name']for x in report['signatureHelp']['parameters']],['definition','libraries'])
            self.assertEqual(report['signatureHelp']['activeParameter'],1)
            help=convert('textDocument/signatureHelp',report,SimpleNamespace(name='main.chaos'),{'main.chaos':text})
            self.assertIn('libraries: Record',help['signatures'][0]['label'])
            self.assertIn('program_link',render_code(SOURCE,'chaos'))
            shadow=analyze('fn program_link(a,b)->String{"local"};let value=program_link(null,null)')
            self.assertEqual(next(x['type']for x in shadow['symbols']if x['name']=='value'),'String')

    def test_linked_bundle_can_be_analyzed_as_exact_multifile_source(self):
        linked=link_program(ROOT,{'vendor/left':LIBRARY,'vendor/right':LIBRARY})
        report=Framework.analyze(linked['files'][linked['entry']],files=linked['files'],entry=linked['entry'],language_versions=linked['languageVersions'])
        self.assertFalse([x for x in report['diagnostics']if x['severity']=='error'])
        self.assertEqual(set(linked['languageVersions']),set(linked['files']))
        self.assertEqual(len(linked['files']),5)

    def test_cli_runs_the_same_source_without_a_model_provider(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'main.chaos';path.write_text(SOURCE)
            output=io.StringIO()
            with patch('sys.argv',['chaos','run',str(path)]),contextlib.redirect_stdout(output):status=main()
            self.assertEqual(status,0);self.assertEqual(json.loads(output.getvalue()),42)

    def test_linked_export_is_actual_agent_tool_implementation(self):
        calls=[]
        def choose(request,context):
            context.checkpoint();calls.append(request.step)
            return Final(request.history[-1]['output']) if request.history else ToolCall('solve',request.input)
        registry=Registry();registry.register_provider(ProviderSpec('choose',choose))
        source=SOURCE.rsplit('module_from(linked).solve(40)',1)[0]+'''let library=module_from(linked);
agent worker{provider:"choose",tools:[]};let bound=agent_bind(worker,[agent_tool("solve",library.solve,input_type="Int",output_type="Int")]);await bound(40)'''
        with Framework(settings=Settings(':memory:'),registry=registry)as app:
            project=app.create_project('linked-tool',source=source,capabilities=[])
            self.assertEqual(app.start(project['id'],project['version'],None).result(10),42)
        self.assertEqual(calls,[1,2])

    def test_http_sdk_ir_edit_preserves_existing_project_snapshot(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            root=Path(folder);server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                project=client.create_project('linked',source=SOURCE,capabilities=[])
                old=client.start(project['id'],project['version'],None);self.assertEqual(old.result(),42)
                program=client.program(project['id'])['program']
                def find(value):
                    if isinstance(value,dict):
                        if value.get('kind')=='literal' and value.get('value')==40:return value
                        for child in value.values():
                            found=find(child)
                            if found:return found
                    elif isinstance(value,list):
                        for child in value:
                            found=find(child)
                            if found:return found
                target=find(program['root']);self.assertIsNotNone(target)
                changed=client.edit_program(project['id'],project['version'],operations=[{'op':'set','id':target['id'],'field':'value','value':41}])
                self.assertEqual(client.start(project['id'],changed['version'],None,mode='graph').result(),43)
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.analyze(changed['source']),Framework.analyze(changed['source']))
            finally:server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
