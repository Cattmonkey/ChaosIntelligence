"""Set construction is incremental on the existing borrowed-cursor scheduler."""
import unittest
from chaos.language import Evaluator, LanguageError, ResourceLimit
from chaos.suspension import TERMINAL


class SetResourceTests(unittest.TestCase):
    def test_invalid_scalar_prevents_later_effects(self):
        calls=[]
        result=Evaluator(tool_handler=lambda *a:calls.append(a)).run('try{{[],tool("later",1)}}catch e{e.code}')
        self.assertEqual(result,'argument_error');self.assertEqual(calls,[])

    def test_invalid_generator_item_stops_before_next_pull_and_keeps_borrow(self):
        for expression in ('set_of(g)','{*g}','{x for x in g}'):
            source='var calls=[];fn* rows(){calls=append(calls,1);yield ([]);calls=append(calls,2);yield 42};let g=rows();let error=try{'+expression+'}catch e{e.code};let before=calls;let next=next(g);[error,before,next,calls]'
            with self.subTest(expression=expression):
                self.assertEqual(Evaluator().run(source),['argument_error',[1],{'done':False,'value':42},[1,2]])

    def test_borrowed_source_is_not_closed_on_success_or_key_failure(self):
        source='var closed=0;fn* rows(){try{yield 1;yield ([])}finally{closed+=1}};let g=rows();try{set_of(g)}catch e{null};let before=closed;close(g);[before,closed]'
        self.assertEqual(Evaluator().run(source),[0,1])

    def test_constructor_never_implicitly_awaits_source_or_items(self):
        for source in ('async fn* rows(){yield 1};set_of(rows())','async fn value(){1};{value()}'):
            evaluator=Evaluator()
            with self.subTest(source=source),self.assertRaises(LanguageError): evaluator.run(source)
            self.assertTrue(all(task.state in TERMINAL for task in evaluator.suspension.tasks))

    def test_async_head_invalid_key_stops_before_next_pull(self):
        source='var calls=0;async fn* rows(){calls+=1;yield 1;calls+=1;yield 2};let g=rows();let e=try{{[] async for x in g}}catch e{e.code};let before=calls;let item=await anext(g);[e,before,item,calls]'
        self.assertEqual(Evaluator().run(source),['argument_error',1,{'done':False,'value':2},2])

    def test_duplicate_attempt_budget_is_shared_across_spreads(self):
        for source in ('{*range(5001),*range(5000)}','set_of((0 for x in range(10001)))','{0 for x in range(101) for y in range(100)}'):
            with self.subTest(source=source),self.assertRaises(ResourceLimit): Evaluator(step_limit=2000000,timeout=30).run(source)

    def test_full_literal_budget_prevents_next_scalar_effect_but_allows_empty_spread(self):
        calls=[];evaluator=Evaluator(step_limit=300000,tool_handler=lambda *a:calls.append(a))
        evaluator.env.bind('source',[0]*10000)
        with self.assertRaises(ResourceLimit):evaluator.run('{*source,tool("overflow",42)}')
        self.assertEqual(calls,[])
        evaluator=Evaluator(step_limit=300000);evaluator.env.bind('source',[0]*10000)
        self.assertEqual(evaluator.run('length({*source,*[]})'),1)

    def test_exact_limit_constructor_probe_and_duplicate_cardinality(self):
        source='var reads=0;fn* rows(){for x in range(10000){reads+=1;yield 0};reads+=1};let s=set_of(rows());[length(s),reads]'
        self.assertEqual(Evaluator(step_limit=2000000,timeout=30).run(source),[1,10001])

    def test_set_comprehension_async_permission_before_effects(self):
        for source in ('fn bad(){ {x async for x in []} }','fn bad(){ {await x for x in []} }'):
            calls=[]
            with self.subTest(source=source),self.assertRaises(LanguageError): Evaluator(tool_handler=lambda *a:calls.append(a)).run('tool("first",0);'+source)
            self.assertEqual(calls,[])

    def test_hard_budget_never_publishes_partial_set(self):
        from chaos.session import Session
        session=Session(step_limit=1000)
        session.evaluate('var saved:Set={42}')
        with self.assertRaises(ResourceLimit):session.evaluate('saved=set_of(range(10000))')
        self.assertEqual(list(session.env.get('saved')),[42])
        self.assertTrue(all(not handle.busy for handle in session.evaluator.suspension.iteration.handles))

    def test_channel_and_class_fields_preserve_native_set(self):
        source='class C{let values={20,22}};let C::{values:s}=C();let ch=channel(1);await channel_send(ch,s);let received=(await channel_receive(ch)).value;[type_of(received),collect(received)]'
        self.assertEqual(Evaluator().run(source),['Set',[20,22]])


if __name__=='__main__':unittest.main()
