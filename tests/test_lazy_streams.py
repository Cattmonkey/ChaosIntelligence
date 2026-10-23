"""Bundled source streams preserve native pull, borrowing and authority semantics."""
import unittest
from pathlib import Path
from chaos.language import Evaluator, LanguageError, ResourceLimit, ExecutionControl, Native, TailCall, serializable
from chaos.tuple_values import TupleValue
from chaos.session import Session
from chaos.modules import STDLIB

IMPORT='import "std/iter" as I;'

class LazyStreamTests(unittest.TestCase):
    def code(self,source,**options):return Evaluator(**options).run(IMPORT+source)
    def assertValues(self,actual,expected):self.assertEqual(serializable(actual),serializable(expected))

    def test_all_27_exports_are_real_language_functions(self):
        exports=self.code('I')
        expected='map filter flat_map take drop take_while drop_while enumerate zip chain batches scan amap afilter aflat_map atake adrop atake_while adrop_while aenumerate azip achain abatches ascan from_sync count repeat'.split()
        self.assertEqual(set(exports),set(expected))
        from chaos.language import Function
        self.assertTrue(all(type(value) is Function for value in exports.values()))
        self.assertEqual(STDLIB['std/iter'],Path(__file__).resolve().parents[1].joinpath('chaos/stdlib/iter.chaos').read_text())

    def test_sync_algebra_all_exports(self):
        result=self.code('''[collect(I.map(fn(x){x*2},[1,2])),collect(I.filter(fn(x){x>1},[1,2,3])),
        collect(I.flat_map(fn(x){[x,x+10]},[1,2])),collect(I.take([1,2,3],2)),collect(I.drop([1,2,3],1)),
        collect(I.take_while(fn(x){x<3},[1,2,3,1])),collect(I.drop_while(fn(x){x<3},[1,2,3,1])),
        collect(I.enumerate([7,8],5)),collect(I.zip([1,2],[3,4],[5])),collect(I.chain([1],[],[2,3])),
        collect(I.batches([1,2,3,4,5],2)),collect(I.scan(fn(a,b){a+b},10,[1,2,3]))]''')
        self.assertValues(result,[[2,4],[2,3],[1,11,2,12],[1,2],[2,3],[1,2],[3,1],
            [TupleValue((5,7)),TupleValue((6,8))],[TupleValue((1,3,5))],[1,2,3],
            [TupleValue((1,2)),TupleValue((3,4)),TupleValue((5,))],[10,11,13,16]])

    def test_async_algebra_all_exports_and_explicit_sync_bridge(self):
        result=self.code('''[await acollect(I.amap(async fn(x){await sleep(0);x*2},I.from_sync([1,2]))),
        await acollect(I.afilter(async fn(x){x>1},I.from_sync([1,2,3]))),
        await acollect(I.aflat_map(async fn(x){I.from_sync([x,x+10])},I.from_sync([1,2]))),
        await acollect(I.atake(I.from_sync([1,2,3]),2)),await acollect(I.adrop(I.from_sync([1,2,3]),1)),
        await acollect(I.atake_while(async fn(x){x<3},I.from_sync([1,2,3,1]))),
        await acollect(I.adrop_while(async fn(x){x<3},I.from_sync([1,2,3,1]))),
        await acollect(I.aenumerate(I.from_sync([7,8]),5)),
        await acollect(I.azip(I.from_sync([1,2]),I.from_sync([3,4]),I.from_sync([5]))),
        await acollect(I.achain(I.from_sync([1]),I.from_sync([]),I.from_sync([2,3]))),
        await acollect(I.abatches(I.from_sync([1,2,3,4,5]),2)),
        await acollect(I.ascan(async fn(a,b){a+b},10,I.from_sync([1,2,3])))]''',step_limit=200000)
        self.assertValues(result,[[2,4],[2,3],[1,11,2,12],[1,2],[2,3],[1,2],[3,1],
            [TupleValue((5,7)),TupleValue((6,8))],[TupleValue((1,3,5))],[1,2,3],
            [TupleValue((1,2)),TupleValue((3,4)),TupleValue((5,))],[10,11,13,16]])

    def test_cold_creation_and_exactly_one_acquisition(self):
        self.assertEqual(self.code('''var acquisitions=0;var pulls=0;var calls=0;
        class Source{var n=0;fn __iter__(s){acquisitions=acquisitions+1;s}fn __next__(s){pulls=pulls+1;s.n=s.n+1;{done:s.n>3,value:s.n}}}
        let stream=I.map(fn(x){calls=calls+1;x},Source());let cold=[acquisitions,pulls,calls];let first=next(stream).value;
        [cold,first,collect(stream),[acquisitions,pulls,calls]]'''),[[0,0,0],1,[2,3],[1,4,3]])

    def test_zero_take_and_initial_scan_never_acquire_early(self):
        self.assertEqual(self.code('''class Bad{fn __iter__(s){throw ValueError("acquired")}fn __aiter__(s){throw ValueError("acquired")}}
        let sync=I.scan(fn(a,b){a+b},42,Bad());let async=I.ascan(fn(a,b){a+b},42,Bad());
        [collect(I.take(Bad(),0)),await acollect(I.atake(Bad(),0)),next(sync).value,(await anext(async)).value,
        try{next(sync)}catch e{e.code},try{await anext(async)}catch e{e.code}]'''),[[],[],42,42,'value_error','value_error'])

    def test_take_does_not_read_after_requested_count(self):
        self.assertEqual(self.code('''let cursor=iter([1,2,3]);let stream=I.take(cursor,2);let result=collect(stream);
        let async=I.from_sync([1,2,3]);let aresult=await acollect(I.atake(async,2));
        [result,next(cursor).value,aresult,(await anext(async)).value]'''),[[1,2],3,[1,2],3])

    def test_drop_while_stops_calling_predicate_after_first_false(self):
        self.assertEqual(self.code('''var calls=[];let result=collect(I.drop_while(fn(x){calls=append(calls,x);x<2},[1,2,1]));[result,calls]'''),[[2,1],[1,2]])

    def test_take_while_consumes_only_the_terminating_item(self):
        self.assertEqual(self.code('''let input=iter([1,2,3,4]);let result=collect(I.take_while(fn(x){x<3},input));[result,next(input).value]'''),[[1,2],4])

    def test_infinite_count_repeat_and_truncation(self):
        self.assertEqual(self.code('''[collect(I.take(I.count(),4)),collect(I.take(I.count(start=4,step=-2),4)),
        collect(I.take(I.count(7,0),3)),collect(I.repeat("a",3)),collect(I.repeat(42,0)),collect(I.take(I.repeat(42),3))]'''),
        [[0,1,2,3],[4,2,0,-2],[7,7,7],['a','a','a'],[],[42,42,42]])

    def test_invalid_finite_parameters_fail_before_input_effects(self):
        invalid=['I.take(source,-1)','I.drop(source,-1)','I.take(source,true)','I.drop(source,1.5)',
                 'I.batches(source,0)','I.batches(source,10001)','I.batches(source,true)',
                 'I.enumerate(source,true)','I.count(true)','I.count(0,1.0)','I.repeat(42,-1)','I.repeat(42,true)',
                 'I.atake(source,-1)','I.adrop(source,true)','I.abatches(source,0)','I.aenumerate(source,1.0)']
        for expression in invalid:
            with self.subTest(expression=expression):
                self.assertEqual(self.code('''var effects=0;class Source{fn __iter__(s){effects=effects+1;throw "input"}fn __aiter__(s){effects=effects+1;throw "input"}}
                let source=Source();try{'''+expression+'''}catch e{null};effects'''),0)
                with self.assertRaises(LanguageError):self.code('let source=[];'+expression)
        self.assertEqual(self.code('''var effects=0;class Count{fn __eq__(s,x){effects=effects+1;true}};
        let e=try{I.repeat(42,Count())}catch e{e.code};[e,effects]'''),['runtime_error',0])

    def test_callbacks_validated_without_invocation(self):
        for name,arguments in [('map','42,[]'),('filter','null,[]'),('flat_map','true,[]'),('take_while','1,[]'),('drop_while','1,[]'),('scan','1,0,[]')]:
            for prefix in ('','a'):
                with self.subTest(name=prefix+name),self.assertRaises(LanguageError):self.code('I.'+prefix+name+'('+arguments+')')
        self.assertEqual(self.code('''var calls=0;class Mapper{fn __call__(s,x){calls=calls+1;x+1}}let stream=I.map(Mapper(),[41]);
        let cold=calls;[cold,collect(stream),calls]'''),[0,[42],1])

    def test_predicates_are_strict_bool_without_truthiness_hooks(self):
        for expression in ['I.filter(fn(x){1},[1])','I.take_while(fn(x){1},[1])','I.drop_while(fn(x){1},[1])']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError):self.code('collect('+expression+')')
        self.assertEqual(self.code('''var effects=0;class Boolish{fn __bool__(s){effects=effects+1;true}};
        let code=try{collect(I.filter(fn(x){Boolish()},[1]))}catch e{e.code};[code,effects]'''),['runtime_error',0])

    def test_async_callbacks_await_once_and_keep_nested_task_values(self):
        self.assertEqual(self.code('''var calls=0;async fn inner(){calls=calls+1;42};async fn outer(x){inner()};
        let stream=I.amap(outer,I.from_sync([1]));let item=(await anext(stream)).value;let before=calls;
        [type_of(item),before,await item,calls]'''),['Task',0,42,1])
        self.assertEqual(self.code('''var calls=0;async fn inner(){calls=calls+1;true};async fn outer(x){inner()};
        let failure=try{await acollect(I.afilter(outer,I.from_sync([1])))}catch e{e.code};[failure,calls]'''),['runtime_error',0])
        self.assertEqual(self.code('await acollect(I.amap(fn(x){x+1},I.from_sync([41])))'),[42])

    def test_sync_callbacks_never_implicitly_await_tasks(self):
        self.assertEqual(self.code('''var calls=0;async fn f(x){calls=calls+1;x};let tasks=collect(I.map(f,[42]));let before=calls;
        [type_of(tasks[0]),before,await tasks[0],calls]'''),['Task',0,42,1])

    def test_async_forms_require_async_sources_and_flatmap_results(self):
        for expression in ['I.amap(fn(x){x},[1])','I.afilter(fn(x){true},[1])','I.atake([1],1)','I.adrop([1],0)',
                           'I.aenumerate([1])','I.azip([1])','I.achain([1])','I.abatches([1],1)',
                           'I.aflat_map(fn(x){[x]},I.from_sync([1]))']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError):self.code('await acollect('+expression+')')

    def test_zip_zero_aliases_shortest_and_left_to_right(self):
        self.assertValues(self.code('''let c=iter([1,2,3,4,5]);let paired=collect(I.zip(c,c));
        let first=iter([1,2,3]);let second=iter([9]);let rows=collect(I.zip(first,second));
        [collect(I.zip()),await acollect(I.azip()),paired,rows,next(first)]'''),
        [[],[],[TupleValue((1,2)),TupleValue((3,4))],[TupleValue((1,9))],{'done':False,'value':3}])

    def test_zip_acquires_all_sources_before_advancing(self):
        self.assertEqual(self.code('''var log=[];class Good{fn __iter__(s){log=append(log,"acquire");s}fn __next__(s){log=append(log,"next");{done:false,value:1}}}
        class Bad{fn __iter__(s){log=append(log,"bad");throw ValueError("stop")}}let stream=I.zip(Good(),Bad());let cold=log;
        try{next(stream)}catch e{null};[cold,log]'''),[[],['acquire','bad']])

    def test_chain_delays_later_acquisition_and_zero_sources(self):
        self.assertEqual(self.code('''var calls=0;class Later{fn __iter__(s){calls=calls+1;throw ValueError("later")}}
        let stream=I.chain([42],Later());let first=next(stream).value;let before=calls;let code=try{next(stream)}catch e{e.code};
        [first,before,code,calls,collect(I.chain()),await acollect(I.achain())]'''),[42,0,'value_error',1,[],[]])

    def test_borrowed_close_error_and_explicit_owner(self):
        self.assertEqual(self.code('''var closed=0;fn* values(){try{yield 1;yield 2;yield 3}finally{closed=closed+1}}
        let input=values();let stream=I.map(fn(x){x},input);next(stream);close(stream);let after=closed;
        let failure=I.map(fn(x){throw ValueError("callback")},input);try{next(failure)}catch e{null};
        [after,closed,next(input).value]'''),[0,0,3])
        self.assertEqual(self.code('''import "std/context" as C;var closed=0;fn* values(){try{yield 42}finally{closed=closed+1}}
        let input=values();with C.closing(input) as owned{let stream=I.map(fn(x){x},owned);next(stream);close(stream)};closed'''),1)

    def test_async_borrowed_close_leaves_upstream_positioned(self):
        self.assertEqual(self.code('''var closed=0;async fn* values(){try{yield 1;yield 2}finally{closed=closed+1}}
        let input=values();let stream=I.amap(fn(x){x},input);await anext(stream);await aclose(stream);
        [closed,(await anext(input)).value]'''),[0,2])

    def test_send_and_throw_do_not_delegate_to_borrowed_source(self):
        self.assertEqual(self.code('''fn* values(){try{yield 1;yield 2;yield 3}catch e{yield "forwarded"}}
        let input=values();let stream=I.map(fn(x){x},input);next(stream);let second=send(stream,99).value;
        let failure=try{throw_into(stream,"stop")}catch e{e.value};[second,failure,next(input).value]'''),[2,'stop',3])

    def test_explicit_upstream_interleaving_and_derived_reentry(self):
        self.assertEqual(self.code('''let cursor=iter([1,2,3,4]);let stream=I.map(fn(x){next(cursor);x},cursor);
        let first=next(stream).value;let intervening=next(cursor).value;[first,intervening,next(stream).value]'''),[1,3,4])
        with self.assertRaises(LanguageError):self.code('var stream=null;stream=I.map(fn(x){next(stream);x},[1]);next(stream)')

    def test_context_values_are_captured_by_original_generator_scope(self):
        self.assertEqual(self.code('''let value=context_var("value",0);value.set(41);let stream=I.map(fn(x){value.get()+x},[1]);
        value.set(0);[next(stream).value,value.get()]'''),[42,0])

    def test_low_authority_callback_cannot_execute_tools(self):
        calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
        ev.env.bind('limited',Native('limited',lambda fn:TailCall(fn,[]),ev,capabilities=frozenset()))
        self.assertEqual(ev.run(IMPORT+'let stream=limited(fn(){I.map(fn(x){tool("secret",x)},[42])});try{next(stream)}catch e{e.code}'),'capability_error')
        self.assertEqual(calls,[])

    def test_foreign_and_stale_sources_fail_on_demand(self):
        foreign=Evaluator().run('fn* f(){yield 42};f()');ev=Evaluator();ev.env.bind('foreign',foreign)
        with self.assertRaises(LanguageError):ev.run(IMPORT+'next(I.map(fn(x){x},foreign))')
        session=Session();session.evaluate(IMPORT+'let source=iter([42])')
        with self.assertRaises(LanguageError):session.evaluate('next(I.take(source,1))')
        callback=Evaluator().run('fn(x){x}');ev=Evaluator();ev.env.bind('foreign',callback)
        with self.assertRaises(LanguageError):ev.run(IMPORT+'I.map(foreign,[])')

    def test_resource_limit_and_cancellation_propagate(self):
        with self.assertRaises(ResourceLimit):self.code('collect(I.count())',step_limit=10000)
        ev=Evaluator();original=ev.tick
        def checkpoint():
            if ev.steps>2000:raise ExecutionControl('cancel')
            original()
        ev.tick=checkpoint
        with self.assertRaises(ExecutionControl):ev.run(IMPORT+'collect(I.count())')
        self.assertEqual(ev.depth,0);self.assertEqual(ev.frames,[])

    def test_retained_stream_capture_seals_native_next(self):
        calls=[];tokens=[];ev=Evaluator(step_limit=1000000,tool_handler=lambda *args:calls.append(args))
        ev.env.bind('limited',Native('limited',lambda fn:TailCall(fn,[]),ev,capabilities=frozenset()))
        ev.env.bind('remember',Native('remember',lambda item:tokens.append(item) or item,ev))
        source=IMPORT+'''let base=generic_method(generic_function("g"),["Any"],fn(x){tool("secret",x)});
        let g=generic_method(base,["Int"],fn(next,x){next},with_next=true);
        let token=remember(g(42));let stream=I.map(fn(x){x},[token]);
        limited(fn(){fn(value){0}})(stream);42'''
        self.assertEqual(ev.run(source),42)
        with self.assertRaises(LanguageError) as caught:ev.invoke(tokens[0],[])
        self.assertEqual(caught.exception.code,'capability_error');self.assertEqual(calls,[])

if __name__=='__main__':unittest.main()
