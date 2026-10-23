import json,unittest
from chaos.language import Evaluator,LanguageError,ResourceLimit
from chaos.program_values import builtins

def definition(files,entry='main.chaos',versions=None):
 return {'kind':'program','entry':entry,'files':files,'languageVersions':versions or {k:2 for k in files}}
def literal(files,entry='main.chaos',versions=None):return 'program_from('+json.dumps(definition(files,entry,versions))+')'
class ModuleValues(unittest.TestCase):
 def test_real_bindings_fresh_cells_and_original_identity(self):
  p=literal({'main.chaos':'var n=seed;fn add(x){n=n+x;n};let original=callback;let Klass=C;export{add,original,Klass}'})
  e=Evaluator();self.assertEqual(e.run('fn callback(x){x+2};class C{};let p='+p+';let a=module_from(p,{seed:20,callback:callback,C:C});let b=module_from(p,{seed:40,callback:callback,C:C});[a.add(2),b.add(2),a.add(20),a.original==callback,a.Klass==C]'),[22,42,42,True,True])
 def test_diamond_and_standard_dependencies_once_per_instance(self):
  calls=[];files={'main.chaos':'import "a.chaos" as A;import "b.chaos" as B;fn result(){A.f()+B.f()};export{result}', 'a.chaos':'import "shared.chaos" as S;let f=S.f;export{f}', 'b.chaos':'import "shared.chaos" as S;let f=S.f;export{f}', 'shared.chaos':'import "std/math" as M;tool("init",seed);fn f(){M.abs(seed)};export{f}'}
  e=Evaluator(tool_handler=lambda n,v:calls.append((n,v)));self.assertEqual(e.run('let p='+literal(files)+';let a=module_from(p,{seed:21});let b=module_from(p,{seed:-21});[a.result(),b.result()]'),[42,42]);self.assertEqual(calls,[('init',21),('init',-21)])
 def test_concurrent_suspended_initializers_and_nested_modules(self):
  files={'main.chaos':'import "lib.chaos" as L;await sleep(0);let f=L.f;export{f}','lib.chaos':'await sleep(0);fn f(){seed};export{f}'}
  source='let p='+literal(files)+';async fn build(seed){module_from(p,{seed:seed})};let values=await gather([build(20),build(22)]);[values[0].f(),values[1].f()]'
  self.assertEqual(Evaluator().run(source),[20,22])
  inner=literal({'nested.chaos':'fn f(){value};export{f}'},'nested.chaos');outer=literal({'main.chaos':'let m=module_from(p,{value:42});let f=m.f;export{f}'})
  self.assertEqual(Evaluator().run('let p='+inner+';module_from('+outer+',{p:p}).f()'),42)
 def test_macro_data_class_and_all_function_flavors(self):
  files={'main.chaos':'import "lib.chaos" as L;data Option{Some(value)};class Box{let value=42};fn f(x){L.inc!(x)};async fn a(x){await sleep(0);f(x)};fn* g(x){yield f(x)};async fn* ag(x){await sleep(0);yield f(x)};export{f,a,g,ag,Some,Box}', 'lib.chaos':'macro inc(x){quote{unquote(x)+1}};export{inc}'}
  self.assertEqual(Evaluator().run('let m=module_from('+literal(files)+');[m.f(41),await m.a(41),collect(m.g(41)),await acollect(m.ag(41)),m.Box().value,match m.Some(42){m.Some(x)=>x}]'),[42,42,[42],[42],42,42])
 def test_program_reflection_and_retained_function_across_epochs(self):
  p=literal({'library.chaos':'fn f(){[program_info(current_program()).entry,program_metadata(current_program()).origin]};export{f}'},'library.chaos')
  e=Evaluator();f=e.run('module_from('+p+').f');self.assertEqual(e.run('saved()',{'saved':f}),['library.chaos',None]);self.assertEqual(e.bundle.entry,'main.chaos');self.assertEqual(e.run('program_info(current_program()).entry'),'main.chaos')
 def test_binding_ownership_validation_before_effects(self):
  e=Evaluator();other=Evaluator();p=builtins(e)['program_from'](definition({'main.chaos':'tool("init",0);export{}'}));foreign=other.run('fn(){42}')
  from chaos.module_values import request
  with self.assertRaises(LanguageError):request(e,p,{'nested':[foreign]})
  with self.assertRaises(LanguageError):request(other,p,{})
  with self.assertRaises(LanguageError):request(e,p,{'while':42})
 def test_invalid_closed_graph_and_registry_quota_before_effects(self):
  calls=[];e=Evaluator(tool_handler=lambda *a:calls.append(a))
  for files in ({'main.chaos':'tool("init",0);export{}','orphan.chaos':'fn ('},{'main.chaos':'import "other.chaos" as X;export{}','other.chaos':'import "main.chaos" as X;export{}'}):
   with self.assertRaises(LanguageError):e.run('module_from('+literal(files)+')')
  files={f'f{i}.chaos':'let answer=42;export{answer}' for i in range(64)};files['f0.chaos']='import "std/math" as M;tool("init",0);export{}'
  with self.assertRaises(ResourceLimit):e.run('module_from('+literal(files,'f0.chaos')+')')
  self.assertEqual(calls,[]);self.assertEqual(e.generated_sources.values,{})
 def test_defaults_once_cold_bodies_and_no_replay_on_failure(self):
  calls=[];e=Evaluator(tool_handler=lambda n,v:calls.append(v) or v)
  files={'main.chaos':'fn f(x=tool("effect",21)){tool("effect",x+1)};export{f}'}
  self.assertEqual(e.run('let m=module_from('+literal(files)+');[m.f(),m.f()]'),[22,22]);self.assertEqual(calls,[21,22,22])
  with self.assertRaises(LanguageError):e.run('module_from('+literal({'main.chaos':'tool("effect",99);throw "failure";export{}'})+')')
  self.assertEqual(calls,[21,22,22,99]);self.assertEqual(e.frames,[])
 def test_team_authority_and_exported_native_flow(self):
  from tests.test_team_functions import definition as team
  p=literal({'main.chaos':'let work=borrowed;fn f(n){var i=0;while i<n{i=i+1};i};export{work,f}'})
  calls=[];e=Evaluator(tool_handler=lambda n,v:calls.append(n) or 42)
  source='let t=team_from('+json.dumps(team())+');fn wide(x){tool("secret",x)};let p='+p+';let b=team_bind(t,{worker:fn(x){module_from(p,{borrowed:wide})}});let m=await b(0);let compiled=compile_function(m.f,target="auto-flow");[try{m.work(0)}catch e{e.code},compiled(42),wide(0),compile_flow_info(compiled)]'
  value=e.run(source);self.assertEqual(value[:3],['capability_error',42,42]);self.assertEqual(calls,['secret'])
  from test_native_jit import NATIVE
  flow=value[3];self.assertEqual(flow['status'],'native' if NATIVE else 'portable')
  self.assertGreater(flow['nativeResumes'] if NATIVE else flow['portableResumes'],0)

 def test_tail_reflection_forwarding_and_outer_context_restore(self):
  p=literal({'library.chaos':'fn own(){current_program()};fn forwarded(){apply(current_program,[])};fn echoed(){compose(fn(_){current_program()},fn(){0})()};fn outer(cb){cb()};export{own,forwarded,echoed,outer}'},'library.chaos')
  source='fn root(){current_program()};let m=module_from('+p+');[program_info(m.own()).entry,program_info(m.forwarded()).entry,program_info(m.echoed()).entry,program_info(m.outer(root)).entry,program_info(current_program()).entry]'
  self.assertEqual(Evaluator().run(source),['library.chaos','library.chaos','library.chaos','main.chaos','main.chaos'])
