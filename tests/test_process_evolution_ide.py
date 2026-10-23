"""Native Process return contracts reach source tooling and public read APIs."""
import inspect
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Client, Framework, Settings
from chaos.code_presentation import render_code
from chaos.ide_language import analyze, builtin_symbols
from chaos.language import Evaluator, Parser
from chaos.lsp import Job, Server
from chaos.lsp_adapters import TOKEN_TYPES, convert
from chaos.lsp_workspace import Workspace
from chaos.native_specs import SHAPES, spec_for
from chaos.openapi import specification
from chaos.program import plain
from chaos.server import make_server
from chaos.source_format import format_source
from tests.test_process_integration import definition, wait_for


SOURCE='''fn step(ctx){
 if ctx.state.count>0 {process_step(merge(ctx.state,{count:ctx.state.count+1}),ctx.programVersion,true)} else {
  let base=current_program();
  let definition=program_info(base);
  let candidate=program_from(merge(definition,{files:set(definition.files,"behavior.chaos","input + "+str(ctx.state.increment))}));
  let decision=process_evolve(base,candidate,[{input:1,expect:42,entry:"behavior.chaos"}],merge(ctx.state,{count:1}),{executed:ctx.programVersion},reason="verified increment");
  let info=process_evolution_info(decision);
  if info.testCount!=1 {throw "unexpected contract"} else {null};
  decision
 }
};export {step}'''


class ProcessEvolutionIDE(unittest.TestCase):
    def result(self,source=SOURCE,**kwargs):
        result=analyze(source,**kwargs)
        self.assertTrue(result['complete'],result['diagnostics']);self.assertEqual(result['diagnostics'],[])
        return result

    def test_native_signatures_match_real_callable_parameters(self):
        env=Evaluator().env;symbols=builtin_symbols()
        for name in ('process_evolve','process_evolution_info','process_from','process_info','process_source','process_step','process_start','process_send','process_status','process_control','process_configure','process_reconcile'):
            with self.subTest(name=name):
                native=env.get(name);spec=spec_for(name);parameters=list(inspect.signature(native.call).parameters.values())
                self.assertEqual(native.spec_id,name)
                self.assertEqual([p.name for p in parameters],[p.name for p in spec.parameters])
                self.assertEqual([p.default is not p.empty for p in parameters],[p.optional for p in spec.parameters])
                self.assertEqual([p[0] for p in symbols[name].parameters],[p.name for p in parameters])
        self.assertEqual(spec_for('process_evolve').result_type,'ProcessEvolution')

    def test_owned_intent_is_nominal_opaque_and_not_a_task_or_function(self):
        symbols={item['name']:item for item in self.result()['symbols']}
        self.assertEqual(symbols['decision']['type'],'ProcessEvolution');self.assertEqual(symbols['info']['type'],'Record')
        prefix='let decision=process_evolve(input,input,[],null);'
        self.assertFalse(analyze(prefix+'decision.')['completions'])
        self.assertIsNone(analyze(prefix+'decision(')['signatureHelp'])
        self.assertTrue(analyze(prefix+'let call:Function=decision')['diagnostics'])
        self.assertTrue(analyze('let decision:ProcessEvolution={}')['diagnostics'])
        self.result(prefix+'let exact:ProcessEvolution=decision')

    def test_aliases_exports_and_lexical_shadowing_preserve_identity(self):
        module='let evolve=process_evolve;export {evolve}'
        source='import "factory.chaos" as Factory;let create=Factory.evolve;let decision=create(input,input,[],null);decision'
        result=self.result(source,files={'factory.chaos':module},position=source.index('Factory.evolve')+len('Factory.evo'))
        self.assertEqual(next(row['type']for row in result['symbols']if row['name']=='decision'),'ProcessEvolution')
        self.assertEqual(result['definition']['file'],'factory.chaos');self.assertEqual(result['definition']['from'],module.index('evolve='))
        for source,expected in [('fn process_evolve(a,b,c,d)->String{"shadow"};let decision=process_evolve(1,2,3,4)','String'),('var evolve=process_evolve;let decision=evolve(input,input,[],null)','Any')]:
            self.assertEqual(next(row['type']for row in self.result(source)['symbols']if row['name']=='decision'),expected)

    def test_reflection_and_persisted_receipt_member_shapes(self):
        source='let decision=process_evolve(input,input,[],null);let info=process_evolution_info(decision);'
        self.assertEqual({row['label']for row in analyze(source+'info.')['completions']},{field.name for field in SHAPES['ProcessEvolutionInfo']})
        self.assertEqual({row['label']for row in analyze(source+'info.base.')['completions']},{'projectId','version','definitionHash'})
        for expression in ('process_status("dot")','process_start("dot",process_from(input))','process_from(input)("dot")'):
            prefix='let record=await '+expression+';'
            completions=analyze(prefix+'record.')['completions']
            evolution=next(row for row in completions if row['label']=='lastEvolution')
            self.assertIn('可选',evolution['detail'])
            self.assertEqual({row['label']for row in analyze(prefix+'record.lastEvolution.')['completions']},{field.name for field in SHAPES['ProgramAdoptionReceipt']})
            result=self.result(prefix+'let version=record.lastEvolution.version')
            self.assertEqual(next(row['type']for row in result['symbols']if row['name']=='version'),'Int')

    def test_signature_defaults_and_real_argument_diagnostics(self):
        result=analyze('process_evolve(input,input,[],null,reason=')
        self.assertEqual(result['signatureHelp']['activeParameter'],6)
        self.assertEqual([row['name']for row in result['signatureHelp']['parameters']],['base','candidate','cases','state','output','done','reason'])
        self.assertIn('-> ProcessEvolution',result['signatureHelp']['label']);self.assertIn('不执行测试或采用',result['signatureHelp']['label'])
        for source in ('process_evolve(42,input,[],null)','let wrong={a:1};process_evolve(input,input,wrong,null)','process_evolve(input,input,[],null,done=1)','process_evolve(input,input,[],null,reason=42)','let wrong={a:1};process_evolution_info(wrong)'):
            with self.subTest(source=source):self.assertTrue(analyze(source)['diagnostics'])

    def test_sdk_lsp_highlight_and_formatter_remain_inert_and_share_ast(self):
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);workspace=Workspace([root]);uri=(root/'dot.chaos').as_uri()
            workspace.open({'uri':uri,'languageId':'chaos','version':1,'text':SOURCE});snapshot=workspace.snapshot(uri);files,entry=snapshot.load()
            with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed source')),patch.object(Evaluator,'run_export',side_effect=AssertionError('analysis ran Process')):
                position=SOURCE.index('reason=')+len('reason=');result=self.result(position=position,files=files,entry=entry)
                self.assertEqual(Framework.analyze(SOURCE,position=position,files=files,entry=entry),result)
                signature=convert('textDocument/signatureHelp',result,snapshot,files)
                self.assertIn('ProcessEvolution',signature['signatures'][0]['label'])
                tokens=convert('textDocument/semanticTokens/full',result,snapshot,files)['data'];self.assertIn(TOKEN_TYPES.index('function'),tokens[3::5])
                formatted=Server(None,None)._calculate(Job(1,'textDocument/formatting',{'options':{'tabSize':2,'insertSpaces':True}},snapshot))[0]['newText']
                self.assertEqual(plain(Parser(SOURCE).program()),plain(Parser(formatted).program()));self.assertEqual(format_source(formatted),formatted)
                html=render_code(SOURCE,'chaos');self.assertIn('process_evolve',html);self.assertIn('chaos-token-function',html)

    def test_openapi_receipt_is_additive_and_shared_with_program_adoption(self):
        schemas=specification()['components']['schemas'];receipt={'$ref':'#/components/schemas/ProgramAdoptionReceipt'}
        self.assertEqual(schemas['ProcessRecord']['properties']['lastEvolution'],receipt)
        self.assertNotIn('lastEvolution',schemas['ProcessRecord']['required'])
        self.assertEqual(schemas['AdoptedProgram']['properties']['receipt'],receipt)
        self.assertEqual(set(schemas['ProgramAdoptionReceipt']['properties']),{field.name for field in SHAPES['ProgramAdoptionReceipt']})

    def test_reflection_fields_match_real_owned_value_without_adoption(self):
        source=SOURCE.replace('if info.testCount!=1 {throw "unexpected contract"} else {null};\n  decision','process_step(ctx.state,info,true)')
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('reflection',source='input',files={'dot.chaos':source,'behavior.chaos':'input'},capabilities=['process.manage','program.adopt'])
            app.create_process('reflection',1,'dot',definition(),state={'count':0,'increment':41})
            row=wait_for(app,'reflection','dot');info=row['output']
            self.assertEqual(set(info),{field.name for field in SHAPES['ProcessEvolutionInfo']})
            self.assertEqual(set(info['base']),{field.name for field in SHAPES['ProgramOriginInfo']})
            self.assertEqual(info['base']['version'],1);self.assertEqual(info['testCount'],1)
            self.assertEqual(info['state'],{'count':1,'increment':41});self.assertFalse(info['done'])
            self.assertEqual(app.get_project('reflection')['version'],1);self.assertNotIn('lastEvolution',row)

    def test_http_reads_actual_atomic_receipt_without_new_mutation_endpoint(self):
        with tempfile.TemporaryDirectory(prefix='chaos-evolution-http-')as folder,Framework(Path(folder)/'runtime.sqlite',settings=Settings(':memory:'))as app:
            app.create_project('evolution',source='input',files={'dot.chaos':SOURCE,'behavior.chaos':'input'},capabilities=['process.manage','program.adopt'])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                client.create_process('evolution',1,'bad',definition(),state={'count':0,'increment':40})
                failed=wait_for(app,'evolution','bad',lambda row:row['status']=='needs_reconciliation')
                self.assertNotIn('lastEvolution',failed);self.assertEqual(client.get_project('evolution')['version'],1)
                client.create_process('evolution',1,'good',definition(),state={'count':0,'increment':41})
                row=wait_for(app,'evolution','good');public=client.get_process('evolution','good')
                self.assertEqual(public,row);self.assertEqual(public['lastVersion'],1);self.assertEqual(public['lastEvolution']['version'],2)
                self.assertEqual(public['state'],{'count':1,'increment':41});self.assertEqual(public['output'],{'executed':1})
                self.assertEqual(set(public['lastEvolution']),{field.name for field in SHAPES['ProgramAdoptionReceipt']})
                self.assertNotIn('botRoles',public)
                self.assertEqual(set(public),{field.name for field in SHAPES['ProcessRecord']} - {'botRoles'})
                self.assertEqual({item['key']for item in client.processes('evolution')['items']},{'bad','good'})
                schema=client._request('GET','/openapi.json');self.assertEqual(schema['components']['schemas']['ProgramAdoptionReceipt'],specification()['components']['schemas']['ProgramAdoptionReceipt'])
            finally:server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
