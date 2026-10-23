import json,unittest
from unittest.mock import patch
from chaos.language import Evaluator,LanguageError,Parser
from chaos.generated_sources import Source,Registry
from chaos.code_values import make,request,construct
class GeneratedSourceRegistry(unittest.TestCase):
 def test_queries_inert_register_dedup_and_version_isolation(self):
  e=Evaluator();c=make(e,'fn(x){x+1}');self.assertEqual(e.generated_sources.values,{})
  f=e.suspension.drive(construct(e,request(e,c,{})));g=e.suspension.drive(construct(e,request(e,c,{})));self.assertEqual(len(e.generated_sources.values),1);self.assertEqual(e.invoke(f,[41]),42)
  ir=json.loads(c.ir_json);ir['languageVersion']=1;other=make(e,ir);h=e.suspension.drive(construct(e,request(e,other,{})));self.assertEqual(len(e.generated_sources.values),2);self.assertEqual(e.invoke(h,[41]),42)
 def test_limit_before_defaults_and_immutable_descriptors(self):
  e=Evaluator(tool_handler=lambda *a:self.fail('default ran before limit'));c=make(e,'fn(x=tool("d",1)){x}')
  with patch('chaos.generated_sources.MAX_SOURCES',0),self.assertRaises(LanguageError):e.suspension.drive(construct(e,request(e,c,{})))
  self.assertEqual(e.generated_sources.values,{})
  e=Evaluator();c=make(e,'fn(){1}');e.suspension.drive(construct(e,request(e,c,{})));d=next(iter(e.generated_sources.values.values()))
  with self.assertRaises(Exception):d.source='2'
  self.assertEqual(e.generated_sources.characters,len(d.source))
 def test_fixed_filename_conflict_before_default(self):
  e=Evaluator(tool_handler=lambda *a:self.fail('default ran'));c=make(e,'fn(x=tool("d",1)){x}');file=json.loads(c.ir_json)['file'];e.run('null',files={file:'null','main.chaos':'null'})
  with self.assertRaises(LanguageError):e.suspension.drive(construct(e,request(e,c,{})))
 def test_old_and_nested_function_source_remains_owned(self):
  seen=[];e=Evaluator(source_handler=lambda d:seen.append(d.file));f=e.run('function_from(code_from("fn(x){fn(y){x+y}}"))(20)');file=f.body._generated_source.file;e.run('null');self.assertEqual(e.invoke(f,[22]),42);self.assertIn(file,seen);self.assertEqual(len(e.generated_sources.values),1)
  e.run('null',files={'main.chaos':'null',file:'null'});seen.clear();self.assertEqual(seen,[])
  with self.assertRaises(LanguageError):e.invoke(f,[22])
 def test_descriptor_integrity_and_character_bound(self):
  e=Evaluator();c=make(e,'fn(){1}');e.suspension.drive(construct(e,request(e,c,{})));d=next(iter(e.generated_sources.values.values()));r=Registry()
  with patch('chaos.generated_sources.MAX_CHARACTERS',len(d.source)-1),self.assertRaises(LanguageError):r.register(d)
  with self.assertRaises(LanguageError):r.register(Source(d.file,d.source,True,d.source_hash,d.semantic_hash))
  r.register(d)
  with self.assertRaises(LanguageError):r.register(Source(d.file,d.source,1,d.source_hash,d.semantic_hash))
