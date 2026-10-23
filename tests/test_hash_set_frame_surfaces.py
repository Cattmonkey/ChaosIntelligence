"""One hashing/Set language across Code, Program, agent callbacks and deployment."""
from contextlib import ExitStack,contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from chaos import Client,Final,Framework,Registry,Settings,ToolCall,compile_program
from chaos.ide_language import analyze
from chaos.language import Evaluator
from chaos.program import parse_program,format_program
from chaos.server import make_server
from chaos.source_format import format_source
from chaos.execution_hash_collections import HASH_COLLECTION_FRAMES
import test_access_frame_surfaces as previous

ROOT=Path(__file__).resolve().parents[1]
COLLECTION_NAMES={cls.__name__ for cls in HASH_COLLECTION_FRAMES}
ALL_NAMES=previous.ALL_NAMES|COLLECTION_NAMES
PREFIX='''let collectionResult=do{
class CollectionKey{var n=0;fn __init__(self,n){self.n=n;null}
 fn __hash__(self){hash(self.n)}
 fn __eq__(self,other){if isinstance(other,CollectionKey){self.n==other.n}else{self.n==other}}}
let first=CollectionKey(1);let duplicate=CollectionKey(1);let other=CollectionKey(2);
let before=hash_map([(first,40),(other,2),(duplicate,42)]);
let after=hash_set(before,other,3);let removed=hash_remove(after,other);
let same=before==hash_map([(CollectionKey(2),2),(CollectionKey(1),42)]);
let values={first,duplicate,other};let part=values&{CollectionKey(2)};
let promoted=set_add({1,2},CollectionKey(3));
[before[duplicate],after[other],hash_get(removed,other,99),same,length(values),
contains(values,CollectionKey(1)),length(values|{CollectionKey(3)}),length(part),
length(values-{CollectionKey(2)}),length(values^{CollectionKey(2)}),values>part,
set_is_disjoint(values,{CollectionKey(4)}),length(set_discard(values,other)),length(promoted),
hash_keys(before)[0]==first,hash(({CollectionKey(1)},))==hash(({1},)),collect({1,*[2,2],3})]
};
'''
COLLECTION_EXPECTED=[42,3,99,True,2,True,3,1,1,1,True,True,1,3,True,True,[1,2,3]]
assert previous.SOURCE.endswith(']\n')
SOURCE=PREFIX+previous.SOURCE[:-2]+',collectionResult]\n'
EXPECTED=previous.EXPECTED+[COLLECTION_EXPECTED]
DOCUMENTED='''class Key {
  var value=0;
  fn __init__(self,value){self.value=value;null}
  fn __hash__(self){hash(self.value)}
  fn __eq__(self,other){isinstance(other,Key) && self.value==other.value}
}
let first=Key(40);
let mapping=hash_map([(first,40),(Key(40),42)]);
let choices={first,Key(40),Key(2)};
[hash_get(mapping,Key(40)),length(choices),length(choices & {Key(2)})]
'''
DOCUMENTED_EXPECTED=[42,2,1]
FUNCTION='''fn(values){class Key{var n=0;fn __init__(s,n){s.n=n;null}fn __hash__(s){hash(s.n)}fn __eq__(s,o){s.n==o.n}}
let m=hash_map([(Key(values[0]),values[0])]);let s={Key(values[0]),Key(values[0])};hash_get(m,Key(values[0]))+values[1]+length(s)-1}'''


@contextmanager
def observe_collection_frames():
    seen={'frames':set(),'steps':{},'phases':{},'live':[],'cursors':set()}
    with previous.observe_access_frames()as old,ExitStack()as stack:
        seen['previous']=old
        for cls in HASH_COLLECTION_FRAMES:
            original=cls.step
            def step(frame,cursor,value,error,_original=original):
                name=type(frame).__name__;seen['frames'].add(name);seen['live'].append(frame)
                seen['cursors'].add(id(cursor));seen['steps'][name]=seen['steps'].get(name,0)+1
                seen['phases'].setdefault(name,set()).add(frame.phase)
                return _original(frame,cursor,value,error)
            stack.enter_context(patch.object(cls,'step',step))
        yield seen


class HashSetFrameSurfaces(unittest.TestCase):
    # Borrow only the fixture helper; inheriting the old TestCase would rerun its IDs.
    run_source=previous.AccessFrameSurfaces.run_source
    def assert_all_frames(self,seen):
        previous.AccessFrameSurfaces.assert_all_frames(self,seen['previous'])
        self.assertEqual(seen['frames'],COLLECTION_NAMES);self.assertEqual(len(seen['frames']),16)
        self.assertEqual(seen['frames']|previous.ALL_NAMES,ALL_NAMES);self.assertEqual(len(ALL_NAMES),90)
        self.assertTrue(all(n>0 for n in seen['steps'].values()))

    def test_source_graph_and_deployment_observe_all_ninety_real_classes(self):
        for mode in ('source','graph'):
            with self.subTest(mode=mode),observe_collection_frames()as seen:
                value,events=self.run_source(SOURCE,mode=mode)
                self.assertEqual(value[:10],previous.EXPECTED);self.assertEqual(value[10],COLLECTION_EXPECTED)
                self.assert_all_frames(seen)
                self.assertEqual(sum(e['type']=='tool.completed'and e['payload'].get('tool')=='text.normalize'for e in events),1)

    def test_both_ir_dialects_and_program_roundtrip_preserve_original_ten_results(self):
        for version in (1,2):
            ir=parse_program(DOCUMENTED,language_version=version);text=format_program(ir)
            self.assertEqual(parse_program(text,language_version=version)['semanticHash'],ir['semanticHash'])
            definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':text},'languageVersions':{'main.chaos':version}}
            with self.subTest(version=version),observe_collection_frames()as seen:
                self.assertEqual(self.run_source('let p=program_from('+json.dumps(definition)+');await p(null)')[0],DOCUMENTED_EXPECTED)
                self.assertTrue({'HashConstructFrame','HashValueFrame','HashFindFrame','SetBuilderAddFrame','SetLiteralEvaluationFrame','SetAlgebraFrame'}<=seen['frames'])
        with observe_collection_frames()as seen:
            self.assertEqual(self.run_source(format_program(parse_program(SOURCE)))[0],EXPECTED)
            self.assert_all_frames(seen)

    def test_code_program_macro_and_compilation_share_actual_collection_steps(self):
        definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':'let build='+FUNCTION+';build([40,2])'},'languageVersions':{'main.chaos':2}}
        sources=['let build=function_from(code_from('+json.dumps(FUNCTION)+'));build([40,2])','let p=program_from('+json.dumps(definition)+');await p(null)',
                 'macro result(get,make,key,value){quote{unquote(get)(unquote(make)([(unquote(key),unquote(value))]),unquote(key))}};result!(hash_get,hash_map,1,42)']
        for source in sources:
            with self.subTest(source=source),observe_collection_frames()as seen:
                self.assertEqual(self.run_source(source)[0],42)
                self.assertTrue({'HashConstructFrame','HashFindFrame','HashResolveFrame'}<=seen['frames'])
        for target in ('interpreter','auto','auto-control','auto-flow'):
            source=FUNCTION.replace('fn(values','fn build(values')+';compile_function(build,target="'+target+'")([40,2])'
            with self.subTest(target=target),observe_collection_frames()as seen:
                self.assertEqual(self.run_source(source)[0],42)
                self.assertIn('SetLiteralEvaluationFrame',seen['frames'])

    def test_real_agent_tool_team_and_bot_callbacks_preserve_context_and_exact_effects(self):
        registry=Registry();effects=[];provider_steps=[]
        @registry.tool('fixture.hash.audit')
        def audit(value,context):context.checkpoint();effects.append(value);return None
        @registry.provider('fixture.hash.provider')
        def provider(request,context):
            context.checkpoint();provider_steps.append((request.input,request.step))
            return ToolCall('work',request.input)if request.step==1 else Final(request.history[-1]['output'])
        team={'kind':'team','name':'hash team','entry':'work','roles':[{'id':'work','parent':None,'capabilities':['fixture.hash.audit'],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':['fixture.hash.audit']}}}]}
        source='''let trace=context_var("trace","root");
class Key{var n=0;fn __init__(s,n){s.n=n;null}fn __hash__(s){hash(s.n)}fn __eq__(s,o){s.n==o.n}}
async fn work(value){with context_bind([{variable:trace,value:"agent"}])as scope{tool("fixture.hash.audit",[value,trace.get()]);hash_get(hash_map([(Key(value),value)]),Key(value))+1}}
agent helper{provider:"fixture.hash.provider",tools:["fixture.hash.audit"]};
let bound=agent_bind(helper,[agent_tool("work",work,capabilities=["fixture.hash.audit"])]);
async fn role(value){with context_bind([{variable:trace,value:"team"}])as scope{tool("fixture.hash.audit",[value,trace.get()]);value+length({Key(value),Key(value)})}}
let crew=team_bind(team_from(TEAM),{work:role});
async fn execute(plan,state){let first=await bound(plan);let second=await crew(first);[first,second,trace.get()]}
fn verify(state){{done:true,context:state.context,output:state.evidence[0]}}
let worker=bot("hash language",fn(state){state.input},execute,verify);(await worker(40)).output'''.replace('TEAM',json.dumps(team))
        with observe_collection_frames()as seen:value,events=self.run_source(source,registry=registry,capabilities=['fixture.hash.audit'])
        self.assertEqual(value,[41,42,'root']);self.assertEqual(effects,[[40,'agent'],[41,'team']])
        self.assertEqual(provider_steps,[(40,1),(40,2)])
        self.assertEqual(sum(e['type']=='agent.language_tool'for e in events),1)
        self.assertEqual(sum(e['type']=='team.role.started'for e in events),1)
        self.assertEqual(sum(e['type']=='tool.completed'for e in events),2)
        self.assertTrue({'HashFindFrame','SetLiteralEvaluationFrame'}<=seen['frames'])
        self.assertIn('BotCommitFrame',seen['previous']['previous']['frames'])

    def test_real_pending_hash_effect_can_complete_or_cancel_and_drain(self):
        for cancelled in (False,True):
            entered=threading.Event();release=threading.Event();ended=threading.Event();calls=[];registry=Registry()
            @registry.provider('fixture.hash.pending')
            def pending(request,context):
                calls.append(request.input);entered.set()
                try:
                    while not release.wait(.005):context.checkpoint()
                    context.checkpoint();return Final(1)
                finally:ended.set()
            source='agent worker{provider:"fixture.hash.pending",tools:[],maxSteps:1};class Key{fn __hash__(s){worker(1)}}async fn work(){hash_values(hash_map([(Key(),42)]))}await work()'
            with self.subTest(cancelled=cancelled),Framework(':memory:',registry=registry,settings=Settings(':memory:'))as app:
                project=app.create_project('pending-hash',source=source,capabilities=[])
                with observe_collection_frames()as seen:
                    run=app.start(project['id'],project['version'],None,mode='source')
                    try:
                        self.assertTrue(entered.wait(4));self.assertTrue(any(type(f).__name__=='InstanceHashFrame'for f in seen['live']))
                        if cancelled:run.cancel();self.assertEqual(run.wait(5)['status'],'cancelled')
                        else:release.set();self.assertEqual(run.result(5),[42])
                        self.assertTrue(ended.is_set())
                    finally:release.set();run.cancel()
                self.assertEqual(calls,[1])

    def test_generators_async_tasks_channels_and_mixed_set_spreads_share_new_frames(self):
        source='''class Key{var n=0;fn __init__(s,n){s.n=n;null}fn __hash__(s){hash(s.n)}fn __eq__(s,o){s.n==o.n}}
fn* source(){yield Key(40);yield Key(40);yield Key(2)}
async fn* asynchronous(){yield Key(40);await sleep(0);yield Key(2)}
let first=set_of(source());let second={value async for value in asynchronous()};
let ch=channel(1);await channel_send(ch,first);let restored=(await channel_receive(ch)).value;channel_close(ch);
[length(first),length(second),first==second,fold(fn(total,key){total+key.n},0,restored),length({*first,Key(2)})]'''
        with observe_collection_frames()as seen:
            self.assertEqual(self.run_source(source)[0],[2,2,True,42,2])
        self.assertTrue({'SetBuilderAddFrame','SetLiteralEvaluationFrame','SetAlgebraFrame'}<=seen['frames'])

    def test_http_sdk_and_cli_preserve_exact_source_and_eleven_result_fields(self):
        with Framework(':memory:',settings=Settings(':memory:'))as app,tempfile.TemporaryDirectory()as folder:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_address[1]));project=client.create_project('hash-http',source=SOURCE,capabilities=['text.normalize'])
                with observe_collection_frames()as seen:
                    self.assertEqual(client.start(project['id'],project['version'],None,mode='graph').result(20),EXPECTED)
                self.assert_all_frames(seen);self.assertEqual(client.get_project(project['id'])['source'],SOURCE)
                path=Path(folder)/'main.chaos';path.write_text(SOURCE)
                env={k:v for k,v in os.environ.items()if not any(word in k.upper()for word in ('MODEL','PROVIDER','KEY','TOKEN'))}
                env.update(CHAOS_CONFIG=str(Path(folder)/'settings.json'),PYTHONPATH=str(ROOT))
                for command in ('check','ir','fmt','run'):
                    result=subprocess.run([sys.executable,'-B','-m','chaos',command,str(path)],cwd=folder,env=env,capture_output=True,text=True,timeout=20)
                    self.assertEqual(result.returncode,0,result.stderr)
                    if command=='run':self.assertEqual(json.loads(result.stdout),EXPECTED)
                    if command in ('fmt','ir'):
                        semantic=parse_program(result.stdout)['semanticHash']if command=='fmt'else json.loads(result.stdout)['semanticHash']
                        self.assertEqual(semantic,parse_program(SOURCE)['semanticHash'])
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_editor_analysis_formatting_and_literal_example_are_inert_and_executable(self):
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('authoring invoked hash protocol')):
            report=analyze(DOCUMENTED);formatted=format_source(DOCUMENTED);compiled=compile_program(DOCUMENTED)
        self.assertEqual(report['diagnostics'],[]);self.assertTrue(compiled)
        self.assertEqual(format_source(formatted),formatted)
        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(DOCUMENTED)['semanticHash'])
        with observe_collection_frames()as seen:self.assertEqual(self.run_source(DOCUMENTED)[0],DOCUMENTED_EXPECTED)
        self.assertTrue({'HashConstructFrame','HashFindFrame','SetLiteralEvaluationFrame'}<=seen['frames'])


if __name__=='__main__':unittest.main()
