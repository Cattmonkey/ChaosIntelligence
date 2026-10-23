"""Native Bot contracts stay shared across source, tools and active runs."""
import contextlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Client, Final, Framework, Registry, Settings, ToolCall, compile_program
from chaos.code_presentation import render_code
from chaos.documentation import Documentation
from chaos.ide_language import analyze
from chaos.language import Evaluator, LanguageError, Native, Parser
from chaos.modules import ProgramBundle, STDLIB
from chaos.native_specs import SHAPES, all_specs, call_spec_for, member_specs_for, spec_for
from chaos.program import decode_program, format_program, parse_program, plain
from chaos.server import make_server
from chaos.source_format import format_source
from chaos.suspension import Scheduler

ROOT=Path(__file__).resolve().parents[1]
NAMES=('bot','bot_info','bot_context','bot_reconcile')
BASIC=(ROOT/'examples/native-bot.chaos').read_text()
EXPECTED={'status':'achieved','output':[2,4],'goal':{'objective':'two verified steps'},'context':{'last':4},'type':'Bot'}
SETUP='fn planner(s){s.input};fn execute(p,s){p};fn verify(s){{done:true,context:s.context,output:s.evidence}};let worker=bot("goal",planner,execute,verify,context={stage:"draft"});'


def visit(value):
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from visit(child)
    elif isinstance(value,list):
        for child in value:yield from visit(child)


class BotSurfaces(unittest.TestCase):
    def run_source(self,source=BASIC,*,input=None,mode='source',registry=None,capabilities=None):
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            project=app.create_project('native-bot',source=source,capabilities=[]if capabilities is None else capabilities)
            run=app.start(project['id'],project['version'],input,mode=mode)
            value=run.result(8);events=app.events(project['id'],run_id=run.id,limit=300)['items']
        return value,events

    def test_exact_shared_signatures_counts_aliases_and_nonmember_receiver(self):
        env=Evaluator().env.values
        signatures={'bot':['goal','planner','execute','verify','context','max_iterations','mailbox','role'],'bot_info':['bot'],'bot_context':['bot'],'bot_reconcile':['bot','context','evidence']}
        for name in NAMES:
            with self.subTest(name=name):
                runtime=list(inspect.signature(env[name].call).parameters.values());spec=spec_for(name)
                self.assertEqual([p.name for p in runtime],signatures[name]);self.assertEqual([p.name for p in spec.parameters],signatures[name])
                self.assertEqual([p.default is not p.empty for p in runtime],[p.optional for p in spec.parameters]);self.assertEqual(env[name].spec_id,name)
                self.assertEqual([p['name']for p in analyze(name+'(')['signatureHelp']['parameters']],signatures[name])
        receiver=call_spec_for('Bot');self.assertEqual(receiver.result_type,'Task');self.assertEqual(receiver.awaited_type,'Record');self.assertEqual(receiver.awaited_shape,'BotResult')
        self.assertEqual([p.name for p in receiver.parameters],['input']);self.assertNotIn('__call__',member_specs_for('Bot'))
        self.assertEqual(len([x for x in env.values()if type(x)is Native]),335);self.assertEqual(len(all_specs()),381);self.assertEqual(len(STDLIB),21)
        self.assertEqual(Framework.evaluate('import "std/bot" as B;['+','.join('B.'+name+'=='+name for name in NAMES)+']'),[True]*4)

    def test_native_alias_module_and_await_shape_analysis_are_execution_free(self):
        base='import "std/bot" as B;let make=B.bot;'+SETUP.replace('bot("goal"','make("goal"')+'let invoke_bot=worker;let cold=invoke_bot(input=21);let answer=await cold;let info=B.bot_info(worker);let state=B.bot_context(worker);'
        source=base+'answer.done'
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis ran source')),patch.object(Scheduler,'_s_resume',side_effect=AssertionError('analysis resumed task')):
            result=analyze(source,position=len(base)+len('answer.'));self.assertEqual(result['diagnostics'],[])
            symbols={row['name']:row for row in result['symbols']};self.assertEqual([symbols[name]['type']for name in ('worker','cold','answer','info','state')],['Bot','Task','Record','Record','Record'])
            self.assertEqual({row['label']for row in result['completions']},{'done','status','state','output'})
            self.assertEqual({row['label']for row in analyze(base+'state.goal',position=len(base)+len('state.'))['completions']},{field.name for field in SHAPES['BotContext']})
            self.assertEqual({row['label']for row in analyze(base+'info.id',position=len(base)+len('info.'))['completions']},{field.name for field in SHAPES['BotInfo']})
            self.assertFalse(analyze(SETUP+'worker.')['completions'])
            help=analyze('fn use(value:Bot){value(')['signatureHelp'];self.assertEqual([p['name']for p in help['parameters']],['input']);self.assertIn('Task',help['label'])
            self.assertEqual(analyze(SETUP+'fn accept(f:Function){null};accept(worker)')['diagnostics'],[])
            self.assertEqual(analyze('fn bot(value)->Int{42};let result=bot(0)')['diagnostics'],[])
            fake=analyze('class WorkerBot{fn __call__(self,value:Int)->Int{value}};let result=WorkerBot()(42)');self.assertEqual(fake['diagnostics'],[])
            self.assertEqual(next(row['type']for row in fake['symbols']if row['name']=='result'),'Int')
            rejected=analyze('class Bot{fn __call__(self,value:Int)->Int{value}};let result=Bot()(42)');self.assertTrue(any(d['code']=='syntax_error'for d in rejected['diagnostics']))
            self.assertEqual({row['label']for row in analyze(SETUP+'let answer=await worker();answer.state.')['completions']},{field.name for field in SHAPES['BotContext']})

    def test_real_source_graph_and_formatted_ir_preserve_long_goal_and_context(self):
        ir=parse_program(BASIC);formatted=format_program(ir)
        self.assertEqual(plain(decode_program(ir)),plain(Parser(formatted).program()))
        self.assertEqual(parse_program(formatted)['semanticHash'],ir['semanticHash'])
        for source in (BASIC,formatted,format_source(BASIC,line_width=56)):
            for mode in ('source','graph'):
                with self.subTest(mode=mode,source=source):
                    result,events=self.run_source(source,mode=mode,input={'initial':'data'})
                    self.assertEqual(result,EXPECTED)
                    self.assertTrue(any(event['type']=='bot.scope.opened'for event in events));self.assertTrue(any(event['type']=='bot.scope.closed'for event in events))
                    self.assertFalse(any('reasoning_content'in json.dumps(event['payload'])for event in events))

    def test_generated_code_macro_program_and_compiler_keep_original_bot_value(self):
        function='fn(value){'+SETUP+'worker(value)}'
        sources=['let build=function_from(code_from('+json.dumps(function)+'));let result=await build(42);result.output',
                 'macro invoke(f,x){quote{unquote(f)(unquote(x))}};'+SETUP+'let result=await invoke!(worker,42);result.output']
        definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':SETUP+'let answer=await worker(input);answer.output'},'languageVersions':{'main.chaos':2}}
        sources.append('let work=program_from('+json.dumps(definition)+');await work(42)')
        for target in ('interpreter','auto','auto-flow'):
            sources.append('fn build(value){'+SETUP+'worker(value)};let build_compiled=compile_function(build,target="'+target+'");let result=await build_compiled(42);result.output')
        for source in sources:
            with self.subTest(source=source):self.assertEqual(self.run_source(source)[0],[42])
        report=compile_program(BASIC);self.assertIn('call',report['summary']['effects']);self.assertIn('unknown',report['summary']['effects'])
        self.assertIn('chaos-token-function',render_code(BASIC,'chaos'))

    def test_cold_callback_copy_and_shared_alias_state_without_host_revival(self):
        evaluator=Evaluator();copied=[]
        def inspect_copy(value):
            from chaos.bot_values import Bot,owned
            self.assertIs(type(value),Bot);self.assertIs(owned(value,evaluator).state,value.state)
            context_native=evaluator.env.get('bot_context')
            first=context_native.call(value);self.assertEqual(first['context']['items'],[1])
            first['context']['items'][0]=99
            second=context_native.call(value)
            self.assertIsNot(first['context'],second['context']);self.assertIsNot(first['context']['items'],value.state.context['items'])
            self.assertEqual(value.state.context['items'],[1]);self.assertEqual(second['context']['items'],[1])
            copied.append(first['context']['items']);return True
        evaluator.env.bind('inspect_copy',Native('inspect_copy',inspect_copy))
        source='var calls=0;fn p(s){calls=calls+1;s.input};fn e(p,s){p};fn v(s){{done:true,context:s.context,output:s.evidence}};let worker=bot({task:"goal"},p,e,v,context={items:[1]});let alias=worker;let inspected=inspect_copy(worker);let cold=alias(42);[calls,type_of(cold),bot_context(worker).context.items,bot_info(worker).busy,worker==alias]'
        self.assertEqual(evaluator.run(source),[0,'Task',[1],False,True]);self.assertEqual(copied,[[99]])
        self.assertEqual(Framework.evaluate(SETUP+'try{await worker(42)}catch e{e.code}'),'capability_error')
        value,events=self.run_source(SETUP+'let alias=worker;let result=await alias(42);[result.output,bot_info(worker).status,bot_info(alias).id==bot_info(worker).id]')
        self.assertEqual(value,[[42],'achieved',True])
        self.assertEqual(analyze(SETUP+'let frozen=parse_json(json_stringify(worker));let inspected=frozen(42)')['diagnostics'],[])

    def test_sdk_http_and_cli_use_shared_bot_metadata_and_original_run(self):
        with tempfile.TemporaryDirectory()as directory,Framework(settings=Settings(':memory:'))as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(directory)/'control',update_home=Path(directory)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));project=client.create_project('bot-http',source=BASIC,capabilities=[])
                self.assertEqual(client.start(project['id'],project['version'],None,mode='source').result(8),EXPECTED)
                self.assertEqual(client.analyze(SETUP+'let result=await worker();result.'),Framework.analyze(SETUP+'let result=await worker();result.'))
                path=Path(directory)/'main.chaos';path.write_text(BASIC)
                for command in ('check','fmt','ir','run'):
                    process=subprocess.run([sys.executable,'-B','-m','chaos',command,str(path)],cwd=directory,env=dict(os.environ,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=15)
                    self.assertEqual(process.returncode,0,process.stderr)
                    if command=='run':self.assertEqual(json.loads(process.stdout),EXPECTED)
                self.assertEqual(path.read_text(),BASIC)
            finally:server.shutdown();server.server_close();thread.join(3)

    def test_guidance_during_cognition_applies_real_revision_without_old_action(self):
        entered=threading.Event();release=threading.Event();plans=[];actions=[];registry=Registry()
        @registry.provider('fixture.cognition')
        def think(request,context):
            context.checkpoint();value=request.input;plans.append(value)
            if len(plans)==1:
                entered.set()
                while not release.wait(.01):context.checkpoint()
            return Final('old'if not value['guidance']else value['guidance'][-1]['input']['text'])
        @registry.tool('fixture.act')
        def act(value,context):context.checkpoint();actions.append(value);return value
        source='agent thinker{provider:"fixture.cognition",tools:[]};fn plan(s){thinker(s)};fn execute(p,s){tool("fixture.act",p)};fn verify(s){{done:true,context:s.context,output:s.evidence}};let worker=bot("long objective",plan,execute,verify,context={kept:true});let answer=await worker();[answer,bot_context(worker)]'
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            project=app.create_project('revision',source=source,capabilities=['fixture.act']);run=app.start(project['id'],project['version'],None,mode='source')
            try:
                self.assertTrue(entered.wait(3));receipt=run.guide({'text':'new direction'},message_id='new');self.assertEqual(receipt['status'],'queued');release.set();answer,state=run.result(8)
            finally:release.set()
            self.assertEqual(actions,['new direction']);self.assertGreaterEqual(len(plans),2);self.assertTrue(answer['done']);self.assertEqual(answer['status'],'achieved');self.assertEqual(state['goal'],'long objective');self.assertEqual(state['context'],{'kept':True});self.assertEqual(state['evidence'],['new direction']);self.assertEqual(state['guidance'][0]['messageId'],'new')
            events=app.events(project['id'],run_id=run.id,limit=300)['items'];applied=[e for e in events if e['type']=='bot.context.applied'];self.assertEqual(len(applied),1);self.assertEqual(applied[0]['payload']['messageId'],'new');self.assertEqual(applied[0]['payload']['revision'],receipt['revision'])
            admissions=[e['payload']for e in events if e['type']=='bot.action.admitted'];self.assertTrue(admissions);self.assertTrue(all({'scopeId','decisionId','revision','actionId','kind'}<=set(e)for e in admissions))
            completed=[e['payload']for e in events if e['type']=='bot.action.completed'];self.assertTrue(completed);self.assertTrue(all(e['status']in ('completed','uncertain')for e in completed))

    def test_real_agent_tool_and_team_composition_use_native_bot_callbacks(self):
        registry=Registry();actions=[]
        @registry.tool('fixture.act')
        def act(value,context):context.checkpoint();actions.append(value);return value+1
        @registry.provider('fixture.agent')
        def provider(request,context):
            context.checkpoint();return ToolCall('act',request.input)if request.step==1 else Final(request.history[-1]['output'])
        source='fn action(value){tool("fixture.act",value)};agent helper{provider:"fixture.agent",tools:["fixture.act"]};let bound=agent_bind(helper,[agent_tool("act",action,capabilities=["fixture.act"])]);fn plan(s){41};async fn execute(p,s){await bound(p)};fn verify(s){{done:true,context:s.context,output:s.evidence}};let worker=bot("goal",plan,execute,verify);let result=await worker();result.output'
        result,events=self.run_source(source,registry=registry,capabilities=['fixture.act']);self.assertEqual(result,[42]);self.assertEqual(actions,[41]);self.assertTrue(any(e['type']=='agent.language_tool'for e in events));self.assertTrue(any(e['type']=='agent.completed'for e in events));self.assertTrue(any(e['type']=='bot.action.admitted'for e in events))
        language_event=next(e for e in events if e['type']=='agent.language_tool');finished_event=next(e for e in events if e['type']=='agent.completed')
        self.assertEqual(language_event['payload']['agent'],'helper');self.assertEqual(language_event['payload']['tool'],'act')
        self.assertIsInstance(language_event['payload']['cursor'],int);self.assertGreater(language_event['payload']['cursor'],0)
        self.assertEqual(language_event['payload']['contract']['name'],'act');self.assertEqual(language_event['payload']['contract']['capabilities'],['fixture.act'])
        self.assertEqual(finished_event['payload']['agent'],'helper');self.assertEqual(finished_event['payload']['languageTools'],['act'])
        self.assertEqual(language_event['payload']['nodeId'],finished_event['payload']['nodeId']);self.assertTrue(language_event['payload']['nodeId']);self.assertLess(language_event['id'],finished_event['id'])
        team={'kind':'team','name':'bot crew','entry':'work','roles':[{'id':'work','parent':None,'capabilities':[],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':[]}}}]}
        source='fn work(value){value+1};let crew=team_bind(team_from('+json.dumps(team)+'),{work:work});fn plan(s){41};async fn execute(p,s){await crew(p)};fn verify(s){{done:true,context:s.context,output:s.evidence}};let worker=bot("goal",plan,execute,verify);let result=await worker();result.output'
        result,events=self.run_source(source);self.assertEqual(result,[42]);self.assertTrue(any(e['type']=='team.role.completed'for e in events))

    def test_real_stdio_lsp_projects_shared_bot_call_without_execution(self):
        from tests.test_lsp_integration import StdioClient
        from chaos.lsp_workspace import TextMap
        source=SETUP+'let cold=worker(input=42);let result=await cold;result'
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);path=root/'main.chaos';path.write_text(source);client=StdioClient(root)
            try:
                client.send('textDocument/didOpen',{'textDocument':{'uri':path.as_uri(),'version':1,'languageId':'chaos','text':source}})
                offset=source.index('worker(input=')+len('worker(input=')
                position=TextMap(source).position(len(source[:offset].encode('utf-16-le'))//2)
                result=client.request('textDocument/signatureHelp',{'textDocument':{'uri':path.as_uri()},'position':position})
                self.assertIn('input',result['signatures'][0]['label']);self.assertIn('Task',result['signatures'][0]['label'])
                self.assertTrue(client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':path.as_uri()}})['data'])
            finally:client.close()
            self.assertEqual(path.read_text(),source)

    def test_registered_docs_have_real_contracts_and_bounded_nonexecuting_fences(self):
        docs=Documentation();page=docs.page('native-bots');self.assertIn('needs_reconciliation',page['markdown']);self.assertIn('不自动重放',page['markdown']);self.assertIn('原生 Bot',page['title'])
        self.assertEqual({field.name for field in SHAPES['BotResult']},{'done','status','state','output'})
        self.assertEqual({field.name for field in SHAPES['BotInfo']},{'id','status','busy','revision','iteration','maxIterations','needsReconciliation'})
        self.assertEqual(len(docs.entries()),101)
        self.assertIn('native-bots',__import__('chaos.ide_agent',fromlist=['LANGUAGE_REFERENCE_TOPICS']).LANGUAGE_REFERENCE_TOPICS)


if __name__=='__main__':unittest.main()
