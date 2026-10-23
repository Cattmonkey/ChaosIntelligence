import unittest
from chaos.language import Evaluator,LanguageError,ResourceLimit,Native

class DecimalContexts(unittest.TestCase):
    def value(self,source):return Evaluator().run(source)
    def test_nested_restore_and_clear(self):
        self.assertEqual(self.value('with decimal_context(precision=3) as outer {decimal(1)/decimal(7);let inner=with decimal_context(precision=5) as c {decimal(1)/decimal(7);decimal_clear_flags();[c.precision,c.flags]};[inner,get_decimal_context()==outer,outer.flags]};get_decimal_context().precision'),28)
    def test_nested_error_and_return(self):
        self.assertEqual(self.value('fn f(){with decimal_context(precision=3) as c {return c.precision}};[f(),get_decimal_context().precision,try{with decimal_context(precision=6) as c {decimal(1)/decimal(0)}}catch e{get_decimal_context().precision}]'),[3,28,28])
    def test_loop_break_continue(self):
        self.assertEqual(self.value('var out=[];for x in [0,1]{with decimal_context(precision=3) as c {if x==0 {continue}else{break}}};get_decimal_context().precision'),28)
    def test_wrong_context_mutation(self):
        self.assertEqual(self.value('let template=decimal_context(precision=3);with template as c {[try{decimal_clear_flags(template)}catch e{e.code},c.precision,template.flags]}'),['context_error',3,[]])
    def test_cold_task_capture_and_child_flags(self):
        self.assertEqual(self.value('async fn f(){await sleep(1);decimal_text(decimal(1)/decimal(7))};let p=with decimal_context(precision=3) as c {f()};[await p,get_decimal_context().precision,get_decimal_context().flags]'),['0.143',28,[]])
    def test_sibling_interleaving(self):
        self.assertEqual(self.value('async fn f(p){with decimal_context(precision=p) as c {await sleep(1);[decimal_text(decimal(1)/decimal(7)),c.flags]}};await gather([f(3),f(6)])'),[['0.143',['Inexact','Rounded']],['0.142857',['Inexact','Rounded']]])
    def test_scope_taskgroup_shares_context(self):
        self.assertEqual(self.value('with decimal_context(precision=3) as c {async with task_group() as g {decimal(1)/decimal(7)};[c.precision,c.flags]}'),[3,['Inexact','Rounded']])
    def test_generator_retains_across_consumers_and_child(self):
        self.assertEqual(self.value('async fn child(){await sleep(1);decimal_text(decimal(1)/decimal(7))};async fn* gen(){with decimal_context(precision=3) as c {await sleep(1);yield await child();await sleep(1);yield decimal_text(decimal(1)/decimal(7))}};let it=gen();let a=with decimal_context(precision=7) as c {await anext(it)};let b=with decimal_context(precision=9) as c {await anext(it)};await aclose(it);[a.value,b.value,get_decimal_context().precision]'),['0.143','0.143',28])
    def test_generator_creation_configuration(self):
        self.assertEqual(self.value('fn* gen(){yield decimal_text(decimal(1)/decimal(7))};let it=with decimal_context(precision=4) as c {gen()};[next(it).value,get_decimal_context().precision]'),['0.1429',28])
    def test_exit_stack_native_lease(self):
        self.assertEqual(self.value('with exit_stack() as s {let c=s.enter_context(decimal_context(precision=3));let inner=s.enter_context(decimal_context(precision=6));[get_decimal_context().precision,c.precision,inner.precision]};get_decimal_context().precision'),28)
    def test_reentrant_drive_shares_flags(self):
        e=Evaluator()
        def nested():return e.run('decimal(1)/decimal(7)')
        # Native invoke recursively drives AST without starting a second epoch.
        def inner():return e.invoke(e.env.get('decimal'),[1])
        self.assertEqual(e.run('with decimal_context(precision=3) as c {bridge();[c.precision,c.flags]}',{'bridge':Native('bridge',lambda:e.evaluate(__import__('chaos.language',fromlist=['Parser']).Parser('decimal(1)/decimal(7)').program(),e.env),e)}),[3,['Inexact','Rounded']])
    def test_owner_epoch_reject(self):
        e=Evaluator();context=e.run('get_decimal_context()')
        with self.assertRaises(LanguageError):e.run('decimal_context_info(input)',{'input':context})
        with self.assertRaises(LanguageError):Evaluator().run('decimal_context_info(input)',{'input':context})
    def test_cancelled_task_restores_caller(self):
        self.assertEqual(self.value('async fn f(){with decimal_context(precision=3) as c {await sleep(100);decimal(1)}};let t=spawn(f());await sleep(1);cancel(t);await sleep(2);get_decimal_context().precision'),28)
    def test_hard_resource_native_release(self):
        e=Evaluator(step_limit=150)
        with self.assertRaises(ResourceLimit):e.run('with decimal_context(precision=3) as c {while true {null}}')
        self.assertEqual(e.suspension.decimal_state.current.precision,28)

    def test_exit_stack_foreign_task_preflight(self):
        self.assertEqual(self.value('let s=exit_stack();s.enter_context(decimal_context(precision=5));async fn bad(){try{s.close();false}catch e{true}};let rejected=await bad();let current=get_decimal_context().precision;s.close();[rejected,current,get_decimal_context().precision]'),[True,5,28])
    def test_async_exit_stack_helper_shares_logical_context(self):
        self.assertEqual(self.value('let s=exit_stack(true);s.enter_context(decimal_context(precision=5));await s.aclose();get_decimal_context().precision'),28)
    def test_exit_stack_non_lifo_rejected_without_pop(self):
        self.assertEqual(self.value('let s=exit_stack();s.enter_context(decimal_context(precision=5));let rejected=with decimal_context(precision=3) as c {try{s.close();false}catch e{true}};let current=get_decimal_context().precision;s.close();[rejected,current,get_decimal_context().precision]'),[True,5,28])

    def test_stale_context_nested_str_checks_owner(self):
        context=Evaluator().run('get_decimal_context()')
        for source in ['str(input)','str({context:input})','str([input])','str((input,))']:
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator().run(source,{'input':context})

    def test_context_nominal_member_in_limited_function(self):
        e=Evaluator()
        limited=Native('limited',lambda f:e.invoke(f,[]),e,capabilities=frozenset())
        self.assertEqual(e.run('limited(fn(){class C{let ctx=get_decimal_context()};C.ctx.precision})',{'limited':limited}),28)
