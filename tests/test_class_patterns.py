"""Nominal field patterns observe effective storage without protocols."""
import unittest

from chaos.language import Evaluator, LanguageError, Parser


class ClassPatternTests(unittest.TestCase):
    def run_code(self, source, files=None): return Evaluator().run(source, files=files)

    def test_defaults_overrides_subset_and_rest(self):
        source = '''class C{let label="ok";var x:Int=20;var y=22;fn method(s){0}};
          let c=C();c.x=21;c.extra=1;match c{C::{x:x,..rest}=>[x,rest]}'''
        self.assertEqual(self.run_code(source), [21, {'extra': 1, 'label': 'ok', 'y': 22}])
        self.assertTrue(self.run_code('class C{var x=42};match C(){C::{}=>true,_=>false}'))
        self.assertEqual(self.run_code('class C{var x=42;var y=1};match C(){C::{x:x}=>x}'), 42)

    def test_c3_first_declaration_and_subclass_identity(self):
        source = '''class A{var x=1;var a=2};class B(A){var x=20};
          class C(A){var x=99;var c=22};class D(B,C){};
          match D(){A::{x:x,c:y,..rest}=>[x+y,rest]}'''
        self.assertEqual(self.run_code(source), [42, {'a': 2}])
        self.assertFalse(self.run_code('class A{};class B{};match A(){B::{}=>true,_=>false}'))

    def test_same_name_classes_from_modules_are_distinct(self):
        source = 'import "a.chaos" as A;import "b.chaos" as B;match A.C(){B.C::{}=>0,A.C::{x:x}=>x}'
        files = {'a.chaos': 'class C{var x=42};export{C}', 'b.chaos': 'class C{var x=42};export{C}'}
        self.assertEqual(self.run_code(source, files), 42)
        source = 'fn make(){class C{};C};let A=make();let B=make();match A(){B::{}=>0,A::{}=>42}'
        self.assertEqual(self.run_code(source), 42)

    def test_empty_nominal_and_missing_fields_dont_fake_instances(self):
        source = 'class C{};[match {"$instance":"C"}{C::{}=>true,_=>false},match C(){C::{x:x}=>0,_=>42}]'
        self.assertEqual(self.run_code(source), [False, 42])

    def test_properties_and_descriptors_never_execute(self):
        source = '''var calls=0;class StoredDescriptor{fn __get__(s,obj,owner){calls+=1;42}
          fn __set__(s,obj,value){calls+=1;null}};
          class C{let computed=property(fn(s){calls+=1;42});let d=StoredDescriptor();
          fn method(s){calls+=1;42};let sm=staticmethod(fn(){42});let cm=classmethod(fn(c){42});var plain=42};
          let c=C();[match c{C::{computed:x}=>0,_=>1},match c{C::{d:x}=>0,_=>2},
          match c{C::{..rest}=>rest},calls]'''
        self.assertEqual(self.run_code(source), [1, 2, {'plain': 42}, 0])

    def test_data_descriptor_hides_existing_slot_but_nondata_can_be_shadowed(self):
        source = '''var calls=0;class Read{fn __get__(s,obj,owner){calls+=1;0}};
          class Data{fn __get__(s,obj,owner){calls+=1;0}fn __set__(s,obj,value){null}};
          class C{var x=1};let c=C();c.x=42;C.x=Data();
          let hidden=match c{C::{x:x}=>0,_=>1};C.x=Read();
          [hidden,match c{C::{x:x}=>x},calls]'''
        self.assertEqual(self.run_code(source), [1, 42, 0])

    def test_method_in_first_mro_class_suppresses_base_default(self):
        source = 'class A{var x=42};class B(A){fn x(s){0}};match B(){A::{x:x}=>0,A::{..rest}=>rest}'
        self.assertEqual(self.run_code(source), {})

    def test_descriptor_stored_directly_in_own_slot_remains_data(self):
        source = '''var calls=0;class D{fn __get__(s,obj,owner){calls+=1;0}};
          class C{};let d=D();let c=C();c.value=d;
          match c{C::{value:v}=>[v==d,calls]}'''
        self.assertEqual(self.run_code(source), [True, 0])

    def test_public_labels_include_underscores_and_string_keys(self):
        source = 'class C{var _private=20};let c=C();setattr(c,"non identifier",22);match c{C::{_private:a,"non identifier":b}=>a+b}'
        self.assertEqual(self.run_code(source), 42)
        self.assertEqual(self.run_code('class C{var x=42};let _=C;match C(){_::{x:x}=>x}'), 42)
        self.assertEqual(self.run_code('class C{var x=42};let _={C:C};let _.C::{x:n}=C();n'), 42)
        self.assertEqual(self.run_code('class C{};let _={C:C};try{throw 1}catch _.C::{}{0}catch e{42}'), 42)

    def test_class_path_resolves_before_same_named_leaf(self):
        self.assertEqual(self.run_code('class C{var x=42};do{let C::{x:C}=C();C}'), 42)
        source = 'class C{var x=42};fn read(value as C::{x:C}){C};read(C())'
        self.assertEqual(self.run_code(source), 42)

    def test_unknown_wrong_and_qualified_nonrecord_class_paths_are_errors(self):
        for path, prefix in [('Missing', ''), ('bad', 'let bad=42;'), ('C.x', 'class C{let x=42};')]:
            with self.subTest(path=path), self.assertRaises(LanguageError):
                self.run_code(prefix+'match 1{'+path+'::{}=>0,_=>42}')

    def test_slices_old_catch_with_and_data_patterns_remain_compatible(self):
        self.assertEqual(self.run_code('[0,1,2,3][::2]'), [0, 2])
        self.assertEqual(self.run_code('try{throw 1}catch e{} {value:42}'), {'value': 42})
        source = 'class M{fn __enter__(s){42}fn __exit__(s,e){null}};with M() as value{value}'
        self.assertEqual(self.run_code(source), 42)
        self.assertEqual(self.run_code('data E{C(x)};match C(42){C(x)=>x}'), 42)
        self.assertEqual(Parser('var x:Int=1').program()[1][0][0], 'var')

    def test_parser_has_no_whitespace_or_capitalization_dependency(self):
        for pattern in ('lower::{x:x}', 'lower : : {x:x}', 'lower:: { x : x }'):
            with self.subTest(pattern=pattern):
                self.assertEqual(self.run_code('class lower{var x=42};match lower(){'+pattern+'=>x}'), 42)

    def test_duplicate_keys_leaves_and_invalid_rest_fail_before_effects(self):
        for pattern in ('C::{x:x,x:y}', 'C::{x:x,y:x}', 'C::{..[x]}', 'C::{x:x,..rest,y:y}'):
            seen = []
            with self.subTest(pattern=pattern), self.assertRaises(LanguageError):
                Evaluator(tool_handler=lambda *a: seen.append(a)).run('tool("bad",null);let '+pattern+'=null')
            self.assertEqual(seen, [])


if __name__ == '__main__': unittest.main()
