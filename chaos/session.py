"""Persistent lexical REPL sessions with a fresh resource budget per submission."""
import time
from .language import Evaluator, Env, LanguageError, ResourceLimit, serializable
from .modules import ProgramBundle


class Session:
    def __init__(self, *, files=None, entry='repl.chaos', step_limit=30000, timeout=20, **handlers):
        self.files, self.entry = dict(files or {}), entry
        self.step_limit, self.timeout = step_limit, timeout
        self.evaluator=Evaluator(step_limit=step_limit,timeout=timeout,**handlers)
        self.env=Env(self.evaluator.env)
        self.evaluator.module_cache={};self.evaluator.module_results={};self.evaluator.module_bindings={}

    def evaluate(self, source):
        evaluator=self.evaluator
        evaluator.steps=0;evaluator.deadline=time.monotonic()+self.timeout
        evaluator.bundle=ProgramBundle(source,self.files,self.entry)
        evaluator.suspension.begin()
        result=None;failure=None
        try:
            for node in getattr(evaluator.bundle,'expanded_trees',evaluator.bundle.trees)[self.entry][1]:result=evaluator.resolve(evaluator.evaluate(node,self.env))
            serializable(result);evaluator.tick();return result
        except RecursionError:
            failure=ResourceLimit('REPL 嵌套深度超过限制');raise failure from None
        except BaseException as error:
            failure=error;raise
        finally:evaluator.suspension.finish(failure)

    def bindings(self):
        return {name:serializable(self.env.get(name))for name in self.env.values}
