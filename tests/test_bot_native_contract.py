"""Bot composition uses the existing runtime and shared static contracts."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Client, Final, Framework, Registry, Settings
from chaos.documentation import Documentation
from chaos.ide_language import analyze
from chaos.language import Evaluator
from chaos.program import format_program, parse_program
from chaos.server import make_server
from chaos.suspension import Scheduler


SOURCE = '''
let worker=agent_from({kind:"agent",name:"worker",config:{provider:"app.double",tools:[]}});
fn dispatch(context){worker(context.input)}
let work=plan([{id:"reply",call:"dispatch"}]);
let answer=await work(21,{dispatch:dispatch});
[answer.reply,agent_info(worker).name,type_of(worker)]
'''


class BotNativeContract(unittest.TestCase):
    def test_real_agent_factory_and_callable_plan_share_source_ir_runtime(self):
        seen=[]
        registry=Registry()
        @registry.provider('app.double')
        def double(request,context):
            context.checkpoint();seen.append(request.input)
            return Final(request.input*2)
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            for index,source in enumerate((SOURCE,format_program(parse_program(SOURCE)))):
                project=app.create_project('bot-'+str(index),source=source,capabilities=[])
                result=app.start(project['id'],project['version'],None,mode='source').result(timeout=5)
                self.assertEqual(result,[42,'worker','Function'])
                self.assertTrue(any(e['type']=='agent.completed' for e in app.events(project['id'])['items']))
        self.assertEqual(seen,[21,21])

    def test_program_alias_shape_and_shadowing_follow_callable_identity(self):
        source='let inspect=program_info;let candidate=current_program();let description=inspect(candidate);description.'
        fields={row['label'] for row in analyze(source)['completions']}
        self.assertEqual(fields,{'kind','entry','files','languageVersions'})
        shadow='fn program_info(value){42};let description=program_info(input)'
        result=analyze(shadow)
        self.assertEqual(result['diagnostics'],[])
        self.assertEqual(next(row['type'] for row in result['symbols'] if row['name']=='description'),'Any')
        declared=analyze('fn program_info(value)->Int{42};let description=program_info(input)')
        self.assertEqual(next(row['type'] for row in declared['symbols'] if row['name']=='description'),'Int')

    def test_native_bot_analysis_and_formatting_never_resume_effects(self):
        source='let session=await session_load("chat");let work=plan([{id:"one",call:"one"}]);let cold=session(input=21,message_id="once");let receipt=await cold;receipt.'
        with patch.object(Evaluator,'run',side_effect=AssertionError('executed source')),patch.object(Scheduler,'_s_resume',side_effect=AssertionError('resumed task')):
            result=analyze(source)
            self.assertEqual({row['label'] for row in result['completions']},{'key','messageId','status','output','revision','replayed'})
            formatted=Framework.format_source(SOURCE)
            self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(SOURCE)['semanticHash'])

    def test_http_device_callable_hint_does_not_open_resource_or_change_project(self):
        source='fn control(device:Device){device('
        with tempfile.TemporaryDirectory() as folder,Framework(settings=Settings(':memory:')) as app:
            project=app.create_project('metadata',source='null',capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                with patch.object(Evaluator,'run',side_effect=AssertionError('opened resource')),patch.object(Scheduler,'_s_resume',side_effect=AssertionError('ran effect')):
                    result=client.analyze(source)
                    self.assertEqual(result,Framework.analyze(source))
                    self.assertEqual([p['name'] for p in result['signatureHelp']['parameters']],['action'])
                    self.assertIn('-> Task',result['signatureHelp']['label'])
                self.assertEqual(app.get_project(project['id'])['version'],project['version'])
                self.assertEqual(app.get_project(project['id'])['capabilities'],[])
            finally:
                server.shutdown();server.server_close();thread.join(3)

    def test_installed_bot_design_topic_has_executable_higher_order_example(self):
        docs=Documentation();page=docs.page('bot-language-design')
        self.assertIn('335',page['markdown'])
        self.assertIn('example=bot-callable-plan',page['markdown'])
        from chaos.ide_agent import LANGUAGE_REFERENCE_TOPICS
        self.assertIn('bot-language-design',LANGUAGE_REFERENCE_TOPICS)
        source='fn worker(context){context.input*2};let work=plan([{id:"reply",call:"worker"}]);let answer=await work(21,{worker:worker});answer.reply'
        self.assertEqual(Framework.evaluate(source),42)


if __name__=='__main__':unittest.main()
