"""Atomic selection is one language across macros, modules, source/IR and APIs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from chaos import Client, Framework, Settings
from chaos.documentation import Documentation
from chaos.server import make_server
from chaos.program import parse_program, format_program

ROOT=Path(__file__).resolve().parent.parent
SOURCE='''macro branch(action,c){quote{unquote(action)(unquote(c))}}
let replies=channel(3);
async fn work(value){await channel_send(replies,value*2)}
async with task_group() as workers{
 for value in input{workers.spawn(work(value))};
 let first=await channel_select([branch!(receive_case,replies)],timeout=1);
 let second=await channel_select([branch!(receive_case,replies)],timeout=1);
 [sort([first.value,second.value]),first.timedOut,second.timedOut]
}'''

class SelectSurfaces(unittest.TestCase):
    def test_macro_source_ir_formatter_and_typed_module_cases(self):
        expected=[[40,42],False,False]
        self.assertEqual(Framework.evaluate(SOURCE,{'input':[20,21]}),expected)
        self.assertEqual(Framework.evaluate(format_program(parse_program(SOURCE)),{'input':[20,21]}),expected)
        formatted=Framework.format_source(SOURCE,line_width=40,indent_size=4)
        self.assertEqual(Framework.format_source(formatted,line_width=40,indent_size=4),formatted)
        self.assertEqual(Framework.evaluate(formatted,{'input':[20,21]}),expected)
        files={'cases.chaos':'fn receive(c:Channel)->ChannelCase{receive_case(c)};export{receive}'}
        self.assertEqual(Framework.evaluate('import "cases.chaos" as C;let c=channel(1);await channel_send(c,42);(await channel_select([C.receive(c)])).value',files=files),42)

    def test_http_structure_change_keeps_old_run_and_original_api(self):
        with tempfile.TemporaryDirectory() as directory, Framework(settings=Settings(':memory:')) as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(directory)/'control',update_home=Path(directory)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                project=client.create_project('select-surfaces',source=SOURCE,capabilities=[])
                old=client.start(project['id'],1,[20,21]);self.assertEqual(old.result(5),[[40,42],False,False])
                events=client.events(project['id'],run_id=old.id)['items']
                selected=[event['payload'] for event in events if event['type']=='program.task.created' and event['payload'].get('kind')=='channel_select']
                self.assertEqual(len(selected),2)
                self.assertTrue(all(item['selection']['caseCount']==1 and item['memberCount']==0 for item in selected))
                ir=client.program(project['id'])['program']
                def targets(value):
                    if isinstance(value,dict):
                        if value.get('kind')=='literal' and value.get('value')==2:yield value['id']
                        for child in value.values():yield from targets(child)
                    elif isinstance(value,list):
                        for child in value:yield from targets(child)
                ids=list(targets(ir['root']));self.assertEqual(len(ids),1)
                saved=client.edit_program(project['id'],1,operations=[{'op':'set','id':ids[0],'field':'value','value':3}])
                self.assertEqual(client.start(project['id'],2,[20,21]).result(5),[[60,63],False,False])
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.get_project(project['id'])['source'],saved['source'])
            finally:server.shutdown();server.server_close();thread.join(3)

    def test_cli_repl_docs_and_packaged_example(self):
        source='let c=channel(1);await channel_select([send_case(c,42)]);(await channel_select([receive_case(c)])).value'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.chaos';path.write_text(source)
            env=dict(os.environ,PYTHONPATH=str(ROOT))
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=directory,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),42)
                if command=='fmt':self.assertEqual(result.stdout,Framework.format_source(source))
            result=subprocess.run([sys.executable,'-m','chaos','repl'],cwd=directory,env=env,input=source+'\n.quit\n',capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout.strip()),42)
        page=Documentation().page('select');self.assertIn('ChannelCase',page['markdown']);self.assertIn('chaos-token-function',page['html'])
        self.assertEqual(Framework.evaluate((ROOT/'examples/select-workers.chaos').read_text()),[[0,42,False],0])

    def test_case_type_and_host_json_descriptor_do_not_reconstitute_live_value(self):
        result=Framework.evaluate('''let c=channel();let candidate=send_case(c,{privateMessage:"not-in-metadata"});
let encoded=json_stringify(candidate);
let bad=try{channel_select([parse_json(encoded)]);false}catch e{e.code};
[type_of(candidate),candidate,case_info(candidate),parse_json(encoded),bad]''')
        self.assertEqual(result[0],'ChannelCase')
        self.assertNotIn('not-in-metadata',json.dumps(result))
        self.assertEqual(result[1]['$handle'],'ChannelCase')
        self.assertEqual(result[1]['id'],result[2]['id'])
        self.assertEqual(result[1],result[3])
        self.assertEqual(result[4],'argument_error')

if __name__=='__main__':unittest.main()
