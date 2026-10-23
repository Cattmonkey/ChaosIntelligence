import json,unittest
from chaos import Framework
from chaos.language import Evaluator,LanguageError
from chaos.code_values import make,request,construct,builtins
from chaos.program import parse_program
class RuntimeCode(unittest.TestCase):
 def test_real_named_recursion_explicit_capture_no_implicit_locals(self):
  self.assertEqual(Framework.evaluate('let c=code_from("fn fact(n){if n<=1{1}else{n*fact(n-1)}}");let f=function_from(c);[f(5),type_of(c),code_info(c).parameterCount]'),[120,'Code',1])
  self.assertEqual(Framework.evaluate('var factor=2;let f=function_from(code_from("fn(x){x*factor}"),{factor:factor});factor=7;f(3)'),6)
  with self.assertRaises(LanguageError):Framework.evaluate('let hidden=2;function_from(code_from("fn(){hidden}"))()')
 def test_code_stage_no_defaults_body_and_default_once(self):
  self.assertEqual(Framework.evaluate('var events=[];fn mark(){events=append(events,"default");21};let c=code_from("fn(x=mark()){body();x*2}");let before=events;let f=function_from(c,{mark:mark,body:fn(){events=append(events,"body")}});let created=events;let value=f();[before,created,events,value]'),[[],['default'],['default','body'],42])
 def test_four_flavors_and_signature(self):
  for source,call,expected in [('fn(a,/,*,b=2){a+b}','function_from(c)(3,b=4)',7),('async fn(a){await sleep(0);a+1}','await function_from(c)(3)',4),('fn*(a){yield a;yield a+1}','collect(function_from(c)(3))',[3,4]),('async fn*(a){yield a;await sleep(0);yield a+1}','await acollect(function_from(c)(3))',[3,4])]:
   self.assertEqual(Framework.evaluate('let c=code_from('+json.dumps(source)+');'+call),expected)
 def test_ir_edit_copy_and_fresh_ast(self):
  e=Evaluator();c=make(e,'fn(x){x+1}');api=builtins(e);ir=api['code_ir'](c);literal=ir['root']['statements'][0]['body']['statements'][0]['right'];new=api['code_edit'](c,[{'op':'set','id':literal['id'],'field':'value','value':2}])
  f=e.suspension.drive(construct(e,request(e,c,{})));g=e.suspension.drive(construct(e,request(e,new,{})));h=e.suspension.drive(construct(e,request(e,c,{})))
  self.assertEqual((e.invoke(f,[3]),e.invoke(g,[3])),(4,5));self.assertIsNot(f.body,h.body);ir['root']['statements'].clear();self.assertTrue(api['code_ir'](c)['root']['statements'])
 def test_invalid_root_and_control_ir(self):
  e=Evaluator()
  for src in ('tool("bad",0);fn(){1}','import "std/math" as M;fn(){1}','1','fn(){break}'):
   with self.assertRaises(LanguageError):make(e,src)
  ir=parse_program('fn(){1}');ir['root']['statements'][0]['body']['statements'][0]={'id':'evil','kind':'break'}
  with self.assertRaises(LanguageError):make(e,ir)
 def test_owner_identity_and_snapshot_associations(self):
  a=Evaluator();b=Evaluator();c=make(a,'fn(){x}');value=[42];binding={'x':value};r=request(a,c,binding);binding['x']=99;f=a.suspension.drive(construct(a,r));self.assertIs(a.invoke(f,[]),value)
  with self.assertRaises(LanguageError):request(b,c,{})
  foreign=b.run('fn(){0}')
  with self.assertRaises(LanguageError):request(a,c,{'x':[foreign]})
 def test_metadata_and_budget(self):
  e=Evaluator();c=make(e,'fn(x=1){while true{}}');api=builtins(e);steps=e.steps;q=api['code_info'](c);q['name']='bad';self.assertEqual(e.steps,steps);self.assertNotEqual(api['code_info'](c)['name'],'bad')
  e.steps=0;e.step_limit=0
  with self.assertRaises(LanguageError):request(e,c,{'x':1})
 def test_opaque_code_and_context_values(self):
  self.assertEqual(Framework.evaluate('let c:Code=code_from("fn(){v.get()}");let v=context_var("x",42);let f=function_from(c,{v:v});[f(),type_of(c),length(code_info(c))]'),[42,'Code',7])
