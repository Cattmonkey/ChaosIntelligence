"""Native products preserve strict language values, binding and effect semantics."""
import unittest
from dataclasses import FrozenInstanceError

from chaos.language import Evaluator, LanguageError, Node, Parser, ResourceLimit, serializable
from chaos.tuple_values import TupleValue
from chaos.indexing import Slice, native_get
from chaos.session import Session


class TupleTests(unittest.TestCase):
    def run_code(self, source, bindings=None, **kwargs): return Evaluator(**kwargs).run(source, bindings)
    def wire(self, source, **kwargs): return serializable(self.run_code(source, **kwargs))

    def test_literals_grouping_generator_and_internal_identity(self):
        self.assertEqual(self.wire('[(),(42,),(20,22),(40+2),collect((x+1 for x in (1,2)))]'),
                         [{'$tuple':[]},{'$tuple':[42]},{'$tuple':[20,22]},42,[2,3]])
        self.assertEqual(Parser('(1,)').program()[1][0],('tuple',[('literal',1)]))
        a,b=TupleValue([1]),TupleValue([1]);self.assertIsNot(a,b);self.assertNotEqual(a,b)
        self.assertEqual(a.items,(1,));self.assertEqual(len({a,b}),2)
        with self.assertRaises(FrozenInstanceError): a.items=(2,)
        for value in ((1,2),Node(('literal',42)),TupleValue([(1,2)])):
            with self.subTest(value=type(value).__name__),self.assertRaises(LanguageError):serializable(value)

    def test_empty_singleton_multi_and_malformed_source(self):
        for source in ['(,)','(1,,2)','(*[1],)','1,2','let (x)=42','let (..r)=()','let (a,a)=(1,2)',
                       'let (a,..[rest])=(1,2)','let (a,..rest,b)=(1,2,3)','(x for x in [1],)']:
            calls=[]
            with self.subTest(source=source),self.assertRaises(LanguageError):
                self.run_code('tool("before",null);'+source,tool_handler=lambda *args:calls.append(args))
            self.assertEqual(calls,[])
        self.assertEqual(self.wire('let ()=();let (value,)=(42,);let (..rest,)=(1,2);(value,rest)'),{'$tuple':[42,{'$tuple':[1,2]}]})

    def test_constructor_keywords_unicode_and_borrowed_cursor(self):
        self.assertEqual(self.wire('[tuple(),tuple(items="🙂a"),tuple((1,2)),tuple(range(3))]'),
                         [{'$tuple':[]},{'$tuple':['🙂','a']},{'$tuple':[1,2]},{'$tuple':[0,1,2]}])
        source='''class Source{var i=0;var closed=false;fn __iter__(s){s}
          fn __next__(s){s.i+=1;{done:s.i>2,value:s.i}}fn __close__(s){s.closed=true;null}}
          let s=Source();let cursor=iter(s);let result=tuple(cursor);[result,next(cursor),s.closed]'''
        self.assertEqual(self.wire(source),[{'$tuple':[1,2]},{'done':True,'value':3},False])
        for source in ('tuple(42)','tuple({x:1})','tuple(1,2)','tuple(extra=[])','async fn* g(){yield 1};tuple(g())'):
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_protocols_contracts_and_explicit_boolean(self):
        self.assertEqual(self.run_code('let x:Tuple=(1,true);[type_of(x),length(x),bool(x),bool(()),contains(x,true),contains((1,),true),(1,)==(true,),(1,)==[1]]'),
                         ['Tuple',2,True,False,True,False,False,False])
        self.assertEqual(self.wire('[()+(),(1,)+(2,3),slice((0,1,2),1),type_of(slice((0,),0))]'),
                         [{'$tuple':[]},{'$tuple':[1,2,3]},{'$tuple':[1,2]},'Tuple'])
        for source in ('let x:Tuple=[]','let x:List=()','(1,)+[2]','(1,)*2','head((1,))','reverse((1,))','if (1,){42}else{0}','bool([])'):
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_index_slice_boundaries_and_immutability(self):
        self.assertEqual(self.wire('[(20,22)[-1],(0,1,2,3)[::-1],(0,1,2)[1:99],(0,1)[99:99],(42,).[0]]'),
                         [22,{'$tuple':[3,2,1,0]},{'$tuple':[1,2]},{'$tuple':[]},42])
        value=TupleValue([1,2,3])
        for start in (None,-9,-1,0,2,9):
            for stop in (None,-9,-1,0,2,9):
                for step in (None,-2,-1,1,2):
                    self.assertEqual(native_get(value,Slice(start,stop,step)).items,value.items[slice(start,stop,step)])
        for source in ('()[0]','(1,)[true]','(1,)[1.0]','(1,)[1,]','(1,)[0]=2','(1,).items','setitem((1,),0,2)','delitem((1,),0)','(1,).[(-1)]'):
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_copy_projection_and_plain_json_never_reifies(self):
        child={'list':[1]};value=TupleValue([child,TupleValue([child])]);copy=Evaluator.copy_value(value)
        child['list'].append(2)
        self.assertEqual(serializable(copy),{'$tuple':[{'list':[1]},{'$tuple':[{'list':[1]}]}]})
        self.assertIsInstance(copy,TupleValue);self.assertIsNot(copy[0],child)
        self.assertEqual(self.run_code('let wire=parse_json(json_stringify((1,(2,))));[type_of(wire),type_of(tuple(wire["$tuple"])),type_of(tuple(wire["$tuple"])[1])]'),['Record','Tuple','Record'])
        payloads=[]
        self.assertEqual(self.wire('tool("save",(20,{nested:(22,)}))',tool_handler=lambda n,v:payloads.append(v) or v),{'$tuple':[20,{'nested':{'$tuple':[22]}}]})
        self.assertEqual(payloads,[{'$tuple':[20,{'nested':{'$tuple':[22]}}]}])
        ev=Evaluator();self.assertEqual(ev.host_value(TupleValue([Slice(1,2)])),{'$tuple':[{'$slice':{'start':1,'stop':2,'step':None}}]})

    def test_nested_pattern_rest_constructors_and_pure_literal_match(self):
        source='''data D{Some(x)};let (a,[b],{x:Some(c)},..rest)=(1,[2],{x:Some(3)},4,5);
          [a,b,c,rest,match (1,true){(true,_)=>0,(1,true)=>42,_=>99}]'''
        self.assertEqual(self.wire(source),[1,2,3,{'$tuple':[4,5]},42])
        self.assertEqual(self.run_code('let (_,value,.._)=(1,42,3);value'),42)
        for source in ('let (a,b)=[1,2]','let [a,b]=(1,2)','let (a,)=(1,2)','let (true,)=(1,)'):
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:self.run_code(source)
            self.assertEqual(caught.exception.code,'match_error')
        source='var count=0;class C{fn __eq__(s,v){count+=1;true}};let result=match (C(),){(1,)=>true,_=>false};[result,count]'
        self.assertEqual(self.run_code(source),[False,0])

    def test_effectful_equality_is_ordered_and_not_memoized_over_instances(self):
        source='''var seen=[];class C{var label=0;fn __init__(s,n){s.label=n;null}
          fn __eq__(s,other){seen=append(seen,s.label);true}};
          let a=C(1);let b=C(2);let left=(a,b);let equal=(left,left)==((0,0),(0,0));
          let has=contains((b,a),7);[equal,has,seen]'''
        self.assertEqual(self.run_code(source),[True,True,[1,2,1,2,2]])
        self.assertEqual(self.run_code('var seen=0;class C{fn __eq__(s,x){seen+=1;false}};[(C(),C())==(1,2),seen]'),[False,1])

    def test_declaration_failure_is_atomic_after_rhs(self):
        session=Session();session.evaluate('var effects=0;let kept=42')
        for source in ('let (fresh,0)=do{effects+=1;(1,2)}','let (fresh,kept)=do{effects+=1;(1,2)}'):
            with self.assertRaises(LanguageError):session.evaluate(source)
        self.assertEqual(session.bindings(),{'effects':2,'kept':42})
        self.assertEqual(self.run_code('var (a,b):Tuple=(20,22);a="new";[a,b]'),['new',22])

    def test_assignment_preflight_typed_cells_and_rhs_first(self):
        source='''var a:Int=1;var b:Int=2;var count=0;
          let bad=try{(a,b)=do{count+=1;(20,"bad")}}catch e{e.code};
          let missing=try{(a,unknown)=(8,9)}catch e{e.code};
          let immutable=do{let fixed=0;try{(a,fixed)=(8,9)}catch e{e.code}};
          let result=do{(a,b)=do{count+=1;(b,a)}};[a,b,count,result]'''
        self.assertEqual(self.wire(source),[2,1,2,{'$tuple':[2,1]}])
        self.assertEqual(self.run_code('var a=1;let read=fn(){a};(a,)=do{a=2;(read()+40,)};a'),42)

    def test_all_function_flavors_bind_before_cold_allocation(self):
        for flavor,consume in [('fn','f((20,22))'),('async fn','await f((20,22))'),('fn*','collect(f((20,22)))[0]'),('async fn*','(await acollect(f((20,22))))[0]')]:
            body='yield a+b' if '*'in flavor else 'a+b'
            with self.subTest(flavor=flavor):self.assertEqual(self.run_code(flavor+' f(pair as (a,b):Tuple,/){'+body+'};'+consume),42)
            ev=Evaluator();seen=[]
            with self.assertRaises(LanguageError) as caught:ev.run(flavor+' f(pair as (a,b):Tuple){tool("body",null)};f((1,))')
            self.assertEqual(caught.exception.code,'match_error');self.assertEqual(len(ev.suspension.generators),0)
            self.assertFalse(any(t.kind=='function'for t in ev.suspension.tasks))
        self.assertEqual(self.run_code('class C{fn f(self,pair as (a,b):Tuple,/){a+b}};let f=C().f;apply(partial(f),( (20,22), ))'),42)

    def test_loop_and_comprehension_bindings_are_fresh(self):
        source='''var functions=[];for (a,b) in ((1,2),(20,22)){functions=append(functions,fn(){a+b})};
          [map(fn(f){f()},functions),[a+b for (a,b) in ((1,2),(20,22))],
           {str(a):b for (a,b) in ((1,2),(20,22))},collect((a+b for (a,b) in ((1,2),(20,22))))]'''
        self.assertEqual(self.run_code(source),[[3,42],[3,42],{'1':2,'20':22},[3,42]])
        self.assertEqual(self.run_code('async fn* g(){yield (20,22)};await acollect(async (a+b async for (a,b) in g()))'),[42])

    def test_shared_iterable_consumers_and_argument_expansion(self):
        self.assertEqual(self.run_code('fn add(a,b){a+b};[add(*(20,22)),apply(add,(20,22)),map(fn(x){x+1},(1,2)),filter(fn(x){x>1},(1,2)),fold(add,0,(20,22)),any(fn(x){x==2},(1,2)),all(fn(x){x>0},(1,2)),collect((1,2))]'),[42,42,[2,3],[2],42,True,True,[1,2]])
        self.assertEqual(self.run_code('fn* g(){yield from (20,22)};collect(g())'),[20,22])
        self.assertEqual(self.run_code('async fn* g(){yield from (20,22)};await acollect(g())'),[20,22])

    def test_composite_keys_and_legacy_dialect_boundary(self):
        source='class C{fn __getitem__(self,key){key}};[C()[1,2],C()[1,],C()[()],C()[(1,2)],C()[1:4:2,::,3]]'
        self.assertEqual(self.wire(source),[{'$tuple':[1,2]},{'$tuple':[1]},{'$tuple':[]},{'$tuple':[1,2]},
                         {'$tuple':[{'$slice':{'start':1,'stop':4,'step':2}},{'$slice':{'start':None,'stop':None,'step':None}},3]}])
        for source in ['obj[1,2]','obj[1,]','obj[1:2,3]']:
            with self.subTest(source=source),self.assertRaises(LanguageError):Parser(source,legacy_items=True).program()
        for source in ['obj.[1,2]','obj[(1:2,3)]','obj[1,,2]','obj[]','obj[1:2:3:4,2]']:
            with self.subTest(source=source),self.assertRaises(LanguageError):Parser(source).program()
        for source in ['[1,2][0,1]','"abc"[0,1]','{x:1}["x",]']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_composite_effects_set_delete_and_augmented_target_capture(self):
        source='''var events=[];fn mark(n,x){events=append(events,n);x}
          class C{var n=20;fn __getitem__(s,k){events=append(events,"get");s.n}
            fn __setitem__(s,k,v){events=append(events,"set");s.n=v;null}
            fn __delitem__(s,k){events=append(events,"delete");s.n=0;null}}
          let c=C();mark("target",c)[mark("start",1):mark("stop",5):mark("step",2),mark("col",3)]+=mark("rhs",22);
          let answer=c.n;delitem(c,(1,3));[answer,c.n,events]'''
        self.assertEqual(self.run_code(source),[42,0,['target','start','stop','step','col','get','rhs','set','delete']])
        self.assertEqual(self.run_code('var calls=0;class C{async fn __getitem__(s,k as (a,b):Tuple){calls+=1;a+b}};let t=C()[20,22];[calls,await t,calls]'),[0,42,1])

    def test_tuple_resource_bounds(self):
        with self.assertRaises(ResourceLimit):TupleValue([0]*10001)
        for source in ('tuple(range(10000))+(1,)','tuple("x"+tool("large",null))'):
            with self.subTest(source=source),self.assertRaises(ResourceLimit):self.run_code(source,step_limit=500000,tool_handler=lambda *a:'x'*10000)
        self.assertEqual(self.run_code('length(tuple(range(10000)))',step_limit=500000),10000)
        nested=TupleValue()
        for _ in range(60):nested=TupleValue([nested])
        with self.assertRaises(ResourceLimit):serializable(nested)
        cycle=[];product=TupleValue([cycle]);cycle.append(product)
        with self.assertRaises(ResourceLimit):serializable(product)

    def test_parenthesized_generators_are_valid_tuple_elements(self):
        from chaos.program import parse_program, format_program
        sources = [
            ('let value=((x for x in (20,22)),);collect(value[0])',[20,22]),
            ('let value=((x for x in (20,22)),42);[collect(value[0]),value[1]]',[[20,22],42]),
            ('async fn* rows(){yield 20;yield 22};let value=(async (x async for x in rows()),);await acollect(value[0])',[20,22]),
            ('let value=(((x for x in (20,22))),);collect(value[0])',[20,22]),
        ]
        for source,expected in sources:
            with self.subTest(source=source):
                self.assertEqual(self.run_code(source),expected)
                self.assertEqual(self.run_code(format_program(parse_program(source))),expected)
        for source in ('(x for x in [1],)', 'async (x async for x in input,)'):
            with self.subTest(source=source),self.assertRaises(LanguageError):Parser(source).program()

    def test_delegation_diagnostics_preserve_legacy_sources(self):
        for value,label in [('[1,2]','List/String'),('"ab"','List/String'),('(1,2)','Tuple'),('iter([1,2])','List/String')]:
            for flavor,start,resume in [('fn*','next(g)','send(g,42)'),('async fn*','await anext(g)','await asend(g,42)')]:
                source=flavor+' values(){yield from '+value+'};let g=values();'+start+';try{'+resume+'}catch e{e.message}'
                with self.subTest(value=value,flavor=flavor):
                    self.assertEqual(self.run_code(source),label+' 委托只接受 null')
