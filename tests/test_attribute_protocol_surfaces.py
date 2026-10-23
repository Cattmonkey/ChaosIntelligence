"""Attribute customization shares source, IR, inert IDE and public runtimes."""
import contextlib
import inspect
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from chaos import Client,Framework,Settings
from chaos.cli import main
from chaos.code_presentation import render_code
from chaos.ide_language import analyze,builtin_symbols
from chaos.language import Evaluator,Parser
from chaos.lsp_adapters import convert
from chaos.native_specs import spec_for
from chaos.program import decode_program,format_program,parse_program,plain
from chaos.server import make_server
from chaos.source_format import format_source
from tests.test_attribute_protocol_integration import BOX

SOURCE='// Native hooks use the original attribute syntax.\n'+BOX+'\nlet box=Box();box.base=40;box.answer'


class AttributeProtocolSurfaces(unittest.TestCase):
    def test_default_native_signatures_metadata_and_aliases(self):
        expected={'default_getattr':(['value','name'],'Any'),'default_setattr':(['value','name','item'],'Any'),
                  'default_delattr':(['value','name'],'Null'),'default_dir':(['value'],'List')}
        ev=Evaluator();symbols=builtin_symbols()
        for name,(params,result)in expected.items():
            spec=spec_for(name);native=ev.env.get(name)
            self.assertEqual(native.spec_id,spec.id);self.assertEqual([p.name for p in spec.parameters],params)
            self.assertEqual(list(inspect.signature(native.call).parameters),params);self.assertEqual(spec.result_type,result)
            self.assertEqual(symbols[name].returns,result)
            report=analyze('let delegated='+name+';delegated(')
            self.assertEqual([p['name']for p in report['signatureHelp']['parameters']],params)
        self.assertEqual(list(inspect.signature(ev.env.get('getattr').call).parameters),['value','name'])
        source='default_setattr(';report=analyze(source)
        lsp=convert('textDocument/signatureHelp',report,SimpleNamespace(name='main.chaos'),{'main.chaos':source})
        self.assertIn('name: String',lsp['signatures'][0]['label'])

    def test_source_ir_format_macro_and_both_dialects_share_nodes(self):
        macro='macro component(read,write,erase,error){quote{let default_getattr=unquote(read);let default_setattr=unquote(write);let default_delattr=unquote(erase);let AttributeError=unquote(error);'+BOX+'Box}};let Type=component!(default_getattr,default_setattr,default_delattr,AttributeError);let value=Type();value.base=40;value.answer'
        for source in (SOURCE,macro):
            for version in (1,2):
                program=parse_program(source,language_version=version)
                for text in (format_program(program),format_source(source,language_version=version,line_width=50)):
                    self.assertEqual(Framework.evaluate(text),42)
                    self.assertEqual(plain(Parser(text).program()),plain(decode_program(program)))
        formatted=format_source(SOURCE);self.assertEqual(format_source(formatted),formatted)
        self.assertIn('// Native hooks',formatted);self.assertIn('default_getattr',render_code(formatted,'chaos'))

    def test_intercepted_attributes_have_no_false_type_signature_or_reference(self):
        source='''class Base{let value:Int=1;fn method(self,x:Int)->Int{x}
fn __getattribute__(self,name){fn(text:String){text}}};class Child(Base){};
let obj=Child();let value=obj.value;let read=getattr(obj,"value");let call=obj.method;'''
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran source')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('ran source')):
            report=analyze(source);self.assertEqual(report['diagnostics'],[])
            for name in ('value','read','call'):
                self.assertEqual(next(s['type']for s in report['symbols']if s['name']==name and s['from']>source.index('let obj')),'Any')
            self.assertIsNone(analyze(source+'obj.method(')['signatureHelp'])
            members={c['label']:c for c in analyze(source+'obj.')['completions']}
            self.assertIn('Any',members['method']['detail']);self.assertNotIn('x: Int',members['method']['detail'])
            self.assertEqual(members['method']['type'],'property')
            # The raw method's declaration is not a proven target of intercepted access.
            self.assertIsNone(analyze(source,position=source.rindex('obj.method')+5)['definition'])

    def test_dynamic_names_write_hooks_and_implicit_protocols_stay_conservative(self):
        source='''class Proxy{let value:Int=1;fn __getattr__(self,name){42}
fn __setattr__(self,name,item){null};fn __call__(self,x:Int)->Int{x};fn __getattribute__(self,name){"dynamic"}};
let obj=Proxy();obj.value="accepted";obj.value+=1;let result=obj(42);'''
        report=analyze(source);self.assertEqual(report['diagnostics'],[])
        self.assertEqual(next(s['type']for s in report['symbols']if s['name']=='result'),'Int')
        known='class C{let value:Int=1};let obj=C();obj.value="bad"'
        self.assertTrue(any(d['code']=='immutable_binding'for d in analyze(known)['diagnostics']))
        dynamic='class C{fn __getattr__(self,name){42}fn __dir__(self){["unpublished"]}};let obj=C();'
        self.assertNotIn('unpublished',{c['label']for c in analyze(dynamic+'obj.')['completions']})
        self.assertEqual(next(s['type']for s in analyze(dynamic+'let unknown=obj.anything')['symbols']if s['name']=='unknown'),'Any')
        fallback='class C{@property\nfn value(self)->Int{throw AttributeError("value")}fn __getattr__(self,name){"fallback"}};let result=C().value;result'
        self.assertEqual(next(s['type']for s in analyze(fallback)['symbols']if s['name']=='result'),'Any')
        self.assertEqual(Framework.evaluate(fallback),'fallback')

    def test_same_source_hook_installation_invalidates_actual_class_and_subclasses(self):
        for mutation in ('C.__getattribute__=fn(name){"dynamic"}',
                         'setattr(C,"__getattribute__",fn(name){"dynamic"})',
                         'let install=setattr;install(value=C,name="__getattribute__",item=fn(name){"dynamic"})',
                         'default_setattr(C,"__getattribute__",fn(name){"dynamic"})',
                         'setattr(C,"__getattribute__",*[fn(name){"dynamic"}])',
                         'setattr(C,**{name:"__getattribute__",item:fn(name){"dynamic"}})'):
            with self.subTest(mutation=mutation):
                source='class C{let value:Int=1};class Child(C){};let before=Child();let original=before.value;'+mutation+';let actual=before.value;actual'
                self.assertEqual(Framework.evaluate(source),'dynamic')
                self.assertEqual(next(s['type']for s in analyze(source)['symbols']if s['name']=='actual'),'Any')
        for erase in ('delattr','default_delattr'):
            source='class C{let value:Int=1};C.__getattribute__=fn(name){"dynamic"};'+erase+'(C,"__getattribute__");let actual=C().value;actual'
            self.assertEqual(Framework.evaluate(source),1)
            self.assertEqual(next(s['type']for s in analyze(source)['symbols']if s['name']=='actual'),'Any')
        shadow='class C{let value:Int=1};let setattr=fn(value,name,item){null};setattr(C,"__getattribute__",fn(name){"fake"});let actual=C().value;actual'
        self.assertEqual(Framework.evaluate(shadow),1)
        self.assertEqual(next(s['type']for s in analyze(shadow)['symbols']if s['name']=='actual'),'Int')
        meta='class Meta(Type){};class C(metaclass=Meta){let value:Int=1};Meta.__getattribute__=fn(name){"dynamic"};let actual=C.value;actual'
        self.assertEqual(Framework.evaluate(meta),'dynamic')
        self.assertEqual(next(s['type']for s in analyze(meta)['symbols']if s['name']=='actual'),'Any')

    def test_attribute_scan_budget_never_certifies_hook_absence(self):
        from chaos.ide_language import Analysis,Info
        def state(steps,hook=True):
            analysis=Analysis('obj.answer=2','main.chaos',{},'main.chaos',None)
            klass=Info('Class',class_complete=True);klass.class_info=klass;klass.mro=[klass]
            if hook:klass.own_members['__getattribute__']=Info('Function')
            instance=Info('C',class_info=klass,members={'answer':Info('Int',mutable=False)})
            analysis.member_steps=steps
            return analysis,klass,instance
        for steps in (19999,20000,20001):
            analysis,klass,instance=state(steps)
            self.assertEqual(analysis.attribute_info(instance,'answer').type,'Any')
            self.assertIsNotNone(analysis.attribute_hook(instance,'__setattr__'))
            analysis.walk(Parser('obj.answer=2').program()[1][0],{'obj':instance})
            self.assertEqual(analysis.diagnostics,[])
            self.assertEqual(analysis.attribute_info(Info('Record',members={'answer':Info('Int')}),'answer').type,'Int')
            self.assertEqual(analysis.attribute_info(Info('Super',class_info=klass,members={'answer':Info('Int')}),'answer').type,'Int')
            self.assertEqual(analysis.attribute_info(Info('Class',metaclass_info=klass,members={'answer':Info('Int')}),'answer').type,'Any')
        analysis,klass,instance=state(20000)
        known=klass.own_members['__getattribute__'];analysis.protocol_cache[(id(klass),'__getattribute__')]=known
        self.assertIs(analysis.attribute_hook(instance,'__getattribute__'),known)
        self.assertEqual(analysis.member_steps,20000)
        analysis,klass,instance=state(20000,hook=False)
        analysis.protocol_cache[(id(klass),'__getattribute__')]=None
        self.assertIsNone(analysis.attribute_hook(instance,'__getattribute__'))
        self.assertEqual(analysis.member_steps,20000)
        analysis.member_steps=20001
        self.assertIsNotNone(analysis.attribute_hook(instance,'__getattribute__'))

    def test_cli_sdk_http_ir_and_old_snapshot_agree(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            root=Path(folder);path=root/'consumer.chaos';path.write_text(SOURCE)
            output=io.StringIO();errors=io.StringIO()
            with patch.object(sys,'argv',['chaos','run',str(path)]),contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):self.assertEqual(main(),0)
            self.assertEqual(json.loads(output.getvalue()),42);self.assertEqual(errors.getvalue(),'')
            self.assertEqual(Framework.evaluate(path.read_text()),42)
            server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));p=client.create_project('attribute-surface',source=SOURCE,capabilities=[])
                old=client.start(p['id'],p['version'],None);self.assertEqual(old.result(),42)
                program=client.program(p['id'])['program']
                assignment=next(node for node in program['root']['statements']if node['kind']=='set_member')
                updated=client.edit_program(p['id'],p['version'],operations=[{'op':'set','id':assignment['value']['id'],'field':'value','value':41}])
                self.assertEqual(client.start(p['id'],updated['version'],None,mode='graph').result(),43)
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.analyze(updated['source']),Framework.analyze(updated['source']))
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_pattern_vars_json_and_default_directory_do_not_execute_hooks(self):
        source='''var calls=0;class Item{var value:Int=42;
fn __getattribute__(self,name){calls+=1;default_getattr(self,name)}
fn __dir__(self){calls+=1;["value"]}}
let item=Item();let Item::{value:raw}=item;let stored=vars(item);let names=default_dir(item);
let wire=parse_json(json_stringify(stored));let before=calls;let visible=dir(item);
[raw,stored,contains(names,"value"),wire,item,before,calls,visible]'''
        self.assertEqual(Framework.evaluate(source),[42,{},True,{}, {'$instance':'Item'},0,1,['value']])

    def test_directory_generators_are_explicit_sync_and_keep_duplicates(self):
        source='''class Direct{fn* __dir__(self){yield "z";yield "a";yield "z"}}
class Returned{fn __dir__(self){fn* names(){yield "b";yield "a"};names()}}
[dir(Direct()),dir(Returned())]'''
        self.assertEqual(Framework.evaluate(source),[['a','z','z'],['a','b']])

    def test_default_delegation_preserves_descriptors_contracts_and_super(self):
        source='''class Base{var stored:Int=0;@property\nfn value(self){self.stored}
@value.setter\nfn value(self,item:Int){self.stored=item;null}}
class Child(Base){fn __getattribute__(self,name){default_getattr(self,name)}
fn __setattr__(self,name,item){default_setattr(self,name,item);null}}
let box=Child();box.value=42;[box.value,super(Child,box).value,try{default_setattr(box,"stored","bad")}catch e{e.code}]'''
        self.assertEqual(Framework.evaluate(source),[42,42,'runtime_error'])


if __name__=='__main__':unittest.main()
