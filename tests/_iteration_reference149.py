"""Exact original15 iteration/stream/comprehension algorithms; test only."""
from contextlib import contextmanager,ExitStack
from unittest.mock import patch
from chaos.iteration import *
from chaos.suspension import CHECKPOINT,GeneratorClose
from chaos.comprehensions import KINDS,FOR_KINDS
from chaos.comprehensions import comprehension_scopes,validate_comprehension

def acquire(self, value, asynchronous=False, *, temporary=False):
    self.evaluator.tick()
    from chaos.channels import Channel
    from chaos.hash_maps import HashMap, items as hash_map_items
    if type(value) is HashMap and not asynchronous:
        values = hash_map_items(self.evaluator,value,'keys')
        return Iterator(self.scheduler,values=values) if temporary else self._register(values=values)
    from chaos.set_runtime import items as set_items
    if type(value) is SetValue and not asynchronous:
        values=set_items(self.evaluator,value)
        return Iterator(self.scheduler,values=values) if temporary else self._register(values=values)
    from chaos.dynamic_contexts import Context,items as context_items
    if type(value) is Context and not asynchronous:return self._register(values=context_items(self.evaluator,value,'keys'))
    if isinstance(value, HANDLES + (Generator, Channel)):
        return self.direct(value, asynchronous)
    if not asynchronous and (isinstance(value, (list, str, TupleValue)) or type(value) is SetValue):
        if (isinstance(value, (list, TupleValue)) or type(value) is SetValue) and len(value) > 10000: raise ResourceLimit('iter 超过 10000 项')
        if isinstance(value, str): serializable(value)
        values = list(value) if (isinstance(value, (list, TupleValue)) or type(value) is SetValue) else value
        # Native consumer cursors cannot escape the consuming call, so they
        # need no epoch registry slot or end-of-run close. Explicit iter and
        # collect, and every user factory/handle, retain registered ownership.
        if temporary: return Iterator(self.scheduler, values=values)
        return self._register(values=values)
    if not isinstance(value, ChaosInstance):
        raise LanguageError('aiter 需要异步可迭代值' if asynchronous else 'iter 需要 List、Tuple、String 或可迭代对象', code='argument_error')
    name = '__aiter__' if asynchronous else '__iter__'
    if self._slot(value, name) is None: raise LanguageError('迭代对象缺少 '+name, code='argument_error')
    # A generator-function factory is valid; an async-function Task is not.
    result = yield from self._invoke(value, name, [])
    return self.direct(result, asynchronous)

def _invoke(self, target, name, args):
    self.evaluator.tick()
    function = yield from self.evaluator._s_access_result(special_method(target, name, self.evaluator))
    return (yield from self.evaluator._s_invoke(function, args))

def _await(self, task):
    self.scheduler.validate(task, Task)
    try:
        from chaos.multiple_values import primary,prepare
        return primary(prepare(self.evaluator,(yield Await(task))),self.evaluator)
    except BaseException:
        if task.state not in TERMINAL: self.scheduler.cancel(task)
        raise

def step(self, value, sent=None, error=None, asynchronous=False, locked=False):
    from chaos.execution_scope import scope
    handle = self.direct(value, asynchronous)
    with scope(self.evaluator,handle.capabilities,handle.team_scope):
        return (yield from self._step(handle,sent,error,asynchronous,locked))

def _step(self, value, sent=None, error=None, asynchronous=False, locked=False):
    handle = self.direct(value, asynchronous)
    if isinstance(handle, Generator):
        return (yield from self.scheduler._s_resume(handle, sent, error, locked=locked))
    self.evaluator.tick()
    if handle.busy and not locked: raise LanguageError('迭代器不能重入', code='argument_error')
    if handle.state in TERMINAL:
        if error is not None: raise error
        return {'done': True, 'value': handle.result}
    from chaos.channels import Channel, receive
    if type(handle.target) is Channel:
        if error is not None: raise error
        if sent is not None: raise LanguageError('Channel 迭代器只接受 null', code='argument_error')
        operation = receive(self.evaluator, handle.target, cursor=handle, locked=locked)
        try: return (yield Await(operation))
        except BaseException:
            if operation.state not in TERMINAL: self.scheduler.cancel(operation)
            raise
    prefix = '__a' if asynchronous else '__'
    if error is not None:
        if not isinstance(error, Raised): raise error
        name, args = prefix+'throw__', [error.value]
    elif sent is not None:
        name, args = prefix+'send__', [sent]
    else:
        name, args = prefix+'next__', []
    if (error is not None or sent is not None) and (handle.target is None or self._slot(handle.target, name) is None):
        if error is not None: raise error
        raise LanguageError('迭代器不支持非 null send', code='argument_error')
    if not locked: handle.busy = True
    handle.state = 'running'
    try:
        if handle.target is None:
            if handle.index >= len(handle.values): result = {'done': True, 'value': None}
            else:
                result = {'done': False, 'value': handle.values[handle.index]}
                handle.index += 1
        else:
            result = yield from self._invoke(handle.target, name, args)
            if asynchronous: result = yield from self._await(result)
            result = self._record(result)
        handle.state = 'completed' if result['done'] else 'suspended'
        if result['done']: handle.result = result['value']
        return result
    except (ResourceLimit, ExecutionControl, GeneratorExit):
        handle.state, handle.result, handle.close_attempted = 'cancelled', None, True
        raise
    except BaseException:
        handle.state, handle.result = 'failed', None
        raise
    finally:
        if not locked: handle.busy = False

def close(self, value, asynchronous=False):
    from chaos.execution_scope import scope
    handle = self.direct(value, asynchronous)
    with scope(self.evaluator,handle.capabilities,handle.team_scope):
        return (yield from self._close(handle,asynchronous))

def _close(self, value, asynchronous=False):
    handle = self.direct(value, asynchronous)
    if isinstance(handle, Generator): return (yield from self.scheduler._s_close(handle))
    if handle.busy: raise LanguageError('迭代器不能重入', code='argument_error')
    if handle.close_attempted: return None
    handle.close_attempted, handle.busy = True, True
    try:
        self.evaluator.tick()
        from chaos.channels import Channel, owned as channel_owned
        if type(handle.target) is Channel:
            channel_owned(handle.target, self.evaluator)
            return None
        name = '__aclose__' if asynchronous else '__close__'
        if handle.target is not None and self._slot(handle.target, name) is not None:
            result = yield from self._invoke(handle.target, name, [])
            if asynchronous: result = yield from self._await(result)
            check_type(result, 'Null')
        return None
    finally:
        handle.state, handle.busy = 'closed', False

def collect(self, value, asynchronous=False):
    handle = yield from self.acquire(value, asynchronous)
    if handle.busy: raise LanguageError('迭代器不能重入', code='argument_error')
    handle.busy = True
    values = []
    try:
        while True:
            item = yield from self.step(handle, asynchronous=asynchronous, locked=True)
            if item['done']: return values
            if len(values) >= 10000: raise ResourceLimit('acollect/collect 超过 10000 项')
            values.append(item['value'])
    except (ResourceLimit, ExecutionControl, GeneratorExit):
        if isinstance(handle, HANDLES):
            handle.state, handle.result, handle.close_attempted = 'cancelled', None, True
        raise
    finally: handle.busy = False

def consume(self, value, operation, callback=None, initial=None, limit=10000):
    "Borrow one synchronous cursor for a bounded, cooperative consumer.\n\n        A full consumer probes once after its limit to distinguish exact-size\n        input from overflow. The probe's effects and cursor advance are committed,\n        but no callback runs for that item. Short-circuit predicates never probe\n        beyond their decisive item. Borrowing never closes the cursor.\n        "
    message = '调用参数超过 10000 项限制' if operation == 'expand' else operation + ' 超过 10000 项'
    # Preserve eager List argument-budget rejection before any consumption.
    if (isinstance(value, (list, TupleValue)) or type(value) is SetValue) and len(value) > limit: raise ResourceLimit(message)
    handle = yield from self.acquire(value, temporary=True)
    if handle.busy: raise LanguageError('迭代器不能重入', code='argument_error')
    handle.busy = True
    values, result, consumed = [], initial, 0
    try:
        while True:
            item = yield from self.step(handle, locked=True)
            if item['done']:
                if operation == 'set': return result
                if operation == 'fold': return result
                if operation in ('any', 'all'): return operation == 'all'
                return values
            if consumed >= limit: raise ResourceLimit(message)
            consumed += 1
            value = item['value']
            if operation == 'set':
                yield from result.add(value)
                continue
            if operation == 'expand':
                values.append(value)
                continue
            arguments = [result, value] if operation == 'fold' else [value]
            result = yield from self.evaluator._s_invoke(callback, arguments)
            if operation == 'map': values.append(result)
            elif operation == 'filter':
                if check_type(result, 'Bool'): values.append(value)
            elif operation in ('any', 'all'):
                truth = check_type(result, 'Bool')
                if truth == (operation == 'any'): return truth
    finally:
        handle.busy = False

def delegate(self, value, asynchronous=False):
    from chaos.channels import Channel
    use_async = asynchronous and (isinstance(value, (AsyncIterator, AsyncGenerator, Channel)) or isinstance(value, ChaosInstance) and self._slot(value, '__aiter__') is not None)
    handle = yield from self.acquire(value, use_async)
    sent, error = None, None
    while True:
        item = yield from self.step(handle, sent, error, use_async)
        if item['done']: return handle.result if isinstance(handle,Generator) else item['value']
        sent, error = None, None
        try:
            sent = yield Yield(item['value'])
            if isinstance(handle, Iterator) and handle.target is None and sent is not None:
                raise LanguageError(('Set' if type(value) is SetValue else 'Tuple' if type(value) is TupleValue else 'List/String') + ' 委托只接受 null')
        except GeneratorClose:
            yield from self.close(handle, use_async)
            raise
        except (ResourceLimit, ExecutionControl): raise
        except LanguageError as injected:
            if isinstance(handle, Generator) or isinstance(injected, Raised): error = injected
            else: raise

def resolve(self, request):
    # Preserve the established scheduling of explicit native sync-generator
    # resumes. New adapters and language consumers use cooperative helpers.
    if isinstance(request.handle, Generator) and not isinstance(request.handle, AsyncGenerator):
        if request.operation == 'next': return self.scheduler.next(request.handle)
        if request.operation == 'send': return self.scheduler.send(request.handle, request.value)
        if request.operation == 'throw': return self.scheduler.throw_into(request.handle, request.value)
    if request.operation == 'set':
        from chaos.set_runtime import Builder
        from chaos.capture_frames import active,retain
        builder = Builder(self.evaluator)
        retain(active(self.evaluator),builder)
        yield from self.consume(request.handle, 'set', initial=builder)
        return builder.finish()
    if request.operation == 'tuple':
        return TupleValue((yield from self.consume(request.handle, 'expand')))
    if request.operation == 'expand':
        return (yield from self.consume(request.handle, 'expand', limit=request.value))
    if request.operation == 'apply':
        callback, kwargs, span = request.value
        args = yield from self.consume(request.handle, 'expand', limit=10000 - len(kwargs))
        return TailCall(callback, args, kwargs=kwargs, span=span)
    if request.operation == 'fold':
        callback, initial = request.value
        return (yield from self.consume(request.handle, 'fold', callback, initial))
    if request.operation in ('map', 'filter', 'any', 'all'):
        return (yield from self.consume(request.handle, request.operation, request.value))
    if request.operation == 'iter': return (yield from self.acquire(request.handle))
    if request.operation == 'aiter': return (yield from self.acquire(request.handle, True))
    if request.operation == 'close': return (yield from self.close(request.handle))
    if request.operation == 'collect': return (yield from self.collect(request.handle))
    error = raised_value(self.evaluator, self.evaluator.copy_value(request.value)) if request.operation == 'throw' else None
    return (yield from self.step(request.handle, request.value if request.operation == 'send' else None, error))

def _s_resume(self, handle, value=None, error=None, locked=False, context=None):
    "Forward suspension requests, never run a second AST interpreter thread.\n\n        The operation's task owns effects/cancellation; the stream keeps logical\n        frames across operations. A yielded item completes this operation only.\n        "
    self.validate(handle, AsyncGenerator if isinstance(handle, AsyncGenerator) else Generator)
    if handle.context_manager is not None and handle.context_manager is not context:
        raise LanguageError('生成器已由上下文管理器独占', code='context_error')
    if handle.busy and not locked: raise LanguageError('生成器不能重入', code='argument_error')
    if handle.state in TERMINAL:
        if error is not None and not isinstance(error, GeneratorClose): raise error
        from chaos.multiple_values import primary,prepare
        return {'done': True, 'value': primary(prepare(self.evaluator,handle.result),self.evaluator)}
    if handle.state == 'created' and value is not None and error is None:
        raise LanguageError('未启动生成器只能发送 null', code='argument_error')
    if not locked: handle.busy = True
    e, runner = self.evaluator, handle.runner
    saved = (e.frames, e.depth, e.current_span, e.handled_error, e.error_context, self.stream)
    from chaos.execution_scope import scope
    authority = scope(e, handle.capabilities, handle.team_scope)
    authority.__enter__()
    runner.task = self.current.task if self.current else None
    for frame in runner.frames:
        frame['taskId'] = runner.task.id if runner.task else None
    self._restore(runner); self.stream = handle
    handle.state = 'running'
    try:
        while True:
            runner.started = True
            try:
                request = runner.iterator.throw(error) if error is not None else runner.iterator.send(value)
            except StopIteration as stop:
                handle.state, handle.result = 'completed', stop.value
                self.event('generator.completed', handle)
                from chaos.multiple_values import primary,prepare
                return {'done': True, 'value': primary(prepare(self.evaluator,stop.value),self.evaluator)}
            except GeneratorClose:
                handle.state = 'closed'
                return {'done': True, 'value': None}
            error, value = None, None
            if isinstance(request, Yield):
                from chaos.cancellation import check_yield
                check_yield(self, handle)
                handle.state = 'suspended'
                self.event('generator.yielded', handle)
                return {'done': False, 'value': request.value}
            try:
                value = yield request
            except BaseException as injected:
                if isinstance(injected, GeneratorExit): raise
                error = injected
    except (ResourceLimit, ExecutionControl, GeneratorExit):
        handle.state = 'cancelled'; self._abort_stream(handle)
        self.event('generator.cancelled', handle)
        raise
    except BaseException as error:
        from chaos.errors import observe
        try: observe(e, error)
        except BaseException as observed: error = observed
        handle.state = 'cancelled' if isinstance(error, (ResourceLimit, ExecutionControl)) else 'failed'
        self._abort_stream(handle)
        self.event('generator.' + handle.state, handle)
        raise error
    finally:
        self._save(runner)
        e.frames, e.depth, e.current_span, e.handled_error, e.error_context, self.stream = saved
        authority.__exit__(None,None,None)
        if not locked: handle.busy = False

def _s_close(self, handle, context=None):
    self.validate(handle, AsyncGenerator if isinstance(handle, AsyncGenerator) else Generator)
    if handle.context_manager is not None and handle.context_manager is not context:
        raise LanguageError('生成器已由上下文管理器独占', code='context_error')
    if handle.busy: raise LanguageError('生成器不能重入', code='argument_error')
    if handle.state in TERMINAL: return None
    if handle.state == 'created':
        handle.runner.iterator.close(); handle.state = 'closed'
        self.event('generator.closed', handle)
        return None
    try:
        item = yield from self._s_resume(handle, error=GeneratorClose(), context=context)
        if not item['done']: raise LanguageError('关闭生成器期间不能 yield')
    finally:
        self._abort_stream(handle)
        handle.state = 'closed'; handle.busy = False
        self.event('generator.closed', handle)
    return None

def evaluate(evaluator, node, env):
    """Create lazy handles without acquiring inputs, or materialize directly."""
    from chaos.language import ReturnSignal, BreakSignal, ContinueSignal, LanguageError
    try:
        outer = yield from evaluator._s_evaluate(node[-1][0][2], env)
        if node[0] in ('gen_comp', 'async_gen_comp'):
            captured_env = env.snapshot()
            iterator = traverse(evaluator, node, captured_env, outer)
            return evaluator.suspension.generator(iterator, asynchronous=node[0] == 'async_gen_comp', capture_roots=(captured_env,outer))
        return (yield from traverse(evaluator, node, env, outer))
    except (ReturnSignal, BreakSignal, ContinueSignal):
        raise LanguageError('控制流不能越过推导式边界', code='syntax_error').attach(getattr(node, 'span', {})) from None

def traverse(evaluator, node, env, outer):
    from chaos.language import Env, LanguageError, ResourceLimit, ReturnSignal, BreakSignal, ContinueSignal, check_type
    from chaos.suspension import Await, Yield, CHECKPOINT
    lazy = node[0] in ('gen_comp', 'async_gen_comp')
    record = node[0] == 'record_comp'
    from chaos.set_runtime import Builder
    builder = Builder(evaluator) if node[0] == 'set_comp' else None
    result = {} if record else []
    entries = 0

    def walk(index, scope):
        nonlocal entries
        from chaos.capture_frames import enter,leave,retain,reset
        from chaos.language import MISSING
        capture=enter(evaluator,'comprehension_clause',scope,result,outer,builder)
        try:
            evaluator.tick()
            if index == len(node[-1]):
                if not lazy and entries >= 10000:
                    raise ResourceLimit('推导式结果超过 10000 项限制')
                if record:
                    key = check_type((yield from evaluator._s_evaluate(node[1], scope)), 'String')
                    value = yield from evaluator._s_evaluate(node[2], scope)
                    result[key] = value
                else:
                    value = yield from evaluator._s_evaluate(node[1], scope)
                    if lazy: yield Yield(value)
                    elif builder is not None: yield from builder.add(value)
                    else: result.append(value)
                entries += 1
                return
            clause = node[-1][index]
            if clause[0] == 'comp_if':
                if check_type((yield from evaluator._s_evaluate(clause[1], scope)), 'Bool'):
                    yield from walk(index + 1, scope)
                return
            source = outer if index == 0 else (yield from evaluator._s_evaluate(clause[2], scope))
            asynchronous = clause[0] in ('comp_async_for', 'comp_async_for_pattern')
            handle = yield from evaluator.suspension.iteration.acquire(source, asynchronous, temporary=True)
            retain(capture,source,handle)
            consumed = 0
            while True:
                reset(capture,scope,result,outer,source,handle,builder)
                # Pure projections and native inputs must still allow siblings,
                # cancellation, debugger pauses and global budgets to make progress.
                yield CHECKPOINT
                evaluator.tick()
                if asynchronous:
                    operation = evaluator.suspension.anext(handle)
                    try: item = yield Await(operation)
                    except BaseException:
                        if operation.state not in ('completed', 'failed', 'cancelled', 'closed'):
                            evaluator.suspension.cancel(operation)
                        raise
                else:
                    item = yield from evaluator.suspension.iteration.step(handle)
                if item['done']: return
                if consumed >= 10000: raise ResourceLimit('推导式输入超过 10000 项限制')
                consumed += 1
                child = Env(scope)
                if clause[0].endswith('_pattern'): evaluator.bind_pattern(clause[1], item['value'], child, lookup_env=scope)
                else: child.bind(clause[1], item['value'])
                yield from walk(index + 1, child)
        finally:
            leave(capture,MISSING)

    try:
        yield from walk(0, env)
        return builder.finish() if builder is not None else result if not lazy else None
    except (ReturnSignal, BreakSignal, ContinueSignal):
        raise LanguageError('控制流不能越过推导式边界', code='syntax_error').attach(getattr(node, 'span', {})) from None

ITERATION_NAMES=['acquire', '_invoke', '_await', 'step', '_step', 'close', '_close', 'collect', 'consume', 'delegate', 'resolve']

@contextmanager
def reference(seen=None):
    import chaos.iteration as iteration
    import chaos.comprehensions as comp
    from chaos.suspension import Scheduler
    implementations={name:globals()[name] for name in ITERATION_NAMES+['_s_resume','_s_close','evaluate','traverse']}
    def tracked(name):
        fn=implementations[name]
        def call(*args,**kwargs):
            if seen is not None:seen.append(name)
            return fn(*args,**kwargs)
        return call
    with ExitStack() as stack:
        for name in ITERATION_NAMES:stack.enter_context(patch.object(iteration.Iteration,name,tracked(name)))
        stack.enter_context(patch.object(Scheduler,'_s_resume',tracked('_s_resume')))
        stack.enter_context(patch.object(Scheduler,'_s_close',tracked('_s_close')))
        stack.enter_context(patch.object(comp,'evaluate',tracked('evaluate')))
        stack.enter_context(patch.object(comp,'traverse',tracked('traverse')))
        stack.enter_context(patch.dict(globals(),{'traverse':tracked('traverse')}))
        yield
