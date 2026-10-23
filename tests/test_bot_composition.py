import unittest
from chaos import Framework,Settings,Registry,ToolSpec


class BotCompositionTests(unittest.TestCase):
    def run_source(self,source,registry=None,capabilities=None):
        app=Framework(registry=registry,settings=Settings(':memory:'));self.addCleanup(app.close)
        app.create_project('bot-compose',source=source,capabilities=capabilities or [])
        return app.start('bot-compose',1,None,mode='source').result(10)

    def test_generated_and_compiled_real_functions(self):
        value=self.run_source('let make=function_from(code_from("fn(s){s.input}"));let execute=compile_function(fn(p,s){p+1},"interpreter");let b=bot("generated",make,execute,fn(s){{done:true,context:null,output:s.evidence}});(await b(2)).output')
        self.assertEqual(value,[3])

    def test_four_child_tasks_are_drained_with_original_scheduler(self):
        value=self.run_source('async fn child(x){await sleep(0);x};async fn execute(p,s){await gather([child(1),child(2),child(3),child(4)])};let b=bot("children",fn(s){null},execute,fn(s){{done:true,context:null,output:s.evidence}});(await b()).output')
        self.assertEqual(value,[[1,2,3,4]])

    def test_full_secondary_bot_value_ownership_precedes_primary_discard(self):
        from chaos.language import Evaluator,LanguageError
        e=Evaluator();b=e.run('bot("old",fn(s){null},fn(p,s){null},fn(s){{done:true,context:null,output:null}})')
        e.env.bind('old',b)
        with self.assertRaises(LanguageError) as caught:e.run('fn values(){return_values(42,old)};let primary=values();primary')
        self.assertEqual(caught.exception.code,'ownership_error')

    def test_explicit_stream_consumption_keeps_guard_and_terminal_values(self):
        value=self.run_source('async fn* stream(){yield 1;yield 2;return_values(3,4)};async fn execute(p,s){let g=stream();var xs=[];async for x in g{xs=append(xs,x)};[xs,multiple_value_list(g)]};let b=bot("stream",fn(s){null},execute,fn(s){{done:true,context:null,output:s.evidence}});(await b()).output')
        self.assertEqual(value,[[[1,2],[3,4]]])
