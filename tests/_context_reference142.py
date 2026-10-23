"""Historical lexical context algorithm used only as a differential oracle."""
from contextlib import contextmanager
from types import MethodType
from unittest.mock import patch
from chaos.contexts import acquire,exit_entry,hard_exit_entry,release
from chaos.language import Env,LanguageError,ResourceLimit,ExecutionControl,ReturnSignal,BreakSignal,ContinueSignal
from chaos.suspension import GeneratorClose

def evaluate(evaluator, node, env):
    from chaos.task_groups import TaskGroup, GroupAbort
    from _scope_reference142 import legacy_group as group_evaluate
    from chaos.control_prompts import NonlocalTransfer,PromptScope
    from _scope_reference142 import legacy_prompt as evaluate_scope
    manager = yield from evaluator._s_evaluate(node[2], env)
    if type(manager) is PromptScope:return (yield from evaluate_scope(evaluator,manager,node,env))
    from chaos.cancellation import CancelShield
    from _scope_reference142 import legacy_shield as shield_evaluate
    if type(manager) is CancelShield:
        return (yield from shield_evaluate(evaluator, manager, node, env))
    if type(manager) is TaskGroup:
        return (yield from group_evaluate(evaluator, manager, node, env))
    value, entry = yield from acquire(evaluator, manager, node[0] in ('async_with', 'async_with_pattern'))
    try:
        try:
            scope = Env(env)
            evaluator.bind_scope_target(node, value, scope, env)
            result = yield from evaluator._s_protected_value(node[3], scope)
        except (ResourceLimit, ExecutionControl):
            try:hard_exit_entry(evaluator,entry)
            except BaseException:pass
            raise
        except GroupAbort as signal:
            yield from exit_entry(evaluator, entry, signal)
            raise
        except LanguageError as error:
            from chaos.errors import observe
            observe(evaluator, error)
            if not (yield from exit_entry(evaluator, entry, error)): raise
            return None
        except (ReturnSignal, BreakSignal, ContinueSignal, GeneratorClose,NonlocalTransfer):
            yield from exit_entry(evaluator, entry)
            raise
        yield from exit_entry(evaluator, entry)
        return result
    finally:
        release(evaluator, entry)


@contextmanager
def reference(evaluator):
    from chaos.execution_resources import ContextFrame
    original=evaluator._s_execute
    def execute(self,node,env,tail=False,**options):
        if node[0] in ('with','async_with','with_pattern','async_with_pattern'):
            self.tick()
            return (yield from evaluate(self,node,env))
        return (yield from original(node,env,tail,**options))
    evaluator._s_execute=MethodType(execute,evaluator)
    try:
        with patch.object(ContextFrame,'KINDS',set()):yield
    finally:evaluator._s_execute=original
