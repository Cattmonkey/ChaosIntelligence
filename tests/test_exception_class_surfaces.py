"""Nominal exception classes retain the shared class, pattern and tooling contracts."""
import inspect
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from chaos import Client,Framework,Settings
from chaos.code_presentation import render_code
from chaos.exception_classes import DEFINITIONS,FIELDS
from chaos.ide_language import analyze,builtin_symbols
from chaos.language import Evaluator,Parser
from chaos.lsp_adapters import convert
from chaos.native_specs import spec_for,SHAPES,SPECS
from chaos.program import decode_program,format_program,parse_program,plain
from chaos.server import make_server
from chaos.source_format import format_source
from chaos.symbols import references,rename

SOURCE='''// Role failures use real nominal classes and stored-field patterns.
class PlanFailure(ValueError){let stage="plan"}
let failure=PlanFailure("bad input");
try{throw failure}catch PlanFailure::{stage:stage,message:message,.._}{
 [stage,message,current_error().instance==failure]
}'''
EXPECTED=['plan','bad input',True]

class ExceptionClassSurfaces(unittest.TestCase):
 def test_shared_native_hierarchy_and_allocator_metadata(self):
  table=builtin_symbols();ev=Evaluator()
  for name,parent,code,codes in DEFINITIONS:
   with self.subTest(name=name):
    info=table[name];actual=ev.env.get(name)
    self.assertEqual(info.type,'Class');self.assertIs(info.class_info,info)
    self.assertEqual([entry.detail.split(' · ')[0]for entry in info.mro],[entry.name for entry in actual.mro])
    self.assertIs(info.metaclass_info,table['Type'])
    self.assertTrue({name for name,_ in FIELDS}<=set(info.members))
  spec=spec_for('Exception.__new__');actual=ev.run('Exception.__new__')
  self.assertEqual(actual.spec_id,spec.id)
  parameters=list(inspect.signature(actual.call).parameters.values())
  self.assertEqual([item.name for item in parameters],[item.name for item in spec.parameters])
  self.assertEqual([item.mode for item in spec.parameters],['positional','varargs','varkw'])
  self.assertEqual(spec.result_type,'Instance');self.assertNotIn(spec.id,SPECS)
  init=spec_for('Exception.__init__');actual=ev.env.get('Exception').members['__init__'].value
  self.assertEqual([p.name for p in inspect.signature(actual.call).parameters.values()],[p.name for p in init.parameters])
  self.assertEqual(init.result_type,'Null');self.assertNotIn(init.id,SPECS)
  self.assertEqual({row.name:row.type for row in SHAPES['ErrorFields']}['instance'],'Instance')
  self.assertEqual(set(Framework.evaluate('dir(error(42))')),{row.name for row in SHAPES['ErrorFields']})

 def test_constructor_signatures_aliases_custom_init_and_shadowing(self):
  for source in ('Exception(','ValueError(','let Make=ValueError;Make('):
   help=analyze(source)['signatureHelp'];self.assertEqual([p['name']for p in help['parameters']],['args'])
   self.assertEqual(help['parameters'][0]['mode'],'varargs')
  source='class Failure(ValueError){fn __init__(self,code:Int,*,detail:String="bad"){null}};Failure(1,detail='
  help=analyze(source)['signatureHelp'];self.assertEqual([p['name']for p in help['parameters']],['code','detail']);self.assertEqual(help['activeParameter'],1)
  source='fn ValueError(n:Int)->Int{n};let answer=ValueError(42)'
  self.assertEqual(next(x['type']for x in analyze(source)['symbols']if x['name']=='answer'),'Int')
  self.assertTrue(analyze('Exception(unexpected=1)')['diagnostics'])
  source='class Dynamic(Exception){fn __new__(cls){42}};let unknown=Dynamic()'
  self.assertEqual(next(x['type']for x in analyze(source)['symbols']if x['name']=='unknown'),'Any')

 def test_exact_instance_members_and_unknown_subclass_catch_are_conservative(self):
  source='let value=ValueError("bad");let code=value.code;let args=value.args;let ValueError::{message:text}=value;text'
  report=analyze(source);self.assertEqual(report['diagnostics'],[])
  types={item['name']:item['type']for item in report['symbols']}
  self.assertEqual([types[n]for n in ('value','code','args','text')],['ValueError','String','Tuple','String'])
  for source in ('try{input()}catch Exception::{code:code,args:args,.._}{[code,args]}',
                 'try{input()}catch* ValueError::{message:text,.._} if text!="" as group{group.code}'):
   # Unknown subclasses may replace stored fields or their allocator.
   report=analyze(source);self.assertEqual(report['diagnostics'],[])
   values={x['name']:x['type']for x in report['symbols']}
   for name in ('code','args','text'):
    if name in values:self.assertEqual(values[name],'Any')
  labels={x['label']for x in analyze('ValueError("bad").')['completions']}
  self.assertTrue({'code','message','args','value'}<=labels)
  self.assertIn('instance',{x['label']for x in analyze('error(0).')['completions']})

 def test_source_ir_and_formatting_preserve_nominal_catch(self):
  for source in (SOURCE,format_source(SOURCE,line_width=40),format_program(parse_program(SOURCE))):
   self.assertEqual(Framework.evaluate(source),EXPECTED)
   self.assertEqual(plain(decode_program(parse_program(source))),plain(Parser(SOURCE).program()))
  formatted=format_source(SOURCE);self.assertEqual(format_source(formatted),formatted)
  self.assertIn('// Role failures',formatted)
  self.assertEqual(analyze(SOURCE)['diagnostics'],[])

 def test_macro_and_rename_preserve_real_class_identity(self):
  source='''macro make(factory,value){quote{unquote(factory)(unquote(value))}};
let failure=make!(ValueError,"bad");try{throw failure}catch ValueError::{message:text,.._}{text}'''
  for text in (source,format_source(source),format_program(parse_program(source))):self.assertEqual(Framework.evaluate(text),'bad')
  at=SOURCE.index('PlanFailure');report=references(SOURCE,position=at)
  self.assertTrue(report['complete']);self.assertEqual(len(report['references']),3)
  changed=rename(SOURCE,position=at,new_name='RoleFailure')['files']['main.chaos']
  self.assertIn('RoleFailure::{stage:stage',changed);self.assertEqual(Framework.evaluate(changed),EXPECTED)

 def test_analysis_lsp_and_document_highlighting_do_not_run_user_code(self):
  source='class Failure(ValueError){fn __init__(self,text:String){tool("never",text)}};Failure("bad")'
  with patch.object(Evaluator,'run',side_effect=AssertionError('ran')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('evaluated')):
   report=analyze(source,position=source.rindex('"bad"'));self.assertEqual(report['diagnostics'],[])
   result=convert('textDocument/signatureHelp',report,SimpleNamespace(name='main.chaos'),{'main.chaos':source})
   self.assertIn('text: String',result['signatures'][0]['label'])
   self.assertIn('chaos-token-type',render_code(SOURCE,'chaos'))
   self.assertIn('只读异常字段',render_code('let failure=ValueError("bad");failure.code','chaos'))

 def test_http_sdk_ir_edits_preserve_prior_snapshot(self):
  with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
   root=Path(folder);server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   try:
    client=Client('http://127.0.0.1:'+str(server.server_port));project=client.create_project('nominal-exceptions',source=SOURCE,capabilities=[])
    old=client.start(project['id'],project['version'],None);self.assertEqual(old.result(),EXPECTED)
    program=client.program(project['id'])['program'];literal=program['root']['statements'][0]['body']['statements'][0]['value']
    updated=client.edit_program(project['id'],project['version'],operations=[{'op':'set','id':literal['id'],'field':'value','value':'execute'}])
    self.assertEqual(client.start(project['id'],updated['version'],None,mode='graph').result(),['execute','bad input',True])
    self.assertEqual(old.get()['snapshot']['source'],SOURCE)
    self.assertEqual(client.analyze(updated['source']),Framework.analyze(updated['source']))
   finally:server.shutdown();thread.join(3);server.server_close()

if __name__=='__main__':unittest.main()
