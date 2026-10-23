"""Editor analysis uses real grammar, scoped symbols and lossless token formatting."""
import unittest
from pathlib import Path
from unittest.mock import patch

from chaos.ide_language import analyze,format_source,SourceMap
from chaos.language import Parser,tokenize,Evaluator,LanguageError,ResourceLimit
from chaos.program import plain


class EditorAnalysis(unittest.TestCase):
    def labels(self,source,**kwargs):return {item['label'] for item in analyze(source,**kwargs)['completions']}

    def test_lexical_and_semantic_colors_and_utf16(self):
        source='// 🙂 comment\nlet greeting="🙂";fn add(x:Int)->Int{x+1};add(2)'
        result=analyze(source)
        mapping=SourceMap(source)
        decoded={source[__import__('bisect').bisect_left(mapping.units,t['from']):__import__('bisect').bisect_left(mapping.units,t['to'])]:t['type'] for t in result['tokens']}
        self.assertEqual(decoded['// 🙂 comment'],'comment')
        self.assertEqual(decoded['"🙂"'],'string')
        self.assertEqual(decoded['Int'],'type')
        self.assertEqual(decoded['add'],'function')
        self.assertEqual(decoded['x'],'parameter')
        quote=next(t for t in result['tokens'] if t['type']=='string')
        self.assertEqual(quote['to']-quote['from'],4)
        self.assertTrue(result['complete']);self.assertEqual(result['diagnostics'],[])

    def test_strings_and_comments_do_not_create_declarations(self):
        source='let text="fn phantom(x){x}"; // let ghost=1\nph'
        labels=self.labels(source)
        self.assertNotIn('phantom',labels);self.assertNotIn('ghost',labels)
        self.assertEqual(analyze('"unterminated fn fake() {}')['completions'],[])

    def test_nested_scope_does_not_leak_and_future_names_are_unavailable(self):
        self.assertNotIn('hidden',self.labels('fn f(){let hidden=1;hidden};hi'))
        self.assertNotIn('hidden',self.labels('fn f(){let hidden=1;}'))
        self.assertIn('f',self.labels('// heading\nfn f(){let hidden=1;}'))
        self.assertIn('local',self.labels('fn f(param:Int){let local=param;lo'))
        self.assertIn('param',self.labels('fn f(param:Int){pa'))
        self.assertNotIn('future',self.labels('fn f(){fu};let future=42',position=9))
        self.assertTrue(any(d['code']=='undefined_name' for d in analyze('fn f(){future};let future=42')['diagnostics']))

    def test_default_expression_uses_outer_environment(self):
        result=analyze('fn f(x=f){x};null')
        self.assertTrue(any(d['code']=='undefined_name' for d in result['diagnostics']))
        result=analyze('let previous=42;fn f(x=previous){x};f()')
        self.assertFalse(result['diagnostics'])

    def test_rest_signature_hover_and_keyword_completions(self):
        source='fn collect(a:Int,b=2,*rest,required,**options)->Record{{}};collect(1,req'
        result=analyze(source)
        item=next(c for c in result['completions'] if c['label']=='required=')
        self.assertEqual(item['apply'],'required=')
        self.assertIn('*rest',item['detail']);self.assertIn('**options',item['detail'])
        self.assertIn('-> Record',result['hover']['contents'])
        self.assertNotIn('a=',self.labels(source))

    def test_module_exports_and_relative_path_completion(self):
        files={'lib/math.chaos':'let secret=9;fn twice(x:Int)->Int{x*2};export{twice}'}
        result=analyze('import "../lib/math.chaos" as M;M.',file='app/main.chaos',entry='app/main.chaos',files=files)
        self.assertEqual({c['label'] for c in result['completions']},{'twice'})
        self.assertIn('-> Int',result['completions'][0]['detail'])
        labels=self.labels('import "../l',file='app/main.chaos',entry='app/main.chaos',files=files)
        self.assertIn('../lib/math.chaos',labels)
        self.assertIn('std/math',self.labels('import "std/m'))

    def test_import_errors_and_cycles_are_diagnostics(self):
        self.assertTrue(any(d['code']=='module_error' for d in analyze('import "missing.chaos" as M;M')['diagnostics']))
        files={'lib.chaos':'import "lib.chaos" as L;export{}'}
        result=analyze('import "lib.chaos" as L;L.',files=files)
        self.assertTrue(any(d['code']=='module_error' for d in result['diagnostics']))

    def test_standard_modules_and_record_members(self):
        self.assertIn('sqrt',self.labels('import "std/math" as M;M.s'))
        self.assertIn('answer',self.labels('let r={answer:42};r.'))
        self.assertNotIn('answer',self.labels('var r={answer:42};r={other:2};r.'))
        result=analyze('import "std/option" as O;let option:O.Option=O.Some(1);option')
        self.assertFalse(any(d['code']=='type_mismatch' for d in result['diagnostics']))

    def test_syntax_and_definite_type_binding_diagnostics(self):
        cases=[('let x:Int="bad"','type_mismatch'),('let x=1;x=2','immutable_binding'),
               ('let x=1;let x=2','duplicate_binding'),('fn f(a){};f()','argument_error'),
               ('fn f(a){};f(1,a=2)','argument_error'),('fn f(a:Int){};f("bad")','type_mismatch')]
        for source,code in cases:
            with self.subTest(source=source):self.assertTrue(any(d['code']==code for d in analyze(source)['diagnostics']))
        result=analyze('let x =')
        self.assertFalse(result['complete']);self.assertTrue(result['diagnostics'])
        self.assertNotIn('x',{s['name'] for s in result['symbols']})

    def test_unknown_callable_and_dynamic_spread_not_misdiagnosed(self):
        self.assertFalse(analyze('fn f(target:Function){target(1,2)}')['diagnostics'])
        self.assertFalse(analyze('fn f(a){};f(*input)')['diagnostics'])
        self.assertFalse(analyze('fn f(a){a};partial(f,1)()')['diagnostics'])

    def test_analysis_does_not_execute_defaults_tools_or_agents(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('must not execute')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('must not execute')):
            result=analyze('fn f(x=tool("side-effect",null)){x};agent A {};A(input=null)')
        self.assertTrue(result['complete'])

    def test_recovery_keeps_complete_siblings_without_fabricating_bad_bindings(self):
        result=analyze('fn f(x:Int){let good=1;let bad=;go')
        self.assertIn('good',{c['label'] for c in result['completions']})
        self.assertNotIn('bad',{s['name'] for s in result['symbols']})
        self.assertFalse(result['complete'])

    def test_utf16_diagnostic_and_hover_after_emoji(self):
        source='let emoji="🙂";let n:Int="bad";n'
        result=analyze(source)
        error=next(d for d in result['diagnostics'] if d['code']=='type_mismatch')
        self.assertEqual(error['from'],len(source[:source.index('"bad"')].encode('utf-16-le'))//2)
        self.assertEqual(result['hover']['contents'],'n: Int')

    def test_bounds_and_invalid_position(self):
        with self.assertRaises(LanguageError):analyze('42',position=100)
        with self.assertRaises(LanguageError):analyze('42',files=[])
        with self.assertRaises(ResourceLimit):analyze(' '*100001)


class EditorFormatting(unittest.TestCase):
    def check(self,source):
        result=format_source(source)
        self.assertEqual(plain(Parser(source).program()),plain(Parser(result).program()))
        old=[(t.kind,t.value) for t in tokenize(source,trivia=True) if t.kind not in ('space','eof')]
        new=[(t.kind,t.value) for t in tokenize(result,trivia=True) if t.kind not in ('space','eof')]
        self.assertEqual(old,new)
        self.assertEqual(format_source(result),result)
        return result

    def test_comments_strings_and_function_layout(self):
        source='// start 🙂\nfn f(x:Int=2){ // body\nlet s="// text # 😀";x+1};f() # end'
        result=self.check(source)
        self.assertIn('\n  let s = "// text # 😀";',result)
        self.assertIn('// body',result);self.assertIn('# end',result)
        self.assertEqual(Evaluator().run(result),3)

    def test_standalone_comments_remain_standalone_and_keep_trailing_text(self):
        source='fn f(){42\n// explanation  \n};f()'
        formatted=self.check(source)
        self.assertIn('\n  // explanation  \n',formatted)
        self.assertNotIn('42 //',formatted)

    def test_control_patterns_defaults_and_spreads(self):
        sources=['fn f(a,b=2,*,c=3,**kw){a+b+c};f(*[1],**{c:4})',
                 'var x=0;while x<2{try{x=x+1}finally{null}};x',
                 'data O{None,Some(value)};match Some(1){Some(x)=>x,None()=>0}',
                 'let r={a:1,b:[2,3]};r.a',
                 'fn f(x=fn(y){y+1}){x(1)};f()',
                 'let x=-1;[!false,x*-2]']
        for source in sources:
            with self.subTest(source=source):self.check(source)

    def test_all_repository_examples_remain_semantically_identical(self):
        root=Path(__file__).resolve().parents[1]
        for path in (root/'examples').rglob('*.chaos'):
            with self.subTest(path=str(path)):self.check(path.read_text())

    def test_invalid_draft_never_yields_replacement(self):
        for source in ('fn f(', 'let x=', '"unterminated'):
            with self.subTest(source=source),self.assertRaises(LanguageError):format_source(source)

    def test_analysis_ranges_do_not_change_default_ast(self):
        source='fn f(x:Int){x+1};f(2).answer'
        ordinary=Parser(source).program();editor=Parser(source,ranges=True).program()
        self.assertEqual(plain(ordinary),plain(editor))
        call=ordinary[1][1][1]
        self.assertFalse(hasattr(call[1],'span'))
        self.assertTrue(hasattr(editor[1][1][1][1],'span'))

    def test_default_lexer_unchanged_and_ide_mode_retains_trivia(self):
        source='// comment\nlet x="🙂";x'
        strict=tokenize(source);rich=tokenize(source,trivia=True)
        self.assertFalse(any(t.kind in ('space','comment') for t in strict))
        self.assertTrue(any(t.kind=='comment' for t in rich))
        self.assertTrue(all(source[t.start:t.end]==t.value for t in rich if t.kind!='eof'))
        self.assertEqual(tokenize('"unfinished',trivia=True,tolerant=True)[0].kind,'invalid')
        with self.assertRaises(LanguageError):tokenize('"unfinished')


class StreamLanguageAnalysis(unittest.TestCase):
    def test_async_stream_and_await_types_are_inferred_without_execution(self):
        source='async fn* stream()->Int{yield 1;return 2};async fn answer()->Int{42};let g=stream();let pending=answer();let result=await pending;let nextResult=await anext(g);let values=await acollect(g);let closed=await aclose(g);null'
        result=analyze(source);self.assertTrue(result['complete']);self.assertEqual(result['diagnostics'],[])
        types={symbol['name']:symbol['type']for symbol in result['symbols']}
        self.assertEqual({name:types[name]for name in ('g','pending','result','nextResult','values','closed')},{'g':'AsyncGenerator','pending':'Task','result':'Int','nextResult':'Record','values':'List','closed':'Null'})
        self.assertIn('async generator',next(symbol['detail']for symbol in result['symbols']if symbol['name']=='stream'))
        self.assertIn('asend',{item['label']for item in analyze('ase')['completions']})

    def test_qualified_macro_completion_hover_and_closed_quote_scopes(self):
        files={'macros.chaos':'macro public(value){helper!(value)};macro helper(value){quote{unquote(value)}};export{public}'}
        source='import "macros.chaos" as M;M.pu'
        result=analyze(source,files=files);choice=next(c for c in result['completions']if c['label']=='public')
        self.assertEqual(choice['apply'],'public!');self.assertEqual(choice['type'],'function')
        self.assertNotIn('helper',{c['label']for c in result['completions']})
        self.assertTrue(any(d['code']=='macro_phase' for d in analyze('import "macros.chaos" as M;M.public',files=files)['diagnostics']))
        source='import "macros.chaos" as M;M.public!(42)'
        result=analyze(source,files=files,position=source.index('public')+2)
        self.assertFalse(result['diagnostics'],result['diagnostics']);self.assertIn('macro public',result['hover']['contents'])
        self.assertFalse(analyze('macro first(x){quote{helper!(unquote(x))}};macro helper(x){quote{unquote(x)}};first!(42)')['diagnostics'])
        self.assertTrue(any(d['code']=='undefined_name'for d in analyze('let secret=42;macro bad(){quote{secret}};bad!()')['diagnostics']))

    def test_macro_namespaces_are_static_and_not_copied_by_runtime_aliases(self):
        from chaos.modules import ProgramBundle
        files={'m.chaos':'macro value(x){quote{unquote(x)}};fn runtime(){1};export{value,runtime}'}
        invalid='import "m.chaos" as M;let Alias=M;Alias.value!(42)'
        self.assertTrue(any(d['code']=='undefined_macro' for d in analyze(invalid,files=files)['diagnostics']))
        with self.assertRaises(LanguageError):ProgramBundle(invalid,files)
        choices=analyze('import "m.chaos" as M;let Alias=M;Alias.',files=files)['completions']
        self.assertEqual({c['label'] for c in choices},{'runtime'})
        source='import "m.chaos" as M;fn f(M){M.value!(42)};f(null)'
        ProgramBundle(source,files)
        self.assertFalse(analyze(source,files=files)['diagnostics'])
        choices=analyze('import "m.chaos" as M;fn f(M){M.va',files=files)['completions']
        self.assertEqual(next(c['apply'] for c in choices if c['label']=='value'),'value!')
        self.assertNotIn('value',{c['label'] for c in analyze('import "m.chaos" as M;let nested={alias:M};nested.alias.',files=files)['completions']})

    def test_qualified_macro_repeated_name_has_distinct_token_ranges(self):
        files={'m.chaos':'macro same(x){quote{unquote(x)}};export{same}'}
        source='import "m.chaos" as same;same.same!(42)'
        alias=source.index('same.same');member=alias+5
        result=analyze(source,files=files,position=member+2)
        self.assertFalse(result['diagnostics'])
        colors={t['from']:t['type'] for t in result['tokens']}
        self.assertEqual(colors[alias],'variable');self.assertEqual(colors[member],'function')
        self.assertIn('macro same',result['hover']['contents'])
        self.assertEqual((result['hover']['from'],result['hover']['to']),(member,member+4))

    def test_async_loop_scopes_and_semantic_colors(self):
        source='async fn* items(){yield 1};async for item in items(){let seen=item;seen};null'
        result=analyze(source);self.assertFalse(result['diagnostics'],result['diagnostics'])
        tokens=[(source[t['from']:t['to']],t['type'])for t in result['tokens']]
        self.assertIn(('async','keyword'),tokens);self.assertIn(('yield','keyword'),tokens)
        self.assertTrue(all(kind=='keyword' for text,kind in tokens if text=='async'))
        self.assertNotIn('item',{item['label']for item in analyze(source+';ite')['completions']})


class ObjectLanguageAnalysis(unittest.TestCase):
    def test_instance_members_bound_methods_callable_and_inherited_constructor(self):
        source='class Base {var value:Int=40;fn __init__(self,value:Int=40){self.value=value;null};fn read(self,delta:Int=2)->Int{self.value+delta}}class Child(Base){fn __call__(self,delta:Int)->Int{super(Child,self).read(delta)}}let item=Child();let detached=item.read;'
        result=analyze(source+'item.')
        choices={c['label']:c for c in result['completions']}
        self.assertEqual(set(choices),{'value','read','__init__','__call__'})
        self.assertEqual(choices['read']['detail'],'read(delta: Int = …) -> Int')
        for suffix,keyword in [('Child(','value='),('item(','delta='),('detached(','delta=')]:
            result=analyze(source+suffix);self.assertIn(keyword,{c['label']for c in result['completions']});self.assertNotIn('self=',{c['label']for c in result['completions']})
        self.assertFalse(analyze(source+'item(2)')['diagnostics'])
        self.assertTrue(any(d['code']=='argument_error'for d in analyze(source+'item()')['diagnostics']))

    def test_nominal_annotations_imports_c3_and_field_contracts(self):
        files={'model.chaos':'class Model {var count:Int=1;fn read(self)->Int{self.count}}export{Model}'}
        source='import "model.chaos" as M;fn read(value:M.Model){value.co'
        self.assertIn('count',{c['label']for c in analyze(source,files=files)['completions']})
        source='class A {fn same(self)->Int{1}}class B(A){}class C(A){fn same(self)->String{"C"}}class D(B,C){}let d=D();d.sa'
        self.assertIn('-> String',next(c['detail']for c in analyze(source)['completions']if c['label']=='same'))
        source='class C {let fixed=1;var number:Int=2}let c=C();c.fixed=2;c.number="bad"'
        self.assertEqual({d['code']for d in analyze(source)['diagnostics']},{'immutable_binding','type_mismatch'})

    def test_class_contract_identity_and_unknown_class_parameter_are_conservative(self):
        source='class A{}class B(A){}class Other{}let child=B();let accepted:A=child;fn need(value:A){null};need(child)'
        self.assertFalse(analyze(source)['diagnostics'])
        source='class A{}class Other{}let other=Other();let rejected:A=other;fn need(value:A){null};need(other)'
        self.assertEqual(sum(d['code']=='type_mismatch'for d in analyze(source)['diagnostics']),2)
        self.assertFalse(analyze('fn construct(kind:Class){kind(1)}')['diagnostics'])

    def test_function_nominal_contracts_capture_definition_scope(self):
        valid='class A{}fn f(x:A){null};let x=A();do{class A{};f(x)}'
        self.assertIsNone(Evaluator().run(valid));self.assertFalse(analyze(valid)['diagnostics'])
        invalid='class A{}fn f(x:A){null};do{class A{};let x=A();f(x)}'
        with self.assertRaises(LanguageError):Evaluator().run(invalid)
        self.assertTrue(any(d['code']=='type_mismatch'for d in analyze(invalid)['diagnostics']))
        files={'a.chaos':'class A{}fn accept(x:A){null};export{A,accept}', 'b.chaos':'class A{}export{A}'}
        source='import "a.chaos" as L;import "b.chaos" as R;let left=L.A();let right=R.A();L.accept(left);L.accept(right)'
        diagnostics=analyze(source,files=files)['diagnostics']
        self.assertEqual(sum(d['code']=='type_mismatch'for d in diagnostics),1)

    def test_field_callables_do_not_bind_and_analysis_does_not_execute_class(self):
        source='agent a{};class C {let worker=a;let callback=fn(value:Int)->Int{value};fn* values(self){yield 1};async fn run(self)->Int{2}}let c=C();'
        with patch.object(Evaluator,'run',side_effect=AssertionError('must not execute')):
            self.assertFalse(analyze(source+'c.callback(1)')['diagnostics'])
            choices={c['label']:c for c in analyze(source+'c.')['completions']}
        self.assertIn('input',choices['worker']['detail']);self.assertIn('value: Int',choices['callback']['detail'])
        self.assertIn('Generator',choices['values']['detail']);self.assertIn('Task',choices['run']['detail'])


if __name__=='__main__':unittest.main()


class DecoratorAnalysis(unittest.TestCase):
    SOURCE="""class C {
 @staticmethod
 fn plus(x:Int=2)->Int{x+1}
 @classmethod
 fn make(cls,x:Int=2)->C{cls()}
 @property
 fn value(self)->Int{42}
 @value.setter
 fn value(self,value:Int){null}
 @property
 async fn later(self)->Int{42}
}
let c=C();"""

    def test_static_class_and_property_completions(self):
        result=analyze(self.SOURCE+'c.')
        members={row['label']:row['detail'] for row in result['completions']}
        self.assertIn('x: Int',members['plus']);self.assertNotIn('self',members['plus'])
        self.assertIn('x: Int',members['make']);self.assertNotIn('cls',members['make'])
        self.assertIn('value: Int',members['value']);self.assertIn('可读写',members['value'])
        self.assertIn('later: Task',members['later'])
        classes={row['label']:row['detail'] for row in analyze(self.SOURCE+'C.')['completions']}
        self.assertIn('property 描述符',classes['value'])
        self.assertEqual(analyze(self.SOURCE+'c.value=42;[C.plus(),c.make()]')['diagnostics'],[])

    def test_builtin_aliases_recognized_but_shadowed_and_unknown_decorators_do_not_execute(self):
        alias='let static=staticmethod;class C{@static\nfn f(x:Int)->Int{x}};C.f(42)'
        self.assertEqual(analyze(alias)['diagnostics'],[])
        shadow='fn staticmethod(x){42};class C{@staticmethod\nfn f(self){1}};let c=C();c.f'
        result=analyze(shadow);self.assertEqual(result['hover']['contents'],'Any · 用户装饰器返回值未知')
        source='fn decorate(f){throw "analysis must not execute"};@decorate\nfn f(x:Int){x};f(1,2)'
        self.assertEqual(analyze(source)['diagnostics'],[])
        cls=analyze('fn decorate(v){1};@decorate\nclass C{};C')
        self.assertEqual(cls['hover']['contents'],'Any · 用户装饰器返回值未知')

    def test_readonly_property_assignment_and_cold_async_result(self):
        result=analyze(self.SOURCE+'c.later=42')
        self.assertTrue(any(d['code']=='immutable_binding' for d in result['diagnostics']))
        result=analyze(self.SOURCE+'let answer=await c.later;answer')
        self.assertIn('Int',result['hover']['contents'])

    def test_staticmethod_keeps_nominal_parameter_contract_at_definition(self):
        prefix='class A{};class C{@staticmethod\nfn f(x:A){null}};let x=A();'
        self.assertEqual(analyze(prefix+'do{class A{};C.f(x)}')['diagnostics'],[])
        bad=analyze(prefix+'do{class A{};let x=A();C.f(x)}')
        self.assertTrue(any(d['code']=='type_mismatch' for d in bad['diagnostics']))

    def test_direct_descriptor_field_and_inherited_classmethod_super(self):
        source='class Base{let static=staticmethod(fn(x:Int)->Int{x});@classmethod\nfn make(cls)->Class{cls}};class Child(Base){@classmethod\nfn other(cls)->Class{super(Child,cls).make()}};let c=Child();[c.static(42),Child.other()]'
        self.assertEqual(analyze(source)['diagnostics'],[])

    def test_property_named_accessors_and_setter_contract_are_distinct(self):
        source='class C{let p=property(fset=fn(self,value:String){null},fget=fn(self)->Int{42})};let c=C();'
        result=analyze(source+'c.p')
        self.assertEqual(result['diagnostics'],[]);self.assertIn('p: Int',result['hover']['contents'])
        self.assertEqual(analyze(source+'c.p="valid"')['diagnostics'],[])
        self.assertTrue(any(d['code']=='type_mismatch' for d in analyze(source+'c.p=42')['diagnostics']))
        writeonly=analyze('class C{let p=property(fget=null,fset=fn(self,value){null})};let c=C();c.p')
        self.assertIn('p: Any',writeonly['hover']['contents'])

    def test_unknown_decorator_property_update_and_first_property_are_conservative(self):
        source='fn install(old){fn(f){old.setter(f)}}class C{var n=0\n@property\nfn x(self){self.n}\n@install(x)\nfn x(self,v){self.n=v;null}\n}let c=C();c.x=42;c.x'
        self.assertEqual(Evaluator().run(source),42)
        self.assertEqual(analyze(source)['diagnostics'],[])
        first='fn install(f){property(f,fn(self,v){self.n=v;null})}class C{var n=0\n@install\nfn x(self){self.n}\n}let c=C();c.x=42;c.x'
        self.assertEqual(Evaluator().run(first),42)
        self.assertEqual(analyze(first)['diagnostics'],[])
        ordinary=analyze('fn identity(f){f};class C{let x=1;@identity\nfn x(self){2}}')
        self.assertTrue(any(d['code']=='duplicate_binding' for d in ordinary['diagnostics']))
        readonly=analyze('class C{@property\nfn x(self){42}};C().x=2')
        self.assertTrue(any(d['code']=='immutable_binding' for d in readonly['diagnostics']))

    def test_mutable_class_builtin_alias_and_unknown_property_lineage(self):
        source='class Helpers{var wrap=staticmethod}Helpers.wrap=fn(f){staticmethod(fn(){42})};class C{\n@Helpers.wrap\nfn f(self,x){x}\n}C.f()'
        self.assertEqual(Evaluator().run(source),42);self.assertEqual(analyze(source)['diagnostics'],[])
        both='fn prop(f){property(f)}fn set(old){fn(f){old.setter(f)}}class C{var n=0\n@prop\nfn x(self){self.n}\n@set(x)\nfn x(self,v){self.n=v;null}\n}let c=C();c.x=42;c.x'
        self.assertEqual(Evaluator().run(both),42);self.assertEqual(analyze(both)['diagnostics'],[])

    def test_mutable_descriptor_storage_contract_is_not_instance_setter_contract(self):
        source='class C{var p:Descriptor=property(fn(self)->Int{42},fn(self,v:String){null})}let c=C();'
        self.assertEqual(Evaluator().run(source+'c.p="valid"'),'valid')
        self.assertEqual(analyze(source+'c.p="valid"')['diagnostics'],[])
        self.assertTrue(any(d['code']=='type_mismatch' for d in analyze(source+'C.p="invalid"')['diagnostics']))

    def test_unknown_factory_property_field_remains_conservative(self):
        source='fn make(){property(fn(self){42},fn(self,v){null})};class C{let x=make()};let c=C();c.x=1;c.x'
        self.assertEqual(Evaluator().run(source),42);self.assertEqual(analyze(source)['diagnostics'],[])

    def test_ordinary_duplicate_unknown_fields_do_not_receive_accessor_exception(self):
        for source in [
            'fn make(){property(fn(self){42})};class C{let x=make();let x=make()}',
            'class C{let x=property(fn(self){42});let x=x.setter(fn(self,v){null})}'
        ]:
            with self.subTest(source=source):
                with self.assertRaises(LanguageError):Evaluator().run(source)
                self.assertTrue(any(d['code']=='duplicate_binding' for d in analyze(source)['diagnostics']))

    def test_nested_descriptor_arguments_are_analyzed_once(self):
        from chaos.ide_language import Analysis
        original=Analysis.walk;calls=[]
        def counted(analysis,node,env):
            calls.append(node[0])
            self.assertLess(len(calls),1000,'nested descriptor analysis repeated a subtree')
            return original(analysis,node,env)
        depth=12
        source='let x='+'staticmethod('*depth+'fn(){42}'+')'*depth+';null'
        with patch.object(Analysis,'walk',counted):result=analyze(source)
        self.assertEqual(result['diagnostics'],[])
        self.assertLessEqual(len(calls),3*depth+20)


class UserDescriptorAnalysis(unittest.TestCase):
    SOURCE="""class NumberField{
 fn __get__(self,instance,owner)->Int{42}
 fn __set__(self,instance,value:String)->Null{null}
 fn __delete__(self,instance)->Null{null}
}
class C{let field=NumberField()};let c=C();"""

    def test_nominal_descriptor_annotation_reads_and_setter_contract(self):
        self.assertEqual(analyze(self.SOURCE+'c.field="value"')['diagnostics'],[])
        result=analyze(self.SOURCE+'c.field')
        self.assertIn('field: Int',result['hover']['contents'])
        typed=self.SOURCE.replace('let field=NumberField()','let field:Descriptor=NumberField()')
        self.assertIn('field: Int',analyze(typed+'c.field')['hover']['contents'])
        self.assertTrue(any(d['code']=='type_mismatch' for d in analyze(self.SOURCE+'c.field=42')['diagnostics']))
        self.assertIn('field: Int',analyze(self.SOURCE+'C.field')['hover']['contents'])
        self.assertTrue(any(d['code']=='immutable_binding' for d in analyze(self.SOURCE+'C.field=42')['diagnostics']))

    def test_class_slot_contract_and_instance_value_contract_are_separate(self):
        source='class D{fn __get__(self,obj,owner)->Int{42};fn __set__(self,obj,value:String){null}}class C{var value:Descriptor=D()};let c=C();'
        self.assertEqual(analyze(source+'c.value="text"')['diagnostics'],[])
        self.assertTrue(any(d['code']=='type_mismatch' for d in analyze(source+'C.value="text"')['diagnostics']))

    def test_inherited_protocol_and_name_only_is_not_a_descriptor(self):
        source='class Base{fn __get__(self,obj,owner)->Int{42};fn __set__(self,obj,v){null}}class D(Base){}class C{let x=D()};let c=C();c.x'
        self.assertIn('x: Int',analyze(source)['hover']['contents'])
        source='class Notify{fn __set_name__(self,owner,name){null}}class C{let x=Notify()};let c=C();c.x'
        self.assertIn('Notify',analyze(source)['hover']['contents'])

    def test_nondata_shadow_is_allowed_and_return_annotation_is_only_a_hint(self):
        source='class D{fn __get__(self,obj,owner)->Int{42}}class C{let value=D()};let c=C();'
        self.assertEqual(analyze(source+'c.value="shadow"')['diagnostics'],[])
        detail=analyze(source+'c.value')['hover']['contents']
        self.assertIn('Any',detail);self.assertIn('__get__ → Int',detail)
        self.assertIn('Int',analyze(source+'C.value')['hover']['contents'])

    def test_descriptor_getter_is_not_executed_during_analysis(self):
        source='class D{fn __get__(self,obj,owner){tool("forbidden",1)}}class C{let value=D()};let c=C();c.value'
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis evaluated a protocol')):
            self.assertEqual(analyze(source)['diagnostics'],[])
        self.assertIn('Any',analyze(source)['hover']['contents'])

    def test_async_getter_and_dynamic_callable_have_conservative_signatures(self):
        source='class D{async fn __get__(self,obj,owner)->Int{42};fn __set__(self,obj,value){null}}class C{let value=D()};let c=C();'
        self.assertIn('Task',analyze(source+'c.value')['hover']['contents'])
        self.assertIn('Int',analyze(source+'let n=await c.value;n')['hover']['contents'])
        source='class Call{fn __get__(self,obj,owner)->Function{fn(*args){42}};fn __set__(self,obj,value){null}}class C{let __call__=Call()};let c=C();c(1,2,3)'
        self.assertEqual(analyze(source)['diagnostics'],[])

    def test_property_deleter_copies_accessors_and_delattr_is_ordinary_builtin(self):
        source='class C{var n=42;@property\nfn x(self)->Int{self.n}\n@x.deleter\nfn x(self){self.n=0;null}\n};let c=C();delattr(c,"x");c.x'
        self.assertEqual(analyze(source)['diagnostics'],[])
        self.assertIn('可删除',analyze(source)['hover']['contents'])
        members={c['label'] for c in analyze(source+';C.x.')['completions']}
        self.assertTrue({'getter','setter','deleter','fget','fset','fdel'}<=members)
        source='let p=property(fdel=fn(self){null},fget=fn(self)->Int{42});class C{let x=p};let c=C();c.x'
        self.assertIn('Int',analyze(source)['hover']['contents'])
        self.assertEqual(analyze('fn delattr(value,name){42};let n=delattr(1,"x");n')['diagnostics'],[])

    def test_raw_mro_resolution_cost_is_bounded_and_ignores_instance_slot_overrides(self):
        from chaos.ide_language import Analysis
        source='class D{fn __get__(self,obj,owner)->Int{42};fn __set__(self,obj,value){null}}let d=D();d.__get__=fn(){"wrong"};class C{let x=d};let c=C();c.x'
        analysis=Analysis(source,'main.chaos',{'main.chaos':source},'main.chaos',None)
        result=analysis.run();self.assertIn('Int',result['hover']['contents'])
        self.assertLess(analysis.member_steps,20000)

    def test_mutable_nominal_descriptor_access_drops_base_protocol_signature(self):
        source='class D{fn __get__(self,obj,owner)->Int{42};fn __set__(self,obj,value:Int){null}}class E(D){fn __get__(self,obj,owner)->String{"new"};fn __set__(self,obj,value:String){null}}class C{var x:D=D()}C.x=E();let c=C();c.x="ok";let value:String=c.x;value'
        self.assertEqual(Evaluator().run(source),'new');self.assertEqual(analyze(source)['diagnostics'],[])
        for suffix in ['C.x=42','class Other{};C.x=Other()']:
            result=analyze(source+';'+suffix)
            self.assertTrue(any(item['code']=='type_mismatch' for item in result['diagnostics']))

    def test_mutable_protocol_presence_can_change_data_precedence(self):
        source='class D{fn __get__(self,obj,owner)->Int{42};var __set__=fn(obj,value){null};fn __set_name__(self,owner,name){delattr(D,"__set__");null}}class C{let x=D()}let c=C();c.x="ok";let value:String=c.x;value'
        self.assertEqual(Evaluator().run(source),'ok');self.assertEqual(analyze(source)['diagnostics'],[])

    def test_naming_hook_mutable_slot_removal_exposes_inherited_contract(self):
        source='class Named{fn __set_name__(self,owner,name){delattr(owner,"x");null}}class Base{var x="text"}class Child(Base){let marker=Named();var x:Int=42;let fixed:Int=7}let result:String=Child.x;result'
        self.assertEqual(Evaluator().run(source),'text');self.assertEqual(analyze(source)['diagnostics'],[])
        self.assertIn('Int',analyze(source+';Child.fixed')['hover']['contents'])

    def test_exhausted_descriptor_resolution_returns_unknown_not_missing_protocol(self):
        from chaos.ide_language import Analysis,Info
        analysis=Analysis('null','main.chaos',{'main.chaos':'null'},'main.chaos',None)
        klass=Info('Class');klass.class_info=klass;klass.mro=[klass]
        klass.own_members={'__get__':Info('Function',returns='Int',method=True),'__set__':Info('Function',returns='Null',method=True)}
        value=Info('D',class_info=klass);analysis.member_steps=19999
        result=analysis.resolve_member('x',value)
        self.assertEqual(result.type,'Any');self.assertTrue(result.write_unknown)

    def test_public_analysis_budget_exhaustion_preserves_unknown_constructor_signature(self):
        source='class Load{'+''.join('let x'+str(i)+'=0;' for i in range(80))+'};'+'Load();'*130+'class C{fn __init__(self,x){null}}C(1)'
        self.assertEqual(type(Evaluator().run(source)).__name__,'ChaosInstance')
        result=analyze(source)
        self.assertEqual(result['diagnostics'],[])
        decorated='fn identity(f){f};class C{@identity\nfn __init__(self,x){null}}C(1)'
        self.assertEqual(type(Evaluator().run(decorated)).__name__,'ChaosInstance')
        self.assertEqual(analyze(decorated)['diagnostics'],[])
