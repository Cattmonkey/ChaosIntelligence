"""Four complete pending pattern forms, against the real pre196 algorithm."""
import ast,copy
from contextlib import ExitStack,nullcontext,contextmanager
import unittest
from unittest.mock import patch
from chaos.language import Evaluator,Parser,Env,Cell,Native,TailCall,LanguageError,ResourceLimit,ExecutionControl,serializable
from chaos.execution_frames import ensure_cursor,ExecutionCursor
from chaos.execution_patterns import PATTERN_FRAMES,PatternSyntaxFrame
from _pattern_reference196 import reference,KINDS,PURE_ORIGINALS
from _effect_schedule158 import effect_schedule
from test_callable_execution_frames import pending,owned_frame
from test_operator_execution_frames import active

WITNESS='''var events=[];
fn note(label,value){events=append(events,label);value}
let [left,{point:right,..rest},..tail]=note("let",[20,{point:22,extra:9},7]);
var {x:a,y:b}=note("var",{x:1,y:2});
[a,b]=note("assign",[b,a]);
let answer=match note("subject",{value:[left,right]}){
 {value:[x,y]} if note("guard0",false)=>0;
 {value:[x,y]} if note("guard1",x+y==42)=>note("body",x+y);
 _=>-1};
[answer,left,right,rest,tail,a,b,events]'''
WITNESS_EXPECTED=[42,20,22,{'extra':9},[7],2,1,['let','var','assign','subject','guard0','guard1','body']]

@contextmanager
def observe_pattern_entries():
    seen={}
    with ExitStack()as stack:
        for cls in PATTERN_FRAMES:
            original=cls.step
            def step(frame,cursor,value,error,_original=original):
                seen[type(frame).__name__]=seen.get(type(frame).__name__,0)+1
                return _original(frame,cursor,value,error)
            stack.enter_context(patch.object(cls,'step',step))
        yield seen

def snapshot(source,legacy=False,limit=30000,cancel=None,setup=None,effect_policy='already_done',inputs=None):
    effects=[];trace=[];debug=[]
    def effect(kind,payload,event):
        effects.append((kind,copy.deepcopy(payload)));return payload['value'] if kind=='tool' else payload.get('input')
    e=Evaluator(step_limit=limit,timeout=30,tool_handler=lambda name,value:value,effect_handler=effect,trace_handler=trace.append)
    e.cancelled=lambda:cancel is not None and e.steps>=cancel
    e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
    if setup:setup(e)
    with effect_schedule(effect_policy)as schedule,reference()if legacy else nullcontext():
        try:result=('value',serializable(e.run(source,inputs)))
        except LanguageError as error:result=('error',error.record())
        except ExecutionControl as error:result=('control',type(error).__name__,str(error))
    return result,e.steps,effects,trace,debug,e.depth,len(e.frames),dict(schedule)

class PatternExecutionFrames(unittest.TestCase):
    def check(self,source,expected=None,**options):
        modern=snapshot(source,**options);old=snapshot(source,legacy=True,**options);self.assertEqual(modern,old)
        if expected is not None:self.assertEqual(modern[0],('value',expected))
        return modern

    def test_all_four_original_branches_are_really_entered_and_modern_route_disabled(self):
        with reference()as reached,observe_pattern_entries()as steps:
            self.assertEqual(Evaluator().run(WITNESS),WITNESS_EXPECTED)
        self.assertEqual(set(reached),set(KINDS));self.assertTrue(all(reached.values()),reached);self.assertEqual(steps,{})
        with observe_pattern_entries()as steps:self.assertEqual(Evaluator().run(WITNESS),WITNESS_EXPECTED)
        self.assertEqual(set(steps),{'PatternSyntaxFrame'});self.assertGreater(steps['PatternSyntaxFrame'],0)

    def test_actual_frame_stays_on_its_real_current_runner_owned_cursor_chain(self):
        roots={};seen=[];original=PatternSyntaxFrame.step
        def step(frame,cursor,value,error):
            runner=cursor.owner.suspension.current;root=runner.iterator
            if id(runner)in roots:self.assertIs(roots[id(runner)][0],runner);self.assertIs(roots[id(runner)][1],root)
            else:roots[id(runner)]=(runner,root)
            self.assertIs(owned_frame(self,root,PatternSyntaxFrame),frame);seen.append(frame.node[0])
            return original(frame,cursor,value,error)
        with patch.object(PatternSyntaxFrame,'step',step):self.assertEqual(Evaluator().run(WITNESS),WITNESS_EXPECTED)
        self.assertEqual(set(seen),set(KINDS));self.assertTrue(roots)

    def test_nested_declarations_assignment_guards_and_order_match_original(self):self.check(WITNESS,WITNESS_EXPECTED)

    def test_rhs_precedes_contract_and_collision_preflight_without_rollback(self):
        for source in ['var calls=0;let kept=42;let error=try{let [fresh,kept]=do{calls+=1;[1,2]}}catch e{e.code};[calls,kept,error]',
          'var calls=0;let reason=try{var [a,b]:Missing=do{calls+=1;[20,22]}}catch e{e.code};[calls,reason]',
          'var calls=0;let reason=try{let [a,b]:Record=do{calls+=1;[20,22]}}catch e{e.code};[calls,reason]']:
            with self.subTest(source=source):self.check(source)

    def test_constructor_lookups_use_outer_environment_before_any_leaf_commit(self):
        self.check('data D{One(x),Other(x)};var C=One;var x=0;[C,C(x)]=[99,One(42)];[C,x]',[99,42])
        self.check('data D{One(x)};let C=One;fn f(){let [C,C(x)]=[99,One(42)];[C,x]};f()',[99,42])

    def test_assignment_preflight_is_atomic_but_rhs_side_effects_remain(self):
        self.check('var a:Int=1;var b:Int=2;var events=0;let reason=try{[a,b]=do{events+=1;a=9;[20,"bad"]}}catch e{e.code};[a,b,events,reason]')
        self.check('var a=1;let b=2;var events=0;try{[a,b]=do{events+=1;[20,22]}}catch e{null};[a,b,events]',[1,2,1])
        self.check('var a=1;var events=0;try{[a,missing]=do{events+=1;[20,22]}}catch e{null};[a,events]',[1,1])

    def test_variable_leaves_preserve_cell_aliases_and_whole_declaration_value(self):
        self.check('var [a,b]:List=[20,22];let read=fn(){[a,b]};let result=do{[a,b]=[b,a]};a="changed";[read(),result]',[['changed',20],[22,20]])
        self.check('do{let [a,b]=[20,22]}',[20,22])

    def test_match_guard_false_effects_scope_shadowing_and_scrutinee_once(self):
        self.check('var calls=[];let x=99;fn value(){calls=append(calls,"subject");[20,22]};let out=match value(){[x,y] if do{calls=append(calls,x);false}=>0;[x,y] if do{calls=append(calls,y);true}=>fn(){x+y}};[out(),x,calls]',[42,99,['subject',20,22]])

    def test_literal_tuple_record_tag_and_nominal_class_patterns_stay_strict(self):
        self.check('data D{Some(value)};class Box{var point=(20,22)};let Some(Box::{point:(a,b)})=Some(Box());[a+b,match true{1=>0;true=>42},match {x:1,y:2}{{x:a}=>0;{x:a,..rest}=>rest}]',[42,42,{'y':2}])
        for source in ['let (a,b)=[20,22]','let [a,b]=(20,22)','match 1{true=>42}','let {x:a}={x:1,y:2}']:
            with self.subTest(source=source):self.check(source)

    def test_matching_does_not_invoke_getters_iterators_equal_or_agent_protocols(self):
        source='var events=[];class Box{var point=42;fn __getattribute__(s,k){events=append(events,"get");default_getattr(s,k)}fn __iter__(s){events=append(events,"iter");iter([42])}fn __eq__(s,o){events=append(events,"eq");true}};let b=Box();let good=match b{Box::{point:n}=>n;_=>0};let reason=try{let [x]=b}catch e{e.code};[good,reason,events]'
        self.check(source,[42,'match_error',[]])

    def test_guard_requires_concrete_bool_and_never_implicitly_awaits_task(self):
        self.check('var seen=0;async fn cold(){seen+=1;true};let reason=try{match 42{x if cold()=>x}}catch e{e.code};[reason,seen]')
        self.check('match 42{x if 1=>x}')

    def test_tail_calls_and_all_multiple_results_survive_selected_match_body(self):
        self.check('fn select(pair){match pair{[x,y]=>return_values(x,y);_=>return_values()}};[multiple_value_list(fn(){select([20,22])}),select([20,22])]',[[20,22],20])
        self.check('fn loop(n){match n{0=>42;x=>loop(x-1)}};loop(100)',42)

    def test_return_break_continue_and_finally_propagate_from_selected_body(self):
        self.check('var out=[];for n in [0,1,2,3]{try{match n{0=>continue;2=>break;x=>do{out=append(out,x)}}}finally{out=append(out,-1)}};out',[-1,1,-1,-1])
        self.check('fn f(){match [42]{[x]=>return x};0};f()',42)

    def test_patterns_share_parameters_loops_comprehensions_with_and_catch(self):
        source='class R{fn __enter__(s){[20,22]}fn __exit__(s,e){false}};fn add(pair as [a,b]){a+b};var seen=[];for [a,b] in [[1,2]]{seen=append(seen,a+b)};let c=[a+b for [a,b] in [[20,22]]];let w=with R()as [a,b]{a+b};let caught=try{throw {x:42}}catch {value:{x:n},.._}{n};[add([20,22]),seen,c,w,caught]'
        self.check(source,[42,[3],[42],42,42])

    def test_real_first_wait_in_every_rhs_guard_and_selected_body(self):
        source='async fn execute(){let [a,b]=tool("let_rhs",[20,22]);var [x,y]=tool("var_rhs",[1,2]);[x,y]=tool("assign_rhs",[a,b]);match tool("subject",[x,y]){[p,q] if tool("guard",true)=>tool("body",p+q)}};await execute()'
        result=self.check(source,42,effect_policy='first_wait');self.assertEqual([v[1]['name']for v in result[2]],['let_rhs','var_rhs','assign_rhs','subject','guard','body']);self.assertEqual(result[-1]['waitBoundaryReleases'],6)

    def test_generator_send_and_close_preserve_pending_binding_and_scope_cleanup(self):
        self.check('var a=1;var b=2;fn* work(){try{[a,b]=yield ([20,22]);yield(match [a,b]{[x,y]=>x+y})}finally{a=99}};let g=work();let first=next(g);let before=[a,b];let second=send(g,[40,2]);close(g);[first,before,second,a,b]',[{'done':False,'value':[20,22]},[1,2],{'done':False,'value':42},99,2])
        self.check('fn* work(){match 42{x if yield true=>x;_=>0}};let g=work();let first=next(g);close(g);first',{'done':False,'value':True})

    def test_original_step_cancel_limits_and_error_debug_spans_remain_exact(self):
        source='var [a,b]=[1,2];[a,b]=[20,22];match [a,b]{[x,y] if x>0=>x+y}'
        baseline=self.check(source,42)
        for limit in range(baseline[1]+1):
            with self.subTest(limit=limit):self.assertEqual(snapshot(source,limit=limit),snapshot(source,legacy=True,limit=limit))
        for cancel in (1,5,10,baseline[1]):
            with self.subTest(cancel=cancel):self.assertEqual(snapshot(source,cancel=cancel),snapshot(source,legacy=True,cancel=cancel))
        self.check('match [1]{[a,b]=>42}')

    def test_pure_matcher_and_atomic_binding_helpers_are_original_algorithms(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        for key,original in PURE_ORIGINALS.items():
            filename,name=key.split(':');source=(root/filename).read_text();node=next(n for n in ast.walk(ast.parse(source))if isinstance(n,ast.FunctionDef)and n.name==name)
            with self.subTest(helper=key):self.assertEqual(ast.dump(ast.parse(ast.get_source_segment(source,node)),include_attributes=False),ast.dump(ast.parse(original),include_attributes=False))

    def test_pending_frame_body_and_scope_are_authoritative_future_operands(self):
        from chaos.suspension import Effect,CHECKPOINT
        e=Evaluator(tool_handler=lambda name,value:value)
        node=Parser('match 40{x if tool("wait",true)=>0;_=>-1}').program()[1][0]
        with active(e,None):
            cursor=ensure_cursor(e,e._s_execute(node,e.env));self.assertIsInstance(pending(cursor),Effect)
            frame=owned_frame(self,cursor,PatternSyntaxFrame);self.assertEqual(frame.phase,'guard')
            frame.body=Parser('x+2').program()[1][0];frame.scope.values['x']=40
            try:
                yielded=cursor.send(True)
                while yielded is CHECKPOINT:yielded=cursor.send(None)
            except StopIteration as stop:self.assertEqual(stop.value,42)
            else:self.fail('chosen body must complete after actual guard resume')
            self.assertTrue(cursor.closed)

    def test_pending_close_and_cold_throw_do_not_commit_or_replay(self):
        from chaos.suspension import Effect
        sources={'match':'match tool("wait",42){x=>x}',
                 'let_pattern':'let [a]=tool("wait",[42])',
                 'var_pattern':'var [a]=tool("wait",[42])',
                 'assign_pattern':'[a]=tool("wait",[42])'}
        for kind,source in sources.items():
            results=[]
            for legacy in (False,True):
                e=Evaluator(tool_handler=lambda name,value:value);e.env.bind('a',Cell(1,'Int')) if kind=='assign_pattern' else None
                node=Parser(source).program()[1][0];held=[]
                with active(e,None),reference(retained=held)if legacy else nullcontext()as reached,observe_pattern_entries()as steps:
                    cold=ensure_cursor(e,e._s_execute(node,e.env));failure=LanguageError('cold-pattern',code='fixture_cold')
                    with self.assertRaises(LanguageError)as caught:cold.throw(failure)
                    self.assertIs(caught.exception,failure);self.assertEqual(e.steps,0);self.assertEqual(steps,{})
                    cursor=ensure_cursor(e,e._s_execute(node,e.env));self.assertIsInstance(pending(cursor),Effect)
                    if legacy:self.assertGreater(reached[kind],0);self.assertEqual(steps,{})
                    else:self.assertGreater(steps.get('PatternSyntaxFrame',0),0)
                    before=e.steps;cursor.close();cursor.close();self.assertEqual(e.steps,before)
                    self.assertTrue(cursor.closed);self.assertEqual(cursor.frames,[])
                    self.assertEqual(e.env.values.get('a').value if kind=='assign_pattern' else e.env.values.get('a'),1 if kind=='assign_pattern' else None)
                    results.append((e.steps,e.depth,len(e.frames),'a'in e.env.values))
                    if legacy:
                        self.assertTrue(held);self.assertTrue(all(iterator.gi_code is code for _,iterator,code in held))
            self.assertEqual(results[0],results[1])

    def test_host_stopiteration_and_generatorexit_keep_original_native_boundary(self):
        sources={'match':'match 42{x=>x}','let_pattern':'let [a]=[42]','var_pattern':'var [a]=[42]','assign_pattern':'[a]=[42]'}
        retained=[]
        for kind,source in sources.items():
            for error_type in (StopIteration,GeneratorExit):
                outcomes=[]
                for legacy in (False,True):
                    e=Evaluator();e.env.bind('a',Cell(1,'Int')) if kind=='assign_pattern' else None
                    sentinel=error_type(object());held=[];node=Parser(source).program()[1][0]
                    with active(e,None),reference(retained=held)if legacy else nullcontext()as reached,observe_pattern_entries()as steps,patch.object(e,'pattern_match',side_effect=sentinel):
                        cursor=ensure_cursor(e,e._s_execute(node,e.env))
                        with self.assertRaises(RuntimeError if error_type is StopIteration else GeneratorExit)as caught:pending(cursor)
                        error=caught.exception
                        if error_type is StopIteration:self.assertIs(error.__cause__,sentinel)
                        else:self.assertIs(error,sentinel)
                        if legacy:self.assertGreater(reached[kind],0);self.assertEqual(steps,{})
                        else:self.assertGreater(steps.get('PatternSyntaxFrame',0),0)
                        outcomes.append((type(error).__name__,e.steps,e.depth,len(e.frames),'a'in e.env.values))
                        retained.append((cursor,held,error,error.__traceback__,sentinel))
                self.assertEqual(outcomes[0],outcomes[1])

    def test_narrow_guard_cannot_widen_effects_or_narrow_unrelated_caller_alias(self):
        def setup(e):e.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
        self.check('fn saved(){tool("secret",42)};fn guard(x){saved();true};[try{limited(fn(){match 42{x if guard(x)=>x}})}catch e{e.code},saved()]',['capability_error',42],setup=setup)

    def test_real_pending_frame_rejects_foreign_epoch_and_metadata_without_host_introspection(self):
        from chaos.capture_graph import seal
        from chaos.tuple_values import TupleValue
        from chaos.suspension import Effect
        class Opaque:
            @property
            def dangerous(self):raise AssertionError('opaque host introspection')
        for mode in ('opaque','foreign','epoch','phase','index','tail','all_results'):
            e=Evaluator(tool_handler=lambda n,v:v);node=Parser('match 42{x if tool("wait",true)=>x;_=>0}').program()[1][0]
            with self.subTest(mode=mode),active(e,None):
                cursor=ensure_cursor(e,e._s_execute(node,e.env));self.assertIsInstance(pending(cursor),Effect);frame=owned_frame(self,cursor,PatternSyntaxFrame)
                if mode=='opaque':frame.value=Opaque();seal(e,(cursor,),None)
                else:
                    if mode=='foreign':frame.value=TupleValue((Native('foreign',lambda:None,Evaluator()),))
                    elif mode=='epoch':frame.epoch+=1
                    elif mode=='phase':frame.phase=Native('bad',lambda:None,Evaluator())
                    elif mode=='index':frame.index=True
                    elif mode=='tail':frame.tail=1
                    elif mode=='all_results':frame.all_results=1
                    with self.assertRaises(LanguageError)as caught:seal(e,(cursor,),None)
                    self.assertEqual(caught.exception.code,'ownership_error')
                cursor.close()

        # Valid case tables are syntax metadata, not new language Lists. The
        # first guard is genuinely pending; neither implementation visits later
        # cases or imposes a new10000-item capture limit on their representation.
        large=[]
        for legacy in (False,True):
            e=Evaluator(tool_handler=lambda n,v:v)
            node=Parser('match 42{x if tool("wait",true)=>x;_=>0}').program()[1][0]
            node[2][:]=[node[2][0]]*10001
            with active(e,None),reference()if legacy else nullcontext()as reached,observe_pattern_entries()as steps:
                cursor=ensure_cursor(e,e._s_execute(node,e.env));self.assertIsInstance(pending(cursor),Effect)
                before=e.steps;seal(e,(cursor,),None);large.append(e.steps-before)
                if legacy:self.assertGreater(reached['match'],0);self.assertEqual(steps,{})
                else:self.assertEqual(len(owned_frame(self,cursor,PatternSyntaxFrame).cases),10001)
                cursor.close()
        self.assertEqual(large[0],large[1])

    def test_capture_failure_after_real_narrowing_candidate_never_commits_next_alias(self):
        from chaos import generic_functions
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        from chaos.tuple_values import TupleValue
        from chaos.suspension import Effect
        e=Evaluator(tool_handler=lambda n,v:v);token=NextDescriptor(e,None);token.capabilities=frozenset({'read'})
        wide=Native('next',lambda:None,e,generic=token,captures=());foreign=Native('foreign',lambda:None,Evaluator())
        node=Parser('match 42{x if tool("wait",true)=>x;_=>0}').program()[1][0]
        observed=[];original=generic_functions.intersection
        def intersection(left,right):
            if right is token.capabilities:observed.append(left)
            return original(left,right)
        with active(e,None):
            cursor=ensure_cursor(e,e._s_execute(node,e.env));self.assertIsInstance(pending(cursor),Effect);frame=owned_frame(self,cursor,PatternSyntaxFrame)
            # The original DFS pops the last Tuple item first: the real next
            # candidate is examined before the later foreign-owner rejection.
            frame.value=TupleValue((foreign,wide))
            with patch.object(generic_functions,'intersection',intersection),self.assertRaises(LanguageError)as caught:seal(e,(cursor,),frozenset())
            self.assertEqual(caught.exception.code,'ownership_error');self.assertTrue(observed);self.assertIn(frozenset(),observed)
            self.assertIsNone(wide.capabilities);self.assertEqual(token.capabilities,frozenset({'read'}));cursor.close()
