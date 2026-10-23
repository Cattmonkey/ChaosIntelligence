import hashlib
import unittest
from unittest.mock import patch

from chaos.language import Evaluator, LanguageError, ResourceLimit, tokenize
from chaos.symbols import references, rename


class SymbolsTests(unittest.TestCase):
    def renamed(self, source, old, new, *, occurrence=0, **kwargs):
        position=[token.start for token in tokenize(source) if token.kind=='id' and token.value==old][occurrence]
        position=len(source[:position].encode('utf-16-le'))//2
        return rename(source,position=position,new_name=new,**kwargs)

    def test_reverse_importers_aliases_reexports_and_structural_keys(self):
        files={'lib.chaos':'let value=41; export {value}',
               'main.chaos':'import "lib.chaos" as L; let local=L.value; {value:local}',
               'other.chaos':'import "lib.chaos" as M; let alias=M; alias.value + M.["value"]',
               'reexport.chaos':'import "lib.chaos" as L; let other=L.value; export {other}'}
        plan=self.renamed(files['lib.chaos'],'value','answer',file='lib.chaos',files=files)
        self.assertEqual(plan['files']['main.chaos'],'import "lib.chaos" as L; let local=L.answer; {value:local}')
        self.assertIn('alias.answer + M.["answer"]',plan['files']['other.chaos'])
        self.assertIn('let other=L.answer; export {other}',plan['files']['reexport.chaos'])
        self.assertEqual(plan['baseHashes']['lib.chaos'],hashlib.sha256(files['lib.chaos'].encode()).hexdigest())
        self.assertEqual(files['lib.chaos'],'let value=41; export {value}')

    def test_local_alias_has_distinct_identity(self):
        s='fn source(x){x};let alias=source;alias(1);source(2)'
        result=self.renamed(s,'alias','local')['files']['main.chaos']
        self.assertEqual(result,'fn source(x){x};let local=source;local(1);source(2)')

    def test_capture_of_target_and_preexisting_unresolved_reference_rejected(self):
        for source,old,new in [('let x=1;fn f(y){x+y};f(2)','x','y'),
                               ('let x=1;fn f(){missing+x};f()','x','missing'),
                               ('let x=1;fn f(){length([x])};f()','x','length')]:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.renamed(source,old,new)

    def test_function_defaults_are_outer_and_body_shadow_is_local(self):
        s='let x=40; fn f(x=x+1){let y=x;y};f()'
        plan=self.renamed(s,'x','base')
        self.assertEqual(plan['files']['main.chaos'],'let base=40; fn f(x=base+1){let y=x;y};f()')
        self.assertEqual(Evaluator().run(plan['files']['main.chaos']),41)

    def test_keywords_through_immutable_function_alias(self):
        s='fn f(amount=1){amount};let call=f;call(amount=42)'
        result=self.renamed(s,'amount','count')['files']['main.chaos']
        self.assertEqual(result,'fn f(count=1){count};let call=f;call(count=42)')
        self.assertEqual(Evaluator().run(result),42)

    def test_mutual_recursive_keyword_call(self):
        s='rec {fn left(n){right(value=n)};fn right(value){value}};left(42)'
        result=self.renamed(s,'value','number',occurrence=1)['files']['main.chaos']
        self.assertIn('right(number=n)',result)
        self.assertEqual(Evaluator().run(result),42)

    def test_exported_parameter_keywords_across_bundle(self):
        files={'lib.chaos':'fn f(value){value};export{f}',
               'main.chaos':'import "lib.chaos" as M;let payload={value:0};M.f(value=42)'}
        report=references(files['lib.chaos'],file='lib.chaos',files=files,position=5)
        self.assertTrue(report['complete'])
        self.assertEqual({r['kind'] for r in report['references']},{'declaration','read','keyword'})
        plan=rename(files['lib.chaos'],file='lib.chaos',files=files,position=5,new_name='amount')
        self.assertEqual(plan['files']['lib.chaos'],'fn f(amount){amount};export{f}')
        self.assertEqual(plan['files']['main.chaos'],'import "lib.chaos" as M;let payload={value:0};M.f(amount=42)')
        self.assertEqual(Evaluator().run(plan['files']['main.chaos'],files=plan['files']),42)

    def test_dynamic_parameter_calls_incomplete_and_rename_denied(self):
        for suffix in ['let escaped={callback:f};escaped.callback(1)', 'f(**{x:1})']:
            source='fn f(x){x};'+suffix
            report=references(source,position=source.index('x'))
            self.assertFalse(report['complete']);self.assertTrue(report['limitations'])
            with self.assertRaises(LanguageError) as error:self.renamed(source,'x','y')
            self.assertEqual(error.exception.code,'symbol_unsupported')

    def test_dynamic_module_namespace_is_explicitly_incomplete(self):
        for tail in ['let key="x";L.[key]','let box={module:L};box.module.x','get(L,"x")','L']:
            files={'lib.chaos':'let x=1;export{x}','main.chaos':'import "lib.chaos" as L;'+tail}
            report=references(files['lib.chaos'],file='lib.chaos',files=files,position=4)
            self.assertFalse(report['complete'])
            with self.assertRaises(LanguageError):rename(files['lib.chaos'],file='lib.chaos',files=files,position=4,new_name='y')

    def test_types_and_pattern_constructors(self):
        files={'lib.chaos':'data Result{Ok(value)};export{Result,Ok}',
               'main.chaos':'import "lib.chaos" as T;fn f(x:T.Result)->T.Result{x};match f(T.Ok(1)){T.Ok(v)=>v}'}
        result=self.renamed(files['lib.chaos'],'Result','Answer',file='lib.chaos',files=files)['files']
        self.assertIn('x:T.Answer)->T.Answer',result['main.chaos'])
        result=self.renamed(files['lib.chaos'],'Ok','Success',file='lib.chaos',files=files)['files']
        self.assertIn('T.Success(1)',result['main.chaos']);self.assertIn('T.Success(v)',result['main.chaos'])

    def test_pattern_and_comprehension_order(self):
        s='let x=2;[x+y for x in [x] for y in [x] if y>0]'
        result=self.renamed(s,'x','outer')['files']['main.chaos']
        self.assertEqual(result,'let outer=2;[x+y for x in [outer] for y in [x] if y>0]')
        s='let {same: x,..rest}={same:42};[x,rest]'
        result=self.renamed(s,'x','value')['files']['main.chaos']
        self.assertEqual(result,'let {same: value,..rest}={same:42};[value,rest]')

    def test_context_catch_loop_and_generator_bindings(self):
        sources=[('with manager as resource{resource}','resource','item'),
                 ('try{throw 1}catch error{error}','error','failure'),
                 ('for item in [1]{item}','item','entry'),
                 ('fn* stream(value){yield value};list(stream(1))','value','item')]
        for source,old,new in sources:
            with self.subTest(source=source):
                result=self.renamed(source,old,new)['files']['main.chaos']
                self.assertNotIn(old,result)

    def test_class_binding_annotations_and_member_exclusion(self):
        s='class C{fn f(self,x:C)->C{x}};fn use(x:C)->C{x};use(C())'
        result=self.renamed(s,'C','Box')['files']['main.chaos']
        self.assertEqual(result,'class Box{fn f(self,x:Box)->Box{x}};fn use(x:Box)->Box{x};use(Box())')
        with self.assertRaises(LanguageError):self.renamed(s,'f','method')

    def test_macro_export_calls_and_runtime_alias_separate(self):
        files={'mac.chaos':'macro identity(x){quote{unquote(x)}};export{identity}',
               'main.chaos':'import "mac.chaos" as M;M.identity!(42)'}
        result=self.renamed(files['mac.chaos'],'identity','passthrough',file='mac.chaos',files=files)['files']
        self.assertIn('M.passthrough!(42)',result['main.chaos'])
        self.assertEqual(Evaluator().run(result['main.chaos'],files=result),42)
        result=self.renamed(files['main.chaos'],'M','Macros',files=files)['files']
        self.assertIn('Macros.identity!',result['main.chaos'])

    def test_macro_parameter_unquote_and_opaque_source(self):
        s='macro identity(value){quote{unquote(value)}};identity!(42)'
        result=self.renamed(s,'value','syntax')['files']['main.chaos']
        self.assertIn('unquote(syntax)',result)
        s='macro identity(value){quote{unquote(value)}};let x=1;identity!(x)'
        report=references(s,position=s.index('let x')+4)
        self.assertFalse(report['complete'])
        with self.assertRaises(LanguageError):rename(s,position=s.index('let x')+4,new_name='y')
        s='macro m(){quote{let local=1;local}};m!()'
        self.assertFalse(references(s,position=s.index('local'))['complete'])

    def test_no_source_or_macro_execution(self):
        s='macro never(){while true {1};quote{1}};let value=tool("secret",null);value'
        with patch.object(Evaluator,'run',side_effect=AssertionError('executed')):
            with patch('chaos.macros.expand_trees',side_effect=AssertionError('expanded')):
                result=self.renamed(s,'value','answer')
        self.assertIn('let answer=tool',result['files']['main.chaos'])

    def test_utf16_unicode_comments_and_quoted_keys(self):
        s='// 😀 世界\nlet 变量=42;{变量:"变量",result:变量}'
        position=len(s[:s.index('变量')].encode('utf-16-le'))//2
        result=rename(s,position=position,new_name='答案')['files']['main.chaos']
        self.assertEqual(result,'// 😀 世界\nlet 答案=42;{变量:"变量",result:答案}')
        with self.assertRaises(LanguageError):references(s,position=4)
        for pos in [-1,True,1.2,10000]:
            with self.assertRaises(LanguageError):references(s,position=pos)

    def test_preserves_legacy_dialect_and_quotes(self):
        files={'lib.chaos':'let value=1;export{value}','main.chaos':"import 'lib.chaos' as L;L['value']"}
        result=rename(files['lib.chaos'],file='lib.chaos',files=files,position=4,new_name='answer',language_versions={'main.chaos':1})
        self.assertIn("L['answer']",result['files']['main.chaos'])
        self.assertEqual(result['languageVersions'],{'lib.chaos':2,'main.chaos':1})

    def test_nested_context_and_class_default_contract_scopes(self):
        source='with first as x,second(x) as y{x+y}'
        result=self.renamed(source,'x','resource')['files']['main.chaos']
        self.assertEqual(result,'with first as resource,second(resource) as y{resource+y}')
        source='class Outer{};class C{let T=Outer;fn f(self,x:T)->T{x}};C().f(Outer())'
        result=self.renamed(source,'Outer','Box')['files']['main.chaos']
        self.assertEqual(result,'class Box{};class C{let T=Box;fn f(self,x:T)->T{x}};C().f(Box())')
        self.assertEqual(type(Evaluator().run(result)).__name__,'ChaosInstance')

    def test_namespace_reexport_and_undefined_member_capture(self):
        files={'lib.chaos':'let x=42;export{x}',
               'bridge.chaos':'import "lib.chaos" as namespace;export{namespace}',
               'main.chaos':'import "bridge.chaos" as B;B.namespace.x'}
        result=rename(files['lib.chaos'],file='lib.chaos',files=files,position=4,new_name='answer')
        self.assertEqual(result['files']['main.chaos'],'import "bridge.chaos" as B;B.namespace.answer')
        self.assertEqual(Evaluator().run(result['files']['main.chaos'],files=result['files']),42)
        files['main.chaos']='import "bridge.chaos" as B;B.namespace.missing+B.namespace.x'
        with self.assertRaises(LanguageError):rename(files['lib.chaos'],file='lib.chaos',files=files,position=4,new_name='missing')

    def test_same_spelling_shadow_and_builtin_annotation_independent(self):
        source='let Int=42;fn f(value:Int)->Int{value};[Int,f(1)]'
        result=self.renamed(source,'Int','number')['files']['main.chaos']
        self.assertEqual(result,'let number=42;fn f(value:Int)->Int{value};[number,f(1)]')
        self.assertEqual(Evaluator().run(result),[42,1])
        source='fn f(x){fn g(x){x};[x,g(x)]};f(x=42)'
        result=self.renamed(source,'x','amount')['files']['main.chaos']
        self.assertEqual(result,'fn f(amount){fn g(x){x};[amount,g(amount)]};f(amount=42)')

    def test_dense_edits_and_hard_checkpoint_during_candidate(self):
        source='let x=1;'+'x;'*1500
        result=rename(source,position=4,new_name='value')
        self.assertEqual(len(result['edits']['main.chaos']),1501)
        self.assertEqual(result['files']['main.chaos'],'let value=1;'+'value;'*1500)
        seen=[]
        def checkpoint():
            seen.append(1)
            if len(seen)==10000:raise ResourceLimit('cancel candidate')
        with self.assertRaises(ResourceLimit):rename(source,position=4,new_name='value',checkpoint=checkpoint)

    def test_parameter_constructor_capture_uses_recursive_closure(self):
        sources=[
            'data R{Cons(value)};let subject=Cons(42);do{fn parse(x as Cons(v)){v};parse(subject)}',
            'data R{Cons(value)};let subject=Cons(42);do{rec{fn parse(x as Cons(v)){helper(v)};fn helper(v){v}};parse(subject)}',
        ]
        for source in sources:
            self.assertEqual(Evaluator().run(source),42)
            with self.assertRaises(LanguageError) as error:
                self.renamed(source,'parse','Cons')
            self.assertEqual(error.exception.code,'symbol_conflict')
            valid=self.renamed(source,'parse','decode')['files']['main.chaos']
            self.assertEqual(Evaluator().run(valid),42)
        # Parameter names cannot shadow constructors in captured pattern lookup.
        source='data R{Cons(value)};fn parse(Cons,x as Cons(v)){v};parse(0,Cons(42))'
        report=references(source,position=source.index('Cons'))
        self.assertEqual(len(report['references']),3)
        renamed=self.renamed(source,'Cons','Some')['files']['main.chaos']
        self.assertEqual(Evaluator().run(renamed),42)

    def test_method_body_uses_outer_closure_not_implicit_self_name(self):
        sources=[
            'fn f(){42};class C{fn f(self){f()}};C().f()',
            'fn f(){42};class C{@staticmethod\nfn f(){f()}};C.f()',
            'data R{Cons(value)};class C{fn Cons(self,x as Cons(v)){v}};C().Cons(Cons(42))',
        ]
        for source in sources:
            self.assertEqual(Evaluator().run(source),42)
            old='Cons' if source.startswith('data') else 'f'
            renamed=self.renamed(source,old,'outer')['files']['main.chaos']
            self.assertEqual(Evaluator().run(renamed),42)
            self.assertIn('fn '+old+'(',renamed)

    def test_dynamic_reexport_namespace_escape_propagates(self):
        files={'main.chaos':'import "outer.chaos" as O;let key="M";let dyn=O[key];dyn.answer()',
               'outer.chaos':'import "inner.chaos" as M;export{M}',
               'inner.chaos':'fn answer(){42};export{answer}'}
        self.assertEqual(Evaluator().run(files['main.chaos'],files=files),42)
        report=references(files['inner.chaos'],file='inner.chaos',files=files,position=3)
        self.assertFalse(report['complete']);self.assertTrue(report['limitations'])
        with self.assertRaises(LanguageError) as error:
            rename(files['inner.chaos'],file='inner.chaos',files=files,position=3,new_name='response')
        self.assertEqual(error.exception.code,'symbol_unsupported')
        # A second re-export layer and a diamond share the same finite closure.
        files['bridge.chaos']='import "outer.chaos" as A;import "inner.chaos" as B;export{A,B}'
        files['main.chaos']='import "bridge.chaos" as O;let key="A";let dyn=O[key];dyn.M.answer()'
        self.assertEqual(Evaluator().run(files['main.chaos'],files=files),42)
        self.assertFalse(references(files['inner.chaos'],file='inner.chaos',files=files,position=3)['complete'])

    def test_dynamic_exported_callable_propagates_parameter_limit(self):
        files={'main.chaos':'import "outer.chaos" as O;let key="alias";let dyn=O[key];dyn(value=42)',
               'outer.chaos':'import "inner.chaos" as M;let alias=M.f;export{alias}',
               'inner.chaos':'fn f(value){value};export{f}'}
        self.assertEqual(Evaluator().run(files['main.chaos'],files=files),42)
        self.assertFalse(references(files['inner.chaos'],file='inner.chaos',files=files,position=5)['complete'])
        with self.assertRaises(LanguageError) as error:
            rename(files['inner.chaos'],file='inner.chaos',files=files,position=5,new_name='amount')
        self.assertEqual(error.exception.code,'symbol_unsupported')
        # Renaming the original function binding preserves the alias contract.
        plan=rename(files['inner.chaos'],file='inner.chaos',files=files,position=3,new_name='worker')
        self.assertEqual(Evaluator().run(plan['files']['main.chaos'],files=plan['files']),42)

    def test_projected_limits_before_candidate_allocation(self):
        from chaos import symbols
        source='let x=1;'+'x;'*500
        original=symbols._build
        calls=[]
        def observed(*args,**kwargs):
            calls.append(len(args[0]))
            return original(*args,**kwargs)
        with patch.object(symbols,'_build',observed):
            with self.assertRaises(ResourceLimit):rename(source,position=4,new_name='a'*1000)
        self.assertEqual(calls,[len(source)])
        # The package byte ceiling is independent of each file's character cap.
        files={'lib.chaos':'let x=1;export{x}'}
        for i in range(12):files['m'+str(i)+'.chaos']='import "lib.chaos" as M;'+'M.x;'*100
        files['main.chaos']='import "lib.chaos" as M;M.x'
        calls.clear()
        with patch.object(symbols,'_build',observed):
            with self.assertRaises(ResourceLimit):
                rename(files['lib.chaos'],file='lib.chaos',files=files,position=4,new_name='值'*300)
        self.assertEqual(calls,[len(files['lib.chaos'])])

    def test_complete_bundle_strict_parse_and_checkpoint(self):
        with self.assertRaises(LanguageError):references('let x=1;x',files={'bad.chaos':'let ='},position=4)
        called=[]
        def checkpoint():
            called.append(1)
            if len(called)>20:raise ResourceLimit('stop')
        with self.assertRaises(ResourceLimit):rename('let x=1;x',position=4,new_name='y',checkpoint=checkpoint)

    def test_declaration_filter_noop_and_reserved(self):
        s='let value=42;value'
        self.assertEqual(len(references(s,position=4,include_declaration=False)['references']),1)
        self.assertEqual(rename(s,position=4,new_name='value')['files']['main.chaos'],s)
        for name in ['for','two words','1a','x.y','']:
            with self.assertRaises(LanguageError):rename(s,position=4,new_name=name)


if __name__=='__main__':unittest.main()
