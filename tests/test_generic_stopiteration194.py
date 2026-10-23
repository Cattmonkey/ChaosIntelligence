"""PEP479 at the original generic algorithm boundary, including host scopes."""
from contextlib import nullcontext
import unittest
from unittest.mock import patch
from chaos import agent_tools,generic_functions as generic
from chaos.language import Evaluator,Native
from chaos.execution_frames import ensure_cursor
from chaos.execution_callables import GenericResolveFrame
from chaos.suspension import Effect,CHECKPOINT
from _callable_reference191 import reference
from test_operator_execution_frames import active
from test_callable_execution_frames import pending,observe_callable_entries,owned_frame


class GenericStopIteration194(unittest.TestCase):
    def test_host_stopiteration_keeps_original_pep479_cause_and_scope_opportunity_on_advance_and_close(self):
        cases=('select','primary','primary_phase','scope_enter','scope_exit',
               'close_primary','close_exit','suppressed_continuation')
        for case in cases:
            outcomes=[];retained=[]
            for legacy in (False,True):
                e=Evaluator(tool_handler=lambda name,value:value)
                marker=object();fault=StopIteration(marker);events=[];scopes=[];primary_calls=[];delegates=[]
                def primary(value):primary_calls.append(value);return 42
                # The new family is tested directly, while the callback still
                # uses the original invocation/Effect machinery.
                callback=Native('before',lambda value:Effect('tool',{'name':'wait','value':None})
                                if case.startswith('close_')else None,e)
                before=generic.Method(('Int',),callback,'before',None)
                main=generic.Method(('Int',),Native('primary',primary,e),'primary',None)
                around=generic.Method(('Int',),Native('around',lambda next,value:42,e),'around',None)
                combination=generic.Combination((main,),()if case=='suppressed_continuation'else(before,),(),
                                                (around,)if case=='suppressed_continuation'else())
                descriptor=generic.Descriptor(e,'pep479',1,(main,before,around))
                request=generic.GenericRequest(descriptor,(42,),{},None if case=='select'else combination,
                                               phase='primary'if case=='primary_phase'else'around')
                original_scope=agent_tools.scope
                class Scope:
                    def __init__(self,owner,ceiling):self.cm=original_scope(owner,ceiling);scopes.append(self)
                    def __enter__(self):
                        events.append(('enter',))
                        if case=='scope_enter':raise fault
                        return self.cm.__enter__()
                    def __exit__(self,kind,error,traceback):
                        events.append(('exit',None if kind is None else kind.__name__))
                        result=self.cm.__exit__(kind,error,traceback)
                        if case=='scope_exit'and kind is None:raise fault
                        if case=='close_exit'and kind is GeneratorExit:raise fault
                        if case=='close_primary'and kind is GeneratorExit:return True
                        if case=='suppressed_continuation'and error is fault:return True
                        return result
                def raise_fault(*args,**kwargs):raise fault
                hook=('select'if case=='select'else'continuation'if case=='suppressed_continuation'else
                      'primary_call'if case in('primary','primary_phase','close_primary')else None)
                with self.subTest(case=case,legacy=legacy),active(e,None)as runner, \
                        reference(retained=delegates)if legacy else nullcontext()as reached, \
                        observe_callable_entries()as steps,patch.object(agent_tools,'scope',Scope), \
                        patch.object(generic,hook,raise_fault)if hook else nullcontext():
                    previous=runner.capabilities
                    iterator=generic.resolve(e,request);cursor=ensure_cursor(e,iterator)
                    held={'iterator':iterator,'cursor':cursor,'scopes':scopes,'delegates':delegates,
                          'fault':fault,'caught':None,'traceback':None};retained.append(held)
                    if case.startswith('close_'):
                        waiting=pending(cursor);self.assertIs(type(waiting),Effect)
                        self.assertEqual(waiting.payload['name'],'wait')
                        if not legacy:self.assertEqual(owned_frame(self,cursor,GenericResolveFrame).phase,'before_return')
                    if case=='suppressed_continuation':
                        while True:
                            try:value=cursor.send(None)
                            except StopIteration as completed:
                                result=e.resolve(completed.value);break
                            self.assertIs(value,CHECKPOINT)
                        self.assertEqual(result,42)
                        self.assertEqual(primary_calls,[42])
                        self.assertIn(('exit','StopIteration'),events)
                        observation=('value',result)
                    else:
                        try:
                            if case.startswith('close_'):cursor.close()
                            else:
                                while True:
                                    value=cursor.send(None)
                                    self.assertIs(value,CHECKPOINT)
                        except RuntimeError as error:
                            held['caught']=error;held['traceback']=error.__traceback__
                            self.assertIs(error.__cause__,fault)
                            self.assertIs(error.__cause__.value,marker)
                            observation=('error',type(error).__name__,type(error.__cause__).__name__)
                        except StopIteration:
                            self.fail('host helper StopIteration became a successful generic completion')
                        else:self.fail('unsuppressed host StopIteration must raise RuntimeError at the original boundary')
                        self.assertEqual(primary_calls,[])
                    if legacy:
                        self.assertGreater(reached['generic.resolve'],0)
                        self.assertEqual(steps,{})
                        self.assertEqual(len(delegates),1)
                        self.assertIs(iterator.iterator,delegates[0][2])
                        self.assertIs(iterator.iterator.gi_code,delegates[0][3])
                    else:self.assertGreater(steps.get('GenericResolveFrame',0),0)
                    self.assertIs(runner.capabilities,previous)
                    outcomes.append((observation,list(events),e.steps,e.depth,len(e.frames)))
                    # Explicit retirement is outside the observed error boundary;
                    # preserve errors/tracebacks/delegates until both runs compare.
                    iterator.close();cursor.close()
            self.assertEqual(len(outcomes),2,case)
            if len(outcomes)==2:self.assertEqual(outcomes[0],outcomes[1],case)
            self.assertEqual(len(retained),2)
