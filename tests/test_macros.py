"""Source-level contracts for bounded, hygienic, phase-separated AST macros."""
import unittest

from chaos.language import Evaluator, LanguageError, Parser, ResourceLimit
from chaos.macros import expand_trees
from chaos.modules import ProgramBundle


class MacroTests(unittest.TestCase):
    def run_source(self, source, *, files=None, **kwargs):
        return Evaluator(**kwargs).run(source, files=files)

    def test_real_ast_macro_and_single_evaluation(self):
        calls = []
        source = '''
        macro twice(value) { quote { let temporary=unquote(value); temporary+temporary } }
        let temporary=100;
        [twice!(tool("probe", 21)), temporary]
        '''
        self.assertEqual(self.run_source(source, tool_handler=lambda name, value: calls.append(name) or value), [42, 100])
        self.assertEqual(calls, ['probe'])

    def test_caller_names_and_generated_names_do_not_capture(self):
        source = '''
        macro m(value) { quote { let temp=1; unquote(value)+temp } }
        let temp=40; let __chaos_macro_1_temp=1000;
        [m!(temp), m!(__chaos_macro_1_temp), temp]
        '''
        self.assertEqual(self.run_source(source), [41, 1001, 40])

    def test_closure_hygiene_preserves_public_keyword_labels(self):
        source = '''
        macro make(value) { quote { fn(x: Int, *, bonus: Int=1)->Int {unquote(value)+x+bonus} } }
        let x=40; let f=make!(x);
        [f(x=1), f(1, bonus=2), x]
        '''
        self.assertEqual(self.run_source(source), [42, 43, 40])

    def test_generated_function_defaults_use_outer_scope(self):
        source = '''
        macro make(value) { quote { let x=2; fn(x=unquote(value), *, y=x) {x+y} } }
        let x=40; make!(x)()
        '''
        self.assertEqual(self.run_source(source), 42)

    def test_local_function_recursion_and_keyword_binding_are_hygienic(self):
        source = '''
        macro descend(value) { quote {
          fn loop(n: Int) {if n==0 {unquote(value)} else {loop(n=n-1)}};
          loop(3)
        } }
        let n=42; let loop=99; [descend!(n), loop]
        '''
        self.assertEqual(self.run_source(source), [42, 99])

    def test_var_loop_catch_and_pattern_bindings_are_hygienic(self):
        source = '''
        macro m(value) { quote {
          var sum=0; for x in [1,2] {sum=sum+x};
          let caught=try {throw 9} catch x {unquote(value)};
          [sum,caught,match [3] {[x]=>x+unquote(value)}]
        } }
        let x=40; m!(x)
        '''
        self.assertEqual(self.run_source(source), [3, 40, 43])

    def test_pure_functions_compose_syntax(self):
        source = '''
        macro addTwo(value) {
          fn compose(f: Function,g: Function)->Function {fn(x){f(g(x))}};
          fn addOne(x) {quote {unquote(x)+1}};
          let composed=compose(addOne,addOne);
          composed(value)
        }
        addTwo!(40)
        '''
        self.assertEqual(self.run_source(source), 42)

    def test_pure_recursion_conditionals_containers_and_helpers(self):
        source = '''
        macro compute(value) {
          fn sum(n, *, acc=0) {if n==0 {acc} else {sum(n-1,acc=acc+n)}};
          let kind=syntax_kind(value);
          if kind=="literal" {syntax_literal({result:sum(syntax_value(value)),items:[1,2]})}
          else {value}
        }
        [compute!(3), compute!(1+2)]
        '''
        self.assertEqual(self.run_source(source), [{'result': 6, 'items': [1, 2]}, 3])

    def test_macro_composition_expands_multiple_levels(self):
        source = '''
        macro inner(x) {quote {let saved=unquote(x);saved+1}}
        macro outer(x) {quote {inner!(inner!(unquote(x)))}}
        let saved=40; outer!(saved)
        '''
        self.assertEqual(self.run_source(source), 42)

    def test_nested_quote_clone_preserves_public_parameter_aliases(self):
        source = '''
        macro make(value) {
          let syntax=quote{fn(x,*,extra=1){unquote(value)+x+extra}};
          quote{unquote(syntax)}
        }
        macro forward(value){quote{unquote(value)}}
        let x=40;forward!(make!(x))(x=1)
        '''
        self.assertEqual(self.run_source(source), 42)

    def test_macro_arguments_are_not_evaluated_when_unused(self):
        calls = []
        self.assertEqual(self.run_source('macro ignore(x){quote{42}}; ignore!(tool("never",null))',
                                         tool_handler=lambda *args: calls.append(args)), 42)
        self.assertEqual(calls, [])

    def test_compile_phase_cannot_call_tools_agents_or_runtime_functions(self):
        calls = []
        sources = [
            'macro bad(x){tool("never",1)};tool("before",0);bad!(1)',
            'fn runtime(x){x};macro bad(x){runtime(x)};bad!(1)',
            'agent remote{model:"fake"};macro bad(x){remote(x)};bad!(1)',
            'macro bad(x){await x};bad!(1)',
            'macro bad(x){var n=1;quote{1}};bad!(1)',
        ]
        for source in sources:
            with self.subTest(source=source), self.assertRaises(LanguageError):
                self.run_source(source, tool_handler=lambda *args: calls.append(args),
                                agent_handler=lambda *args: calls.append(args))
        self.assertEqual(calls, [])

    def test_explicit_effect_injection_runs_only_after_expansion(self):
        calls = []
        source = '''macro invoke(f,x){quote{unquote(f)(unquote(x))}};
                    agent remote{model:"fake"};invoke!(remote,{question:"hello"})'''
        bundle = ProgramBundle(source)
        self.assertEqual(calls, [])
        self.assertEqual(self.run_source(bundle, agent_handler=lambda name, config, value: calls.append(value) or 42), 42)
        self.assertEqual(calls, [{'question': 'hello'}])

    def test_closed_template_rejects_free_name_instead_of_capturing(self):
        source = 'let helper=1;macro bad(){quote{helper}};do{let helper=2;bad!()}'
        with self.assertRaises(LanguageError) as caught: self.run_source(source)
        self.assertEqual(caught.exception.code, 'macro_error')
        self.assertIn('unquote', caught.exception.message)
        self.assertEqual(self.run_source('macro safe(helper){quote{unquote(helper)}};let helper=1;do{let helper=2;safe!(helper)}'), 2)

    def test_macro_scope_is_module_local_and_runtime_exports_work(self):
        files = {'helper.chaos': 'macro inc(x){quote{unquote(x)+1}};fn answer(){inc!(41)};export{answer}'}
        source = 'import "helper.chaos" as helper;helper.answer()'
        self.assertEqual(Evaluator().run(source, files=files), 42)
        for text in ['import "helper.chaos" as helper;inc!(41)']:
            with self.subTest(text=text), self.assertRaises(LanguageError) as caught:
                ProgramBundle(text, files=files)
            self.assertEqual(caught.exception.code, 'macro_error')

    def test_exported_macro_and_runtime_exports_have_separate_namespaces(self):
        source = 'import "lib.chaos" as L;[L.inc!(41),L.answer(),keys(L)]'
        files = {'lib.chaos': 'macro inc(x){quote{unquote(x)+1}};fn answer(){inc!(41)};export{inc,answer}'}
        bundle = ProgramBundle(source, files=files)
        self.assertEqual(self.run_source(bundle), [42,42,['answer']])
        self.assertEqual(bundle.exports['lib.chaos'], ['answer'])
        self.assertEqual(bundle.macro_exports['lib.chaos'], ['inc'])
        self.assertEqual(bundle.summary()['macroExports']['lib.chaos'], ['inc'])
        self.assertEqual(bundle.imports['main.chaos'], {'L':'lib.chaos'})
        with self.assertRaises(LanguageError):
            self.run_source('import "lib.chaos" as L;L.inc(41)', files=files)

    def test_definition_private_helpers_and_caller_macro_arguments_keep_their_modules(self):
        files = {'lib.chaos': '''
            macro helper(x){quote{unquote(x)+1}}
            macro outer(x){quote{helper!(helper!(unquote(x)))}}
            export{outer}
        '''}
        source = '''
            import "lib.chaos" as L;
            macro helper(x){quote{unquote(x)+100}}
            L.outer!(helper!(40))
        '''
        self.assertEqual(self.run_source(source,files=files),142)

    def test_definition_import_alias_cannot_be_hijacked_by_caller_alias_or_value(self):
        files = {
            'lib.chaos': 'import "base.chaos" as H;macro outer(x){quote{H.inc!(unquote(x))}};export{outer}',
            'base.chaos': 'macro inc(x){quote{unquote(x)+1}};export{inc}',
            'caller.chaos': 'macro inc(x){quote{unquote(x)+100}};export{inc}',
        }
        source = 'import "lib.chaos" as L;import "caller.chaos" as H;do{let H={inc:0};let L=0;L.outer!(H.inc!(40))}'
        self.assertEqual(self.run_source(source,files=files),141)

    def test_cross_module_template_hygiene_and_function_keyword_aliases_survive_forwarding(self):
        files = {
            'lib.chaos': '''macro make(x){quote{let saved=1;fn(value,*,bonus=1){unquote(x)+value+bonus+saved}}};export{make}''',
            'forward.chaos': 'macro identity(x){let saved=quote{unquote(x)};quote{unquote(saved)}};export{identity}',
        }
        source = 'import "lib.chaos" as L;import "forward.chaos" as F;let saved=40;let value=99;F.identity!(L.make!(saved))(value=0)'
        self.assertEqual(self.run_source(source,files=files),42)

    def test_private_runtime_and_nonimport_macro_targets_are_rejected(self):
        files = {'lib.chaos':'macro hidden(x){x};fn normal(){42};export{normal}'}
        sources = ['import "lib.chaos" as L;L.hidden!(41)',
                   'import "lib.chaos" as L;L.normal!()',
                   'let L={hidden:1};L.hidden!(41)',
                   'import "lib.chaos" as L;let alias=L;alias.hidden!(41)']
        for source in sources:
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:
                ProgramBundle(source,files=files)
            self.assertEqual(caught.exception.code,'macro_error')
        for source, code in [('macro m(x){x};export{m,m}','syntax_error'),
                             ('macro m(x){x};export{missing}','module_error'),
                             ('macro m(x){x};let m=1;export{m}','module_error')]:
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:ProgramBundle(source)
            self.assertEqual(caught.exception.code,code)

    def test_imported_macro_is_not_implicitly_reexported_through_runtime_alias(self):
        files = {'leaf.chaos':'macro m(x){x};export{m}',
                 'middle.chaos':'import "leaf.chaos" as L;export{L}'}
        source='import "middle.chaos" as M;keys(M.L)'
        self.assertEqual(self.run_source(source,files=files),[])
        with self.assertRaises(LanguageError):
            ProgramBundle('import "middle.chaos" as M;M.L.m!(42)',files=files)
        files['middle.chaos']='import "leaf.chaos" as L;macro m(x){quote{L.m!(unquote(x))}};export{m}'
        self.assertEqual(self.run_source('import "middle.chaos" as M;M.m!(42)',files=files),42)

    def test_cross_module_compile_purity_and_global_budget_precede_any_effect(self):
        calls=[]
        files={'lib.chaos':'macro bad(x){tool("compile",x)};export{bad}'}
        with self.assertRaises(LanguageError) as caught:
            self.run_source('import "lib.chaos" as L;tool("runtime",0);L.bad!(42)',files=files,
                            tool_handler=lambda *args:calls.append(args))
        self.assertEqual(caught.exception.code,'macro_error');self.assertEqual(calls,[])
        files={'lib.chaos':'macro loop(x){quote{loop!(unquote(x))}};export{loop}'}
        with self.assertRaises(ResourceLimit):
            ProgramBundle('import "lib.chaos" as L;L.loop!(42)',files=files)

    def test_cross_module_errors_and_inserted_syntax_report_correct_source(self):
        files={'lib.chaos':'macro inner(x){quote{missing}};macro outer(x){quote{inner!(unquote(x))}};export{outer}'}
        with self.assertRaises(LanguageError) as caught:
            ProgramBundle('import "lib.chaos" as L;\n\nL.outer!(42)',files=files,entry='entry.chaos')
        error=caught.exception
        self.assertEqual((error.file,error.line),('entry.chaos',3))
        self.assertEqual(error.value['definition']['file'],'lib.chaos')
        self.assertEqual(error.value['definitionModule'],'lib.chaos')
        self.assertEqual(error.value['expansion'],['L.outer','inner'])
        files={'lib.chaos':'macro pass(x){quote{unquote(x)}};export{pass}'}
        with self.assertRaises(LanguageError) as caught:
            ProgramBundle('import "lib.chaos" as L;\nL.pass!(\nmissing!())',files=files)
        self.assertEqual((caught.exception.file,caught.exception.line),('main.chaos',2))
        self.assertEqual(caught.exception.value['origin']['line'],3)
        self.assertIn('未声明宏 missing',caught.exception.message)

    def test_cross_module_source_trees_have_no_private_resolution_metadata(self):
        source='import "lib.chaos" as L;L.make!(40)(value=2)'
        files={'lib.chaos':'macro make(x){quote{fn(value){unquote(x)+value}}};export{make}'}
        bundle=ProgramBundle(source,files=files)
        self.assertEqual(self.run_source(bundle),42)
        pending=list(bundle.trees.values())
        while pending:
            value=pending.pop()
            self.assertFalse(hasattr(value,'macro_module'))
            self.assertFalse(hasattr(value,'macro_bindings'))
            if isinstance(value,(tuple,list)):pending.extend(value)
        self.assertEqual(bundle.trees['main.chaos'][1][-1][1][1],'L.make')
        self.assertEqual(bundle.expanded_trees,ProgramBundle(source,files=files).expanded_trees)

    def test_macro_generated_async_generator_preserves_send_and_public_parameters(self):
        files = {'lib.chaos': '''
            macro make(value){quote{async fn*(amount:Int)->Int {
                let reply=yield unquote(value); yield reply+amount; return 99
            }}}
            export{make}
        '''}
        source = '''import "lib.chaos" as L;let x=40;let g=L.make!(x)(amount=2);
            let first=await anext(g);let second=await asend(g,10);
            [first,second,await anext(g),x]'''
        self.assertEqual(self.run_source(source,files=files),
            [{'done':False,'value':40},{'done':False,'value':12},{'done':True,'value':99},40])

    def test_macro_generated_async_for_is_hygienic_and_checks_placement(self):
        files={'lib.chaos': 'macro total(stream){quote{var total=0;async for x in unquote(stream){total=total+x};total}};export{total}'}
        source='import "lib.chaos" as L;async fn* values(){yield 20;yield 22};let x=100;[L.total!(values()),x]'
        self.assertEqual(self.run_source(source,files=files),[42,100])
        with self.assertRaises(LanguageError) as caught:
            ProgramBundle('import "lib.chaos" as L;fn bad(){L.total!(null)}',files=files)
        self.assertEqual(caught.exception.code,'macro_error')

    def test_relative_definition_imports_and_forward_helpers_are_linked_before_expansion(self):
        files={'pkg/lib.chaos':'macro outer(x){quote{H.inc!(helper!(unquote(x)))}};macro helper(x){x};import "../base.chaos" as H;export{outer}',
               'base.chaos':'macro inc(x){quote{unquote(x)+1}};export{inc}'}
        source='L.outer!(41);import "pkg/lib.chaos" as L;L.outer!(41)'
        self.assertEqual(self.run_source(source,files=files),42)

    def test_class_template_preserves_member_labels_and_receiver_hygiene(self):
        source='''macro make(seed){quote{
          class Box {var value=unquote(seed);fn bump(self,by:Int=1){self.value=self.value+by;self.value}}
          Box
        }}
        let self=40;let value=100;let Box=make!(self);let item=Box();
        [item.bump(by=2),item.value,self,value,hasattr(item,"bump"),hasattr(item,"value")]
        '''
        self.assertEqual(self.run_source(source),[42,42,40,100,True,True])

    def test_class_template_method_body_and_defaults_use_distinct_lexical_scopes(self):
        source='''macro make(){quote{
          let value=40;
          class C {let value=1;fn answer(self,extra=value){value+extra}}
          C
        }}
        let C=make!();C().answer(extra=2)
        '''
        self.assertEqual(self.run_source(source),42)
        self.assertEqual(self.run_source(source.replace('answer(extra=2)','answer()')),41)

    def test_class_template_self_contract_and_explicit_type_alias_are_hygienic(self):
        source='''macro make(){quote{
          class Item {fn identity(self:Item)->Item{self}}
          let T=Item;fn checked(value:T)->T{value};[Item,checked]
        }}
        let pair=make!();let C=pair[0];let checked=pair[1];let object=C();
        [checked(object.identity())==object,isinstance(object,C)]
        '''
        self.assertEqual(self.run_source(source),[True,True])

    def test_class_template_base_injection_and_caller_self_are_not_captured(self):
        source='''class Parent {fn amount(self){40}}
        macro make(base,value){quote{
          class Child(unquote(base)){fn total(self){self.amount()+unquote(value)}}
          Child
        }}
        let self=2;let C=make!(Parent,self);C().total()
        '''
        self.assertEqual(self.run_source(source),42)

    def test_member_assignment_template_evaluates_target_key_value_once_in_order(self):
        calls=[]
        source='''class C {var value=0};let item=C();
        macro write(target,key,value){quote{unquote(target)[unquote(key)]=unquote(value)}}
        fn target(){tool("target",null);item}
        fn key(){tool("key",null);"value"}
        fn value(){tool("value",null);42}
        write!(target(),key(),value());item.value
        '''
        self.assertEqual(self.run_source(source,tool_handler=lambda n,v:calls.append(n)),42)
        self.assertEqual(calls,['target','key','value'])

    def test_class_and_member_writes_do_not_execute_in_pure_macro_phase(self):
        sources=['macro bad(){class C {};quote{42}};bad!()',
                 'macro bad(x){x.value=1;quote{42}};bad!(null)']
        calls=[]
        for source in sources:
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:
                self.run_source('tool("before",0);'+source,tool_handler=lambda *a:calls.append(a))
            self.assertEqual(caught.exception.code,'macro_error')
        self.assertEqual(calls,[])

    def test_macro_calls_inside_authored_class_defaults_and_methods_expand(self):
        source='''macro inc(x){quote{unquote(x)+1}}
        class C {let seed=inc!(40);fn value(self){inc!(self.seed)}}
        C().value()
        '''
        self.assertEqual(self.run_source(source),42)

    def test_exported_class_contract_preserves_runtime_identity(self):
        files={'types.chaos':'class C {var value=42};export{C}'}
        source='import "types.chaos" as T;fn checked(value:T.C)->T.C{value};let x=T.C();checked(x).value'
        bundle=ProgramBundle(source,files=files)
        self.assertEqual(bundle.exports['types.chaos'],['C'])
        self.assertEqual(bundle.macro_exports['types.chaos'],[])
        self.assertEqual(self.run_source(bundle),42)

    def test_class_quotation_forwarding_preserves_public_method_parameter_aliases(self):
        files={'factory.chaos':'macro make(x){quote{class C{fn value(self,*,bonus=2){unquote(x)+bonus}};C}};export{make}',
               'forward.chaos':'macro forward(x){let tree=quote{unquote(x)};quote{unquote(tree)}};export{forward}'}
        source='import "factory.chaos" as M;import "forward.chaos" as F;let self=40;let C=F.forward!(M.make!(self));C().value(bonus=2)'
        self.assertEqual(self.run_source(source,files=files),42)

    def test_class_templates_preserve_all_four_method_flavors(self):
        source='''macro make(x){quote{class C{
          fn value(self){unquote(x)}
          async fn later(self){unquote(x)}
          fn* values(self){yield unquote(x)}
          async fn* stream(self){yield unquote(x)}
        };C}}
        let C=make!(42);let object=C();
        [object.value(),await object.later(),collect(object.values()),await acollect(object.stream())]
        '''
        self.assertEqual(self.run_source(source),[42,42,[42],[42]])

    def test_class_template_qualified_explicit_type_alias_and_default_cell(self):
        files={'types.chaos':'class Answer{let value=42};export{Answer}'}
        source='''import "types.chaos" as Types;
        macro make(types){quote{
          let T=unquote(types);
          class C{
            var count:Int=1;
            let previous=do{count=count+1;count};
            fn checked(self,value:T.Answer)->T.Answer{value}
          };C
        }}
        let C=make!(Types);let item=C();
        [item.checked(Types.Answer()).value,C.count,C.previous,item.count]
        '''
        self.assertEqual(self.run_source(source,files=files),[42,2,2,2])

    def test_class_template_rejects_free_type_and_runtime_member_names(self):
        for source in [
            'class Outside{};macro bad(){quote{class C{fn f(self,x:Outside){x}};C}};bad!()',
            'macro bad(){quote{class C{let value=42;fn f(self){value}};C}};bad!()',
        ]:
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:ProgramBundle(source)
            self.assertEqual(caught.exception.code,'macro_error')

    def test_class_template_defaults_do_not_inherit_control_targets(self):
        invalid = [
            'macro make(){quote{class C{let x=return 1};C}};fn f(){make!()};f()',
            'macro make(){quote{class C{let x=break};C}};while true{make!();break}',
            'macro make(){quote{class C{let x=yield 1};C}};fn* f(){make!()};collect(f())',
            'macro make(x){quote{class C{let value=unquote(x)};C}};fn f(){make!(return 1)};f()',
            'macro make(x){quote{class C{let value=unquote(x)};C}};while true{make!(break);break}',
            'macro make(x){quote{class C{let value=await unquote(x)};C}};fn f(){make!(null)};f()',
        ]
        calls = []
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(LanguageError) as caught:
                self.run_source('tool("before",0);'+source, tool_handler=lambda *a:calls.append(a))
            self.assertEqual(caught.exception.code, 'macro_error')
        self.assertEqual(calls, [])

    def test_class_template_default_await_keeps_declaration_permission(self):
        macro = 'macro make(x){quote{class C{let value=await unquote(x)};C}};'
        task = 'async fn answer(){42};'
        self.assertEqual(self.run_source(macro+task+'let C=make!(answer());C().value'), 42)
        self.assertEqual(self.run_source(macro+task+'async fn factory(){let C=make!(answer());C()};(await factory()).value'), 42)

    def test_decorated_template_preserves_caller_names_and_keyword_labels(self):
        source='''fn identity(value){value}
        macro make(decorator,value){quote{
          @unquote(decorator)
          fn result(x:Int,*,bonus:Int=1){unquote(value)+x+bonus}
          result
        }}
        let result=100;let x=40;let f=make!(identity,x);[f(x=1),result,x]
        '''
        self.assertEqual(self.run_source(source),[42,100,40])

    def test_template_decorator_expression_uses_predeclaration_binding(self):
        source='''macro make(){quote{
          fn identity(value){value}
          do{
            @identity
            fn identity(x){x+2}
            identity(40)
          }
        }};make!()'''
        self.assertEqual(self.run_source(source),42)

    def test_decorated_class_and_agent_templates_keep_raw_identity(self):
        source='''fn identity(value){value}
        macro make(decorator){quote{
          @unquote(decorator)
          class C{fn same(self:C)->C{self}}
          C
        }}
        macro actor(decorator){quote{
          @unquote(decorator)
          agent worker{provider:"fixture",tools:[]}
          worker
        }}
        let C=make!(identity);let item=C();
        [item.same()==item,actor!(identity)(42)]
        '''
        self.assertEqual(self.run_source(source,agent_handler=lambda n,c,v:v),[True,42])

    def test_template_descriptor_members_preserve_public_labels_and_lineage(self):
        source='''macro make(prop,static,class_method,seed){quote{
          class C{
            var stored=unquote(seed);
            @unquote(prop)
            fn amount(self){self.stored}
            @amount.setter
            fn amount(self,value){self.stored=value;null}
            @amount.getter
            fn amount(self){self.stored+1}
            @unquote(static)
            fn twice(x){x*2}
            @unquote(class_method)
            fn identity(cls){cls}
          };C
        }}
        let self=40;let value=99;let C=make!(property,staticmethod,classmethod,self);
        class Child(C){};let instance=Child();instance.amount=41;
        [instance.amount,instance.twice(x=21),instance.identity()==Child,self,value]
        '''
        self.assertEqual(self.run_source(source),[42,42,True,40,99])

    def test_decorated_method_defaults_use_prior_class_scope_not_body_scope(self):
        source='''fn identity(value){value}
        macro make(decorator){quote{
          let value=40;
          class C{
            let value=1;
            @unquote(decorator)
            fn answer(self,extra=value){value+extra}
          };C
        }}
        let C=make!(identity);[C().answer(),C().answer(extra=2)]
        '''
        self.assertEqual(self.run_source(source),[41,42])

    def test_descriptor_property_update_cannot_relax_ordinary_duplicates(self):
        sources=[
            '''fn identity(x){x};macro make(d){quote{class C{
              @unquote(d)
              fn value(self){1}
              @unquote(d)
              fn value(self){2}
            };C}};make!(identity)''',
            '''macro make(p){quote{class C{
              @unquote(p)
              fn value(self){1}
              @unquote(p)
              fn value(self){2}
            };C}};make!(property)''',
        ]
        for source in sources:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_source(source)

    def test_decorated_source_module_exports_final_replacement(self):
        files={'answer.chaos':'''fn replace(value){42}
        @replace
        class Answer{}
        export{Answer}'''}
        bundle=ProgramBundle('import "answer.chaos" as A;A.Answer',files=files)
        self.assertEqual(bundle.exports['answer.chaos'],['Answer'])
        self.assertEqual(bundle.trees['answer.chaos'][1][1][0],'decorated')
        self.assertEqual(self.run_source(bundle),42)
        with self.assertRaises(LanguageError):ProgramBundle('''fn same(){};
@fn(x){x}
fn same(){}''')

    def test_decorator_macro_calls_keep_definition_and_caller_modules(self):
        files={'library.chaos':'''macro helper(){quote{fn(x){x}}}
        macro make(value){quote{
          @helper!()
          fn answer(){unquote(value)}
          answer
        }}
        export{make}'''}
        source='''import "library.chaos" as L;
        macro helper(){quote{fn(x){99}}}
        L.make!(42)()
        '''
        self.assertEqual(self.run_source(source,files=files),42)

    def test_decorated_execution_is_rejected_in_pure_macro_phase(self):
        source='''macro bad(){
          @fn(x){x}
          fn answer(){42}
          quote{42}
        };tool("before",0);bad!()'''
        calls=[]
        with self.assertRaises(LanguageError) as caught:self.run_source(source,tool_handler=lambda *a:calls.append(a))
        self.assertEqual(caught.exception.code,'macro_error');self.assertEqual(calls,[])

    def test_generated_decorator_control_cannot_escape_declaration_scope(self):
        invalid=[
            '''macro make(){quote{@do{return fn(x){x}}
              fn answer(){42};answer}};fn call(){make!()};call()''',
            '''macro make(){quote{@do{break}
              fn answer(){42};answer}};while true{make!();break}''',
            '''macro make(){quote{@do{yield 1}
              fn answer(){42};answer}};fn* call(){make!()};collect(call())''',
            '''macro make(value){quote{@await unquote(value)
              fn answer(){42};answer}};fn call(){make!(null)};call()''',
        ]
        calls=[]
        for source in invalid:
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:
                self.run_source('tool("before",0);'+source,tool_handler=lambda *a:calls.append(a))
            self.assertEqual(caught.exception.code,'macro_error')
        self.assertEqual(calls,[])

    def test_nested_template_clone_keeps_decorated_parameter_aliases(self):
        source='''fn identity(x){x}
        macro make(d,value){let syntax=quote{@unquote(d)
          fn answer(x,*,extra=1){unquote(value)+x+extra};answer};quote{unquote(syntax)}}
        let x=40;make!(identity,x)(x=1)
        '''
        self.assertEqual(self.run_source(source),42)

    def test_decorated_template_recursion_keeps_raw_hygienic_identity(self):
        source='''var calls=0;
        fn observe(target){fn(n){calls=calls+1;target(n)}}
        macro make(decorator){quote{
          @unquote(decorator)
          fn descend(n){if n==0{42}else{descend(n-1)}}
          descend
        }}
        let descend=99;let f=make!(observe);[f(4),calls,descend]
        '''
        self.assertEqual(self.run_source(source),[42,1,99])

    def test_generated_decorator_await_keeps_outer_async_permission(self):
        macro='''macro make(value){quote{@await unquote(value)
          fn answer(){42};answer}};'''
        task='async fn decorator(){fn(x){x}};'
        self.assertEqual(self.run_source(macro+task+'make!(decorator())()'),42)
        self.assertEqual(self.run_source(macro+task+'async fn factory(){make!(decorator())};(await factory())()'),42)
        source='''macro make(value){quote{class C{
          @await unquote(value)
          fn answer(self){42}
        };C}}
        async fn decorator(){fn(x){x}};let C=make!(decorator());C().answer()
        '''
        self.assertEqual(self.run_source(source),42)

    def test_decorator_template_errors_keep_call_and_definition_sources(self):
        files={'library.chaos':'''macro make(){quote{@missing
          fn answer(){42};answer}}
        export{make}'''}
        with self.assertRaises(LanguageError) as caught:
            ProgramBundle('import "library.chaos" as L;\nL.make!()',files=files,entry='entry.chaos')
        self.assertEqual(caught.exception.code,'macro_error')
        self.assertEqual((caught.exception.file,caught.exception.line),('entry.chaos',2))
        self.assertEqual(caught.exception.value['definition']['file'],'library.chaos')

    def test_decorator_wrapper_survives_original_tree_and_hygienic_expansion(self):
        source='''macro make(){quote{@fn(x){x}
          fn answer(extra=2){40+extra};answer}}
        make!()(extra=2)'''
        bundle=ProgramBundle(source)
        original=bundle.trees['main.chaos'][1][0][3][1][0][1][1][0]
        self.assertEqual(original[0],'decorated');self.assertEqual(original[2][1],'answer')
        def decorated(value):
            if isinstance(value,tuple) and value and value[0]=='decorated':return value
            if isinstance(value,(tuple,list)):
                for child in value:
                    found=decorated(child)
                    if found is not None:return found
        expanded=decorated(bundle.expanded_trees['main.chaos'])
        self.assertIsNotNone(expanded);self.assertNotEqual(expanded[2][1],'answer')
        self.assertEqual(expanded[2][2][1][0][0],'extra')
        self.assertTrue(expanded[2][2].macro_bindings)
        self.assertEqual(self.run_source(bundle),42)

    def test_generated_protocol_preserves_public_labels_and_caller_values(self):
        source = '''macro make(value, erase){quote{
          class Field {
            var label="";
            fn __set_name__(self, owner, name){self.label=name;null}
            fn __get__(self, instance, owner){if instance==null{self}else{instance.stored}}
            fn __set__(self, instance, item){instance.stored=item;null}
            fn __delete__(self, instance){unquote(erase)(instance,"stored");null}
          }
          class Owner {var stored=unquote(value);let answer=Field()}
          Owner
        }}
        let self=40;let owner=99;let name="caller";let item=100;
        let Owner=make!(self,delattr);let value=Owner();value.answer=42;
        let saved=value.answer;delattr(value,"answer");
        [saved,value.answer,Owner.answer.label,self,owner,name,item]
        '''
        self.assertEqual(self.run_source(source),[42,40,'answer',40,99,'caller',100])

    def test_exported_macro_and_nominal_class_keep_descriptor_behavior(self):
        library = '''macro make(value){quote{
          class Field{fn __get__(self,instance,owner){unquote(value)}}
          class Owner{let answer=Field()}
          Owner
        }}
        let Shared=make!(42);export{make,Shared}'''
        source = '''import "fields.chaos" as F;
        let owner=40;let Local=F.make!(owner);let a=Local();let b=F.Shared();
        a.answer="cached";let saved=a.answer;delattr(a,"answer");
        [saved,a.answer,b.answer,isinstance(b,F.Shared),keys(F),owner]'''
        bundle=ProgramBundle(source,files={'fields.chaos':library})
        self.assertEqual(bundle.macro_exports['fields.chaos'],['make'])
        self.assertEqual(self.run_source(bundle),['cached',40,42,True,['Shared'],40])
        with self.assertRaises(LanguageError) as caught:
            ProgramBundle('import "fields.chaos" as F;F.private!()',files={'fields.chaos':library})
        self.assertEqual(caught.exception.code,'macro_error')

    def test_generated_naming_and_access_hooks_execute_only_after_expansion(self):
        calls=[]
        source='''macro make(action){quote{
          class Field{
            fn __set_name__(self,owner,name){unquote(action)("named",name);null}
            fn __get__(self,instance,owner){unquote(action)("read",42)}
          }
          class Owner{let answer=Field()}
          Owner
        }}
        let Owner=make!(tool);Owner().answer'''
        bundle=ProgramBundle(source)
        self.assertEqual(calls,[])
        self.assertEqual(self.run_source(bundle,tool_handler=lambda name,value:calls.append((name,value)) or value),42)
        self.assertEqual(calls,[('named','answer'),('read',42)])
        self.assertEqual(bundle.trees['main.chaos'][1][0][0],'macro')

    def test_pure_macros_cannot_delete_or_invoke_runtime_descriptor_hooks(self):
        calls=[]
        sources=[
            'macro bad(x){delattr(x,"field");quote{42}};bad!(null)',
            'macro bad(x){x.__get__(null,null);quote{42}};bad!(null)',
            'macro bad(x){x.__set_name__(null,"field");quote{42}};bad!(null)',
            'macro bad(){class Field{fn __set_name__(self,owner,name){tool("bad",0);null}};quote{42}};bad!()',
        ]
        for source in sources:
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:
                self.run_source('tool("before",0);'+source,tool_handler=lambda *args:calls.append(args))
            self.assertEqual(caught.exception.code,'macro_error')
        self.assertEqual(calls,[])

    def test_generated_property_deleter_keeps_update_lineage(self):
        source='''macro make(decorator,erase){quote{
          class Box{
            var stored=40;
            @unquote(decorator)
            fn value(self){self.stored}
            @value.setter
            fn value(self,item){self.stored=item;null}
            @value.deleter
            fn value(self){unquote(erase)(self,"stored");null}
          }
          Box
        }}
        let Box=make!(property,delattr);let b=Box();b.value=42;
        let value=b.value;delattr(b,"value");[value,b.value]'''
        self.assertEqual(self.run_source(source),[42,40])

    def test_generated_naming_sees_complete_class_before_publication(self):
        source='''macro make(value){quote{
          class Field{
            var complete=null;
            fn __set_name__(self,owner,name){self.complete=owner().later;null}
            fn __get__(self,instance,owner){self.complete}
          }
          class Owner{let answer=Field();var later=unquote(value)}
          Owner
        }}
        let later=42;let Owner=make!(later);[Owner.answer,Owner().answer,later]'''
        self.assertEqual(self.run_source(source),[42,42,42])

    def test_quoted_deletion_uses_explicit_lexical_caller_binding(self):
        source='''macro invoke(erase,value){quote{unquote(erase)(unquote(value),"answer")}}
        fn delattr(value,name){[value,name]}
        invoke!(delattr,42)'''
        self.assertEqual(self.run_source(source),[42,'answer'])
        with self.assertRaises(LanguageError) as caught:
            ProgramBundle('macro bad(x){quote{delattr(unquote(x),"answer")}};bad!(null)')
        self.assertEqual(caught.exception.code,'macro_error')

    def test_generated_descriptor_does_not_capture_outer_receiver_or_class_name(self):
        source='''macro make(value){quote{
          class Field{fn __get__(self,instance,owner){[unquote(value),owner]}}
          class Owner{let answer=Field()}
          Owner
        }}
        let instance=42;let Owner=99;let Generated=make!(instance);
        let pair=Generated().answer;[pair[0],pair[1]==Generated,instance,Owner]'''
        self.assertEqual(self.run_source(source),[42,True,42,99])

    def test_operator_template_keeps_protocol_names_and_keyword_aliases(self):
        source='''macro make(value,decline){quote{
          class Amount{
            fn __add__(self,other:Int){unquote(value)+other}
            fn __radd__(self,other)->NotImplemented{unquote(decline)}
          };Amount
        }}
        let self=40;let other=99;let Amount=make!(self,NotImplemented);let n=Amount();
        [n+2,n.__add__(other=2),type_of(n.__radd__(null)),self,other]'''
        self.assertEqual(self.run_source(source),[42,42,'NotImplemented',40,99])

    def test_operator_notimplemented_injection_keeps_caller_lexical_binding(self):
        source='''macro make(decline){quote{
          class Left{fn __add__(self,other){unquote(decline)}};Left
        }}
        class Right{fn __radd__(self,other){42}}
        let Actual=make!(NotImplemented);
        let local=do{let NotImplemented={"$notImplemented":true};make!(NotImplemented)};
        [Actual()+Right(),local()+Right()]'''
        self.assertEqual(self.run_source(source),[42,{'$notImplemented':True}])

    def test_operator_templates_cross_module_export_and_decline(self):
        files={'ops.chaos':'''macro make(value,decline){quote{
          class Adder{
            fn __add__(self,other){if other==0{unquote(decline)}else{unquote(value)+other}}
            fn __radd__(self,other){unquote(value)+other}
          };Adder
        }};let Shared=make!(40,NotImplemented);export{make,Shared}'''}
        source='''import "ops.chaos" as O;let other=40;
        let Adder=O.make!(other,NotImplemented);
        [Adder()+2,2+O.Shared(),isinstance(O.Shared(),O.Shared),keys(O),other]'''
        self.assertEqual(self.run_source(source,files=files),[42,42,True,['Shared'],40])

    def test_operator_compilation_does_not_call_generated_effectful_hooks(self):
        source='''macro make(action){quote{
          class Amount{fn __add__(self,other){unquote(action)("plus",other)}};Amount
        }};let Amount=make!(tool);Amount()+42'''
        calls=[];bundle=ProgramBundle(source)
        self.assertEqual(calls,[])
        self.assertEqual(self.run_source(bundle,tool_handler=lambda name,value:calls.append((name,value)) or value),42)
        self.assertEqual(calls,[('plus',42)])

    def test_operator_protocol_can_be_generated_descriptor_member(self):
        source='''macro make(value){quote{
          class Method{fn __get__(self,instance,owner){fn(other){unquote(value)+other}}}
          class Amount{let __add__=Method()};Amount
        }};let other=40;let Amount=make!(other);[Amount()+2,other]'''
        self.assertEqual(self.run_source(source),[42,40])

    def test_operator_pure_phase_rejects_runtime_builtins_and_sentinel(self):
        calls=[]
        forms=['NotImplemented','abs(-2)','pow(2,3)','floordiv(5,2)',
               'length([1])','contains([1],1)','bool(true)',
               'syntax_literal(NotImplemented)']
        for form in forms:
            with self.subTest(form=form),self.assertRaises(LanguageError) as caught:
                self.run_source('tool("before",0);macro bad(){'+form+';quote{42}};bad!()',
                                tool_handler=lambda *args:calls.append(args))
            self.assertEqual(caught.exception.code,'macro_error')
        self.assertEqual(calls,[])
        with self.assertRaises(LanguageError) as caught:
            ProgramBundle('macro bad(value){syntax_literal(value)};bad!(NotImplemented)')
        self.assertEqual(caught.exception.code,'macro_error')

    def test_operator_sentinel_is_never_inserted_as_live_ast_literal(self):
        from chaos.macros import Expander
        from chaos.operators import NOT_IMPLEMENTED
        with self.assertRaises(LanguageError) as caught:Expander({}).literal(NOT_IMPLEMENTED,None)
        self.assertEqual(caught.exception.code,'macro_error')
        bundle=ProgramBundle('macro forward(value){quote{unquote(value)}};forward!(NotImplemented)')
        def visit(value):
            self.assertIsNot(value,NOT_IMPLEMENTED)
            if isinstance(value,dict):
                for child in value.values():visit(child)
            elif isinstance(value,(tuple,list)):
                for child in value:visit(child)
        visit(bundle.trees);visit(bundle.expanded_trees)
        self.assertIs(self.run_source(bundle),NOT_IMPLEMENTED)
        source='''macro marker(){syntax_literal({"$notImplemented":true})}
        class Left{fn __add__(self,other){marker!()}}
        class Right{fn __radd__(self,other){42}};Left()+Right()'''
        self.assertEqual(self.run_source(source),{'$notImplemented':True})

    def test_generated_operator_equality_does_not_change_pure_patterns(self):
        source='''macro make(action){quote{
          class Item{fn __eq__(self,other){unquote(action)("eq",other);true}};Item
        }};let Item=make!(tool);let value=Item();
        let matched=match value{1=>"one",_=>"other"};[matched,value==1]'''
        calls=[]
        self.assertEqual(self.run_source(source,tool_handler=lambda name,value:calls.append((name,value))),['other',True])
        self.assertEqual(calls,[('eq',1)])

    def test_generated_bool_protocol_requires_explicit_conversion(self):
        source='''macro make(action){quote{
          class Flag{fn __bool__(self){unquote(action)("bool",null);true}};Flag
        }};let Flag=make!(tool);
        let bad=try{if Flag(){1}else{0}}catch error{"strict"};[bad,bool(Flag())]'''
        calls=[]
        self.assertEqual(self.run_source(source,tool_handler=lambda name,value:calls.append((name,value))),['strict',True])
        self.assertEqual(calls,[('bool',None)])

    def test_iterator_template_preserves_protocol_labels_and_keyword_names(self):
        source='''macro make(value){quote{
          class Cursor{
            fn __iter__(self){self}
            fn __next__(self){ {done:true,value:unquote(value)} }
            fn __send__(self,item){ {done:false,value:item+unquote(value)} }
          };Cursor
        }};let self=40;let item=99;let Cursor=make!(self);let c=Cursor();
        [c.__send__(item=2),next(iter(c)),self,item]'''
        self.assertEqual(self.run_source(source),[{'done':False,'value':42},{'done':True,'value':40},40,99])

    def test_iterator_macro_module_exports_remain_runtime_separate(self):
        files={'stream.chaos':'''macro make(value){quote{
          class Stream{fn* __iter__(self){yield unquote(value);yield 2}};Stream
        }};let Shared=make!(40);export{make,Shared}'''}
        source='''import "stream.chaos" as S;let self=41;
        let Stream=S.make!(self);[collect(Stream()),collect(S.Shared()),keys(S),self]'''
        self.assertEqual(self.run_source(source,files=files),[[41,2],[40,2],['Shared'],41])

    def test_iterator_generated_async_factory_and_async_steps(self):
        source='''macro make(value){quote{
          class Stream{async fn* __aiter__(self){yield unquote(value);yield 2}};Stream
        }};macro step(value){quote{
          class Cursor{async fn __anext__(self){ {done:true,value:unquote(value)} }};Cursor
        }};let self=40;let Stream=make!(self);let Cursor=step!(self);
        [await acollect(Stream()),await anext(Cursor()),self]'''
        self.assertEqual(self.run_source(source),[[40,2],{'done':True,'value':40},40])

    def test_iterator_generated_consumer_and_delegation_are_hygienic(self):
        source='''macro consume(source,adding){quote{
          var result=[];for item in unquote(source){result=unquote(adding)(result,item)};result
        }};macro forward(source){quote{fn*(){return yield from unquote(source)}}}
        class Source{fn* __iter__(self){yield 40;yield 42;return 84}}
        let result=9;let item=99;let g=forward!(Source())();
        [consume!(Source(),append),next(g),next(g),next(g),result,item]'''
        self.assertEqual(self.run_source(source),[[40,42],{'done':False,'value':40},{'done':False,'value':42},{'done':True,'value':84},9,99])

    def test_iterator_template_async_loop_and_descriptor_step(self):
        source='''macro consume(source,adding){quote{async fn(){
          var result=[];async for item in unquote(source){result=unquote(adding)(result,item)};result
        }}}
        macro cursor(value){quote{
          class Method{fn __get__(self,instance,owner){fn(){{done:true,value:unquote(value)}}}}
          class Cursor{let __next__=Method();fn __iter__(self){self}};Cursor
        }}
        class Source{async fn* __aiter__(self){yield 40;yield 42}}
        let item=99;let result=7;let consumeSource=consume!(Source(),append);
        let Cursor=cursor!(item);[await consumeSource(),next(Cursor()),item,result]'''
        self.assertEqual(self.run_source(source),[[40,42],{'done':True,'value':99},99,7])

    def test_iterator_macro_generated_optional_hooks_and_shared_completion(self):
        source='''macro make(){quote{
          class Cursor{
            var closed=false;
            fn __iter__(self){self}
            fn __next__(self){ {done:false,value:1} }
            fn __send__(self,value){ {done:false,value:value} }
            fn __throw__(self,value){ {done:true,value:value} }
            fn __close__(self){self.closed=true;null}
          };Cursor
        }};let Cursor=make!();let raw=Cursor();let one=iter(raw);let two=iter(raw);
        let values=[send(one,40),throw_into(two,42),next(raw)];close(one);
        [values,next(two),raw.closed]'''
        self.assertEqual(self.run_source(source),[[{'done':False,'value':40},{'done':True,'value':42},{'done':True,'value':42}],{'done':True,'value':42},True])

    def test_iterator_compilation_never_invokes_factory_step_or_cleanup(self):
        source='''macro make(action){quote{
          class Cursor{
            fn __iter__(self){unquote(action)("factory",null);self}
            fn __next__(self){unquote(action)("step",null);{done:true,value:42}}
            fn __close__(self){unquote(action)("close",null);null}
          };Cursor
        }};let Cursor=make!(tool);next(iter(Cursor()))'''
        calls=[];bundle=ProgramBundle(source)
        self.assertEqual(calls,[])
        self.assertEqual(self.run_source(bundle,tool_handler=lambda name,value:calls.append(name)),{'done':True,'value':42})
        self.assertEqual(calls,['factory','step','close'])

    def test_iterator_pure_phase_has_no_runtime_acquisition_or_consumers(self):
        calls=[]
        forms=['iter([1])','aiter(null)','next(null)','anext(null)','collect([1])','acollect(null)',
               'send(null,1)','asend(null,1)','throw_into(null,1)','athrow(null,1)','close(null)','aclose(null)']
        for form in forms:
            with self.subTest(form=form),self.assertRaises(LanguageError) as caught:
                self.run_source('tool("before",null);macro bad(){'+form+';quote{42}};bad!()',
                                tool_handler=lambda *args:calls.append(args))
            self.assertEqual(caught.exception.code,'macro_error')
        self.assertEqual(calls,[])
        with self.assertRaises(LanguageError) as caught:
            ProgramBundle('macro bad(x){syntax_literal(x)};bad!(iter([1]))')
        self.assertEqual(caught.exception.code,'macro_error')

    def test_iterator_handles_cannot_be_ast_literals_or_hidden_in_containers(self):
        from chaos.macros import Expander
        from chaos.iteration import Iterator, AsyncIterator
        sources=['iter([1])',
                 'aiter(do{class S{fn __aiter__(self){self};async fn __anext__(self){{done:true,value:null}}};S()})']
        for source,expected in zip(sources,(Iterator,AsyncIterator)):
            handle=self.run_source(source)
            self.assertIsInstance(handle,expected)
            for value in (handle,[handle],{'stream':handle}):
                with self.subTest(source=source,value_type=type(value).__name__),self.assertRaises(LanguageError) as caught:
                    Expander({}).literal(value,None)
                self.assertEqual(caught.exception.code,'macro_error')

    def test_iterator_template_runtime_contracts_do_not_change_authored_ir(self):
        from chaos.program import parse_program
        source='''macro consume(value,acquire){quote{
          let cursor:Iterator=unquote(acquire)(unquote(value));cursor
        }};next(consume!([42],iter))'''
        bundle=ProgramBundle(source)
        self.assertEqual(self.run_source(bundle),{'done':False,'value':42})
        original=parse_program(source)
        self.assertEqual(parse_program(source),original)
        self.assertEqual(bundle.trees['main.chaos'][1][0][0],'macro')
        self.assertFalse(any(node[0]=='macro' for node in bundle.expanded_trees['main.chaos'][1]))

    def test_indexing_pure_native_reads_slices_and_legacy_limits(self):
        source='''macro values(){
          let xs=[10,20,30];let key="answer";
          syntax_literal([xs[-1],xs[::2],xs[::-1],"好!"[::-1],{answer:42}[key]])
        };values!()'''
        self.assertEqual(self.run_source(source),[30,[10,30],[30,20,10],'!好',42])
        for expression in ('[1].[-1]','[1][true]','[1][::0]','[1][false:]','{a:1}[:]'):
            with self.subTest(expression=expression),self.assertRaises(LanguageError) as caught:
                ProgramBundle('macro bad(){syntax_literal('+expression+')};bad!()')
            self.assertEqual(caught.exception.code,'macro_error')

    def test_indexing_quote_subscript_slice_optional_bounds_are_hygienic(self):
        source='''macro window(value,start){quote{
          let result=unquote(value);result[unquote(start)::2]
        }};let result=99;let start=0;
        [window!([10,20,30,40],start),start,result]'''
        self.assertEqual(self.run_source(source),[[10,30],0,99])
        source='''macro flip(value){quote{unquote(value)[::-1]}};
        macro all(value){quote{unquote(value)[:]}};[flip!([1,2]),all!("ok")]'''
        self.assertEqual(self.run_source(source),[[2,1],'ok'])

    def test_indexing_macro_legacy_and_item_access_never_conflate(self):
        source='''macro access(value,key){quote{
          [unquote(value).[unquote(key)],unquote(value)[unquote(key)]]
        }};class C{let answer=40;fn __getitem__(self,key){42}}
        access!(C(),"answer")'''
        self.assertEqual(self.run_source(source),[40,42])
        tree=ProgramBundle(source).expanded_trees['main.chaos']
        kinds=[]
        from chaos.macros import _children
        def visit(node):kinds.append(node[0]);_children(node,visit)
        visit(tree)
        self.assertIn('index',kinds);self.assertIn('subscript',kinds)

    def test_indexing_generated_protocol_labels_and_named_parameters(self):
        source='''macro make(value){quote{
          class Box{
            var amount=unquote(value);
            fn __getitem__(self,key){self.amount+key}
            fn __setitem__(self,key,item){self.amount=item-key;null}
            fn __delitem__(self,key){self.amount=key;null}
          };Box
        }};let self=40;let key=99;let item=77;let Box=make!(self);let box=Box();
        let first=box.__getitem__(key=2);box[2]=44;box.__delitem__(key=3);
        [first,box.amount,self,key,item]'''
        self.assertEqual(self.run_source(source),[42,3,40,99,77])

    def test_indexing_macro_sliced_assignment_and_evaluation_order(self):
        source='''macro write(target,start,stop,step,value){quote{
          unquote(target)[unquote(start):unquote(stop):unquote(step)]=unquote(value)
        }};var seen=[];fn mark(label,value){seen=append(seen,label);value}
        class C{fn __setitem__(self,key,item){seen=append(seen,[key.start,key.stop,key.step,item]);null}}
        write!(mark("object",C()),mark("start",0),mark("stop",3),mark("step",2),mark("value",42));seen'''
        self.assertEqual(self.run_source(source),['object','start','stop','step','value',[0,3,2,42]])

    def test_indexing_macro_module_origin_and_runtime_exports(self):
        files={'forms.chaos':'''macro reverse(value){quote{unquote(value)[::-1]}}
        macro make(value){quote{class C{fn __getitem__(self,key){unquote(value)[key]}};C}}
        let Shared=make!([40,42]);export{reverse,make,Shared}'''}
        source='''import "forms.chaos" as F;let key=1;let C=F.make!([40,42]);
        [F.reverse!([1,2]),C()[key],F.Shared()[0],keys(F),key]'''
        self.assertEqual(self.run_source(source,files=files),[[2,1],42,40,['Shared'],1])

    def test_indexing_macro_compilation_defers_get_set_delete_effects(self):
        source='''macro make(action){quote{
          class C{
            fn __getitem__(self,key){unquote(action)("get",key)}
            fn __setitem__(self,key,item){unquote(action)("set",item);null}
            fn __delitem__(self,key){unquote(action)("delete",key);null}
          };C
        }};let C=make!(tool);let c=C();let first=c[42];c[0]=41;delitem(c,0);first'''
        calls=[];bundle=ProgramBundle(source);self.assertEqual(calls,[])
        self.assertEqual(self.run_source(bundle,tool_handler=lambda name,value:calls.append((name,value)) or value),42)
        self.assertEqual(calls,[('get',42),('set',41),('delete',0)])

    def test_indexing_pure_phase_rejects_runtime_dispatch_and_slice_constructor(self):
        forms=['getitem([1],0)','setitem(null,0,1)','delitem(null,0)',
               'slice_key(null,null,-1)','slice_indices(null,1)','class C{}']
        calls=[]
        for form in forms:
            with self.subTest(form=form),self.assertRaises(LanguageError) as caught:
                self.run_source('tool("before",null);macro bad(){'+form+';quote{42}};bad!()',
                                tool_handler=lambda *args:calls.append(args))
            self.assertEqual(caught.exception.code,'macro_error')
        self.assertEqual(calls,[])

    def test_indexing_live_slice_literal_is_rejected_even_nested(self):
        from chaos.indexing import slice_key
        from chaos.macros import Expander
        spec=slice_key(None,None,-1)
        for value in (spec,[spec],{'window':spec}):
            with self.subTest(kind=type(value).__name__),self.assertRaises(LanguageError) as caught:
                Expander({}).literal(value,None)
            self.assertEqual(caught.exception.code,'macro_error')
        self.assertEqual(self.run_source(ProgramBundle('macro marker(){syntax_literal({"$slice":{start:null,stop:null,step:-1}})};type_of(marker!())')),'Record')

    def test_indexing_slice_bounds_are_checked_for_invalid_generated_control(self):
        calls=[]
        sources=['macro bad(x){quote{fn(value=[1][unquote(x):]){value}}};fn outer(){bad!(return 1)};outer()',
                 'macro bad(x){quote{fn(){[1][::unquote(x)]}}};bad!(await input)',
                 'macro bad(x){quote{fn(){[1][unquote(x):]}}};while true{bad!(break);break}']
        for source in sources:
            with self.subTest(source=source),self.assertRaises(LanguageError):
                self.run_source('tool("before",null);'+source,tool_handler=lambda *args:calls.append(args))
        self.assertEqual(calls,[])
        from chaos.macros import Expander
        with self.assertRaises(LanguageError) as caught:
            Expander({}).validate_scope(('block',[('slice',None,None,None)]))
        self.assertEqual(caught.exception.code,'macro_error')

    def test_indexing_slice_clone_keeps_macro_origin_and_call_locations(self):
        source='macro bad(value){quote{unquote(value)[::0]}};\nbad!([1])'
        with self.assertRaises(LanguageError) as caught:self.run_source(source)
        self.assertEqual(caught.exception.line,2)
        bundle=ProgramBundle('macro reverse(value){quote{unquote(value)[::-1]}};reverse!([1,2])')
        self.assertEqual(bundle.trees['main.chaos'][1][0][0],'macro')
        self.assertEqual(self.run_source(bundle),[2,1])
        from chaos.macros import _children
        slices=[]
        def visit(node):
            if node[0]=='slice':slices.append(node)
            _children(node,visit)
        visit(bundle.expanded_trees['main.chaos'])
        self.assertEqual(len(slices),1);self.assertIn('macroOrigin',slices[0].span)

    def test_indexing_mixed_dialect_macro_preserves_definition_and_caller_ast(self):
        files={'old.chaos':'''macro read(value,key){quote{unquote(value)[unquote(key)]}}
        macro identity(value){quote{unquote(value)}};export{read,identity}'''}
        source='''import "old.chaos" as O;class C{let answer=40;fn __getitem__(self,key){42}}
        [O.read!(C(),"answer"),O.identity!(C()["answer"])]'''
        bundle=ProgramBundle(source,files,language_versions={'main.chaos':2,'old.chaos':1})
        self.assertEqual(self.run_source(bundle),[40,42])
        self.assertEqual(bundle.language_versions,{'main.chaos':2,'old.chaos':1})

    def test_original_ast_is_retained_and_expansion_is_deterministic(self):
        source = 'macro twice(x){quote{let saved=unquote(x);saved+saved}};twice!(21)'
        first, second = ProgramBundle(source), ProgramBundle(source)
        self.assertEqual(first.trees['main.chaos'][1][0][0], 'macro')
        self.assertEqual(first.trees['main.chaos'][1][1][0], 'macrocall')
        self.assertEqual(first.expanded_trees, second.expanded_trees)
        self.assertEqual(self.run_source(first), 42)

    def test_contextual_names_and_record_keys_are_not_phase_nodes(self):
        source = '''
        macro identity(value){value}
        fn f(macro,quote,unquote){[macro,quote,unquote]};
        [f(1,2,3),identity!({quote:1,unquote:2,macro:3})]
        '''
        self.assertEqual(self.run_source(source), [[1, 2, 3], {'quote': 1, 'unquote': 2, 'macro': 3}])

    def test_invalid_phase_forms_and_result_types_are_rejected(self):
        sources = [
            'quote{1}', 'macro bad(){1};bad!()', 'macro bad(x){quote{unquote(1)}};bad!(1)',
            'macro bad(){quote{quote{1}}};bad!()', 'macro bad(x){x};bad!()',
            'macro x(){quote{1}};let x=2;x!()',
            'macro bad(x){syntax_literal(x)};bad!(1)',
            'macro bad(x){syntax_value(x)};bad!(1+2)',
        ]
        for source in sources:
            with self.subTest(source=source), self.assertRaises(LanguageError): ProgramBundle(source)

    def test_generated_control_is_checked_before_effects(self):
        calls = []
        invalid = [
            'macro m(){quote{return 1}};tool("before",0);m!()',
            'macro m(){quote{break}};m!()',
            'macro m(){quote{continue}};fn f(){m!()};f()',
            'macro m(){quote{yield 1}};m!()',
            'macro m(x){quote{await unquote(x)}};fn f(){m!(1)};f()',
            'macro m(){quote{return 1}};fn f(x=m!()){x};f()',
            'macro m(x){quote{fn(){unquote(x)}}};while true{m!(break)}',
            'macro m(x){quote{fn(v=unquote(x)){v}}};fn f(){m!(return 1)};f()',
        ]
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(LanguageError) as caught:
                self.run_source(source, tool_handler=lambda *args: calls.append(args))
            self.assertEqual(caught.exception.code, 'macro_error')
        self.assertEqual(calls, [])
        self.assertEqual(self.run_source('macro stop(x){quote{return unquote(x)}};fn f(){stop!(42);0};f()'), 42)
        self.assertEqual(self.run_source('macro stop(){quote{break}};var x=0;while true{x=x+1;stop!()};x'), 1)

    def test_unused_macro_body_is_parsed_but_not_evaluated(self):
        self.assertEqual(self.run_source('macro unused(){tool("never",null)};42'), 42)
        with self.assertRaises(LanguageError) as caught:
            ProgramBundle('macro m(){return quote{42}};m!()')
        self.assertEqual(caught.exception.code, 'syntax_error')
        self.assertEqual(self.run_source('macro m(){fn helper(){return quote{42}};helper()};m!()'), 42)

    def test_unbounded_macro_expansion_and_pure_recursion_are_stopped(self):
        for source in ['macro loop(){quote{loop!()}};loop!()',
                       'macro loop(){fn again(){again()};again()};loop!()']:
            with self.subTest(source=source), self.assertRaises(ResourceLimit): ProgramBundle(source)

    def test_expansion_budgets_cover_nodes_steps_calls_and_time(self):
        tree = Parser('macro m(x){quote{unquote(x)+1}};m!(41)').program()
        for limits in [{'step_limit': 1}, {'node_limit': 1}, {'call_limit': 0}, {'timeout': 0}]:
            with self.subTest(limits=limits), self.assertRaises(ResourceLimit):
                expand_trees({'main.chaos': tree}, **limits)
        huge = 'macro m(){fn grow(n,x){if n==0{x}else{grow(n-1,x+x)}};syntax_literal(grow(15,[1]))};m!()'
        with self.assertRaises(ResourceLimit): ProgramBundle(huge)

    def test_macro_errors_report_call_and_definition_locations(self):
        source = 'macro bad(x){quote{missing}};\n\nbad!(1)'
        with self.assertRaises(LanguageError) as caught: ProgramBundle(source, entry='entry.chaos')
        error = caught.exception
        self.assertEqual((error.file, error.line), ('entry.chaos', 3))
        self.assertEqual(error.value['definition']['line'], 1)
        for suffix in ('bad!(1)+1', 'bad!(1)()'):
            with self.subTest(suffix=suffix), self.assertRaises(LanguageError) as caught:
                ProgramBundle('macro bad(x){quote{missing}};\n\n'+suffix, entry='entry.chaos')
            self.assertEqual((caught.exception.file, caught.exception.line, caught.exception.column),
                             ('entry.chaos', 3, 1))
        source = 'macro bad(){quote{1/0}};\n\nbad!()'
        with self.assertRaises(LanguageError) as caught: self.run_source(source)
        self.assertEqual(caught.exception.line, 3)

    def test_macro_generated_generator_and_async_closure_aliases(self):
        source = '''
        macro sequence(value){quote{fn*(x){yield unquote(value);yield x}}}
        macro deferred(value){quote{async fn(x){unquote(value)+x}}}
        let x=40; let g=sequence!(x);let f=deferred!(x);
        [collect(g(x=2)),await f(x=2)]
        '''
        self.assertEqual(self.run_source(source), [[40, 2], 42])


if __name__ == '__main__': unittest.main()
