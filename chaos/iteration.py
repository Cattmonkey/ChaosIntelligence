"""Language iterator adapters. Every protocol runs on the existing AST scheduler.

Adapters are execution-epoch resources, not Python iterators. Their target never
crosses the host/JSON boundary and special methods are resolved afresh per use.
"""

from .exception_classes import raised as raised_value
from dataclasses import dataclass
from .tuple_values import TupleValue
from .set_values import SetValue, SetBuilder

from .language import LanguageError, ResourceLimit, ExecutionControl, Raised, TailCall, check_type, serializable
from .objects import ChaosInstance, declaration, owned, special_method
from .suspension import Generator, AsyncGenerator, Task, Await, Yield, GeneratorClose, TERMINAL


class _IteratorHandle:
    def __init__(self, scheduler, target=None, values=None):
        self.owner, self.epoch = scheduler, scheduler.epoch
        self.capabilities = scheduler.current.capabilities if scheduler.current else None
        self.team_scope = scheduler.current.team_scope if scheduler.current else None
        self.bot_guard = getattr(scheduler.current,'bot_guard',None) if scheduler.current else None
        self.id = 'iterator-' + str(scheduler.next_id())
        self.location = dict(scheduler.evaluator.current_span)
        self.target, self.values, self.index = target, values, 0
        self.state, self.busy, self.result = 'created', False, None
        self.close_attempted = False
        parent = scheduler.current.task if scheduler.current else None
        self.recording_scope = parent.recording_scope if parent else None


class Iterator(_IteratorHandle):
    pass


class AsyncIterator(_IteratorHandle):
    pass


HANDLES = (Iterator, AsyncIterator)


@dataclass(frozen=True)
class IterationRequest:
    operation: str
    handle: object
    value: object = None


class Iteration:
    def __init__(self, scheduler):
        self.scheduler, self.evaluator = scheduler, scheduler.evaluator
        self.handles, self.cache = [], {}

    def _frame_request(self,operation,values):
        from .execution_frames import request_proxy,ensure_cursor
        from .execution_iteration import IterationFrameRequest
        iterator=request_proxy(IterationFrameRequest(operation,values))
        return iterator if getattr(self.evaluator,'_execution_cursor',None) is not None else ensure_cursor(self.evaluator,iterator)

    def begin(self):
        self.handles.clear(); self.cache.clear()

    def _slot(self, value, name):
        owned(value, self.evaluator); owned(value.klass, self.evaluator)
        return declaration(value.klass, name)

    def _register(self, target=None, values=None, asynchronous=False):
        if len(self.handles) >= 10000: raise ResourceLimit('迭代器数量超过 10000')
        handle = (AsyncIterator if asynchronous else Iterator)(self.scheduler, target, values)
        self.handles.append(handle)
        return handle

    def direct(self, value, asynchronous=False):
        """Adapt a step target without executing its factory or descriptors."""
        scheduler = self.scheduler
        expected = AsyncIterator if asynchronous else Iterator
        from .channels import Channel, owned as channel_owned
        if type(value) is Channel:
            channel_owned(value, self.evaluator)
            if not asynchronous: raise LanguageError('Channel 只支持异步迭代', code='argument_error')
            return self._register(value, asynchronous=True)
        if isinstance(value, HANDLES): return scheduler.validate(value, expected)
        if isinstance(value, Generator):
            scheduler.validate(value, AsyncGenerator if asynchronous else Generator)
            if value.context_manager is not None:
                raise LanguageError('生成器已由上下文管理器独占', code='context_error')
            return value
        if not scheduler.active: raise LanguageError('迭代器需要活动执行周期')
        if not isinstance(value, ChaosInstance):
            raise LanguageError('需要 '+('AsyncIterator/AsyncGenerator' if asynchronous else 'Iterator/Generator'), code='argument_error')
        owned(value, self.evaluator)
        key = (asynchronous, id(value))
        if key in self.cache: return scheduler.validate(self.cache[key], expected)
        name = '__anext__' if asynchronous else '__next__'
        if self._slot(value, name) is None:
            raise LanguageError('迭代对象缺少 '+name, code='argument_error')
        key = (asynchronous, id(value))
        if key not in self.cache: self.cache[key] = self._register(value, asynchronous=asynchronous)
        return self.cache[key]

    def acquire(self,value,asynchronous=False,*,temporary=False):
        return self._frame_request('acquire',(value,asynchronous,temporary))

    def _invoke(self,target,name,args):
        return self._frame_request('invoke',(target,name,args))

    def _await(self,task):
        return self._frame_request('await',(task,))

    def _record(self, result):
        if type(result) is not dict or set(result) != {'done', 'value'} or type(result['done']) is not bool:
            raise LanguageError('迭代步骤必须返回 {done: Bool, value: Any}', code='argument_error')
        serializable(result)
        # Validate handles even when they are nested in a yielded value.
        from .indexing import validate_owned
        validate_owned(self.evaluator, result['value'])
        return {'done': result['done'], 'value': result['value']}

    def step(self,value,sent=None,error=None,asynchronous=False,locked=False):
        return self._frame_request('step',(value,sent,error,asynchronous,locked,True))

    def _step(self,value,sent=None,error=None,asynchronous=False,locked=False):
        return self._frame_request('step',(value,sent,error,asynchronous,locked,False))

    def close(self,value,asynchronous=False):
        return self._frame_request('close',(value,asynchronous,True))

    def _close(self,value,asynchronous=False):
        return self._frame_request('close',(value,asynchronous,False))

    def collect(self,value,asynchronous=False):
        return self._frame_request('collect',(value,asynchronous))

    def consume(self,value,operation,callback=None,initial=None,limit=10000):
        return self._frame_request('consume',(value,operation,callback,initial,limit))

    def delegate(self,value,asynchronous=False):
        return self._frame_request('delegate',(value,asynchronous))

    def resolve(self,request):
        from .execution_frames import request_proxy,ensure_cursor
        iterator=request_proxy(request)
        return iterator if getattr(self.evaluator,'_execution_cursor',None) is not None else ensure_cursor(self.evaluator,iterator)

    def hard_close(self):
        for handle in self.handles:
            handle.close_attempted, handle.busy, handle.state = True, False, 'closed'
