"""Normal-completion clauses are native scoped expressions, not flag lowering."""
import unittest
from unittest.mock import patch

from chaos.language import (Evaluator, Env, LanguageError, Parser, BreakSignal,
                            ContinueSignal, serializable)
from chaos.suspension import CHECKPOINT


class CompletionControlTests(unittest.TestCase):
    def run_code(self, source, **kwargs):return Evaluator(**kwargs).run(source)

    def test_additive_nodes_keep_historical_arity_and_nested_ranges(self):
        examples=[('while false{null}', 'while',3),('for x in []{null}','for',4),
                  ('for (x,) in []{null}','for_pattern',4),('async for x in input{null}','async_for',4),
                  ('async for [x] in input{null}','async_for_pattern',4),('try{1}catch e{2}','try',4)]
        for source,kind,arity in examples:
            with self.subTest(kind=kind):
                old=Parser(source).program()[1][0];new=Parser(source+'else{42}').program()[1][0]
                self.assertEqual((old[0],len(old)),(kind,arity));self.assertEqual(new[0],'try_else'if kind=='try'else'loop_else')
                self.assertEqual(new[1],old);self.assertEqual(new[1].span,old.span)
                self.assertLess(new[1].span['endColumn'],new.span['endColumn'])
        node=Parser('try{1}catch e{2}else{42}finally{null}').program()[1][0]
        self.assertEqual((node[0],node[1][0],node[1][1][0]),('finally','try_else','try'))

    def test_all_five_loop_kinds_exhaust_empty_and_nonempty(self):
        self.assertEqual(self.run_code('var i=0;let a=while i<3{i+=1}else{i+39};[a,i,while false{}else{42}]'),[42,3,42])
        self.assertEqual(self.run_code('var sum=0;let result=for x in (20,22){sum+=x}else{sum};[result,for x in []{}else{42}]'),[42,42])
        self.assertEqual(self.run_code('var sum=0;let result=for (a,b) in ((10,11),(10,11)){sum+=a+b}else{sum};[result,for [x] in []{}else{42}]'),[42,42])
        prefix='async fn* rows(){yield 20;yield 22};async fn* empty(){};'
        self.assertEqual(self.run_code(prefix+'var sum=0;let result=async for x in rows(){sum+=x}else{sum};[result,async for x in empty(){}else{42}]'),[42,42])
        prefix='async fn* rows(){yield (20,22)};async fn* empty(){};'
        self.assertEqual(self.run_code(prefix+'var sum=0;let result=async for (a,b) in rows(){sum+=a+b}else{sum};[result,async for [x] in empty(){}else{42}]'),[42,42])

    def test_break_and_continue_use_final_body_control(self):
        source='''var n=0;let result=while n<3{n+=1;try{break}finally{continue}}else{42};
          [result,n,for x in [1,2]{try{continue}finally{break}}else{99},
           for x in [1,2]{continue}else{42},for x in [1,2]{break}else{99}]'''
        self.assertEqual(self.run_code(source),[42,3,None,42,None])
        self.assertEqual(self.run_code('var n=0;let result=for x in [1,2]{for y in [1,2]{n+=1;break}}else{42};[result,n]'),[42,2])

    def test_else_control_targets_only_enclosing_loop(self):
        self.assertEqual(self.run_code('var n=0;let result=for outer in [1,2]{for inner in []{}else{n+=1;continue};n+=100}else{42};[result,n]'),[42,2])
        self.assertEqual(self.run_code('var n=0;let result=for outer in [1,2]{while false{}else{n+=1;break};n+=100}else{42};[result,n]'),[None,1])
        self.assertEqual(self.run_code('fn f(){for x in []{}else{return 42};0};f()'),42)
        self.assertEqual(self.run_code('fn f(){for x in [1]{return 42}else{0};1};f()'),42)

    def test_source_and_condition_transfers_are_not_inner_body_breaks(self):
        for signal,expected in [('break',[]),('continue',['outer'])]:
            for inner in ['while do{SIGNAL;false}{}else{seen=append(seen,"inner")}',
                          'for x in do{SIGNAL;[]}{}else{seen=append(seen,"inner")}']:
                source='var seen=[];for outer in [1,2]{'+inner.replace('SIGNAL',signal)+';seen=append(seen,"body")}else{seen=append(seen,"outer")};seen'
                with self.subTest(signal=signal,inner=inner):self.assertEqual(self.run_code(source),expected)

    def test_acquire_step_and_binding_do_not_expand_body_catch_boundary(self):
        for phase in ('acquire','step','binding'):
            for signal,expected in [(BreakSignal,[]),(ContinueSignal,['outer'])]:
                ev=Evaluator();iteration=ev.suspension.iteration
                if phase=='acquire':
                    original=iteration.acquire
                    def replacement(value,*args,**kwargs):
                        if value==[99]:raise signal()
                        return (yield from original(value,*args,**kwargs))
                    target,method=iteration,'acquire';inner='for inner in [99]'
                elif phase=='step':
                    original=iteration.step
                    def replacement(handle,*args,**kwargs):
                        if handle.values==[99]:raise signal()
                        return (yield from original(handle,*args,**kwargs))
                    target,method=iteration,'step';inner='for inner in [99]'
                else:
                    original=ev.bind_pattern
                    def replacement(pattern,value,*args,**kwargs):
                        if value==[99]:raise signal()
                        return original(pattern,value,*args,**kwargs)
                    target,method=ev,'bind_pattern';inner='for [inner] in [[99]]'
                source='var seen=[];for outer in [1,2]{'+inner+'{}else{seen=append(seen,"inner")};seen=append(seen,"body")}else{seen=append(seen,"outer")};seen'
                with self.subTest(phase=phase,signal=signal.__name__),patch.object(target,method,replacement):self.assertEqual(ev.run(source),expected)

    def test_loop_else_is_outer_sibling_scope(self):
        source='let x=42;let a=for x in []{}else{x};let b=for (x,) in ((1,),){let private=7}else{x};[a,b,x]'
        self.assertEqual(self.run_code(source),[42,42,42])
        self.assertEqual(self.run_code('var n=0;let result=while false{let hidden=1}else{let hidden=42;n=hidden;hidden};[result,n]'),[42,42])
        for source in ['for x in []{}else{x}','for [x] in []{}else{x}','while false{let hidden=1}else{hidden}']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_loop_result_can_be_any_language_value_without_private_marker(self):
        source='let result=for x in []{}else{(fn(v){v+1},41)};let (action,value)=result;[action(value),for x in []{}else{},while false{}else{null}]'
        self.assertEqual(self.run_code(source),[42,None,None])
        self.assertEqual(serializable(self.run_code('for x in []{}else{(20,22)}')),{'$tuple':[20,22]})

    def test_try_success_handler_and_else_errors_are_distinct(self):
        source='''var events=[];
          let success=try{events=append(events,"body");1}catch e{events=append(events,"handler");2}else{events=append(events,"else");42};
          let handled=try{throw 7}catch e{e.value}else{events=append(events,"wrong");99};
          let failure=try{try{1}catch e{events=append(events,"wrong catch");0}else{throw 8}}catch outer{outer.value};
          [success,handled,failure,events]'''
        self.assertEqual(self.run_code(source),[42,7,8,['body','else']])
        self.assertEqual(self.run_code('try{try{throw 1}catch e{throw 2}else{42}}catch outer{outer.value}'),2)

    def test_try_else_sibling_scope_and_success_body_value_is_discarded(self):
        self.assertEqual(self.run_code('let error=42;try{let private=1;"unused"}catch error{0}else{error}'),42)
        self.assertEqual(self.run_code('try{42}catch error{0}else{}'),None)
        for source in ['try{let hidden=1}catch error{0}else{hidden}','try{1}catch caughtIssue{0}else{caughtIssue}']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_try_protected_tail_calls_resolve_inside_catch(self):
        source='fn bad(){throw 7};[try{bad()}catch e{e.value}else{42},try{true&&fn(){1}()}catch e{false}else{42}]'
        self.assertEqual(self.run_code(source),[7,False])
        self.assertEqual(self.run_code('fn bad(){throw 7};fn f(){try{return bad()}catch e{return e.value}else{42}};f()'),7)
        self.assertEqual(self.run_code('fn f(){try{return 7}catch e{0}else{42}};f()'),7)

    def test_else_tail_calls_and_handler_tail_calls_remain_stack_safe(self):
        loops='fn recur(n){if n==0{42}else{while false{}else{recur(n-1)}}};recur(1200)'
        tries='fn recur(n){if n==0{42}else{try{1}catch e{0}else{recur(n-1)}}};recur(1200)'
        handlers='fn recur(n){if n==0{42}else{try{throw 1}catch e{recur(n-1)}else{0}}};recur(1200)'
        for source in (loops,tries,handlers):
            with self.subTest(source=source[:45]):self.assertEqual(self.run_code(source,step_limit=150000),42)

    def test_cold_task_is_success_without_implicit_await(self):
        source='''var calls=0;var pending=null;async fn work(){calls+=1;throw 7};
          let result=try{pending=work();pending}catch e{"handled"}else{task_info(pending).state};
          let before=calls;let error=try{await pending}catch e{e.value}else{99};[result,before,calls,error]'''
        self.assertEqual(self.run_code(source),['created',0,1,7])
        self.assertEqual(self.run_code('async fn work(){42};fn build(){try{1}catch e{null}else{work()}};let task=build();[task_info(task).state,await task]'),['created',42])

    def test_finally_covers_body_handler_and_else_and_preserves_result(self):
        source='''var events=[];
          let a=try{events=append(events,"body");1}catch e{0}else{events=append(events,"else");42}finally{events=append(events,"cleanup");99};
          let b=try{throw 7}catch e{events=append(events,"catch");e.value}else{0}finally{events=append(events,"handled cleanup")};
          [a,b,events]'''
        self.assertEqual(self.run_code(source),[42,7,['body','else','cleanup','catch','handled cleanup']])
        self.assertEqual(self.run_code('var events=[];let result=try{try{1}catch e{0}else{throw 7}finally{events=append(events,"cleanup")}}catch outer{outer.value};[result,events]'),[7,['cleanup']])
        self.assertEqual(self.run_code('fn f(){try{1}catch e{0}else{return 7}finally{return 42}};f()'),42)

    def test_else_return_tail_is_resolved_before_outer_cleanup(self):
        self.assertEqual(self.run_code('var x=1;fn read(){x};fn f(){try{1}catch e{0}else{return read()}finally{x=2}};[f(),x]'),[1,2])
        self.assertEqual(self.run_code('var x=1;fn read(){x};fn f(){try{for i in []{}else{return read()}}finally{x=2}};[f(),x]'),[1,2])

    def test_try_control_transfer_skips_else_and_respects_outer_loops(self):
        source='var seen=[];for x in [1,2]{try{continue}catch e{0}else{seen=append(seen,"bad")}finally{seen=append(seen,x)}}else{seen=append(seen,42)};seen'
        self.assertEqual(self.run_code(source),[1,2,42])
        self.assertEqual(self.run_code('for x in [1]{try{break}catch e{0}else{42}}else{99}'),None)
        self.assertEqual(self.run_code('for x in [1]{try{1}catch e{0}else{break}}else{99}'),None)

    def test_invalid_forms_reject_before_any_module_effect(self):
        invalid=['try{1}else{42}','try{1}else{42}finally{null}','try{1}finally{null}else{42}',
                 'try{1}catch e{0}else 42','while false{}else 42','for x in []{}else if true{1}else{2}',
                 'for x in []{}else{break}','while false{}else{continue}','async for x in input{}else{break}',
                 'for x in [1]{fn f(){while false{}else{break}}}',
                 'while false{}else{}else{}','try{1}catch e{}else{}else{}']
        for source in invalid:
            seen=[]
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code('tool("unexpected",null);'+source,tool_handler=lambda *args:seen.append(args))
            self.assertEqual(seen,[])

    def test_instrumentation_retains_old_child_kind_range_and_budget_checkpoint(self):
        source='while false {\n null\n} else {\n 42\n}'
        tree=Parser(source).program()[1][0];seen=[];trace=[]
        self.assertEqual(self.run_code(source,debug_hook=lambda node,env,frames:seen.append((node[0],dict(getattr(node,'span',{})))),trace_handler=trace.append),42)
        self.assertIn(('while',tree[1].span),seen);self.assertIn(('loop_else',tree.span),seen)
        self.assertTrue(any(row.get('kind')=='while'and row['location']==tree[1].span for row in trace))
        for source,count in [('while false{}',1),('while false{}else{42}',2),('try{1}catch e{2}else{42}',2)]:
            ev=Evaluator();ev.suspension.begin();events=[];iterator=ev._s_evaluate(Parser(source).program()[1][0],Env(ev.env))
            try:
                while True:events.append(next(iterator))
            except StopIteration:pass
            finally:ev.suspension.finish()
            self.assertEqual(sum(item is CHECKPOINT for item in events),count)

    def test_same_ast_concurrent_calls_have_independent_completion_states(self):
        source='''async fn run(fail){try{await sleep(0);if fail{throw 7}else{1}}catch e{e.value}else{42}};
          async fn search(stop){for item in [1,2]{await sleep(0);if stop{break}else{continue}}else{42}};
          await gather([run(true),run(false),search(true),search(false),run(true),run(false)])'''
        self.assertEqual(self.run_code(source),[7,42,None,42,7,42])
        self.assertEqual(self.run_code('fn f(n){try{if n==0{throw 7}else{f(n-1)}}catch e{e.value}else{42}};[f(0),f(1),f(2)]'),[7,42,42])
