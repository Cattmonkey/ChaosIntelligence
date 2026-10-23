"""Structural binding is atomic and does not change iterator ownership."""
import threading
import unittest

from chaos.language import Evaluator, LanguageError, Parser, ResourceLimit
from chaos.session import Session

SYNC='''class Source{var reads=0;var factories=0;var closes=0;
fn __iter__(self){self.factories=self.factories+1;self}
fn __next__(self){self.reads=self.reads+1;{done:false,value:[self.reads,self.reads*10]}}
fn __close__(self){self.closes=self.closes+1;null}}let raw=Source();raw.closes=0;'''
ASYNC='''class Source{var reads=0;var factories=0;var closes=0;
fn __aiter__(self){self.factories=self.factories+1;self}
async fn __anext__(self){self.reads=self.reads+1;{done:false,value:[self.reads,self.reads*10]}}
async fn __aclose__(self){self.closes=self.closes+1;null}}let raw=Source();raw.closes=0;'''


class PatternResourceTests(unittest.TestCase):
    def evaluate(self, source, **kwargs):return Evaluator(**kwargs).run(source)

    def test_duplicate_names_fields_and_rest_rejected_before_any_effect(self):
        bad=['let [a,{x:a}]=[1,{x:2}]','var [a,..a]=[1,2]',
             'let {x:a,x:b}={x:1}','let [a,..r,..s]=[1,2]',
             'let {x:a,..r,..s}={x:1}',
             'for [a,a] in []{null}','[1 for [a,a] in []]',
             'async (1 async for [a,a] in input)',
             'class C{let [a,b]=[1,2]}','class C{var {x:a}={x:1}}']
        for source in bad:
            seen=[]
            with self.subTest(source=source),self.assertRaises(LanguageError):
                self.evaluate('tool("unexpected",null);'+source,tool_handler=lambda *args:seen.append(args))
            self.assertEqual(seen,[])

    def test_whole_value_type_checked_before_matching_and_no_binding_leaks(self):
        for declaration,bindings in [('let [a,b]:Record=[1,2]',{}),('var [a,b]:Record=[1,2]',{})]:
            session=Session()
            with self.subTest(declaration=declaration),self.assertRaises(LanguageError) as error:
                session.evaluate(declaration)
            self.assertNotEqual(error.exception.code,'match_error')
            self.assertEqual(session.bindings(),bindings)
        for declaration in ['let [a,b]:List=[1]','var [a,b]:List=[1]']:
            session=Session()
            with self.subTest(declaration=declaration),self.assertRaises(LanguageError) as error:session.evaluate(declaration)
            self.assertEqual(error.exception.code,'match_error');self.assertEqual(session.bindings(),{})

    def test_literals_and_data_constructor_targets_keep_strict_match_identity(self):
        cases=[('let true=true;let null=null;let -2=-2;42',42),
               ('data Value{Pair(a,b)}let Pair(left,right)=Pair(20,22);left+right',42),
               ('import "std/result" as R;let R.Ok([left,right])=R.Ok([20,22]);left+right',42),
               ('let [_,value,.._]=[0,42,9];value',42),
               ('let _=40;for _ in [1,2]{};_+2',42)]
        for source,expected in cases:
            with self.subTest(source=source):self.assertEqual(self.evaluate(source),expected)
        for source in ['let true=1','let [left,right]="ab"',
                       'let {value:x}={value:1,extra:2}',
                       'data A{Item(x)}data B{Other(x)}let Item(value)=Other(1)']:
            with self.subTest(source=source),self.assertRaises(LanguageError) as error:self.evaluate(source)
            self.assertEqual(error.exception.code,'match_error')

    def test_for_mismatch_consumes_one_item_without_body_or_borrowed_close(self):
        for prefix,acquire,loop,advance in [
            (SYNC,'iter(raw)','for [left,right,third] in cursor{tool("unexpected",left)}','next(cursor)'),
            (ASYNC,'aiter(raw)','async for [left,right,third] in cursor{tool("unexpected",left)}','await anext(cursor)')]:
            seen=[]
            with self.subTest(loop=loop):
                result=self.evaluate(prefix+'let cursor='+acquire+';let error=try{'+loop+'}catch e{e.code};[error,raw.reads,raw.closes,'+advance+']',tool_handler=lambda *args:seen.append(args))
                self.assertEqual(result,['match_error',1,0,{'done':False,'value':[2,20]}]);self.assertEqual(seen,[])

    def test_all_comprehension_forms_fail_before_guard_head_key_or_value(self):
        forms=['[tool("head",left) for [left,right,third] in cursor if tool("guard",left)]',
               '{tool("key",left):tool("value",right) for [left,right,third] in cursor}',
               'collect((tool("head",left) for [left,right,third] in cursor))',
               'await acollect(async (tool("head",left) async for [left,right,third] in cursor))']
        for expression in forms:
            asynchronous='async for' in expression
            prefix,acquire,advance=(ASYNC,'aiter(raw)','await anext(cursor)') if asynchronous else (SYNC,'iter(raw)','next(cursor)')
            seen=[]
            with self.subTest(expression=expression):
                result=self.evaluate(prefix+'let cursor='+acquire+';let error=try{'+expression+'}catch e{e.code};[error,raw.reads,raw.closes,'+advance+']',tool_handler=lambda *args:seen.append(args))
                self.assertEqual(result,['match_error',1,0,{'done':False,'value':[2,20]}]);self.assertEqual(seen,[])

    def test_lazy_targets_acquire_and_match_only_at_first_advance(self):
        for prefix,expression,advance,closing in [
            (SYNC,'(left+right for [left,right,third] in selected)','next(comp)','close(comp)'),
            (ASYNC,'async (left+right async for [left,right,third] in selected)','await anext(comp)','await aclose(comp)')]:
            with self.subTest(expression=expression):
                self.assertEqual(self.evaluate(prefix+'var selected=raw;let comp='+expression+';selected=Source();let before=[raw.factories,raw.reads];let err=try{'+advance+'}catch e{e.code};[before,err,raw.factories,raw.reads,raw.closes,selected.reads]'),[[0,0],'match_error',1,1,0,0])
                self.assertEqual(self.evaluate(prefix+'var selected=raw;let comp='+expression+';'+closing+';[raw.factories,raw.reads,raw.closes]'),[0,0,0])

    def test_iteration_bindings_have_fresh_closure_scopes_and_preserve_outer_cells(self):
        source='''var left=90;var functions=[];
for [left,right] in [[1,10],[2,20]]{functions=append(functions,fn(){left+right})};
let eager=[fn(){left+right} for [left,right] in [[3,30],[4,40]]];
let lazy=collect((fn(){left+right} for [left,right] in [[5,50],[6,60]]));
[left,map(fn(f){f()},functions),map(fn(f){f()},eager),map(fn(f){f()},lazy)]'''
        self.assertEqual(self.evaluate(source),[90,[11,22],[33,44],[55,66]])

    def test_break_continue_return_and_throw_preserve_borrowed_cursor_position(self):
        source=SYNC+'''let cursor=iter(raw);var total=0;
for [left,right] in cursor{if left==1{continue}else{null};total=total+right;break};
fn first(){for [left,right] in cursor{return right}}let returned=first();
let error=try{for [left,right] in cursor{throw left}}catch e{e.value};
[total,returned,error,raw.closes,next(cursor)]'''
        self.assertEqual(self.evaluate(source),[20,30,4,0,{'done':False,'value':[5,50]}])
        source=ASYNC+'''let cursor=aiter(raw);var total=0;
async for [left,right] in cursor{if left==1{continue}else{null};total=total+right;break};
async fn first(){async for [left,right] in cursor{return right}}let returned=await first();
let error=try{async for [left,right] in cursor{throw left}}catch e{e.value};
[total,returned,error,raw.closes,await anext(cursor)]'''
        self.assertEqual(self.evaluate(source),[20,30,4,0,{'done':False,'value':[5,50]}])

    def test_patterns_do_not_implicitly_iterate_or_index_user_objects(self):
        seen=[]
        source='''class C{fn __iter__(self){tool("unexpected",null);self}
fn __next__(self){tool("unexpected",null);{done:false,value:1}}
fn __getitem__(self,key){tool("unexpected",key);1}}let value=C();
[try{let [item]=value}catch e{e.code},try{let {x:item}=value}catch e{e.code}]'''
        self.assertEqual(self.evaluate(source,tool_handler=lambda *args:seen.append(args)),['match_error','match_error'])
        self.assertEqual(seen,[])

    def test_native_pattern_comprehensions_do_not_leak_registered_cursors(self):
        for expression in ('[left for [left,right] in [[1,2]]]', '{left:right for [left,right] in [["key",2]]}'):
            ev=Evaluator(step_limit=5000000,timeout=30)
            with self.subTest(expression=expression):
                self.assertEqual(ev.run('var i=0;while i<10001{'+expression+';i=i+1};i'),10001)
                self.assertEqual(ev.suspension.iteration.handles,[])

    def test_cancel_inflight_async_step_drains_before_return_and_does_not_close_borrow(self):
        cancelled,entered,exited=threading.Event(),threading.Event(),threading.Event()
        failures=[]
        def effect(kind,payload,cancel):
            entered.set()
            try:
                if not cancel.wait(3):raise AssertionError('step cancellation was not delivered')
                return [1,2]
            finally:exited.set()
        ev=Evaluator(tool_handler=lambda *args:None,effect_handler=effect,cancelled=cancelled.is_set)
        source='''class Source{var closes=0;fn __aiter__(self){self}
async fn __anext__(self){{done:false,value:tool("wait",null)}}
async fn __aclose__(self){self.closes=self.closes+1;null}}let raw=Source();raw.closes=0;
let cursor=aiter(raw);await acollect(async (left+right async for [left,right] in cursor))'''
        def run():
            try:ev.run(source)
            except BaseException as error:failures.append(error)
        worker=threading.Thread(target=run);worker.start()
        try:self.assertTrue(entered.wait(3))
        finally:cancelled.set();worker.join(5)
        self.assertFalse(worker.is_alive());self.assertTrue(exited.is_set())
        self.assertEqual(len(failures),1);self.assertIsInstance(failures[0],ResourceLimit)
        raw=next(h.target for h in ev.suspension.iteration.handles if h.target is not None)
        self.assertEqual(raw.fields['closes'].value,0)
        self.assertTrue(all(not h.busy for h in ev.suspension.iteration.handles))
        self.assertIsNone(ev.suspension.pool);self.assertEqual(ev.frames,[])


if __name__=='__main__':unittest.main()
