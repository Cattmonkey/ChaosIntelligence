"""Native Program refactoring drives real source adoption and persistent work."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from chaos import Framework,Settings,Registry,ProviderSpec,ToolCall,Final
from chaos.language import Evaluator,LanguageError
from chaos.ide_language import analyze,format_source
from chaos.program import parse_program
from chaos.program_values import canonical_hash


def definition():
    files={'main.chaos':'import "math.chaos" as M;M.add(input)',
           'math.chaos':'fn add(x:Int)->Int{x+2};export {add};',
           'consumer.chaos':'import "math.chaos" as M;M.add(input)+1'}
    return {'kind':'program','entry':'main.chaos','files':files,'languageVersions':{n:2 for n in files}}


class ProgramSymbols(unittest.TestCase):
    def test_native_query_candidate_and_alias_invariance(self):
        d=definition();e=Evaluator()
        result=e.run('let p=program_from(input);let r=program_references(p,"math.chaos",3);let q=program_rename(p,"math.chaos",3,"increase");[r,type_of(q),program_info(p),program_info(q),program_metadata(q)]',{'input':d})
        refs,typ,original,candidate,metadata=result
        self.assertTrue(refs['complete']);self.assertEqual(len(refs['references']),4)
        self.assertEqual(typ,'Program');self.assertEqual(original,d)
        self.assertEqual(candidate['files']['consumer.chaos'],'import "math.chaos" as M;M.increase(input)+1')
        self.assertIsNone(metadata['origin']);self.assertEqual(metadata['definitionHash'],canonical_hash(candidate))
        self.assertEqual(Framework.evaluate(candidate['files']['main.chaos'],{'input':40},files=candidate['files']),42)

    def test_no_declaration_option_and_owned_program_boundary(self):
        d=definition();e=Evaluator();p=e.run('program_from(input)',{'input':d})
        with self.assertRaises(LanguageError)as error:Evaluator().run('program_rename(input,"math.chaos",3,"next")',{'input':p})
        self.assertEqual(error.exception.code,'ownership_error')
        result=e.run('program_references(input,"math.chaos",3,false)',{'input':p})
        self.assertEqual(len(result['references']),3)

    def test_native_rename_does_not_compile_or_execute_macros_again(self):
        from chaos.program_values import builtins
        d=definition();d['files']['syntax.chaos']='macro identity(x){x};identity!(42)';d['languageVersions']['syntax.chaos']=2
        e=Evaluator();p=e.run('program_from(input)',{'input':d})
        original=p.definition_json
        with patch('chaos.macros.expand_trees',side_effect=AssertionError('rename executed compiler')):
            proof=builtins(e)['program_references'](p,'math.chaos',3).value
            candidate=builtins(e)['program_rename'](p,'math.chaos',3,'increase')
        self.assertTrue(proof['complete']);self.assertIs(candidate.owner,e)
        self.assertEqual(p.definition_json,original)
        self.assertEqual(candidate.definition['files']['syntax.chaos'],d['files']['syntax.chaos'])
        self.assertEqual(candidate.definition['languageVersions'],d['languageVersions'])
        self.assertEqual(Framework.evaluate(candidate.definition['files']['main.chaos'],{'input':40},files=candidate.definition['files']),42)

    def test_failed_candidate_does_not_mutate_original(self):
        d=definition();d['files']['math.chaos']+='let increase=0;'
        result=Evaluator().run('let p=program_from(input);let before=program_metadata(p).definitionHash;let caught=try{program_rename(p,"math.chaos",3,"increase");false}catch e{true};[caught,before==program_metadata(p).definitionHash,program_info(p)]',{'input':d})
        self.assertEqual(result,[True,True,d])

    def test_refactor_keeps_legacy_access_dialect(self):
        d=definition();d['files']['math.chaos']='fn add(x:Int)->Int{[x,x+2][1]};export {add};';d['languageVersions']['math.chaos']=1
        result=Evaluator().run('let p=program_from(input);program_info(program_rename(p,"math.chaos",3,"increase"))',{'input':d})
        self.assertEqual(result['languageVersions'],d['languageVersions'])
        self.assertIn('[x,x+2][1]',result['files']['math.chaos'])

    def test_native_signatures_highlight_and_format_are_inert(self):
        source='let p:Program=input;let next=program_rename(p,"main.chaos",3,"answer");let proof=program_references(p,"main.chaos",3);[next,proof.complete]'
        data=analyze(source,position=source.index('3,"answer"')+1)
        self.assertTrue(data['complete']);self.assertEqual(data['diagnostics'],[])
        types={v['name']:v['type']for v in data['symbols']};self.assertEqual(types['next'],'Program');self.assertEqual(types['proof'],'Record')
        self.assertIn('program_rename',data['signatureHelp']['label']);self.assertEqual([x['name']for x in data['signatureHelp']['parameters']],['value','file','position','new_name'])
        self.assertEqual(parse_program(source)['semanticHash'],parse_program(format_source(source))['semanticHash'])

    def test_cli_queries_and_candidate_do_not_write_bundle(self):
        d=definition()
        with tempfile.TemporaryDirectory()as directory:
            path=Path(directory)/'program.json';path.write_text(json.dumps(d));before=path.read_bytes()
            argv=[sys.executable,'-m','chaos','symbols']
            result=subprocess.run(argv+['references',str(path),'--file','math.chaos','--position','3'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(len(json.loads(result.stdout)['references']),4)
            result=subprocess.run(argv+['rename','-','--file','math.chaos','--position','3','--new-name','increase'],input=json.dumps(d),capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr);candidate=json.loads(result.stdout)
            self.assertEqual(candidate['files']['main.chaos'],'import "math.chaos" as M;M.increase(input)')
            self.assertEqual(path.read_bytes(),before)
            result=subprocess.run(argv+['rename',str(path),'--file','math.chaos','--position','3','--new-name','return'],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0);self.assertEqual(path.read_bytes(),before)

    def test_real_agent_tool_refactors_tests_adopts_and_retains_snapshot(self):
        requests=[];registry=Registry()
        def provider(request,context):
            requests.append(request)
            return Final(request.history[-1]['output']) if request.history else ToolCall('rename',{'name':'increase'})
        registry.register_provider(ProviderSpec('app.refactor',provider))
        source='''import "math.chaos" as M;
if type_of(input)=="Record" {
 let base=current_program();agent designer{provider:"app.refactor",tools:[]};
 let handler=agent_tool("rename",fn(change:Record)->Record{program_info(program_rename(base,"math.chaos",3,change.name))},input_type="Record",output_type="Record");
 let designerFunction=agent_bind(designer,[handler]);
 let candidate=program_from(await designerFunction({request:"rename"}));
 let receipt=await program_adopt(base,candidate,[{input:40,expect:42},{entry:"consumer.chaos",input:40,expect:43}],"semantic refactor");
 [receipt.version,M.add(40),program_metadata(candidate).origin,program_info(base).files["math.chaos"]]
}else{M.add(input)}
'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            files=definition()['files'];app.create_project('refactor',source=source,files=files,capabilities=['program.adopt'])
            result=app.start('refactor',1,{},mode='source').result(10)
            self.assertEqual(result[:2],[2,42]);self.assertIsNone(result[2]) # JSON candidate intentionally cannot restore origin
            self.assertEqual(result[3],files['math.chaos']);self.assertEqual(len(requests),2)
            self.assertEqual(app.start('refactor',2,40,mode='source').result(10),42)
            current=app.get_project('refactor');self.assertIn('M.increase(input)',current['files']['main.chaos'])
            self.assertEqual(app.program_revision('refactor',1)['definition']['files']['math.chaos'],files['math.chaos'])

    def test_persistent_dot_adopts_then_restarts_on_renamed_source(self):
        root=Path(__file__).resolve().parents[1]/'examples/refactoring-dot'
        files={p.name:p.read_text()for p in root.glob('*.chaos')}
        def wait(app,turn,status):
            until=time.monotonic()+8
            while time.monotonic()<until:
                item=app.get_process('dot','dot')
                if item['turn']==turn and item['status']==status:return item
                self.assertNotEqual(item['status'],'needs_reconciliation',item)
                time.sleep(.01)
            self.fail(str(item))
        with tempfile.TemporaryDirectory()as directory:
            db=Path(directory)/'state.sqlite'
            with Framework(db,settings=Settings(':memory:'))as app:
                app.create_project('dot',source=files['main.chaos'],files=files,capabilities=['process.manage','program.adopt'])
                app.start('dot',1,None,mode='source').result(10);first=wait(app,1,'waiting')
                self.assertEqual(first['output'],42);self.assertEqual(first['lastEvolution']['version'],2)
                self.assertIn('B.respond',app.get_project('dot')['files']['dot.chaos'])
            with Framework(db,settings=Settings(':memory:'))as app:
                app.send_process('dot','dot',None,message_id='after-restart');second=wait(app,2,'completed')
                self.assertEqual(second['output'],42);self.assertEqual(second['state'],2)
                self.assertEqual(second['lastVersion'],2)

if __name__=='__main__':unittest.main()
