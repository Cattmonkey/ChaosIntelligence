"""Independent closed executable-image contracts; original evaluator and host."""
import copy
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Evaluator, Framework, LanguageError, Registry, Settings, Final
from chaos.executable_image import compile_objects, link_objects, compile_executable, load_executable
from chaos.language import Native, Node, serializable
from chaos.modules import ProgramBundle


ROOT = Path(__file__).resolve().parents[1]
FOUR = '''var entered=[];
fn plain(x){entered=append(entered,"fn");x+1};
async fn waiting(x){entered=append(entered,"async");await sleep(0);x+2};
fn* stream(x){entered=append(entered,"gen");yield x;return_values(x+3,x+4)};
async fn* both(x){entered=append(entered,"asyncgen");await sleep(0);yield x;return_values(x+5,x+6)};
let a=waiting(40);let g=stream(42);let ag=both(42);let cold=entered;
let values=[plain(41),await a,collect(g),await acollect(ag)];
[cold,values,multiple_value_list(g),multiple_value_list(ag),entered]'''
FOUR_EXPECTED = [[], [42,42,[42],[42]], [45,46], [47,48], ['fn','async','gen','asyncgen']]


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)


def rehash_object(value):
    value['objectHash'] = hashlib.sha256(canonical({k:v for k,v in value.items() if k != 'objectHash'}).encode()).hexdigest()
    return value


def rehash_image(value):
    for obj in value.get('objects', []):
        rehash_object(obj)
    value['imageHash'] = hashlib.sha256(canonical({k:v for k,v in value.items() if k != 'imageHash'}).encode()).hexdigest()
    return value


@contextmanager
def no_frontend():
    """Tripwire only: no substitute parser, expansion or semantic oracle."""
    with ExitStack() as stack:
        for target in ('chaos.modules.ProgramBundle.__init__', 'chaos.language.Parser.program', 'chaos.modules.parse_unit',
                       'chaos.macros.expand_trees', 'chaos.program.parse_program'):
            stack.enter_context(patch(target, side_effect=AssertionError('Executable path entered frontend: '+target)))
        yield


def run_image(image, **kwargs):
    evaluator = Evaluator(timeout=20, **kwargs)
    with no_frontend():
        value = evaluator.run(load_executable(json.loads(canonical(image))))
    assert evaluator.frames == [] and evaluator.depth == 0 and not evaluator.suspension.active
    return serializable(value), evaluator


def syntax_nodes(bundle):
    pending = list(bundle.expanded_trees.values()); found = []
    while pending:
        value = pending.pop()
        if type(value) is Node:
            found.append(value); pending.extend(value[1:])
        elif type(value) in (list, tuple):
            pending.extend(value)
    return found


class ExecutableImage231(unittest.TestCase):
    def bad(self, image):
        with no_frontend(), self.assertRaises(LanguageError) as caught:
            load_executable(image)
        self.assertEqual(caught.exception.code, 'executable_error', caught.exception.record())

    def test_compile_objects_link_load_are_data_only_and_effect_inert(self):
        source = 'tool("never",1);fn f(x=tool("default",2)){x};42'
        with patch.object(Evaluator, 'run', side_effect=AssertionError('Source executed while constructing image')):
            objects = compile_objects(source)
            with no_frontend():
                image = link_objects(json.loads(canonical(objects)))
                loaded = load_executable(json.loads(canonical(image)))
        self.assertIs(type(objects), list); self.assertTrue(objects)
        self.assertIsInstance(loaded, ProgramBundle)
        self.assertEqual(loaded.summary()['entry'], 'main.chaos')
        self.assertEqual(image, json.loads(canonical(image)))
        pending = [image]
        while pending:
            value = pending.pop()
            self.assertIn(type(value), (dict,list,str,int,float,bool,type(None)))
            if type(value) is dict:
                self.assertTrue(all(type(k) is str for k in value)); pending.extend(value.values())
            elif type(value) is list:
                pending.extend(value)

    def test_json_roundtrip_runs_without_parser_or_macro_expander(self):
        source = 'macro twice(x){quote{let local=unquote(x);local+local}};let local=100;[twice!(tool("once",21)),local]'
        calls = []; image = compile_executable(source); before = copy.deepcopy(image)
        value, _ = run_image(image, tool_handler=lambda n,v:calls.append((n,v)) or v)
        self.assertEqual(value, [42,100]); self.assertEqual(calls, [('once',21)])
        self.assertEqual(image, before)

    def test_cross_module_private_macro_alias_and_keyword_hygiene(self):
        files = {
            'base.chaos':'macro inc(x){quote{unquote(x)+1}};export{inc}',
            'lib.chaos':'import "base.chaos" as H;macro make(x){quote{let saved=1;fn(value,*,bonus=H.inc!(0)){unquote(x)+value+bonus+saved}}};export{make}',
            'forward.chaos':'macro identity(x){let saved=quote{unquote(x)};quote{unquote(saved)}};export{identity}',
        }
        source = 'import "lib.chaos" as L;import "forward.chaos" as F;let saved=40;let H=0;let value=99;F.identity!(L.make!(saved))(value=0)'
        original = Evaluator().run(source, files=files)
        self.assertEqual(original, 42)
        image = compile_executable(source, files=files)
        self.assertEqual(run_image(image)[0], original)
        loaded = load_executable(image)
        self.assertEqual(loaded.exports['lib.chaos'], [])
        self.assertEqual(loaded.macro_exports['lib.chaos'], ['make'])

    def test_numeric_and_condition_macro_constructor_captures_survive_shadowing(self):
        files = {'values.chaos':'''macro number(){syntax_literal(complex(fraction(1,3),1))};
macro situation(){syntax_literal(condition("missing",data={n:decimal("0.10")}))};export{number,situation}'''}
        source = '''import "values.chaos" as M;let expected=complex(fraction(1,3),1);
let fraction=fn(){0};let complex=fn(){0};let decimal=fn(){0};let condition=fn(){0};
let c=M.situation!();[M.number!()==expected,c.kind,c.["data"].n==numeric_decode({"$numeric":{kind:"Decimal",sign:0,coefficient:"10",exponent:-2}})]'''
        expected = Evaluator().run(source, files=files)
        self.assertEqual(expected, [True,'missing',True])
        self.assertEqual(run_image(compile_executable(source, files=files))[0], expected)

    def test_diamond_import_initializes_once_per_run_and_fresh_on_next_run(self):
        files = {
            'base.chaos':'let initialized=tool("init",40);fn answer(){initialized};export{answer}',
            'left.chaos':'import "base.chaos" as B;fn answer(){B.answer()+1};export{answer}',
            'right.chaos':'import "base.chaos" as B;fn answer(){B.answer()+2};export{answer}',
        }
        source = 'import "left.chaos" as L;import "right.chaos" as R;[L.answer(),R.answer()]'
        image = compile_executable(source, files=files)
        for _ in range(2):
            calls = []
            value, e = run_image(image, tool_handler=lambda n,v:calls.append((n,v)) or v)
            self.assertEqual(value, [41,42]); self.assertEqual(calls, [('init',40)])
            self.assertEqual(set(e.module_cache), {'main.chaos','base.chaos','left.chaos','right.chaos'})

    def test_defaults_closure_cells_are_runtime_fresh_and_function_formation_not_jit(self):
        source = '''fn make(seed=tool("outer-default",0)){
var cell=seed;fn child(delta=tool("inner-default",1)){cell+=delta;cell};child};
let a=make();let b=make();[a(),a(),b()]'''
        image = compile_executable(source)
        for _ in range(2):
            calls=[]
            with patch('chaos.jit.compile_function', side_effect=AssertionError('Image implicitly compiled a function')):
                value, _ = run_image(image, tool_handler=lambda n,v:calls.append((n,v)) or v)
            self.assertEqual(value,[1,2,1]);self.assertEqual(calls,[('outer-default',0),('inner-default',1),('inner-default',1)])

    def test_all_four_flavors_keep_cold_handles_yields_and_full_final_values(self):
        image = compile_executable(FOUR)
        self.assertEqual(serializable(Evaluator().run(FOUR)), FOUR_EXPECTED)
        self.assertEqual(run_image(image)[0], FOUR_EXPECTED)

    def test_every_shipped_standard_module_is_compilable_and_loads_without_frontend(self):
        from chaos.modules import STDLIB
        source=';'.join('import '+json.dumps(name)+' as Library'+str(index) for index,name in enumerate(STDLIB))+';42'
        original=Evaluator(step_limit=200000,timeout=20).run(source)
        self.assertEqual(original,42)
        image=compile_executable(source)
        self.assertTrue(set(STDLIB) <= {obj['module'] for obj in image['objects']})
        self.assertEqual(run_image(image,step_limit=200000)[0],original)

    def test_original_class_data_resource_protection_and_comprehension_compose(self):
        source='''var events=[];data Choice{Some(value),None};
class Resource{fn __enter__(self){Some(40)};fn __exit__(self,error){events=append(events,"close");null}};
fn work(){with Resource() as item{try{match item{Some(v)=>v+[n+1 for n in [1] if n>0][0];None()=>0}}finally{events=append(events,"finally")}}};
[work(),events]'''
        original=serializable(Evaluator().run(source))
        self.assertEqual(original,[42,['finally','close']])
        self.assertEqual(run_image(compile_executable(source))[0],original)

    def test_explicit_optional_flow_compilation_is_allowed_and_reuses_original_states(self):
        from chaos.compiled_flow_state import FlowState
        states=[]; original=FlowState.__init__
        def observe(state,*args,**kwargs):
            original(state,*args,**kwargs);states.append(state)
        source='async fn original(x){await sleep(0);if x>0{x+1}else{0}};let f=compile_function(original,"auto-flow");await f(41)'
        image=compile_executable(source)
        with patch.object(FlowState,'__init__',observe):value,_=run_image(image)
        self.assertEqual(value,42);self.assertTrue(states)
        for state in states:
            self.assertGreater(state.program.resume_calls,0);self.assertTrue(state.closed)
            self.assertEqual(state.frames,[]);self.assertEqual(state._owned_activations,[])
            self.assertEqual(state._transfers,[]);self.assertIsNone(state.pending_cursor);self.assertIsNone(state.pending_request)

    def test_two_loads_have_fresh_nodes_without_image_alias_or_cross_owner(self):
        image=compile_executable('fn make(x){fn(){x}};make(42)');before=copy.deepcopy(image)
        with no_frontend():a=load_executable(image);b=load_executable(image)
        self.assertIsNot(a,b);self.assertTrue(syntax_nodes(a));self.assertEqual(len(syntax_nodes(a)),len(syntax_nodes(b)))
        self.assertFalse({id(n) for n in syntax_nodes(a)} & {id(n) for n in syntax_nodes(b)})
        def rejected_foreign(value):
            receiver=Evaluator();receiver.env.bind('foreign',value)
            with self.assertRaises(LanguageError) as caught:receiver.run('foreign()')
            self.assertEqual(receiver.frames,[]);self.assertFalse(receiver.suspension.active)
            return caught.exception.record()
        # Original InvocationDispatchFrame deliberately uses the default
        # runtime_error code for foreign Function, rather than ownership_error.
        original=Evaluator().run('fn make(x){fn(){x}};make(42)')
        reference=rejected_foreign(original)
        self.assertEqual(reference['code'],'runtime_error')
        self.assertIn('可调用值不能跨执行上下文传递',reference['message'])
        self.assertEqual(rejected_foreign(Evaluator().run(a)),reference)
        self.assertEqual(image,before)

    def test_link_rejects_missing_duplicate_and_conflicting_modules_before_effects(self):
        objects=compile_objects('import "lib.chaos" as L;L.answer()',files={'lib.chaos':'tool("init",1);fn answer(){42};export{answer}'})
        calls=[]
        self.assertEqual(run_image(link_objects(objects),tool_handler=lambda n,v:calls.append((n,v)) or v)[0],42)
        self.assertEqual(calls,[('init',1)])
        without=[o for o in objects if o['module']!='lib.chaos']
        for invalid in (without,objects+[copy.deepcopy(objects[0])]):
            with self.subTest(modules=[o['module'] for o in invalid]),no_frontend(),self.assertRaises(LanguageError) as caught:link_objects(invalid)
            self.assertEqual(caught.exception.code,'executable_error')

    def test_rehashed_unknown_node_and_authority_metadata_are_rejected(self):
        image=compile_executable('42')
        for kind in ('host-frame','python','unknown'):
            bad=copy.deepcopy(image);bad['objects'][0]['tree'][0]=kind;self.bad(rehash_image(bad))
        for field in ('owner','_module_scope','capabilities','pythonCallable'):
            bad=copy.deepcopy(image);bad['objects'][0]['tree'][2][field]='forged';self.bad(rehash_image(bad))

    def test_image_and_object_abi_hash_entry_and_extra_fields_are_strict(self):
        image=compile_executable('42')
        for field,value in (('schema','other'),('abi','unknown'),('entry','absent.chaos'),('imageHash','0'*64)):
            bad=copy.deepcopy(image);bad[field]=value
            if field!='imageHash':rehash_image(bad)
            self.bad(bad)
        bad=copy.deepcopy(image);bad['execute']=True;self.bad(rehash_image(bad))
        bad=copy.deepcopy(image);bad['objects'][0]['grantCapabilities']=['robot.control'];self.bad(rehash_image(bad))
        bad=copy.deepcopy(image);bad['objects'][0]['objectHash']='0'*64;bad['imageHash']=hashlib.sha256(canonical({k:v for k,v in bad.items() if k!='imageHash'}).encode()).hexdigest();self.bad(bad)

    def test_rehashed_ast_rejects_illegal_control_before_first_effect_and_in_orphans(self):
        image=compile_executable('tool("never",1);42',files={'orphan.chaos':'42'})
        def encoded_nodes(tree):
            pending=[tree]
            while pending:
                value=pending.pop()
                if type(value)is list:
                    if len(value)==3 and value[0]=='node':yield value
                    pending.extend(item for item in value if type(item)is list)
        for module in ('main.chaos','orphan.chaos'):
            for illegal in ('break','continue','yield','return'):
                with self.subTest(module=module,illegal=illegal):
                    bad=copy.deepcopy(image);obj=next(o for o in bad['objects'] if o['module']==module)
                    literal=next(n for n in encoded_nodes(obj['tree']) if n[1][0]==['str','literal'] and n[1][1][0]=='int' and int(n[1][1][1],16)==42)
                    literal[1]=[['str',illegal]]
                    self.bad(rehash_image(bad))

    def test_rehashed_unknown_operator_and_intrinsic_capture_on_wrong_node_reject(self):
        image=compile_executable('1+2')
        def nodes(value):
            if type(value)is list:
                if len(value)==3 and value[0]=='node':yield value
                for child in value:yield from nodes(child)
        bad=copy.deepcopy(image)
        binary=next(n for n in nodes(bad['objects'][0]['tree']) if n[1][0]==['str','binary'])
        binary[1][1]=['str','host-eval'];self.bad(rehash_image(bad))
        bad=copy.deepcopy(image);bad['objects'][0]['tree'][2]['numeric_capture']='fraction';self.bad(rehash_image(bad))

    def test_plain_evaluator_gains_no_tool_agent_or_robot_authority_from_image(self):
        for source in ('tool("not-granted",null)','agent a{provider:"not-granted",tools:[]};a(null)','await robot_open("not-granted")'):
            with self.subTest(source=source):
                image=compile_executable(source)
                with no_frontend(),self.assertRaises(LanguageError):Evaluator().run(load_executable(image))

    def test_real_host_bot_agent_and_software_robot_use_original_runtime_once(self):
        from chaos.runtime import Context
        from chaos.robots import RobotSpec
        import test_robot_values as robots
        entered=threading.Event();release=threading.Event();log=[];requests=[];registry=Registry()
        @registry.tool('fixture.image231.hold',effect='pure')
        def hold(value,ctx):
            entered.set()
            if not release.wait(10):raise AssertionError('Host fixture not released')
            ctx.checkpoint();return None
        @registry.provider('fixture.image231')
        def provider(request,ctx):
            ctx.checkpoint();requests.append((request.step,request.input));return Final(request.input+2)
        def factory(ctx):ctx.checkpoint();log.append(('open',{}));return robots.Adapter(log)
        registry.register_robot(RobotSpec('rig',factory,robots.ACTUATORS,profile='fixture/1',resource_id='fixture:image231'))
        source='''agent worker{provider:"fixture.image231",tools:[]};
fn plan(s){s.input};async fn execute(p,s){let answer=worker(p);async with await robot_open("rig") as r{let o=await robot_observe(r);let receipt=await r(robot_command(o,{pan:0,tilt:1}));[answer,receipt.status]}};
fn verify(s){{done:true,context:null,output:s.evidence[-1]}};
let b=bot("image once",plan,execute,verify);let pending=b(40);let cold=bot_info(b).busy;let actual=await pending;[cold,actual.status,actual.output]'''
        image=compile_executable(source)
        with Framework(':memory:',registry=registry,settings=Settings(':memory:')) as app:
            project=app.create_project('image-host',source='tool("fixture.image231.hold",null)',capabilities=['fixture.image231.hold','robot.control'])
            run=app.start(project['id'],project['version'],None,mode='source');context=None
            try:
                self.assertTrue(entered.wait(3))
                context=Context(app._engine,run.id,project,app._engine.active[run.id][0]);e=context.evaluator()
                with no_frontend():actual=serializable(e.run(load_executable(image)))
                self.assertEqual(actual,[False,'achieved',[42,'sent']]);self.assertFalse(e.suspension.active)
            finally:
                if context is not None:context.close_services('completed')
                release.set();run.result(12)
        self.assertEqual(requests,[(1,40)]);self.assertEqual([name for name,_ in log],['open','observe','act','close'])

    def test_fresh_process_cli_build_link_validate_run_after_source_removed(self):
        bootstrap='''import json,runpy,socket,sys
from pathlib import Path
sys.path.insert(0,sys.argv.pop(1))
def deny(*a,**k):raise AssertionError("Executable fixture forbids network")
socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
if sys.argv[1]=="guard":
 sys.argv.pop(1)
 from unittest.mock import patch
 import chaos.language,chaos.macros,chaos.program,chaos.modules
 for module,name in ((chaos.modules.ProgramBundle,"__init__"),(chaos.language.Parser,"program"),(chaos.macros,"expand_trees"),(chaos.program,"parse_program"),(chaos.modules,"parse_unit")):
  setattr(module,name,deny)
sys.argv[0]="chaos";runpy.run_module("chaos",run_name="__main__")'''
        with tempfile.TemporaryDirectory(prefix='executable231-') as directory:
            work=Path(directory);source=work/'main.chaos';source.write_text('input+2');imagefile=work/'image.json'
            env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','USERPROFILE','SYSTEMROOT','WINDIR','TMPDIR','TMP','TEMP','LANG','LC_ALL')}
            env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',CHAOS_CONFIG=str(work/'settings.json'))
            def command(*args,guard=False):
                result=subprocess.run([sys.executable,'-I','-S','-B','-c',bootstrap,str(ROOT)]+(['guard'] if guard else [])+['executable',*map(str,args)],cwd=work,env=env,text=True,capture_output=True,timeout=25)
                self.assertEqual(result.returncode,0,(args,result.stdout,result.stderr));return json.loads(result.stdout)
            data=command('build',source,'--root',work)
            objects=command('build',source,'--root',work,'--objects');objectsfile=work/'objects.json';objectsfile.write_text(canonical(objects))
            linked=command('link',objectsfile,'--entry','main.chaos',guard=True)
            self.assertEqual(linked,data)
            imagefile.write_text(canonical(data));source.unlink()
            self.assertTrue(command('validate',imagefile,guard=True)['valid'])
            self.assertEqual(command('run',imagefile,'--input-json','40',guard=True),42)
            self.assertEqual(json.loads(imagefile.read_text()),data)


    def test_rehashed_statement_roles_cannot_become_value_operands(self):
        image=compile_executable('let x=1;40+2')
        def nodes(value):
            if type(value)is list:
                if len(value)==3 and value[0]=='node':yield value
                for child in value:yield from nodes(child)
        bad=copy.deepcopy(image);tree=bad['objects'][0]['tree']
        declaration=next(n for n in nodes(tree) if n[1][0]==['str','let'])
        binary=next(n for n in nodes(tree) if n[1][0]==['str','binary'])
        binary[1][2]=copy.deepcopy(declaration);self.bad(rehash_image(bad))

    def test_rehashed_rec_and_decorated_require_real_declarations(self):
        def nodes(value):
            if type(value)is list:
                if len(value)==3 and value[0]=='node':yield value
                for child in value:yield from nodes(child)
        for source,kind in (('rec{fn f(){42}};f()','rec'),('fn identity(f){f};@identity\nfn f(){42};f()','decorated')):
            with self.subTest(kind=kind):
                bad=compile_executable(source);tree=bad['objects'][0]['tree']
                literal=next(n for n in nodes(tree) if n[1][0]==['str','literal'])
                target=next(n for n in nodes(tree) if n[1][0]==['str',kind])
                if kind=='rec':target[1][1][1][0]=copy.deepcopy(literal)
                else:target[1][2]=copy.deepcopy(literal)
                self.bad(rehash_image(bad))

    def test_rehashed_parameter_default_pattern_and_float_overflow_are_inert_errors(self):
        def nodes(value):
            if type(value)is list:
                if len(value)==3 and value[0]=='node':yield value
                for child in value:yield from nodes(child)
        for mode in ('default','pattern'):
            with self.subTest(mode=mode):
                bad=compile_executable('let outside=1;fn f(x=1){x};f()');tree=bad['objects'][0]['tree']
                function=next(n for n in nodes(tree) if n[1][0]==['str','fn'])
                declaration=next(n for n in nodes(tree) if n[1][0]==['str','let'])
                literal=next(n for n in nodes(tree) if n[1][0]==['str','literal'])
                parameter=function[1][1][1][0][1]
                if mode=='default':parameter[3]=copy.deepcopy(declaration)
                else:
                    self.assertEqual(len(parameter),4)
                    parameter.append(copy.deepcopy(literal))
                self.bad(rehash_image(bad))
        bad=compile_executable('1.5');literal=next(n for n in nodes(bad['objects'][0]['tree']) if n[1][0]==['str','literal'])
        literal[1][1]=['float','0x1p+9999999'];self.bad(rehash_image(bad))
