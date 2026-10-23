"""Channel has one source/IR/formatter/SDK/HTTP execution semantics."""
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
from chaos.language import Evaluator, LanguageError, serializable
from chaos.server import make_server
from chaos.program import parse_program, format_program

ROOT = Path(__file__).resolve().parent.parent
SOURCE = '''macro call(action,value){quote{unquote(action)(unquote(value))}}
async fn produce(out:Channel,values:List){
    for value in values{await channel_send(out,call!(fn(x){x*2},value))}
    channel_close(out);null
}
let stream:Channel=channel(1);
let result=async with task_group() as workers{
    workers.spawn(produce(stream,input));await acollect(stream)
};
[result,type_of(stream),channel_info(stream).state]'''

class ChannelSurfaces(unittest.TestCase):
    def test_source_macro_ir_modules_and_formatter_have_identical_results(self):
        expected = [[40, 42], 'Channel', 'closed']
        self.assertEqual(Framework.evaluate(SOURCE, {'input':[20,21]}), expected)
        self.assertEqual(Framework.evaluate(format_program(parse_program(SOURCE)), {'input':[20,21]}), expected)
        formatted = Framework.format_source(SOURCE, line_width=40, indent_size=4)
        self.assertEqual(Framework.format_source(formatted, line_width=40, indent_size=4), formatted)
        self.assertEqual(Framework.evaluate(formatted, {'input':[20,21]}), expected)
        self.assertEqual(Framework.evaluate('import "pipe.chaos" as P;let ch=P.make();await channel_send(ch,42);channel_close(ch);await acollect(ch)', files={'pipe.chaos':'fn make()->Channel{channel(1)};export{make}'}), [42])

    def test_http_structure_edit_does_not_change_old_run(self):
        with tempfile.TemporaryDirectory() as directory, Framework(settings=Settings(':memory:')) as app:
            server = make_server('127.0.0.1', 0, framework=app, state_dir=Path(directory)/'control', update_home=Path(directory)/'updates')
            thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
            try:
                client = Client('http://127.0.0.1:'+str(server.server_port))
                project = client.create_project('channel-surfaces',source=SOURCE,capabilities=[])
                old = client.start(project['id'],1,[20,21])
                self.assertEqual(old.result(5),[[40,42],'Channel','closed'])
                events = client.events(project['id'],run_id=old.id)['items']
                self.assertTrue(any(e['type'].startswith('program.channel.') for e in events))
                self.assertTrue(any(e['type']=='program.task.created' and e['payload'].get('kind')=='channel_send' for e in events))
                ir = client.program(project['id'])['program']
                def targets(value):
                    if isinstance(value,dict):
                        if value.get('kind')=='literal' and value.get('value')==2:yield value['id']
                        for child in value.values():yield from targets(child)
                    elif isinstance(value,list):
                        for child in value:yield from targets(child)
                ids=list(targets(ir['root']));self.assertEqual(len(ids),1)
                saved=client.edit_program(project['id'],1,operations=[{'op':'set','id':ids[0],'field':'value','value':3}])
                self.assertEqual(client.start(project['id'],2,[20,21]).result(5),[[60,63],'Channel','closed'])
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.get_project(project['id'])['source'],saved['source'])
            finally:server.shutdown();server.server_close();thread.join(3)

    def test_cli_repl_installed_documentation_source(self):
        source='let ch:Channel=channel(1);await channel_send(ch,42);channel_close(ch);await acollect(ch)'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.chaos';path.write_text(source)
            env=dict(os.environ,PYTHONPATH=str(ROOT))
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=directory,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),[42])
                if command=='fmt':self.assertEqual(result.stdout,Framework.format_source(source))
            result=subprocess.run([sys.executable,'-m','chaos','repl'],cwd=directory,env=env,input=source+'\n.quit\n',capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout.strip()),[42])
        page=Documentation().page('channels')
        self.assertIn('channel_send',page['markdown']);self.assertIn('chaos-token-type',page['html'])
        self.assertEqual(Framework.evaluate((ROOT/'examples/channel_pipeline.chaos').read_text()),[40,42])

    def test_host_descriptor_is_not_a_live_channel_and_checks_nested_ownership(self):
        seen=[]
        value=Evaluator(tool_handler=lambda name,value:seen.append(value)).run('let ch=channel();tool("inspect",{channel:ch});type_of(ch)')
        self.assertEqual(value,'Channel')
        descriptor=seen[0]['channel'];self.assertIsInstance(descriptor,dict)
        self.assertEqual(descriptor['$handle'],'Channel')
        self.assertNotIn('buffer',descriptor)
        with self.assertRaises(LanguageError):Framework.evaluate('channel_info(input)',{'input':descriptor})
        from chaos.channels import create
        owner=Evaluator();other=Evaluator();owner.suspension.begin();other.suspension.begin()
        try:
            ch=create(owner)
            self.assertEqual(serializable(ch)['$handle'],'Channel')
            with self.assertRaises(LanguageError):other.host_value({'nested':[ch]})
        finally:owner.suspension.finish();other.suspension.finish()

if __name__=='__main__':unittest.main()
