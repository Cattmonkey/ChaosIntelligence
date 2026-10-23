"""Whole module-state algorithms against exact original native/eager paths."""
import ast,copy,json,textwrap
from contextlib import ExitStack,contextmanager,nullcontext
from pathlib import Path
import unittest
from unittest.mock import patch
from chaos import Framework
from chaos.language import Evaluator,Parser,Env,Cell,Native,LanguageError,ResourceLimit,ExecutionControl,serializable
from chaos.modules import ProgramBundle
from chaos import module_values,program_values
from chaos.execution_frames import ensure_cursor,ExecutionCursor
from chaos.execution_modules import (MODULE_FRAMES,ModuleLoadFrame,ModuleImportFrame,ModuleConstructFrame,
 LegacyModuleLoadFrame,ModuleSyntaxFrame,ModuleExportFrame)
from _module_reference198 import reference,TARGETS,PURE_ORIGINALS
from _effect_schedule158 import effect_schedule
from test_callable_execution_frames import pending,owned_frame
from test_operator_execution_frames import active
ROOT=Path(__file__).resolve().parents[1]
def definition(files,entry='entry.chaos'):
    return {'kind':'program','entry':entry,'files':dict(files),'languageVersions':{name:2 for name in files}}
def literal(files,entry='entry.chaos'):
    return 'program_from('+json.dumps(definition(files,entry),ensure_ascii=False)+')'
FILES={'entry.chaos':'import "left.chaos" as A;import "right.chaos" as B;import "std/math" as M;let answer=M.abs(A.value+B.value);fn solve(x){answer+x};export{answer,solve}',
       'left.chaos':'import "shared.chaos" as S;let value=S.seed;export{value}',
       'right.chaos':'import "shared.chaos" as S;let value=S.seed;export{value}',
       'shared.chaos':'let seed=21;export{seed}'}
WITNESS='let library=module_from('+literal(FILES)+');[library.answer,library.solve(0)]'
WITNESS_EXPECTED=[42,42]
@contextmanager
def observe_module_entries():
    counts={}
    with ExitStack()as stack:
        for cls in MODULE_FRAMES:
            original=cls.step
            def step(frame,cursor,value,error,_original=original):
                name=type(frame).__name__;counts[name]=counts.get(name,0)+1
                return _original(frame,cursor,value,error)
            stack.enter_context(patch.object(cls,'step',step))
        yield counts
def snapshot(source,legacy=False,limit=30000,cancel=None,setup=None,effect_policy='already_done',inputs=None,files=None,entry='main.chaos',export=None):
    effects=[];trace=[];debug=[]
    def effect(kind,payload,event):
        effects.append((kind,copy.deepcopy(payload)))
        return payload['value']if kind=='tool'else payload.get('input')
    e=Evaluator(step_limit=limit,timeout=30,tool_handler=lambda n,v:v,effect_handler=effect,trace_handler=trace.append)
    e.cancelled=lambda:cancel is not None and e.steps>=cancel
    e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
    if setup:setup(e)
    with effect_schedule(effect_policy)as schedule,reference()if legacy else nullcontext():
        try:
            result=e.run_export(ProgramBundle(source,files,entry),entry,export,40)if export else e.run(source,inputs,files=files,entry=entry)
            result=('value',serializable(result))
        except LanguageError as error:result=('error',error.record())
        except ExecutionControl as error:result=('control',type(error).__name__,str(error))
    return result,e.steps,effects,trace,debug,e.depth,len(e.frames),dict(schedule)
def scope(e,files,bindings=None):
    value=program_values.builtins(e)['program_from'](definition(files))
    return module_values.Scope(e,module_values.request(e,value,{}if bindings is None else bindings))

class ModuleExecutionFrames(unittest.TestCase):
    def check(self,source,expected=None,**options):
        old=snapshot(source,legacy=True,**options);modern=snapshot(source,**options)
        self.assertEqual(modern,old)
        if expected is not None:self.assertEqual(modern[0],('value',expected))
        return modern

    def test_all_original_six_responsibilities_have_real_witnesses_and_old_mode_has_no_new_steps(self):
        records=[]
        for legacy in (True,False):
            retained=[]
            with reference(retained)if legacy else nullcontext()as reached,observe_module_entries()as seen:
                e=Evaluator();self.assertEqual(e.run(WITNESS),WITNESS_EXPECTED)
                self.assertEqual(e.run_export(ProgramBundle('fn solve(x){process_step(x,x+2,true)};export{solve}'), 'main.chaos','solve',40),{'state':40,'output':42,'done':True})
                if legacy:
                    self.assertTrue(all(reached.values()),reached);self.assertEqual(seen,{})
                    self.assertTrue(retained);self.assertTrue(all(iterator.gi_code is code for _,iterator,code in retained))
                else:self.assertEqual(set(seen),{cls.__name__ for cls in MODULE_FRAMES});self.assertTrue(all(seen.values()))
                records.append((e.depth,len(e.frames)))
        self.assertEqual(records,[(0,0),(0,0)])

    def test_source_scope_diamond_standard_imports_and_instances_match_original(self):
        self.check(WITNESS,WITNESS_EXPECTED)
        files={'entry.chaos':'import "left.chaos" as A;import "right.chaos" as B;[A.value,B.value];let total=A.value+B.value;export{total}',
               'left.chaos':'import "shared.chaos" as S;let value=S.value;export{value}',
               'right.chaos':'import "shared.chaos" as S;let value=S.value;export{value}',
               'shared.chaos':'let value=tool("loaded",21);export{value}'}
        text='let p='+literal(files)+';async fn work(){let a=module_from(p);let b=module_from(p);[a.total,b.total]};await work()'
        result=self.check(text,[42,42],effect_policy='first_wait')
        self.assertEqual([row[1]['name']for row in result[2]],['loaded','loaded']);self.assertEqual(result[-1]['waitBoundaryReleases'],2)

    def test_explicit_bindings_keep_callable_class_identity_and_independent_cells(self):
        files={'entry.chaos':'var n=seed;fn next(){n+=1;n};fn same(x){callback(x)};let Kind=kind;export{next,same,Kind}'}
        self.check('class Kind{};fn saved(x){x};let p='+literal(files)+';let a=module_from(p,{seed:40,callback:saved,kind:Kind});let b=module_from(p,{seed:1,callback:saved,kind:Kind});[a.next(),a.next(),b.next(),a.same(42),a.Kind==Kind]',[41,42,2,42,True])

    def test_cache_hit_returns_same_record_before_loading_and_missing_checks(self):
        outcomes=[]
        for legacy in (True,False):
            e=Evaluator();s=scope(e,{'entry.chaos':'let answer=42;export{answer}'})
            with reference()if legacy else nullcontext():
                first=e.suspension.drive(s.load('entry.chaos'));before=e.steps;s.loading.add('entry.chaos');del s.bundle.trees['entry.chaos']
                second=e.suspension.drive(s.load('entry.chaos'));self.assertIs(first,second);self.assertEqual(second,{'answer':42})
                outcomes.append((e.steps-before,len(e.frames),dict(s.cache)))
        self.assertEqual(outcomes[0],outcomes[1])

    def test_failed_initializer_does_not_commit_itself_but_keeps_completed_dependencies(self):
        outcomes=[]
        files={'entry.chaos':'import "ok.chaos" as O;tool("failing",O.answer);throw "bad";let unreachable=0;export{unreachable}', 'ok.chaos':'let answer=tool("dependency",42);export{answer}'}
        for legacy in (True,False):
            calls=[];e=Evaluator(tool_handler=lambda n,v:(calls.append(n),v)[1]);s=scope(e,files)
            with reference()if legacy else nullcontext():
                errors=[]
                for attempt in range(2):
                    with self.assertRaises(LanguageError)as caught:e.suspension.drive(s.load('entry.chaos'))
                    errors.append((caught.exception.code,caught.exception.message))
                    self.assertEqual(s.loading,set());self.assertEqual(e.frames,[]);self.assertNotIn('entry.chaos',s.cache)
                self.assertEqual(set(s.cache),{'ok.chaos'});self.assertEqual(calls,['dependency','failing','failing'])
                outcomes.append((errors,e.steps,calls,e.depth))
        self.assertEqual(outcomes[0],outcomes[1])

    def test_cycle_missing_and_import_target_errors_preserve_original_order(self):
        for name in ('entry.chaos','missing.chaos'):
            rows=[]
            for legacy in (True,False):
                e=Evaluator();s=scope(e,{'entry.chaos':'42'});s.loading.add(name)
                with reference()if legacy else nullcontext():
                    with self.assertRaises(LanguageError)as caught:e.suspension.drive(s.load(name))
                    rows.append((caught.exception.code,caught.exception.message,e.steps,sorted(s.loading),len(e.frames)))
            self.assertEqual(rows[0],rows[1])
        self.check('module_from('+literal({'entry.chaos':'import "missing.chaos" as M;42'})+')')

    def test_generated_source_preflight_and_orphan_errors_precede_all_effects(self):
        files={'entry.chaos':'tool("never",1);let answer=42;export{answer}','orphan.chaos':'let ='}
        result=self.check('module_from('+literal(files)+')');self.assertEqual(result[2],[])
        cyclic={'entry.chaos':'import "other.chaos" as O;tool("never",1)','other.chaos':'import "entry.chaos" as E;42'}
        result=self.check('module_from('+literal(cyclic)+')');self.assertEqual(result[2],[])

    def test_defaults_initialize_once_and_exported_cold_handles_remain_cold(self):
        files={'entry.chaos':'fn f(x=tool("default",42)){x};async fn cold(){tool("cold",0)};fn* values(){tool("stream",0);yield 42};let task=cold();let stream=values();export{f,task,stream}'}
        result=self.check('async fn main(){let m=module_from('+literal(files)+');[m.f(),m.f(),type_of(m.task),type_of(m.stream)]};await main()',[42,42,'Task','Generator'],effect_policy='first_wait')
        self.assertEqual([row[1]['name']for row in result[2]],['default'])

    def test_alias_bind_failure_keeps_already_completed_module_cache(self):
        outcomes=[]
        for legacy in (True,False):
            e=Evaluator();bundle=ProgramBundle('import "lib.chaos" as L;42',{'lib.chaos':'let answer=42;export{answer}'})
            e.bundle=bundle;e.module_cache={};e.module_results={};e.module_bindings={};env=Env(e.env);env.bind('L',0)
            node=bundle.expanded_trees[bundle.entry][1][0]
            with reference()if legacy else nullcontext():
                with self.assertRaises(LanguageError)as caught:e.suspension.drive(e._s_execute(node,env))
                self.assertEqual(e.module_cache['lib.chaos'],{'answer':42});self.assertEqual(env.get('L'),0)
                outcomes.append((caught.exception.code,e.steps,e.module_results['lib.chaos'],len(e.frames)))
        self.assertEqual(outcomes[0],outcomes[1])

    def test_legacy_loader_keeps_two_real_runner_boundaries_per_statement(self):
        rows=[]
        for legacy in (True,False):
            e=Evaluator();e.bundle=ProgramBundle('let answer=40;answer+2');e.module_cache={};e.module_results={};e.module_bindings={};calls=[]
            evaluate=e.evaluate;resolve=e.resolve
            def evaluated(*args,**kwargs):calls.append(('evaluate',args[0][0]));return evaluate(*args,**kwargs)
            def resolved(*args,**kwargs):calls.append(('resolve',type(args[0]).__name__));return resolve(*args,**kwargs)
            with reference()if legacy else nullcontext(),patch.object(e,'evaluate',evaluated),patch.object(e,'resolve',resolved):
                self.assertEqual(e.load_module('main.chaos'),{});self.assertEqual(e.module_results['main.chaos'],42)
            self.assertEqual([kind for kind,_ in calls],['evaluate','resolve','evaluate','resolve']);rows.append((calls,e.steps,e.depth,len(e.frames)))
        self.assertEqual(rows[0],rows[1])

    def test_export_entry_sync_async_and_multiple_values_match_original(self):
        for source in ['fn solve(x){process_step(x,x+2,true)};export{solve}','async fn solve(x){await sleep(0);process_step(x,x+2,true)};export{solve}','fn solve(x){return_values(process_step(x,x+2,true),99)};export{solve}']:
            with self.subTest(source=source):self.check(source,{'state':40,'output':42,'done':True},export='solve',effect_policy='first_wait')

    def test_runtime_import_export_trace_and_errors_keep_source_spans(self):
        self.check('import "./lib.chaos" as A;import "lib.chaos" as B;[A.answer,B.answer]',[42,42],files={'lib.chaos':'let answer=42;export{answer}'})
        self.check('import "lib.chaos" as L;L.fail()',files={'lib.chaos':'fn fail(){1+"bad"};export{fail}'})

    def test_retained_exported_function_restores_outer_module_program_context(self):
        files={'entry.chaos':'import "lib.chaos" as L;let f=L.f;export{f}','lib.chaos':'fn f(){program_info(current_program()).entry};export{f}'}
        self.check('let m=module_from('+literal(files)+');[m.f(),program_info(current_program()).entry]',['entry.chaos','main.chaos'])

    def test_narrow_initializer_cannot_widen_or_narrow_unrelated_caller_alias(self):
        from chaos.language import TailCall
        def setup(e):e.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
        files={'entry.chaos':'let answer=callback();export{answer}'}
        self.check('fn saved(){tool("secret",42)};let p='+literal(files)+';[try{limited(fn(){module_from(p,{callback:saved})})}catch e{e.code},saved()]',['capability_error',42],setup=setup)

    def test_step_and_cancellation_boundaries_keep_original_exact_result_effects_and_cleanup(self):
        source='module_from('+literal({'entry.chaos':'let answer=40+2;export{answer}'})+').answer'
        normal=self.check(source,42)
        for limit in (1,2,5,10,normal[1]-1,normal[1]):
            with self.subTest(limit=limit):self.assertEqual(snapshot(source,limit=limit),snapshot(source,legacy=True,limit=limit))
        for cancel in (1,5,normal[1]-1):self.assertEqual(snapshot(source,cancel=cancel),snapshot(source,legacy=True,cancel=cancel))

    def test_request_and_scope_preparation_algorithms_remain_original_pure_bytes(self):
        for key,expected in PURE_ORIGINALS.items():
            path,name=key.split(':');text=(ROOT/path).read_text();tree=ast.parse(text);parts=name.split('.')
            parent=next(n for n in tree.body if isinstance(n,ast.ClassDef)and n.name==parts[0])if len(parts)>1 else tree
            node=next(n for n in parent.body if isinstance(n,ast.FunctionDef)and n.name==parts[-1])
            actual=textwrap.dedent(''.join(text.splitlines(keepends=True)[node.lineno-1:node.end_lineno]))
            self.assertEqual(actual,expected)

    def test_real_pending_environment_is_authoritative_for_later_exports(self):
        from chaos.suspension import Effect
        e=Evaluator(tool_handler=lambda n,v:v);s=scope(e,{'entry.chaos':'let answer=tool("hold",42);export{answer}'})
        with active(e,None):
            cursor=ensure_cursor(e,s.load('entry.chaos'));self.assertIsInstance(pending(cursor),Effect)
            frame=owned_frame(self,cursor,ModuleLoadFrame);self.assertEqual(frame.phase,'evaluate');self.assertIs(frame.scope,s)
            original=frame.environment;replacement=Env(e.env);replacement.bind('answer',99);frame.environment=replacement
            try:
                request=cursor.send(42)
            except StopIteration as completed:result=completed.value
            else:
                from chaos.suspension import CHECKPOINT
                self.assertIs(request,CHECKPOINT);result=e.suspension.drive(cursor)
            self.assertEqual(result,{'answer':99});self.assertIs(s.cache['entry.chaos'],result);self.assertEqual(original.get('answer'),42)
            self.assertEqual(s.loading,set());self.assertEqual(e.frames,[])

    def test_pending_module_live_roots_reject_foreign_epoch_and_metadata(self):
        from chaos.capture_graph import seal
        from chaos.suspension import Effect
        from chaos.tuple_values import TupleValue
        for mode in ('foreign','scope-binding','epoch','phase','index','capabilities'):
            e=Evaluator(tool_handler=lambda n,v:v);s=scope(e,{'entry.chaos':'let answer=tool("hold",42);export{answer}'})
            with self.subTest(mode=mode),active(e,None):
                cursor=ensure_cursor(e,s.load('entry.chaos'));self.assertIsInstance(pending(cursor),Effect);frame=owned_frame(self,cursor,ModuleLoadFrame)
                foreign=Native('foreign',lambda:None,Evaluator())
                saved=(frame.value,s.bindings,frame.epoch,frame.phase,frame.index,frame.capabilities)
                if mode=='foreign':frame.value=TupleValue((foreign,))
                elif mode=='scope-binding':s.bindings=(('foreign',foreign),)
                elif mode=='epoch':frame.epoch+=1
                elif mode=='phase':frame.phase=foreign
                elif mode=='index':frame.index=True
                else:frame.capabilities=frozenset((42,))
                try:
                    with self.assertRaises(LanguageError)as caught:seal(e,(cursor,),None)
                    self.assertEqual(caught.exception.code,'ownership_error')
                finally:
                    frame.value,s.bindings,frame.epoch,frame.phase,frame.index,frame.capabilities=saved
                    cursor.close()
                self.assertEqual(s.loading,set());self.assertEqual(e.frames,[])

    def test_fresh_pending_value_graph_keeps_node_and_depth_budgets_and_opaque_host_boundary(self):
        from chaos.capture_graph import seal
        from chaos.suspension import Effect
        from chaos.tuple_values import TupleValue
        class Opaque:
            @property
            def dangerous(self):raise AssertionError('must not inspect opaque host values')
        for mode in ('opaque','depth','nodes'):
            e=Evaluator(tool_handler=lambda n,v:v);s=scope(e,{'entry.chaos':'let answer=tool("hold",42);export{answer}'})
            with self.subTest(mode=mode),active(e,None):
                cursor=ensure_cursor(e,s.load('entry.chaos'));self.assertIsInstance(pending(cursor),Effect);frame=owned_frame(self,cursor,ModuleLoadFrame)
                if mode=='opaque':frame.value=Opaque();seal(e,(cursor,),None)
                else:
                    if mode=='depth':
                        value=42
                        for _ in range(102):value=TupleValue((value,))
                    else:value=TupleValue(tuple(TupleValue((i,))for i in range(10000)))
                    frame.value=value
                    with self.assertRaises(ResourceLimit):seal(e,(cursor,),None)
                cursor.close();self.assertEqual(s.loading,set());self.assertEqual(e.frames,[])
