"""Native module frontiers use actual old algorithms on each interpreter."""
import copy
from contextlib import ExitStack,nullcontext
import unittest
from unittest.mock import patch
from chaos.language import Evaluator,Parser,Env,LanguageError
from chaos import module_values
from chaos.suspension import CHECKPOINT,Effect
from chaos.execution_frames import ExecutionCursor,EvaluationRequest,InvocationRequest,ProtectedValueRequest,gateway
from _module_reference198 import reference
from test_module_execution_frames import scope,observe_module_entries
from test_operator_execution_frames import active

class ModuleNativeOracle198(unittest.TestCase):
    def test_original_native_cold_creation_and_eager_legacy_call_timing(self):
        for legacy in (True,False):
            e=Evaluator();s=scope(e,{'entry.chaos':'42'});before=e.steps;retained=[]
            with reference(retained)if legacy else nullcontext()as reached,observe_module_entries()as steps:
                iterator=s.load('entry.chaos')
                self.assertEqual(e.steps,before);self.assertEqual(s.loading,set());self.assertEqual(e.frames,[]);self.assertEqual(steps,{})
                cold=RuntimeError('cold throw')
                with self.assertRaises(RuntimeError)as caught:iterator.throw(cold)
                self.assertIs(caught.exception,cold);self.assertEqual(e.steps,before);self.assertEqual(s.cache,{})
                if legacy:self.assertEqual(reached['scope.load'],0)
                self.assertEqual(steps,{})
                from chaos.modules import ProgramBundle
                e.bundle=ProgramBundle('42');e.module_cache={};e.module_results={};e.module_bindings={}
                eager_before=e.steps;self.assertEqual(e.load_module('main.chaos'),{})
                self.assertEqual(e.module_results['main.chaos'],42);self.assertGreater(e.steps,eager_before)
                if legacy:self.assertGreater(reached['legacy.load'],0)
                else:self.assertGreater(steps.get('LegacyModuleLoadFrame',0),0)

    def test_plain_host_stopiteration_and_generatorexit_remain_original_native_errors(self):
        retained=[]
        for failure_type in (StopIteration,GeneratorExit):
            rows=[];payload=object()
            for legacy in (True,False):
                e=Evaluator();s=scope(e,{'entry.chaos':'42'});fault=failure_type(payload);held=[]
                def evaluate(*args,**kwargs):raise fault
                with active(e,None),reference(held)if legacy else nullcontext()as reached,observe_module_entries()as steps,patch.object(e,'_s_evaluate',evaluate):
                    iterator=s.load('entry.chaos');native=iterator.iterator if legacy else iterator;retained.append((iterator,native,held,fault))
                    with self.assertRaises(RuntimeError if failure_type is StopIteration else GeneratorExit)as caught:next(iterator)
                    error=caught.exception;retained.append((error,error.__traceback__))
                    if failure_type is StopIteration:self.assertIs(error.__cause__,fault)
                    else:self.assertIs(error,fault)
                    self.assertEqual(s.loading,set());self.assertEqual(s.cache,{});self.assertEqual(e.frames,[])
                    if legacy:self.assertGreater(reached['scope.load'],0);self.assertEqual(steps,{})
                    else:self.assertGreater(steps.get('ModuleLoadFrame',0),0)
                    rows.append((type(error).__name__,str(error),e.steps,e.depth,len(e.frames)))
            self.assertEqual(rows[1],rows[0])

        # A synchronous legacy coordinator has no native-generator PEP479
        # boundary: ordinary host StopIteration must stay that very exception.
        from chaos.modules import ProgramBundle
        for failure_type in (StopIteration,GeneratorExit):
            payload=object();rows=[]
            for legacy in (True,False):
                e=Evaluator();e.bundle=ProgramBundle('42');e.module_cache={};e.module_results={};e.module_bindings={};fault=failure_type(payload)
                with reference()if legacy else nullcontext()as reached,observe_module_entries()as steps,patch.object(e,'evaluate',side_effect=fault):
                    with self.assertRaises(failure_type)as caught:e.load_module('main.chaos')
                    self.assertIs(caught.exception,fault);self.assertEqual(e.module_cache,{})
                    if legacy:self.assertGreater(reached['legacy.load'],0);self.assertEqual(steps,{})
                    else:self.assertGreater(steps.get('LegacyModuleLoadFrame',0),0)
                    rows.append((type(caught.exception).__name__,str(caught.exception),e.steps,e.depth,len(e.frames)))
            self.assertEqual(rows[1],rows[0])

    def test_native_child_close_completion_second_close_send_throw_and_cache_cleanup_match_original(self):
        retained=[]
        for operation in ('load','resolve','imported','construct','syntax','export'):
            for failure_kind in ('stop','generator_exit','runtime'):
                for resume in ('close','send','throw'):
                    with self.subTest(operation=operation,failure=failure_kind,resume=resume):
                        outcomes=[]
                        for legacy in (True,False):
                            e=Evaluator();s=scope(e,{'entry.chaos':'42','dep.chaos':'let answer=42;export{answer}'})
                            # The static bundle does not claim this orphan is a dependency.
                            # This trusted direct-protocol fixture supplies that exact edge.
                            s.bundle.dependencies['entry.chaos']={'dep.chaos':'dep.chaos'}
                            node=Parser('import "dep.chaos" as M').program()[1][0];node._module_scope=s;node._module_file='entry.chaos'
                            environment=Env(e.env);events=[];held=[];children=[];errors=[]
                            completion={'answer':42}if operation in ('imported','construct','syntax')else 42
                            cause=ValueError('original explicit cause');fault=StopIteration(completion)if failure_kind=='stop'else GeneratorExit()if failure_kind=='generator_exit'else RuntimeError('original raw close')
                            if failure_kind=='runtime':fault.__cause__=cause
                            resume_error=LanguageError('original resume',code='module_resume_test')
                            class RawChild:
                                def __init__(self,rows,error):self.rows=rows;self.error=error
                                def __iter__(self):return self
                                def __next__(self):return self.send(None)
                                def send(self,value):self.rows.append(('send',value));return Effect('tool',{'name':'module-raw-pending','value':42})
                                def throw(self,error):raise error
                                def close(self):self.rows.append(('close',type(self.error).__name__));raise self.error
                            raw=RawChild(events,fault)
                            with active(e,None),reference(held)if legacy else nullcontext()as reached,observe_module_entries()as steps,ExitStack()as stack:
                                if operation=='load':stack.enter_context(patch.object(e,'_s_evaluate',lambda *a,**kw:raw));iterator=s.load('entry.chaos');target='scope.load'
                                elif operation=='resolve':stack.enter_context(patch.object(e,'_s_resolve',lambda *a,**kw:raw));iterator=s.load('entry.chaos');target='scope.load'
                                elif operation=='imported':stack.enter_context(patch.object(module_values.Scope,'load',lambda *a,**kw:raw));iterator=s.imported(node);target='scope.imported'
                                elif operation=='construct':
                                    stack.enter_context(patch.object(module_values.Scope,'load',lambda *a,**kw:raw));iterator=module_values.construct(e,module_values.request(e,__import__('chaos.program_values',fromlist=['builtins']).builtins(e)['program_from']({'kind':'program','entry':'entry.chaos','files':{'entry.chaos':'42'},'languageVersions':{'entry.chaos':2}}),{}));target='construct'
                                elif operation=='syntax':stack.enter_context(patch.object(module_values.Scope,'imported',lambda *a,**kw:raw));iterator=e._s_execute(node,environment);target='syntax.import'
                                else:stack.enter_context(patch.object(e,'_s_invoke',lambda *a,**kw:raw));iterator=e._s_export(None,40);target='export.run'
                                native=iterator.iterator if legacy else iterator
                                retained.append((iterator,native,raw,held,errors,children,fault,cause,s))
                                def advance(actual,sent=None):
                                    for _ in range(4096):
                                        try:request=actual.send(sent)
                                        except StopIteration as done:return True,done.value
                                        if request is CHECKPOINT:sent=None;continue
                                        if type(request)in(EvaluationRequest,InvocationRequest,ProtectedValueRequest):
                                            child=gateway(e,request);children.append(child);self.assertIsInstance(child,ExecutionCursor)
                                            done,value=advance(child);self.assertTrue(done,'pre-target gateway must complete');sent=value;continue
                                        return False,request
                                    self.fail('bounded actual gateway did not reach selected raw child')
                                done,request=advance(iterator);self.assertFalse(done);self.assertIsInstance(request,Effect);self.assertEqual(request.payload['name'],'module-raw-pending')
                                if legacy:self.assertGreater(reached[target],0);self.assertEqual(steps,{});self.assertTrue(any(old is native and old.gi_code is code for _,old,code in held))
                                else:self.assertGreater(steps.get({'load':'ModuleLoadFrame','resolve':'ModuleLoadFrame','imported':'ModuleImportFrame','construct':'ModuleConstructFrame','syntax':'ModuleSyntaxFrame','export':'ModuleExportFrame'}[operation],0),0)
                                observations=[]
                                for index in range(2):
                                    error=None;returned=None
                                    try:
                                        if index==0 or resume=='close':returned=iterator.close()
                                        elif resume=='send':returned=iterator.send(42)
                                        else:returned=iterator.throw(resume_error)
                                    except BaseException as caught:error=caught;errors.append((caught,caught.__traceback__))
                                    actual_cause=getattr(error,'__cause__',None)
                                    observations.append({'errorType':type(error).__name__ if error is not None else None,'errorMessage':str(error)if error is not None else None,'code':getattr(error,'code',None),'isOriginal':error is fault,'isResume':error is resume_error,'causeIsOriginal':actual_cause is fault,'causeIsExplicit':actual_cause is cause,'returned':copy.deepcopy(returned),'events':copy.deepcopy(events),'cache':copy.deepcopy(s.cache),'loading':sorted(s.loading),'boundM':copy.deepcopy(environment.values.get('M')),'steps':e.steps,'depth':e.depth,'frames':len(e.frames),'nativeClosed':native.gi_frame is None})
                                self.assertEqual(sum(row[0]=='close'for row in observations[0]['events']),1)
                                outcomes.append(observations)
                        self.assertEqual(outcomes[1],outcomes[0])
        for iterator,native,raw,held,errors,children,fault,cause,s in retained:
            if native.gi_frame is not None:
                try:iterator.close()
                except BaseException as error:errors.append((error,error.__traceback__))
