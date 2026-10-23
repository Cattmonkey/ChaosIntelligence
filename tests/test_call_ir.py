"""Advanced calls retain one semantics across source, program edits and APIs."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from chaos import Client, Framework, Registry, Settings
from chaos.client import ValidationError
from chaos.language import LanguageError, Evaluator
from chaos.program import parse_program, format_program, decode_program, plain, patch_program, project_program, templates
from chaos.server import make_server


class CallIRTests(unittest.TestCase):
    def test_roundtrip_parameter_kinds_and_argument_nodes(self):
        source='fn f(a:Int,b:Int=2,*rest:List,required:Int,option=3,**options:Record){[a,b,rest,required,option,options]};f(1,*[4,5],required=6,**{extra:7})'
        program=parse_program(source);function=program['root']['statements'][0]['function']
        self.assertEqual(function['parameters'][0],{'name':'a','type':'Int'})
        self.assertEqual([row.get('mode','positional') for row in function['parameters']],['positional','positional','varargs','keyword_only','keyword_only','varkw'])
        self.assertEqual([row['kind'] for row in program['root']['statements'][1]['arguments']],['literal','spread','keyword','kwspread'])
        text=format_program(program)
        self.assertEqual(plain(decode_program(program)),plain(decode_program(parse_program(text))))
        self.assertEqual(Evaluator().run(text),[1,4,[5],6,3,{'extra':7}])

    def test_default_expression_is_projected_and_patchable_with_identity(self):
        program=parse_program('fn add(x,step=1){x+step};add(41)')
        default=program['root']['statements'][0]['function']['parameters'][1]['default']
        target=default['id']
        self.assertTrue(any(edge['slot']=='parameters.1.default' and edge['target']==target for edge in project_program(program)['edges']))
        updated=patch_program(program,[{'op':'set','id':target,'field':'value','value':2}])
        self.assertEqual(Evaluator().run(format_program(updated)),43)
        self.assertEqual(updated['root']['statements'][0]['function']['parameters'][1]['default']['id'],target)
        self.assertEqual(default['value'],1)
        self.assertNotEqual(updated['semanticHash'],program['semanticHash'])

    def test_default_callable_scope_and_precise_numbers_roundtrip(self):
        source='fn f(big=9007199254740993,decimal=1.0,transform=fn(x){x+1}){[big,decimal,transform(41)]};f()'
        program=parse_program(source);params=program['root']['statements'][0]['function']['parameters']
        self.assertEqual(params[0]['default']['value'],{'$number':'9007199254740993'})
        self.assertEqual(params[1]['default']['value'],{'$number':'1.0'})
        result=Evaluator().run(format_program(json.loads(json.dumps(program))))
        self.assertEqual(result,[9007199254740993,1.0,42]);self.assertIs(type(result[1]),float)

    def test_bare_keyword_marker_and_legacy_parameter_encoding(self):
        self.assertEqual(Evaluator().run(format_program(parse_program('fn f(*,x,y=2){x+y};f(x=40)'))),42)
        program=parse_program('fn f(x){x};f(42)');row=program['root']['statements'][0]['function']['parameters'][0]
        self.assertEqual(row,{'name':'x','type':'Any'})
        row.update(mode='positional',default=None)
        self.assertEqual(Evaluator().run(format_program(program)),42)
        self.assertEqual(parse_program(format_program(program))['root']['statements'][0]['function']['parameters'][0],{'name':'x','type':'Any'})

    def test_invalid_parameter_ir_rejected_atomically(self):
        original=parse_program('fn f(x=1,*,required){x+required};f(required=41)')
        for change in ('unknown_mode','rest_default','partial_row','duplicate_id','required_after_default'):
            broken=copy.deepcopy(original);params=broken['root']['statements'][0]['function']['parameters']
            if change=='unknown_mode':params[0]['mode']='unknown'
            elif change=='rest_default':params[0]['mode']='varargs'
            elif change=='partial_row':params[0].pop('mode')
            elif change=='duplicate_id':params[1]['default']=copy.deepcopy(params[0]['default'])
            else:params[1]={'name':'required','type':'Any'}
            with self.subTest(change=change),self.assertRaises(LanguageError):format_program(broken)
        self.assertEqual(Evaluator().run(format_program(original)),42)
        for kind in ('keyword','spread','kwspread'):
            broken=copy.deepcopy(original);node=copy.deepcopy(templates()[kind]);node['id']='isolated_'+kind
            broken['root']['statements'].append(node)
            with self.subTest(kind=kind),self.assertRaises(LanguageError):format_program(broken)

    def test_effect_order_matches_formatted_source(self):
        source='fn f(a=tool("default",1),*rest,**options){tool("body",[a,rest,options])};f(tool("arg",2),*tool("spread",[3]),x=tool("named",4),**tool("keys",{y:5}))'
        outcomes=[]
        for text in (source,format_program(parse_program(source))):
            calls=[]
            result=Evaluator(tool_handler=lambda name,value:(calls.append(name),value)[1]).run(text)
            outcomes.append((result,calls))
        self.assertEqual(outcomes[0],outcomes[1])
        self.assertEqual(outcomes[0],([2,[3],{'x':4,'y':5}],['default','arg','spread','named','keys','body']))

    def test_sdk_http_edits_snapshots_and_registered_agents(self):
        registry=Registry();calls=[]
        @registry.tool('test.record',effect='write')
        def record(value,context):calls.append(value);return value
        with tempfile.TemporaryDirectory() as folder, Framework(registry=registry,settings=Settings(':memory:')) as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                source='import "lib.chaos" as L;L.work(input,*[1],step=2,**{tag:"sdk"})'
                files={'lib.chaos':'fn work(x,*rest,step=1,**options){tool("test.record",[x,rest,step,options]);x+step};export{work}'}
                p=client.create_project('functions',source=source,files=files,capabilities=['test.record'])
                run=client.start('functions',p['version'],40);self.assertEqual(run.result(),42)
                ir=client.program('functions')['program'];call=ir['root']['statements'][1];target=call['arguments'][2]['value']['id']
                snapshot=client.snapshot('functions')
                p=app.edit_program('functions',p['version'],operations=[{'op':'set','id':target,'field':'value','value':3}])
                self.assertEqual(client.get_project('functions')['source'],p['source'])
                self.assertEqual(client.start('functions',p['version'],40).result(),43)
                self.assertEqual(run.get()['snapshot']['source'],source)
                self.assertEqual(calls,[[40,[1],2,{'tag':'sdk'}],[40,[1],3,{'tag':'sdk'}]])
                self.assertTrue(any(e['payload'].get('version')==p['version'] for e in client.events('functions',after=snapshot['eventCursor'])['items']))
                invalid=copy.deepcopy(p['program']);invalid['root']['statements'][1]['arguments'].append(copy.deepcopy(templates()['spread']))
                with self.assertRaises(ValidationError):client.edit_program('functions',p['version'],program=invalid)
                self.assertEqual(client.get_project('functions')['version'],p['version'])
                agent=client.create_project('agent_kwargs',source='agent review{provider:"local",mode:"review"};review(input=input)')
                self.assertEqual(client.start('agent_kwargs',agent['version'],42).result()['result'],42)
            finally:server.shutdown();server.server_close();worker.join(2)

    def test_cli_check_format_run_and_repl(self):
        with tempfile.TemporaryDirectory() as folder:
            entry=Path(folder)/'main.chaos';entry.write_text('fn f(x=40,*,step=2){x+step};f(**{})')
            env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parent.parent)
            for args in (['check',str(entry)],['fmt',str(entry)],['ir',str(entry)],['run',str(entry)]):
                result=subprocess.run([sys.executable,'-m','chaos',*args],cwd=folder,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if args[0]=='run':self.assertEqual(json.loads(result.stdout),42)
            result=subprocess.run([sys.executable,'-m','chaos','repl'],input='fn f(x=40,*,step=2){x+step}\nf()\nf(step=3)\n.quit\n',cwd=folder,env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()][1:],[42,43])


if __name__=='__main__':unittest.main()
