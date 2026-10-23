"""Public Mode native frontiers retain native Python version behavior."""
import copy
import unittest
from contextlib import nullcontext
from chaos.language import Evaluator, LanguageError, Native
from chaos.suspension import Effect, Await
from chaos.task_groups import GroupRun, ScopeOutcome
import chaos.modes as modes
from test_operator_execution_frames import active
from _mode_reference224 import original_modes, fixed_execution_ids
from _mode_fixture224 import observe_frames

class ModeNativeFrontiers224(unittest.TestCase):
    def test_host_public_iterator_send_throw_and_second_close_preserve_exact_ticks(self):
        for second in ('send','throw','close'):
            with self.subTest(second=second):
                rows=[]
                for legacy in (True,False):
                    e=Evaluator(mode_handler=lambda op,payload:None);fault=LanguageError('host resume',code='mode_fixture');retained=[]
                    def observe(call):
                        try:return ('value',call())
                        except StopIteration as stop:return ('done',stop.value)
                        except BaseException as error:return ('error',type(error).__name__,str(error),error is fault,type(error.__cause__).__name__ if error.__cause__ else None)
                    with active(e,None),original_modes() if legacy else nullcontext(),observe_frames(self)as frames:
                        iterator=modes.host(e,'fixture.operation',{'value':42});retained.append(iterator)
                        before=e.steps;request=iterator.send(None)
                        self.assertIs(type(request),Effect);self.assertEqual(request.payload,{'operation':'fixture.operation','payload':{'value':42}})
                        after_first=e.steps-before
                        reply=observe(lambda:iterator.send({'saved':42}))if second=='send' else observe(lambda:iterator.throw(fault))if second=='throw' else observe(iterator.close)
                        again=observe(iterator.close);last=observe(lambda:iterator.send(99))
                        rows.append((after_first,e.steps-before,reply,again,last))
                        self.assertEqual(after_first,1)
                        if second=='send':self.assertEqual(reply,('done',{'saved':42}));self.assertEqual(e.steps-before,2)
                        if not legacy:self.assertTrue(any(type(frame).__name__=='ModeHostFrame' for frame,cursor in frames[0]))
                self.assertEqual(rows[1],rows[0])

    def test_actual_GroupRun_body_native_close_keeps_consumed_GeneratorExit_completion(self):
        for operation in ('close','throw'):
            with self.subTest(operation=operation):
                rows=[]
                for legacy in (True,False):
                    e=Evaluator();fault=GeneratorExit('body close');retained=[]
                    definition=modes.validate_definition({'kind':'plan','name':'body','max_concurrency':2,'steps':[{'id':'a','call':'work'},{'id':'b','call':'work'}]})
                    action=Native('work',lambda context:42,e)
                    metadata={'executionId':'body224','name':'body','definitionHash':modes.definition_hash(definition),'maxConcurrency':2}
                    def normal(value):
                        if type(value)is ScopeOutcome:return ('ScopeOutcome',normal(value.value),normal(value.signal),normal(value.error))
                        if isinstance(value,BaseException):return (type(value).__name__,str(value),value is fault)
                        return value
                    def invoke(call):
                        try:return ('value',normal(call()))
                        except StopIteration as stop:return ('done',normal(stop.value))
                        except BaseException as error:return ('error',normal(error))
                    with active(e,None)as parent_runner,original_modes() if legacy else nullcontext(),fixed_execution_ids(),observe_frames(self)as observed:
                        parent=modes._parallel_plan(e,definition,{'work':action},None,metadata);retained.append(parent)
                        group_request=parent.send(None);self.assertIs(type(group_request),GroupRun)
                        body=group_request.iterator;retained.append(body)
                        scheduler=e.suspension
                        scheduler._group_start(parent_runner,group_request)
                        body_task=group_request.group.body;body_runner=body_task.runner
                        body_cursor=body_runner.iterator;retained.extend((body_task,body_cursor))
                        self.assertIs(body_task.scope_group,group_request.group)
                        self.assertIsNot(body_cursor,parent_runner.iterator)
                        self.assertTrue(any(getattr(frame,'iterator',None)is body for frame in body_cursor.frames))
                        # Original GroupRun admission creates the actual body Task
                        # and active scope. Execute its real root cursor until the
                        # Await; retain the public native iterator for close tests.
                        scheduler.current=body_runner;scheduler._restore(body_runner)
                        try:
                            pending=body_cursor.send(None);self.assertIs(type(pending),Await)
                            self.assertEqual(len(group_request.group.members),2)
                            first=invoke(body.close)if operation=='close'else invoke(lambda:body.throw(fault))
                            second=invoke(body.close);third=invoke(lambda:body.send(42))
                            rows.append((e.steps,first,second,third))
                            if not legacy:self.assertTrue(any(type(frame).__name__=='ModeParallelBodyFrame' for frame,cursor in observed[0]))
                        finally:
                            scheduler._save(body_runner)
                            scheduler.current=parent_runner;scheduler._restore(parent_runner)
                            try:parent.close()
                            except BaseException:pass
                self.assertEqual(rows[1],rows[0])
