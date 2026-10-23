"""Structural reassignment updates real lexical Cells as one checked group."""
import time
import unittest

from chaos.language import Cell, Env, Evaluator, LanguageError, Parser, ResourceLimit
from chaos.session import Session


class PatternStateTests(unittest.TestCase):
    def run_code(self, source, **options):
        return Evaluator(**options).run(source)

    def test_swap_nested_rest_and_original_result(self):
        source='''var a:Int=1;var b:Int=2;var rest={};var tail=[];
          let result=do{[a,{x:b,..rest},..tail]=[b,{x:a,extra:3},4,5]};
          [a,b,rest,tail,result]'''
        self.assertEqual(self.run_code(source),[2,1,{'extra':3},[4,5],[2,{'x':1,'extra':3},4,5]])
        self.assertEqual(self.run_code('var x=1;[_,x,.._]=[0,42,9];{x:x,.._}={x:x,extra:0};x'),42)
        self.assertEqual(self.run_code('[]=[];{}={};42'),42)

    def test_rhs_once_before_lookup_and_effects_are_not_rolled_back(self):
        for declaration in ('let b=0;',''):
            source='var calls=0;var a=1;'+declaration+'try{[a,b]=do{calls+=1;a=9;[20,22]}}catch e{null};[a,calls]'
            self.assertEqual(self.run_code(source),[9,1])
        self.assertEqual(self.run_code('var a=1;var b=2;var calls=0;fn rhs(){calls+=1;[b,a]};[a,b]=rhs();[a,b,calls]'),[2,1,1])

    def test_contract_failure_never_publishes_earlier_leaves(self):
        session=Session();session.evaluate('var a:Int=1;var b:Int=2;var events=0')
        for rhs in ('[20,"bad"]','[20]','{a:20,b:22}'):
            with self.subTest(rhs=rhs),self.assertRaises(LanguageError):
                session.evaluate('[a,b]=do{events+=1;'+rhs+'}')
            self.assertEqual([session.bindings()[n] for n in ('a','b')],[1,2])
        self.assertEqual(session.bindings()['events'],3)
        session.evaluate('[a,b]=[20,22]')
        self.assertEqual([session.bindings()[n] for n in ('a','b')],[20,22])

    def test_existing_nominal_contracts_and_closures_are_preserved(self):
        source='''class C{};var object:C=C();let original=object;var n:Int=1;
          fn read(){[object==original,n]};try{[n,object]=[42,0]}catch e{null};
          let before=read();[n,object]=[42,C()];[before,read()]'''
        self.assertEqual(self.run_code(source),[[True,1],[False,42]])
        source='''data D{Value(x)};var item:D=Value(1);var n=0;
          try{[n,item]=[42,1]}catch e{null};[n,item]'''
        self.assertEqual(self.run_code(source),[0,{'$type':'main.chaos::D','$tag':'Value','$values':[1]}])

    def test_constructor_lookup_after_rhs_before_any_writes(self):
        source='''data D{One(x),Other(x)};var C=One;var x=0;
          [C,C(x)]=[99,One(42)];[C,x]'''
        self.assertEqual(self.run_code(source),[99,42])
        source='''data D{One(x),Other(x)};var C=One;var x=0;
          C(x)=do{C=Other;Other(42)};[x,C(1)]'''
        self.assertEqual(self.run_code(source),[42,{'$type':'main.chaos::D','$tag':'Other','$values':[1]}])
        self.assertEqual(self.run_code('import "std/option" as O;var x=0;O.Some(x)=O.Some(42);x'),42)
        self.assertEqual(self.run_code('data D{Empty};Empty()=Empty();42'),42)

    def test_literal_mismatch_strictness_and_no_user_protocols(self):
        self.assertEqual(self.run_code('var x=0;let error=try{[x,1]=[42,true]}catch e{e.code};[x,error]'),[0,'match_error'])
        self.assertEqual(self.run_code('''var calls=0;var x=1;
          class C{fn __iter__(s){calls+=1;iter([42])}fn __getitem__(s,k){calls+=1;42}}
          let error=try{[x]=C()}catch e{e.code};[x,calls,error]'''),[1,0,'match_error'])
        for rhs in ('"ab"','iter([20,22])','(x for x in [20,22])'):
            self.assertEqual(self.run_code('var a=1;var b=2;try{[a,b]='+rhs+'}catch e{null};[a,b]'),[1,2])

    def test_no_implicit_await_and_typed_task_failure_is_cold(self):
        source='''var calls=0;async fn work(){calls+=1;42};var a:Int=1;var b:Int=2;
          let task=work();try{[a,b]=[20,task]}catch e{null};let before=[a,b,calls,task_info(task).state];
          var result=null;[result]=[task];[before,calls,await result,calls]'''
        self.assertEqual(self.run_code(source),[[1,2,0,'created'],0,42,1])
        self.assertEqual(self.run_code('var a=0;var b=0;async fn pair(){[20,22]};[a,b]=await pair();a+b'),42)

    def test_yield_rhs_resumes_before_commit(self):
        source='''var a=1;var b=2;fn* work(){[a,b]=yield ([20,22]);yield ([a,b])};
          let g=work();let first=next(g);let before=[a,b];let second=send(g,[40,2]);[first,before,second,a,b]'''
        self.assertEqual(self.run_code(source),[{'done':False,'value':[20,22]},[1,2],{'done':False,'value':[40,2]},40,2])
        source='''var a=1;var b=2;async fn* work(){[a,b]=yield ([20,22]);yield ([a,b])};
          let g=work();await anext(g);let before=[a,b];await asend(g,[40,2]);[before,a,b]'''
        self.assertEqual(self.run_code(source),[[1,2],40,2])

    def test_duplicates_and_nonstructural_targets_fail_before_effects(self):
        for target in ('[x,x]','[x,..x]','{a:x,a:y}','{a:x,b:x}','C(x,x)','[x.y]','[x[0]]','[x:Int]','42','true','_()'):
            calls=[]
            with self.subTest(target=target),self.assertRaises(LanguageError):
                self.run_code('tool("before",null);'+target+'=tool("rhs",null)',tool_handler=lambda *a:calls.append(a))
            self.assertEqual(calls,[])
        for source in ('[x]+=1','[x]=[y]=[1]','([x])=[1]'):
            with self.subTest(source=source),self.assertRaises(LanguageError):Parser(source).program()

    def test_plain_underscore_and_contextual_names_keep_ast_identity(self):
        tree=Parser('var _=1;_=2;var with=0;[with]=[42];with').program()
        self.assertEqual(tree[1][1],('assign','_',('literal',2)))
        self.assertEqual(tree[1][3][0],'assign_pattern')
        self.assertEqual(self.run_code('var _=1;_=2;[_]=[42];_'),2)
        self.assertEqual(self.run_code('fn with(x){x};with(42)'),42)

    def test_parser_ranges_and_large_ordinary_calls_remain_bounded(self):
        node=Parser('var x=0;\n[x]=[42]','state.chaos',ranges=True).program()[1][1]
        self.assertEqual(node[1].span['column'],1)
        self.assertEqual(node[1][1][0].span['column'],2)
        self.assertEqual(node.span['line'],2)
        source='fn f(x){x};'+'f('*90+'42'+')'*90
        started=time.monotonic();Parser(source).program();self.assertLess(time.monotonic()-started,2)

    def test_precommit_cancellation_and_budget_leave_all_cells_unchanged(self):
        pattern=Parser('[a,b]=[20,22]').program()[1][0][1]
        # There are structural traversal and per-target checkpoints before the
        # final precommit tick. Every possible failure point must be atomic.
        baseline=Evaluator();baseline.assign_pattern(pattern,[20,22],self.env())
        total=baseline.steps
        self.assertGreater(total,3)
        for limit in range(total):
            env=self.env();ev=Evaluator(step_limit=limit)
            with self.subTest(limit=limit),self.assertRaises(ResourceLimit):ev.assign_pattern(pattern,[20,22],env)
            self.assertEqual([env.get('a'),env.get('b')],[1,2])
        counter=[0]
        def cancel():
            counter[0]+=1
            return counter[0]==total
        env=self.env()
        with self.assertRaises(ResourceLimit):Evaluator(cancelled=cancel).assign_pattern(pattern,[20,22],env)
        self.assertEqual([env.get('a'),env.get('b')],[1,2])

    @staticmethod
    def env():
        env=Env();env.bind('a',Cell(1,'Int'));env.bind('b',Cell(2,'Int'));return env


if __name__=='__main__':unittest.main()
