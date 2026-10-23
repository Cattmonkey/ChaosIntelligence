"""Occurrence-based catch* handling on the existing execution cursor.

Only the fields below choose the next leaf, guard and handler. Protected
children use the same cursor; no generator retains an invisible partition.
"""


class GroupTryFrame:
    KINDS = {'try_group'}

    def __init__(self, boundary):
        self.boundary = boundary
        self.node, self.env, self.options = boundary.node, boundary.env, boundary.options
        self.phase = 'start'
        self.original = None
        self.occurrences = []
        self.remaining, self.reraised = set(), set()
        self.failures, self.replacements = [], {}
        self.result = None
        self.case_index = self.leaf_index = 0
        self.selected = set()
        self.path = self.leaf = self.subgroup = self.scope = None
        self.pattern = self.guard = self.handler = self.name = None
        self.previous_error = self.previous_context = None
        self.context_active = False

    def child(self, node, scope):
        from .execution_frames import Push
        from .execution_protection import ProtectedValueFrame
        return Push(ProtectedValueFrame(node, scope))

    def restore(self, evaluator):
        if self.context_active:
            evaluator.handled_error = self.previous_error
            evaluator.error_context = self.previous_context
            self.context_active = False

    def selection_failure(self, evaluator, error):
        from .errors import observe, originates
        observe(evaluator, error)
        # No selected occurrence is removed until every guard has completed.
        if not originates(error, self.leaf):
            self.failures.append(error)
        elif error is not self.leaf:
            self.replacements[self.path] = error
        self.phase = 'merge'

    def step(self, cursor, value, error):
        try:
            if getattr(self.boundary,'flow_structural',False):
                from .compiled_flow_protection import step
                return step(self,cursor,value,error,self.advance)
            return self.advance(cursor, value, error)
        except BaseException:
            self.restore(cursor.owner)
            raise

    def advance(self, cursor, value, error):
        from .execution_frames import Done
        from .language import LanguageError, ResourceLimit, Env, check_type, validate_try_group
        from .errors import GroupedError, leaves, project, merged, observe, originates
        from .error_values import capture
        from .multiple_values import primary
        from .exception_classes import match as match_exception
        e = cursor.owner

        if self.phase == 'body':
            if error is None:
                completion = self.options.get('_completion')
                if completion is not None:
                    completion.state = 'normal'
                return Done(value)
            if isinstance(error, ResourceLimit) or not isinstance(error, LanguageError):
                raise error
            self.original = error
            self.occurrences = list(leaves(error))
            self.remaining = {path for path, _ in self.occurrences}
            self.previous_error, self.previous_context = e.handled_error, e.error_context
            self.context_active = True
            self.phase = 'case'
            error = None
        elif self.phase == 'guard':
            try:
                if error is not None:
                    raise error
                truth=check_type(primary(value, e), 'Bool')
                if getattr(self.boundary,'flow_structural',False):
                    from .compiled_flow_protection import decide
                    truth=decide(self,truth)
                if truth:
                    self.selected.add(self.path)
            except ResourceLimit:
                raise
            except LanguageError as failure:
                self.selection_failure(e, failure)
            else:
                self.leaf_index += 1
                self.scope = None
                self.phase = 'select'
        elif self.phase == 'handler':
            if isinstance(error, ResourceLimit):
                raise error
            if error is not None:
                if not isinstance(error, LanguageError):
                    raise error
                observe(e, error)
                if originates(error, self.subgroup):
                    self.reraised |= self.selected
                    if error is not self.subgroup:
                        selected_paths = [path for path, _ in self.occurrences if path in self.selected]
                        self.replacements.update(zip(selected_paths, (leaf for _, leaf in leaves(error))))
                else:
                    self.failures.append(error)
            else:
                self.result = value
            self.scope = self.subgroup = None
            self.case_index += 1
            self.phase = 'case'
        elif error is not None:
            raise error

        if self.phase == 'start':
            e.tick()
            validate_try_group(self.node, tick=e.tick)
            self.phase = 'body'
            return self.child(self.node[1], self.env)

        while True:
            if self.phase == 'case':
                if self.case_index >= len(self.node[2]):
                    self.phase = 'merge'
                    continue
                _, self.pattern, self.guard, self.name, self.handler = self.node[2][self.case_index]
                self.selected = set()
                self.leaf_index = 0
                self.path = self.leaf = self.scope = None
                self.phase = 'select'

            if self.phase == 'select':
                while self.leaf_index < len(self.occurrences):
                    self.path, self.leaf = self.occurrences[self.leaf_index]
                    if self.path not in self.remaining:
                        self.leaf_index += 1
                        continue
                    try:
                        e.tick()
                        e.handled_error = e.error_context = self.leaf
                        bindings = {}
                        if not match_exception(e, self.pattern, self.leaf, self.env, bindings):
                            self.leaf_index += 1
                            continue
                        self.scope = Env(self.env)
                        for key, item in bindings.items():
                            self.scope.bind(key, item)
                        if self.guard is not None:
                            self.phase = 'guard'
                            return self.child(self.guard, self.scope)
                        self.selected.add(self.path)
                        self.scope = None
                        self.leaf_index += 1
                    except ResourceLimit:
                        raise
                    except LanguageError as failure:
                        self.selection_failure(e, failure)
                        break
                if self.phase == 'merge':
                    continue
                if not self.selected:
                    self.case_index += 1
                    self.phase = 'case'
                    continue
                self.remaining -= self.selected
                self.subgroup = project(self.original, self.selected)
                if type(self.subgroup) is not GroupedError:
                    self.subgroup = GroupedError('捕获的错误', [self.subgroup])
                e.handled_error = e.error_context = self.subgroup
                self.scope = Env(self.env)
                self.scope.bind(self.name, capture(e, self.subgroup))
                self.phase = 'handler'
                return self.child(self.handler, self.scope)

            if self.phase == 'merge':
                e.error_context = self.previous_context
                failure = merged(self.original, self.remaining, self.reraised, self.failures, self.replacements)
                if failure is not None:
                    raise failure
                completion = self.options.get('_completion')
                if completion is not None:
                    completion.state = 'handled'
                result = self.result
                self.restore(e)
                return Done(result)
            raise RuntimeError('Invalid group-handler-frame phase')

    def roots(self):
        # Private occurrence maps use tuple keys, unlike language Records.
        # Expose their actual live values directly to the existing root walker;
        # inert path sets themselves contain no language values or authority.
        return ((self.env, self.original, self.result, self.leaf, self.subgroup,
                 self.scope, self.previous_error, self.previous_context)
                + tuple(leaf for _, leaf in self.occurrences)
                + tuple(self.failures) + tuple(self.replacements.values()))

    def close(self, cursor):
        # Forced retirement is bookkeeping only, never user handler execution.
        self.restore(cursor.owner)
