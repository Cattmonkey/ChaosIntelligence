"""Native ordered catch patterns share the structural language contract."""
import unittest

from chaos.language import (Evaluator, LanguageError, Parser, Native, catch_pattern_names,
                            validate_try_cases)


class ExceptionHandlerTests(unittest.TestCase):
    def run_code(self, source, **kwargs):
        files = kwargs.pop("files", None)
        return Evaluator(**kwargs).run(source, files=files)

    def test_legacy_names_and_root_underscore_keep_old_ast_and_behavior(self):
        for name in ('error', '_'):
            source = 'try{throw 42}catch ' + name + '{' + name + '.value}'
            node = Parser(source).program()[1][0]
            self.assertEqual((node[0], len(node), node[2]), ('try', 4, name))
            self.assertEqual(self.run_code(source), 42)
        self.assertEqual(self.run_code('try{throw 42}catch _ if _.value==42{_.value}'), 42)
        self.assertEqual(self.run_code('try{throw [1,42]}catch {value:[_,x],.._}{x}'), 42)

    def test_complete_error_record_matches_code_value_and_location(self):
        source = '''try{throw (20,22)}
          catch {code:"other",.._}{0}
          catch {code:"user_throw",value:(a,b),location:{file:f,line:n,.._},.._}
            if f=="main.chaos"&&n==1{a+b}'''
        self.assertEqual(self.run_code(source), 42)
        self.assertEqual(self.run_code('try{throw 42}catch 42{0}catch e{e.value}'), 42)

    def test_all_structural_patterns_and_rest_use_shared_matcher(self):
        source = '''data Fault{Bad(payload)};try{throw Bad({rows:[(20,22),99],extra:true})}
          catch {value:Bad({rows:[(a,b),..rest],..remaining}),..errorRest}{
            [a+b,rest,remaining.extra,errorRest.code]}'''
        self.assertEqual(self.run_code(source), [42, [99], True, 'user_throw'])

    def test_constructor_lookup_precedes_leaf_binding_and_is_nominal(self):
        self.assertEqual(self.run_code('data E{C(x)};try{throw C(42)}catch {value:C(C),.._}{C}'), 42)
        source = 'import "left.chaos" as A;import "right.chaos" as B;try{throw A.C(1)}catch {value:B.C(x),.._}{0}catch e{42}'
        self.assertEqual(self.run_code(source, files={'left.chaos': 'data E{C(x)};export{C}', 'right.chaos': 'data E{C(x)};export{C}'}), 42)

    def test_matching_never_invokes_user_equality_or_iteration(self):
        source = '''class C{fn __eq__(s,x){tool("bad",null);true}
          fn __iter__(s){tool("bad",null);s}};
          try{throw C()}catch {value:42,.._}{0}catch {value:[x],.._}{0}catch e{42}'''
        seen = []
        self.assertEqual(self.run_code(source, tool_handler=lambda *a: seen.append(a)), 42)
        self.assertEqual(seen, [])

    def test_rejected_guard_gets_fresh_error_copy_in_later_arm(self):
        source = '''var seen=[];try{throw [1]}
          catch first if do{mutate(first);seen=append(seen,first.value[0]);false}{0}
          catch {value:[value],.._} if value==1{[value,seen]}'''
        evaluator = Evaluator()
        def mutate(record): record['value'][0] = 99
        evaluator.env.bind('mutate', Native('mutate', mutate, evaluator))
        self.assertEqual(evaluator.run(source), [1, [99]])

    def test_rejected_guard_does_not_rollback_shared_live_object_effects(self):
        source = '''class Box{var value=1};let shared=Box();try{throw shared}
          catch e if do{e.value.value=42;false}{0}
          catch {value:box,.._}{[box.value,shared.value,box==shared]}'''
        self.assertEqual(self.run_code(source), [42, 42, True])

    def test_order_exactly_once_body_guards_and_isolated_bindings(self):
        source = '''var events=[];let result=try{events=append(events,"body");throw 42}
          catch {value:0,.._} if do{events=append(events,"wrong");true}{0}
          catch {value:x,.._} if do{events=append(events,x);false}{0}
          catch {value:x,.._} if do{events=append(events,x+1);true}{let local=x;local}
          catch any{events=append(events,"wrong");0};
          [result,events,try{x}catch e{e.code},try{local}catch e{e.code}]'''
        self.assertEqual(self.run_code(source), [42, ['body', 42, 43], 'runtime_error', 'runtime_error'])

    def test_matching_guard_and_handler_errors_never_try_sibling_cases(self):
        for cases in ('catch {value:Missing(x),.._}{0}',
                      'catch e if do{throw 9}{0}', 'catch e if true{throw 9}'):
            source = 'var later=false;try{try{throw 7}' + cases + 'catch next{later=true;0}}catch outer{[outer.value,later]}'
            value = self.run_code(source)
            self.assertFalse(value[1])
            if 'Missing' not in cases: self.assertEqual(value[0], 9)
        source = 'data E{C(x)};var later=false;try{try{throw C(1)}catch {value:C(),.._}{0}catch e{later=true}}catch out{later}'
        self.assertFalse(self.run_code(source))

    def test_guards_require_bool_without_truthiness(self):
        for guard in ('null', '0', '1', '"yes"', '[]', '{}', '()'):
            with self.subTest(guard=guard):
                source = 'var later=false;try{try{throw 7}catch e if '+guard+'{0}catch e{later=true}}catch out{later}'
                self.assertFalse(self.run_code(source))

    def test_unmatched_error_is_reraised_by_identity_with_original_location(self):
        failure = LanguageError('provider failed', line=11, column=5, file='provider.chaos',
                                code='provider_error', value={'items': [1]})
        def fail(*args): raise failure
        with self.assertRaises(LanguageError) as raised:
            self.run_code('try{tool("fail",null)}catch {code:"other",.._}{0}', tool_handler=fail)
        self.assertIs(raised.exception, failure)
        self.assertEqual(failure.record()['location'], {'file': 'provider.chaos', 'line': 11, 'column': 5})

    def test_normal_else_finally_and_control_values_keep_existing_rules(self):
        source = '''var events=[];let a=try{20}catch e if false{0}else{42}finally{events=append(events,1)};
          let b=try{throw 21}catch {value:x,.._}{x*2}else{0}finally{events=append(events,2)};
          [a,b,events]'''
        self.assertEqual(self.run_code(source), [42, 42, [1, 2]])
        self.assertEqual(self.run_code('fn f(){try{throw 1}catch e if do{return 42}{0}catch e{0}};f()'), 42)

    def test_guard_can_suspend_but_stays_in_its_existing_async_scope(self):
        self.assertEqual(self.run_code('async fn yes(x){await sleep(0);x==42};try{throw 42}catch {value:x,.._} if await yes(x){x}'), 42)
        seen = []
        with self.assertRaises(LanguageError):
            self.run_code('tool("bad",null);fn f(){try{throw 1}catch e if await sleep(0){0}}', tool_handler=lambda *a: seen.append(a))
        self.assertEqual(seen, [])

    def test_malformed_patterns_rejected_before_module_effects(self):
        bad = ['try{1}catch {value:[x,x],.._}{0}', 'try{1}catch {a:x,a:y}{0}',
               'try{1}catch e if {0}', 'try{1}catch e{0}catch', 'try{1}else{0}']
        for source in bad:
            seen = []
            with self.subTest(source=source), self.assertRaises(LanguageError):
                self.run_code('tool("bad",null);'+source, tool_handler=lambda *a: seen.append(a))
            self.assertEqual(seen, [])

    def test_shared_validator_closes_new_nodes_without_case_limit(self):
        block = ('block', [])
        bad = [('try_cases', block, []), ('try_cases', block, [(('p_any',), None, block)]),
               ('try_cases', block, [(('p_bind', 'e'), None, block)]),
               ('try_cases', block, [(('p_bind', 'e'), 42, block)]),
               ('try_cases', block, [(('p_bind', 'e'), ('literal', True), ('literal', 0))])]
        for node in bad:
            with self.subTest(node=node), self.assertRaises(LanguageError): validate_try_cases(node)
        self.assertEqual(catch_pattern_names(('p_bind', '_')), ('_',))
        many = 'try{throw 42}'+''.join('catch e if false{0}' for _ in range(70))+'catch e{e.value}'
        self.assertEqual(self.run_code(many), 42)


if __name__ == '__main__': unittest.main()
