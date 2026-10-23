import contextlib, io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from chaos import cli
from chaos.command_intelligence import ASK_SOURCE
from chaos.project import init_project, main as project_main
from chaos.settings import Settings

ROOT=Path(__file__).resolve().parents[1]

class CommandIntelligenceTests(unittest.TestCase):
    def call(self,args,source=None):
        env={k:v for k,v in os.environ.items() if not k.startswith(('CHAOS_','OPENAI_'))}
        env['PYTHONPATH']=str(ROOT)
        with tempfile.TemporaryDirectory() as d:
            result=subprocess.run([sys.executable,'-m','chaos',*args],input=source,cwd=d,env=env,capture_output=True,text=True,timeout=20)
        return result

    def test_inline_stdin_file_same_output_and_help(self):
        for args,text in [(['-c','fn twice(x){x*2};twice(21)'],None),(['run','-'],'fn twice(x){x*2};twice(21)'),(['-'],'40+2')]:
            r=self.call(args,text);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout),42)
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'sample file.chaos';file.write_text('40+2')
            r=self.call([str(file)]);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout),42)
        r=self.call(['--help']);self.assertEqual(r.returncode,0);self.assertIn('chaos -c',r.stdout);self.assertIn('chaos assist',r.stdout)

    def test_invalid_input_does_not_run_and_stream_same_result(self):
        for args in (['-c','let ='],['run','x.chaos','-c','42'],['run','-c','42','--root','.'],['-c','x'*100001]):
            r=self.call(args);self.assertNotEqual(r.returncode,0)
        r=self.call(['-c','40+2','--stream']);self.assertEqual(r.returncode,0,r.stderr)
        events=[json.loads(line) for line in r.stdout.splitlines()]
        self.assertEqual(events[-1]['type'],'run.result');self.assertEqual(events[-1]['output'],42)

    def test_repl_state_and_legacy_serve_dispatch(self):
        r=self.call(['repl'],'let x=40\nx+2\n.quit\n');self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout.splitlines()[-1]),42)
        before=sys.argv
        with patch.object(cli,'serve_main',return_value=17) as serve:
            self.assertEqual(cli.main([]),17);self.assertEqual(cli.main(['serve','--port','0']),17);self.assertEqual(serve.call_count,2)
        self.assertIs(sys.argv,before)

    def test_repl_help_reuses_ask_without_sending_bindings(self):
        with patch('sys.stdin',io.StringIO('let private_value="local"\n.ask explain functions\nprivate_value\n.quit\n')), patch('chaos.command_intelligence.ask_main',return_value=0) as ask, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.repl(config='private.json'),0)
        ask.assert_called_once_with(['--role','assistant','--topic','getting-started','--config','private.json','--','explain functions'])
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1]),'local')

    def test_repl_question_options_are_data_and_project_empty_skips_entry(self):
        with patch('sys.stdin',io.StringIO('.ask --flag\n40+2\n.quit\n')),patch('chaos.command_intelligence.ask_main',return_value=0) as ask,contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.repl(),0)
        self.assertEqual(ask.call_args.args[0][-2:],['--','--flag'])
        self.assertEqual(json.loads(out.getvalue().splitlines()[-1]),42)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'project';init_project(root);(root/'main.chaos').write_text('throw "no entry"')
            with patch('sys.stdin',io.StringIO('40+2\n.quit\n')),contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(project_main(['repl','--empty','--config',str(root/'.chaos/settings.json')],root=root),0)
            self.assertEqual(json.loads(out.getvalue()),42)

    def test_project_inline_pipe_and_path_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'project';init_project(root);(root/'main.chaos').write_text('throw "do not execute entry"')
            for args,stdin in [(['-c','40+2'],''),(['run','-'],'40+2'),(['-'],'40+2')]:
                with patch('sys.stdin',io.StringIO(stdin)),contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(project_main(args,root=root),0)
                self.assertEqual(json.loads(out.getvalue()),42)
            with contextlib.redirect_stderr(io.StringIO()):self.assertNotEqual(project_main(['../escape.chaos'],root=root),0)

    def test_ask_uses_fixed_source_and_json_input(self):
        from chaos.command_intelligence import ask_main
        prompt='"; throw "not code"; //'
        with patch.object(cli,'main',return_value=0) as run:
            self.assertEqual(ask_main([prompt,'--role','planner','--stream','--config','private.json']),0)
        argv=run.call_args.args[0];self.assertEqual(argv[argv.index('-c')+1],ASK_SOURCE)
        self.assertEqual(json.loads(argv[argv.index('--input-json')+1]),{'request':prompt,'role':'planner'})
        self.assertIn('--stream',argv)

    def test_model_call_uses_existing_runtime_and_project_configuration(self):
        requests=[];configs=[]
        class Client:
            def __init__(self,c):configs.append(c);self.chat=NS(completions=self)
            def create(self,**kw):requests.append(kw);return NS(choices=[NS(message=NS(content='fixture answer',tool_calls=[]))])
            def close(self):pass
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'project';init_project(root)
            config=root/'.chaos/settings.json';settings=Settings(config)
            settings.update({'providers':{'text':{'apiKey':'fixture-only-secret'}},'roles':{'executor':{'model':'fixture-model','autoUpgrade':False}}},1)
            def settings_for(path=None):return Settings(path,client_factory=Client)
            with patch('chaos.settings.Settings',side_effect=settings_for),contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(project_main(['ask','explain functions','--stream'],root=root),0)
            events=[json.loads(line) for line in out.getvalue().splitlines()]
            self.assertEqual(events[-1]['type'],'run.result');self.assertEqual(events[-1]['output'],'fixture answer')
            self.assertTrue(any(e['type']=='route.selected' for e in events))
            self.assertEqual(requests[0]['model'],'fixture-model');self.assertEqual(len(requests),1)
            self.assertNotIn('fixture-only-secret',out.getvalue())

    def test_explicit_config_and_no_key_fail_without_client(self):
        with tempfile.TemporaryDirectory() as d:
            config=Path(d)/'private.json'
            settings=Settings(config);settings.update({'runtime':{'workspace':str(Path(d)/'tasks')}},1)
            with patch('chaos.settings.Settings.client') as client,contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(cli.main(['ask','help','--config',str(config)]),1)
            client.assert_not_called();self.assertIn('error',err.getvalue())

if __name__=='__main__':unittest.main()
