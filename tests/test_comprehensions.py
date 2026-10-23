"""Native comprehension syntax, evaluation boundaries and cooperative traversal."""
import unittest

from chaos.language import Evaluator, LanguageError, Parser, ResourceLimit, comprehension_scopes


class Comprehensions(unittest.TestCase):
    def run_code(self, source, **options):
        return Evaluator(**options).run(source)

    def test_list_nested_for_and_guards(self):
        self.assertEqual(self.run_code('[x*10+y for x in [1,2,3] if x>1 for y in [4,5] if y==5]'), [25,35])
        self.assertEqual(self.run_code('[x for x in []]'), [])
        self.assertEqual(self.run_code('[x for x in "a😀b" if x!="b"]'), ['a','😀'])
        self.assertEqual(self.run_code('[x for x in [1,2] for x in [x+10]]'), [11,12])

    def test_record_dynamic_keys_and_last_value_wins(self):
        self.assertEqual(self.run_code('{str(x%2):x for x in [1,2,3]}'), {'1':3,'0':2})
        self.assertEqual(self.run_code('{x:x+x for x in "ab"}'), {'a':'aa','b':'bb'})
        self.assertEqual(self.run_code('{"key":x for x in [1,2]}'), {'key':2})
        self.assertEqual(self.run_code('{str(x):x for x in []}'), {})

    def test_record_key_checked_before_value(self):
        seen=[]
        with self.assertRaises(LanguageError):
            self.run_code('{tool("key",1):tool("value",2) for x in [0]}',tool_handler=lambda name,value:(seen.append(name),value)[1])
        self.assertEqual(seen,['key'])
        for key in ('true','false','null'):
            seen=[]
            with self.subTest(key=key), self.assertRaises(LanguageError) as caught:
                self.run_code('{'+key+':tool("value",2) for x in [0]}',tool_handler=lambda *args:seen.append(args))
            self.assertNotEqual(caught.exception.code,'syntax_error')
            self.assertEqual(seen,[])
        self.assertEqual(self.run_code('{true:1,false:2,null:3}'),{'true':1,'false':2,'null':3})
        self.assertEqual(self.run_code('var seen=[];fn key(x){seen=append(seen,"key");str(x)}fn value(x){seen=append(seen,"value");x}let r={key(x):value(x) for x in [1,2]};[r,seen]'),[{'1':1,'2':2},['key','value','key','value']])

    def test_per_item_closures_and_no_binding_leak(self):
        self.assertEqual(self.run_code('let x=99;let fs=[fn(){x} for x in [1,2,3]];[map(fn(f){f()},fs),x]'),[[1,2,3],99])
        self.assertEqual(self.run_code('let fs=collect((fn(){x} for x in [1,2,3]));map(fn(f){f()},fs)'),[1,2,3])
        self.assertEqual(self.run_code('let fs=[fn(){[x,y]} for x in [1,2] for y in [3,4]];map(fn(f){f()},fs)'),[[1,3],[1,4],[2,3],[2,4]])
        with self.assertRaises(LanguageError): self.run_code('let xs=[x for x in [1]];x')

    def test_sources_use_shared_protocol(self):
        prefix='class C{var n=0;fn __iter__(self){self}fn __next__(self){self.n=self.n+1;{done:self.n>3,value:self.n}}}'
        for source in ['[1,2,3]','iter([1,2,3])','(fn*(){yield 1;yield 2;yield 3})()','C()']:
            with self.subTest(source=source):
                self.assertEqual(self.run_code(prefix+'[x*2 for x in '+source+']'),[2,4,6])
                self.assertEqual(self.run_code(prefix+'collect((x*2 for x in '+source+'))'),[2,4,6])

    def test_lazy_creation_first_source_only_and_no_prefetch(self):
        source='''var seen=[];fn mark(k,v){seen=append(seen,k);v}
class C{var n=0;fn __iter__(self){mark("iter",null);self}
fn __next__(self){self.n=self.n+1;mark("next",null);{done:self.n>2,value:self.n}}}
let g=(mark("head",x+y) for x in mark("outer",C()) if mark("guard",true) for y in mark("inner",[10,20]));
let before=seen;let first=next(g);let after=seen;close(g);[before,first,after,seen]'''
        expected=['outer','iter','next','guard','inner','head']
        self.assertEqual(self.run_code(source),[['outer'],{'done':False,'value':11},expected,expected])

    def test_lazy_snapshot_retains_cells_and_outer_value_once(self):
        source='var xs=[1];var offset=10;let g=(x+offset for x in xs);xs=[9];offset=20;collect(g)'
        self.assertEqual(self.run_code(source),[21])
        with self.assertRaises(LanguageError): self.run_code('let g=(later for x in [1]);let later=2;collect(g)')

    def test_close_created_generator_never_acquires(self):
        source='var n=0;class C{fn __iter__(self){n=n+1;self}fn __next__(self){{done:true,value:null}}}let g=(x for x in C());close(g);n'
        self.assertEqual(self.run_code(source),0)

    def test_borrow_is_only_held_during_step(self):
        source='let it=iter([1,2,3,4]);let g=(x for x in it);let a=next(g);let b=next(it);close(g);[a,b,next(it)]'
        self.assertEqual(self.run_code(source),[{'done':False,'value':1},{'done':False,'value':2},{'done':False,'value':3}])
        self.assertEqual(self.run_code('let it=iter([1,2,3,4]);[next(it).value for x in it]'),[2,4])

    def test_send_ignores_value_and_throw_does_not_delegate(self):
        self.assertEqual(self.run_code('let g=(x for x in [1,2]);[next(g),send(g,99),next(g)]'),[{'done':False,'value':1},{'done':False,'value':2},{'done':True,'value':None}])
        source='let it=iter([1,2,3]);let g=(x for x in it);next(g);let failure=try{throw_into(g,"stop")}catch e{e.value};[failure,next(it)]'
        self.assertEqual(self.run_code(source),['stop',{'done':False,'value':2}])

    def test_projection_failure_consumes_one_and_keeps_source_open(self):
        source='let it=iter([1,2,3]);let result=try{[if x==2{throw "bad"}else{x} for x in it]}catch e{e.value};[result,next(it)]'
        self.assertEqual(self.run_code(source),['bad',{'done':False,'value':3}])

    def test_guards_are_bool_and_tasks_stay_cold(self):
        self.assertEqual(self.run_code('var started=0;async fn work(x){started=started+1;x*2}let tasks=[work(x) for x in [1,2]];let before=started;[before,await gather(tasks),started]'),[0,[2,4],2])
        for guard in ['1','null','work(x)']:
            with self.subTest(guard=guard), self.assertRaises(LanguageError):
                self.run_code('async fn work(x){true}[x for x in [1] if '+guard+']')

    def test_async_generator_sync_and_async_sources(self):
        self.assertEqual(self.run_code('async fn double(x){x*2}await acollect(async (await double(x) for x in [1,2]))'),[2,4])
        self.assertEqual(self.run_code('async fn* values(){yield 1;yield 2}await acollect(async (x*2 async for x in values()))'),[2,4])
        self.assertEqual(self.run_code('async fn* values(){yield 1;yield 2}[x+y async for x in values() for y in [3,4]]'),[4,5,5,6])
        self.assertEqual(self.run_code('async fn* values(){yield "a";yield "b"}{x:await (async fn(){x+x})() async for x in values()}'),{'a':'aa','b':'bb'})

    def test_async_body_allowed_in_sync_function_but_outer_source_not(self):
        source='async fn work(x){x*2}fn make(){async (await work(x) for x in [1,2] if await (async fn(){true})())}await acollect(make())'
        self.assertEqual(self.run_code(source),[2,4])
        source='async fn values(){[1]}fn make(){async (x for x in await values())}null'
        with self.assertRaises(LanguageError): self.run_code(source)
        self.assertEqual(self.run_code('async fn values(){[1,2]}collect((x for x in await values()))'),[1,2])

    def test_async_outer_source_is_evaluated_at_creation(self):
        source='var seen=[];async fn values(){seen=append(seen,"source");[1]}let g=async (do{seen=append(seen,"head");x} for x in await values());let before=seen;let item=await anext(g);[before,item,seen]'
        self.assertEqual(self.run_code(source),[['source'],{'done':False,'value':1},['source','head']])

    def test_sync_body_and_sync_function_eager_forbid_await(self):
        for expression in ['(await f(x) for x in [1])','(x for x in [1] if await f(x))','(x for x in [1] for y in await values())','(x async for x in values())','fn bad(){[await f(x) for x in [1]]}null','fn bad(){[x async for x in values()]}null']:
            with self.subTest(expression=expression), self.assertRaises(LanguageError):
                self.run_code('async fn f(x){x}async fn values(){[1]}'+expression)

    def test_no_implicit_await_of_source_or_sync_async_coercion(self):
        for source in ['{x:1}','42','values()','stream()']:
            with self.subTest(source=source), self.assertRaises(LanguageError):
                self.run_code('async fn values(){[1]}async fn* stream(){yield 1}[x for x in '+source+']')
        with self.assertRaises(LanguageError): self.run_code('await acollect(async (x async for x in [1]))')

    def test_control_cannot_escape_comprehension_boundary(self):
        for expression in ['fn f(){[return x for x in [1]]}f()','fn* f(){[yield x for x in [1]]}collect(f())','for x in [1]{[break for y in [1]]}','for x in [1]{[continue for y in [1]]}','fn f(){[x for x in do{return [1]}]}f()','fn f(){(return x for x in [1])}f()']:
            seen=[]
            with self.subTest(expression=expression), self.assertRaises(LanguageError):
                self.run_code('tool("before",null);'+expression,tool_handler=lambda *args:seen.append(args))
            self.assertEqual(seen,[])
        self.assertEqual(self.run_code('[do{for y in [1]{break};(fn(){return x})()} for x in [1,2]]'),[1,2])
        self.assertEqual(self.run_code('[collect((fn*(){yield x})()) for x in [1,2]]'),[[1],[2]])

    def test_syntax_preserves_ordinary_literals_and_async_identifier(self):
        self.assertEqual(self.run_code('[[],[1,2,],{if:1,"else":2,},(1+2)]'),[[],[1,2],{'if':1,'else':2},3])
        self.assertEqual(self.run_code('fn async(x){x}async(3)'),3)
        self.assertEqual(self.run_code('fn async(x){x}async(null == for x in []{null})'),True)
        for source in ['[x if true]','{x:x if true}','[x for x in [1],]','{(1+2):4}','async (x)','(x for x in [1],)']:
            with self.subTest(source=source), self.assertRaises(LanguageError): self.run_code(source)

    def test_clause_nodes_spans_and_shared_scope_contract(self):
        node=Parser('async (await f(x) for x in xs if true async for y in ys)',ranges=True).program()[1][0]
        self.assertEqual(node[0],'async_gen_comp')
        self.assertEqual([c[0] for c in node[-1]],['comp_for','comp_if','comp_async_for'])
        self.assertEqual([c.span['column'] for c in node[-1]],[19,31,39])
        allowed,expressions=comprehension_scopes(node,False)
        self.assertTrue(allowed)
        self.assertEqual([v for _,v in expressions],[True,False,True,True])

    def test_clause_count_limit(self):
        valid='[x for x in [1]'+' if true'*63+']'
        self.assertEqual(self.run_code(valid),[1])
        with self.assertRaises(LanguageError): self.run_code(valid[:-1]+' if true]')

    def test_nested_async_heads_have_bounded_parse_work(self):
        from chaos.ide_language import RecoveryParser
        for parser_type in (Parser, RecoveryParser):
            for depth in (15,20):
                with self.subTest(parser=parser_type.__name__,depth=depth):
                    source='1'
                    for _ in range(depth): source='async ('+source+' for x in [])'
                    steps=[0]
                    def checkpoint():
                        steps[0]+=1
                        if steps[0]>15000: raise ResourceLimit('nested async parser work')
                    tree=parser_type(source,ranges=True,legacy_items=True,checkpoint=checkpoint).program()
                    self.assertEqual(tree[1][0][0],'async_gen_comp')
                    self.assertLess(steps[0],15000)

    def test_async_probe_does_not_cache_resource_failures(self):
        parser=Parser('async (x for x in [])')
        def interrupted(): raise ResourceLimit('interrupted probe')
        parser.checkpoint=interrupted
        with self.assertRaises(ResourceLimit): parser.async_comprehension_start()
        self.assertEqual(parser.i,0)
        self.assertEqual(parser._async_comprehension_tokens[1],{})
        parser.checkpoint=None
        self.assertTrue(parser.async_comprehension_start())

    def test_input_limit_and_eof_probe_even_when_every_guard_fails(self):
        source='class C{var n=0;fn __iter__(self){self}fn __next__(self){self.n=self.n+1;tool("read",self.n);{done:self.n>LIMIT,value:self.n}}}[x for x in C() if false]'
        for limit,expected_error in [(10000,False),(10001,True)]:
            seen=[]
            with self.subTest(limit=limit):
                evaluator=Evaluator(step_limit=3000000,timeout=30,tool_handler=lambda name,value:seen.append(value))
                if expected_error:
                    with self.assertRaises(ResourceLimit): evaluator.run(source.replace('LIMIT',str(limit)))
                else: self.assertEqual(evaluator.run(source.replace('LIMIT',str(limit))),[])
                self.assertEqual(len(seen),10001)

    def test_materialized_limit_counts_duplicate_keys(self):
        for expression in ['[x for x in range(101) for y in range(100)]','{"same":x for x in range(101) for y in range(100)}']:
            with self.subTest(expression=expression), self.assertRaises(ResourceLimit):
                self.run_code(expression,step_limit=2000000,timeout=30)

    def test_native_temporary_cursors_do_not_accumulate(self):
        evaluator=Evaluator(step_limit=3000000,timeout=30)
        self.assertEqual(evaluator.run('length([[y for y in ""] for x in range(10000)])'),10000)
        self.assertEqual(len(evaluator.suspension.iteration.handles),0)

    def test_short_circuit_shared_consumer_does_not_prefetch(self):
        source='var n=0;let it=iter([1,2,3]);let g=(do{n=n+1;x} for x in it);let result=any(fn(x){x==1},g);[result,n,next(it),next(g)]'
        self.assertEqual(self.run_code(source),[True,1,{'done':False,'value':2},{'done':False,'value':3}])


if __name__ == '__main__': unittest.main()
