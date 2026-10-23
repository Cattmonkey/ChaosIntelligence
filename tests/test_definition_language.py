"""Source-backed lexical definitions use the shared inert editor analysis."""
import unittest
from unittest.mock import patch

from chaos.ide_language import analyze


def units(text):
    return len(text.encode('utf-16-le')) // 2


class DefinitionLanguage(unittest.TestCase):
    def definition(self, source, needle, *, files=None, file='main.chaos', occurrence=-1):
        offset=source.rindex(needle) if occurrence==-1 else source.index(needle)
        return analyze(source,file=file,files=files,position=units(source[:offset]))['definition']

    def assert_location(self, result, source, name, *, file='main.chaos', offset=None):
        start=source.index(name) if offset is None else offset
        self.assertEqual(result,{'file':file,'from':units(source[:start]),'to':units(source[:start+len(name)])})

    def test_local_shadowing_alias_and_assignment(self):
        source='let value=1; fn f(value:Int){let alias=value; alias}; var count=0; count=2; value'
        self.assert_location(self.definition(source,'value'),source,'value')
        self.assert_location(self.definition(source,'value;'),source,'value',offset=source.index('value:Int'))
        self.assert_location(self.definition(source,'alias}'),source,'alias')
        self.assert_location(self.definition(source,'count=2'),source,'count')

    def test_binding_name_itself_is_a_definition(self):
        source='let answer=42; answer'
        self.assert_location(self.definition(source,'answer',occurrence=0),source,'answer')

    def test_named_function_self_reference_and_parameter(self):
        source='fn twice(x:Int)->Int{if x==0{0}else{twice(x-1)}}; twice(2)'
        self.assert_location(self.definition(source,'twice(x-1)'),source,'twice')
        self.assert_location(self.definition(source,'x-1'),source,'x')
        self.assert_location(self.definition(source,'twice(2)'),source,'twice')

    def test_parameter_name_cannot_match_a_previous_annotation(self):
        source='class T{}; fn f(x:T,T){T}'
        self.assert_location(self.definition(source,'T}'),source,'T',offset=source.index('T){'))
        source='class T{}; fn f(x:T=T(),T=1){T}'
        self.assert_location(self.definition(source,'T}'),source,'T',offset=source.index('T=1'))

    def test_mutual_recursion_has_exact_wrapped_name_locations(self):
        source='rec {fn even(n){odd(n)}; async fn odd(n){even(n)}}; even(2)'
        self.assert_location(self.definition(source,'odd(n)}'),source,'odd',offset=source.index('odd(n){'))
        self.assert_location(self.definition(source,'even(n)}}'),source,'even')
        self.assert_location(self.definition(source,'even(2)'),source,'even')
        cursor=units(source[:source.index('odd(n)}')+2])
        self.assertIn('odd',{item['label'] for item in analyze(source,position=cursor)['completions']})

    def test_imported_unsaved_module_uses_its_utf16_map(self):
        source='// 🙂 caller\r\nimport "lib/math.chaos" as M; let Alias=M; Alias.add(1,2)'
        library='// 🧠 unsaved target\r\nfn add(left,right){left+right}; export {add}'
        result=self.definition(source,'add(1',files={'lib/math.chaos':library})
        self.assert_location(result,library,'add',file='lib/math.chaos')
        self.assert_location(self.definition(source,'Alias.add',files={'lib/math.chaos':library}),source,'Alias')

    def test_function_alias_navigates_to_lexical_alias_binding(self):
        source='import "lib.chaos" as M; let local=M.original; local(1)'
        library='fn original(x){x}; export {original}'
        self.assert_location(self.definition(source,'local(1)',files={'lib.chaos':library}),source,'local')
        self.assert_location(self.definition(source,'original;',files={'lib.chaos':library}),library,'original',file='lib.chaos')

    def test_inherited_instance_static_and_class_method_slots(self):
        source='class Base { let field=1; fn method(self){self.field}; @staticmethod\nfn fixed(x){x}; @classmethod\nfn make(cls){cls} }; class Child(Base){}; let child=Child(); child.method(); Child.fixed(1); child.make()'
        for needle,name in [('field}','field'),('method();','method'),('fixed(1)','fixed'),('make()','make')]:
            with self.subTest(name=name):self.assert_location(self.definition(source,needle),source,name)

    def test_builtin_property_points_to_slot_not_its_return_class(self):
        source='class Result {} class C { @property\nfn item(self)->Result{Result()} }; let c=C(); c.item'
        self.assert_location(self.definition(source,'item'),source,'item')

    def test_instance_and_class_slot_assignment_names(self):
        source='class C {var field:Int=0}; let c=C(); c.field=1; C.field=2'
        self.assert_location(self.definition(source,'field=1'),source,'field')
        self.assert_location(self.definition(source,'field=2'),source,'field')

    def test_property_replacement_chain_preserves_identity(self):
        source='class C { @property\nfn item(self)->Int{1}; @item.setter\nfn item(self,x){null}; @item.deleter\nfn item(self){null} }; let c=C(); c.item'
        result=analyze(source,position=units(source))
        self.assertEqual(result['diagnostics'],[])
        self.assert_location(result['definition'],source,'item',offset=source.index('item(self){null}'))

    def test_user_descriptor_does_not_guess_a_computed_definition(self):
        source='class Result {} class D { fn __get__(self,obj,owner)->Result { Result() } } class C { let item=D() } let c=C(); c.item'
        self.assertIsNone(self.definition(source,'item'))
        self.assertIsNone(self.definition(source+'; C.item','item'))

    def test_result_type_does_not_become_a_value_definition(self):
        source='class Result {} fn build()->Result{Result()}; build()'
        self.assertIsNone(analyze(source,position=units(source))['definition'])
        self.assert_location(self.definition(source,'build()'),source,'build')

    def test_local_and_imported_macro_names_use_source_declarations(self):
        source='macro pass(x){quote{unquote(x)}}; pass!(1)'
        self.assert_location(self.definition(source,'pass!'),source,'pass')
        source='import "macro.chaos" as M; M.pass!(1)'
        library='// 🙂\nmacro pass(x){quote{unquote(x)}}; export {pass}'
        self.assert_location(self.definition(source,'pass!',files={'macro.chaos':library}),library,'pass',file='macro.chaos')
        self.assert_location(self.definition(source,'M.pass',files={'macro.chaos':library}),source,'M')

    def test_macro_parameter_and_quote_local_scope(self):
        source='let value=1; macro pass(value){quote{let value=2; value; unquote(value)}}'
        self.assert_location(self.definition(source,'value; unquote'),source,'value',offset=source.index('value=2'))
        self.assert_location(self.definition(source,'value)}}'),source,'value',offset=source.index('value){quote'))
        source='macro same(same){quote{unquote(same)}}; same!(1)'
        self.assert_location(self.definition(source,'same)}}'),source,'same',offset=source.index('same){quote'))

    def test_record_field_does_not_inherit_stored_value_definition(self):
        source='let original=1; let record={field:original}; record.field'
        self.assertIsNone(self.definition(source,'field'))
        self.assert_location(self.definition(source,'original}'),source,'original')

    def test_patterns_and_comprehensions_retain_leaf_bindings(self):
        source='fn f(pair as [left,right]){left+right}; let [a,b]=[1,2]; [a+b+item for item in [1,2]]'
        for needle,name in [('left+right','left'),('right};','right'),('a+b','a'),('b+item','b'),('item for','item')]:
            with self.subTest(name=name):
                offset=source.index('item in') if name=='item' else source.index('[a,b]')+1 if name=='a' else source.index(name)
                self.assert_location(self.definition(source,needle),source,name,offset=offset)

    def test_recovery_uses_only_parsed_bindings(self):
        source='let answer=42; fn unfinished(x){ answer;'
        self.assert_location(self.definition(source,'answer;'),source,'answer')
        self.assertIsNone(self.definition('unknown','unknown'))

    def test_native_standard_module_literals_comments_and_whitespace_have_no_definition(self):
        for source in ('length','import "std/list" as L; L.map','"answer"','// answer','let answer=42; '):
            with self.subTest(source=source):self.assertIsNone(analyze(source,position=units(source))['definition'])

    def test_analysis_cannot_evaluate_agent_macro_or_descriptor(self):
        source='agent remote{provider:"model",tools:[]}; remote'
        with patch('chaos.language.Evaluator.evaluate',side_effect=AssertionError('must not evaluate')):
            self.assert_location(self.definition(source,'remote'),source,'remote')


if __name__=='__main__':unittest.main()
