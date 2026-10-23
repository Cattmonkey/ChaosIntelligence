"""Object access uses actual execution state on every public language surface."""
from contextlib import ExitStack, contextmanager
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
from chaos.documentation import Documentation
from chaos.execution_access import ACCESS_FRAMES
from chaos.ide_language import analyze
from chaos.language import Evaluator, Native
from chaos.modules import STDLIB
from chaos.native_jit import BackendUnavailable
from chaos.native_specs import all_specs
from chaos.program import parse_program, format_program
from chaos.server import make_server
from chaos.source_format import format_source
import test_operator_frame_surfaces as previous

ROOT = Path(__file__).resolve().parents[1]
ACCESS_NAMES = {cls.__name__ for cls in ACCESS_FRAMES}
ALL_NAMES = previous.ALL_NAMES | ACCESS_NAMES
PREFIX = '''let accessResult=do{
var accessEvents=[];
class AccessField{
 fn __get__(self,object,owner){if object==null{self}else{accessEvents=append(accessEvents,["descriptor-get"]);default_getattr(object,"stored")}}
 fn __set__(self,object,value){accessEvents=append(accessEvents,["descriptor-set",value]);default_setattr(object,"stored",value);null}
 fn __delete__(self,object){accessEvents=append(accessEvents,["descriptor-delete"]);default_delattr(object,"stored");null}
}
class AccessIndex{fn __index__(self){accessEvents=append(accessEvents,["integer"]);1}}
class AccessBox{
 var stored:Int=0;let field=AccessField();
 fn __getattribute__(self,name){if name=="answer"{default_getattr(self,"field")+2}else{default_getattr(self,name)}}
 fn __getattr__(self,name){accessEvents=append(accessEvents,["fallback",name]);7}
 fn __setattr__(self,name,value){default_setattr(self,name,value);null}
 fn __delattr__(self,name){default_delattr(self,name);null}
 fn* __dir__(self){yield "z";yield "answer";yield "answer"}
 fn __getitem__(self,key){accessEvents=append(accessEvents,["item-get",type_of(key)]);if type_of(key)=="Tuple"{[key[0].start,key[0].stop,key[1]]}else{default_getattr(self,key)}}
 fn __setitem__(self,key,value){accessEvents=append(accessEvents,["item-set",key,value]);default_setattr(self,key,value);null}
 fn __delitem__(self,key){accessEvents=append(accessEvents,["item-delete",key]);default_delattr(self,key);null}
}
class AccessMeta(Type){fn __getattr__(cls,name){accessEvents=append(accessEvents,["meta",name]);42}}
class AccessClass(metaclass=AccessMeta){}
let box=AccessBox();box.field=40;let first=box.answer;
box["stored"]+=2;box.stored+=0;let item=box["stored"];
let key=box[1:3,2];let indexed=[40,42][AccessIndex()];let legacy=[40,42].[1];
let converted=index(AccessIndex());let spec=slice_key(AccessIndex());let normalized=slice_indices(spec,3);
let names=dir(box);let missing=box.missing;let meta=AccessClass.answer;
delitem(box,"stored");let reset=box.field;default_setattr(box,"stored",reset);delattr(box,"field");
{values:[first,item,key,indexed,legacy,converted,normalized,names,missing,meta,reset],events:accessEvents}
};
'''
ACCESS_EXPECTED = {
    'values': [42, 42, [1, 3, 2], 42, 42, 1, [1, 3, 1], ['answer', 'answer', 'z'], 7, 42, 0],
    'events': [['descriptor-set', 40], ['descriptor-get'], ['item-get', 'String'],
               ['item-set', 'stored', 42], ['item-get', 'String'], ['item-get', 'Tuple'],
               ['integer'], ['integer'], ['integer'], ['fallback', 'missing'],
               ['meta', 'answer'], ['item-delete', 'stored'], ['descriptor-get'], ['descriptor-delete']],
}
# The original deployment source and its nine result expressions stay intact;
# only a pure tenth result is appended. New definitions are block local.
assert previous.SOURCE.endswith(']\n')
SOURCE = PREFIX + previous.SOURCE[:-2] + ',accessResult]\n'
EXPECTED = previous.EXPECTED + [ACCESS_EXPECTED]
DOCUMENTED = '''class Position { fn __index__(self) -> Int { 1 } }
class Component {
  var value:Int = 40;
  fn __getattr__(self, name) { if name == "answer" { self.value + 2 } else { throw AttributeError(name) } }
  fn __getitem__(self, key) { default_getattr(self, key) }
  fn __setitem__(self, key, item) { default_setattr(self, key, item); null }
}
let component = Component();
component["value"] += 2;
[component.answer, component["value"], [20, 42][Position()]]
'''
DOCUMENTED_EXPECTED = [44, 42, 42]
FUNCTION = 'fn(values){class Position{fn __index__(self){1}}class Box{var value=0;fn __getitem__(self,key){default_getattr(self,key)}fn __setitem__(self,key,item){default_setattr(self,key,item);null}}let box=Box();box["value"]=values[0];box["value"]+=values[Position()];box["value"]}'


@contextmanager
def observe_access_frames():
    seen = {'frames': set(), 'steps': {}, 'phases': {}, 'cursors': set(), 'live': []}
    with previous.observe_operator_frames() as old, ExitStack() as changes:
        seen['previous'] = old
        for cls in ACCESS_FRAMES:
            original = cls.step
            def observed(frame, cursor, value, error, original=original):
                name = type(frame).__name__
                seen['frames'].add(name); seen['cursors'].add(id(cursor)); seen['live'].append(frame)
                seen['steps'][name] = seen['steps'].get(name, 0) + 1
                seen['phases'].setdefault(name, set()).add(frame.phase)
                return original(frame, cursor, value, error)
            changes.enter_context(patch.object(cls, 'step', observed))
        yield seen


class AccessFrameSurfaces(unittest.TestCase):
    def run_source(self, source, *, mode='source', registry=None, capabilities=None):
        with Framework(':memory:', settings=Settings(':memory:'), registry=registry) as app:
            project = app.create_project('access-frames', source=source,
                capabilities=['text.normalize'] if capabilities is None else capabilities)
            run = app.start(project['id'], project['version'], None, mode=mode)
            result = run.result(20); events = []; after = 0
            while True:
                page = app.events(project['id'], run_id=run.id, after=after, limit=300)
                events.extend(page['items'])
                if page['nextCursor'] is None: break
                after = page['nextCursor']
            return result, events

    def assert_all_frames(self, seen):
        old = seen['previous']
        self.assertEqual(old['frames'], previous.ALL_NAMES)
        self.assertEqual(len(old['frames']), 59)
        self.assertTrue(all(n > 0 for n in old['steps'].values()))
        self.assertGreater(len(old['cursors']), 1)
        self.assertTrue({'__iadd__','__add__','__radd__','__eq__','__lt__','__len__','__contains__','__bool__','__neg__'} <= old['methods'])
        self.assertEqual(seen['frames'], ACCESS_NAMES)
        self.assertEqual(len(seen['frames']), 15)
        self.assertEqual(len(old['frames'] | seen['frames']), 74)
        self.assertEqual(old['frames'] | seen['frames'], ALL_NAMES)
        self.assertTrue(all(n > 0 for n in seen['steps'].values()))

    def test_source_graph_and_deployment_observe_all_seventy_four_real_step_classes(self):
        for mode in ('source', 'graph'):
            with self.subTest(mode=mode), observe_access_frames() as seen:
                result, events = self.run_source(SOURCE, mode=mode)
                self.assertEqual(result[:9], previous.EXPECTED)
                self.assertEqual(result[9], ACCESS_EXPECTED)
                self.assert_all_frames(seen)
                kinds = ('bot.scope.opened','bot.scope.closed','bot.result.committed','bot.decision.created')
                self.assertEqual({kind:sum(e['type']==kind for e in events) for kind in kinds}, dict(zip(kinds,(1,1,1,3))))
                self.assertEqual(sum(e['type']=='tool.completed' and e['payload'].get('tool')=='text.normalize' for e in events), 1)

    def test_both_ir_dialects_and_program_roundtrip_preserve_original_nine_results(self):
        for version in (1, 2):
            # The complete dialect-neutral attribute fixture is exercised in
            # both versions; modern item syntax remains explicitly dialect 2.
            text = DOCUMENTED if version == 2 else DOCUMENTED.replace('component["value"]', 'component.["value"]').replace('[20, 42][Position()]', '[20, 42].[index(Position())]')
            expected = DOCUMENTED_EXPECTED if version == 2 else [44, 42, 42]
            ir = parse_program(text, language_version=version); formatted = format_program(ir)
            self.assertEqual(parse_program(formatted, language_version=version)['semanticHash'], ir['semanticHash'])
            definition = {'kind':'program','entry':'main.chaos','files':{'main.chaos':formatted},'languageVersions':{'main.chaos':version}}
            with self.subTest(version=version), observe_access_frames() as seen:
                self.assertEqual(self.run_source('let p=program_from('+json.dumps(definition)+');await p(null)')[0], expected)
                required = {'AttributeReadFrame','AttributeWriteFrame','AccessResultFrame',
                    'ScopedGetMemberFrame','ScopedSetMemberFrame','IntegerIndexFrame',
                    'ObjectAccessEvaluationFrame'}
                required |= {'LegacyIndexFrame'} if version == 1 else {'ItemOperationFrame','ItemInvokeFrame'}
                self.assertTrue(required <= seen['frames'], sorted(required-seen['frames']))
        with observe_access_frames() as seen:
            ir = parse_program(SOURCE, language_version=2)
            self.assertEqual(self.run_source(format_program(ir))[0], EXPECTED)
            self.assert_all_frames(seen)

    def test_code_program_macros_and_compilation_use_real_access_frames(self):
        definition = {'kind':'program','entry':'main.chaos','files':{'main.chaos':'let build='+FUNCTION+';build([40,2])'},'languageVersions':{'main.chaos':2}}
        sources = ['let build=function_from(code_from('+json.dumps(FUNCTION)+'));build([40,2])',
            'let p=program_from('+json.dumps(definition)+');await p(null)',
            'macro read(object,key){quote{unquote(object)[unquote(key)]}};class Box{fn __getitem__(self,key){40+key}};read!(Box(),2)']
        for source in sources:
            with self.subTest(source=source), observe_access_frames() as seen:
                self.assertEqual(self.run_source(source)[0], 42)
                self.assertTrue({'ObjectAccessEvaluationFrame','ItemOperationFrame','ItemInvokeFrame'} <= seen['frames'])
        for target in ('interpreter','auto','auto-control','auto-flow','portable'):
            chosen = 'auto-flow' if target == 'portable' else target
            source = FUNCTION.replace('fn(values','fn build(values')+';let compiled=compile_function(build,target="'+chosen+'");compiled([40,2])'
            with self.subTest(target=target), observe_access_frames() as seen:
                if target == 'portable':
                    with patch('chaos.native_flow.compile_flow', side_effect=BackendUnavailable('fixture portable')):
                        self.assertEqual(self.run_source(source)[0], 42)
                else: self.assertEqual(self.run_source(source)[0], 42)
                self.assertTrue({'ItemOperationFrame','ItemInvokeFrame','IntegerIndexFrame'} <= seen['frames'])

    def test_real_agent_tool_team_bot_callbacks_keep_context_and_exactly_once_effects(self):
        registry = Registry(); effects = []; provider_steps = []
        @registry.tool('fixture.access.audit')
        def audit(value, context): context.checkpoint(); effects.append(value); return None
        @registry.provider('fixture.access.provider')
        def provider(request, context):
            context.checkpoint(); provider_steps.append((request.input, request.step))
            return ToolCall('work', request.input) if request.step == 1 else Final(request.history[-1]['output'])
        team = {'kind':'team','name':'access team','entry':'work','roles':[{'id':'work','parent':None,'capabilities':['fixture.access.audit'],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':['fixture.access.audit']}}}]}
        source = '''let trace=context_var("trace","root");
class Component{var value=0;fn __init__(self,value){self.value=value;null}
 fn __getattr__(self,name){tool("fixture.access.audit",["attribute",self.value,trace.get()]);self.value+1}
 fn __getitem__(self,key){tool("fixture.access.audit",["item",self.value,key,trace.get()]);self.value+key}}
async fn work(value:Int){with context_bind([{variable:trace,value:"agent"}])as scope{Component(value).answer}}
agent helper{provider:"fixture.access.provider",tools:["fixture.access.audit"]};
let bound=agent_bind(helper,[agent_tool("work",work,input_type="Int",output_type="Int",capabilities=["fixture.access.audit"])]);
async fn role(value){with context_bind([{variable:trace,value:"team"}])as scope{Component(value)[21]}}
let crew=team_bind(team_from(TEAM),{work:role});
async fn execute(plan,state){let first=await bound(plan);let second=await crew(first);[first,second,trace.get()]}
fn verify(state){{done:true,context:state.context,output:state.evidence[0]}}
let worker=bot("native access",fn(state){state.input},execute,verify);(await worker(20)).output'''.replace('TEAM', json.dumps(team))
        with observe_access_frames() as seen:
            result, events = self.run_source(source, registry=registry, capabilities=['fixture.access.audit'])
        self.assertEqual(result, [21,42,'root'])
        self.assertEqual(effects, [['attribute',20,'agent'],['item',21,21,'team']])
        self.assertEqual(provider_steps, [(20,1),(20,2)])
        self.assertEqual(sum(e['type']=='agent.language_tool' for e in events), 1)
        self.assertEqual(sum(e['type']=='team.role.started' for e in events), 1)
        self.assertEqual(sum(e['type']=='tool.completed' for e in events), 2)
        self.assertTrue({'AttributeReadFrame','ItemInvokeFrame'} <= seen['frames'])
        self.assertIn('BotCommitFrame', seen['previous']['frames'])

    def test_actual_pending_access_frame_survives_real_effect_and_cancellation_drains(self):
        for cancelled in (False, True):
            entered = threading.Event(); release = threading.Event(); ended = threading.Event(); calls = []; registry = Registry()
            @registry.provider('fixture.access.pending')
            def pending(request, context):
                calls.append(request.input); entered.set()
                try:
                    while not release.wait(.005): context.checkpoint()
                    context.checkpoint(); return Final(request.input+1)
                finally: ended.set()
            source = 'agent worker{provider:"fixture.access.pending",tools:[],maxSteps:1};class Component{fn __getattr__(self,name){worker(41)}};async fn work(){Component().answer};await work()'
            with self.subTest(cancelled=cancelled), Framework(':memory:', registry=registry, settings=Settings(':memory:')) as app:
                project = app.create_project('pending-access', source=source, capabilities=[])
                with observe_access_frames() as seen:
                    run = app.start(project['id'], project['version'], None, mode='source')
                    try:
                        self.assertTrue(entered.wait(4))
                        live = [frame for frame in seen['live'] if type(frame).__name__ == 'AttributeReadFrame']
                        self.assertTrue(live)
                        self.assertIsNotNone(live[-1].target)
                        self.assertEqual(live[-1].label, 'answer')
                        if cancelled:
                            run.cancel(); self.assertEqual(run.wait(5)['status'], 'cancelled'); self.assertTrue(ended.is_set())
                        else:
                            release.set(); self.assertEqual(run.result(5), 42); self.assertTrue(ended.is_set())
                    finally: release.set(); run.cancel()
                self.assertEqual(calls, [41])
                self.assertIn('AttributeReadFrame', seen['frames'])

    def test_cold_tasks_generators_and_synchronous_write_contracts(self):
        cases = [
            ('var calls=0;class C{async fn __getattr__(self,name){calls+=1;42}fn* __getitem__(self,key){yield key}}let pending=C().answer;let before=calls;[before,await pending,calls,collect(C()[42])]', [0,42,1,[42]]),
            ('var calls=0;class C{async fn __setattr__(self,name,item){calls+=1;null}}let reason=try{C().value=1}catch error{error.code};[reason,calls]', ['argument_error',0]),
            ('var events=[];class C{fn __getattribute__(self,name){throw AttributeError("primary")}fn __getattr__(self,name){events=append(events,name);throw ValueError("fallback")}}let result=try{C().answer}catch error{[error.code,current_error().context.code]};[result,events,current_error()]', [['value_error','attribute_error'],['answer'],None]),
        ]
        for source, expected in cases:
            with self.subTest(source=source), observe_access_frames() as seen:
                self.assertEqual(self.run_source(source)[0], expected)
                self.assertTrue({'AttributeReadFrame','AttributeWriteFrame'} & seen['frames'])

    def test_http_sdk_and_cli_keep_exact_source_and_ten_result_fields(self):
        with Framework(':memory:', settings=Settings(':memory:')) as app, tempfile.TemporaryDirectory() as folder:
            server = make_server('127.0.0.1', 0, framework=app, state_dir=Path(folder)/'control', update_home=Path(folder)/'updates')
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                client = Client('http://127.0.0.1:'+str(server.server_address[1]))
                project = client.create_project('access-http', source=SOURCE, capabilities=['text.normalize'])
                with observe_access_frames() as seen:
                    self.assertEqual(client.start(project['id'],project['version'],None,mode='graph').result(20), EXPECTED)
                self.assert_all_frames(seen); self.assertEqual(client.get_project(project['id'])['source'], SOURCE)
                path = Path(folder)/'main.chaos'; path.write_text(SOURCE)
                env = {k:v for k,v in os.environ.items() if not any(word in k.upper() for word in ('MODEL','PROVIDER','KEY','TOKEN'))}
                env['CHAOS_CONFIG'] = str(Path(folder)/'settings.json'); env['PYTHONPATH'] = str(ROOT)
                for command in ('check','ir','fmt','run'):
                    result = subprocess.run([sys.executable,'-B','-m','chaos',command,str(path)], cwd=folder, env=env, capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if command == 'run': self.assertEqual(json.loads(result.stdout), EXPECTED)
                    if command == 'fmt': self.assertEqual(parse_program(result.stdout)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                    if command == 'ir': self.assertEqual(json.loads(result.stdout)['semanticHash'],parse_program(SOURCE)['semanticHash'])
            finally: server.shutdown(); server.server_close(); thread.join(2)

    def test_inert_editor_catalogue_and_exact_documented_access_example(self):
        with patch.object(Evaluator, '_s_invoke', side_effect=AssertionError('authoring invoked callback')):
            report = analyze(DOCUMENTED); formatted = format_source(DOCUMENTED); compiled = compile_program(DOCUMENTED)
        self.assertEqual(report['diagnostics'], []); self.assertTrue(compiled)
        self.assertEqual(format_source(formatted), formatted)
        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(DOCUMENTED)['semanticHash'])
        self.assertEqual(len([value for value in Evaluator().env.values.values() if type(value) is Native]), 335)
        self.assertEqual(len(all_specs()), 381); self.assertEqual(len(STDLIB), 21)
        self.assertIn(DOCUMENTED.strip(), Documentation().page('attribute-protocol')['markdown'])
        self.assertEqual(analyze('default_setattr(')['signatureHelp']['activeParameter'], 0)
        self.assertEqual(analyze('index(')['signatureHelp']['parameters'][0]['name'], 'value')
        with observe_access_frames() as seen:
            self.assertEqual(self.run_source(DOCUMENTED)[0], DOCUMENTED_EXPECTED)
        self.assertTrue({'AttributeReadFrame','AttributeWriteFrame','ItemOperationFrame','ItemInvokeFrame','IntegerIndexFrame','ObjectAccessEvaluationFrame'} <= seen['frames'])


if __name__ == '__main__': unittest.main()
