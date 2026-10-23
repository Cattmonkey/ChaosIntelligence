"""Invocation-local compiled PC and exact live lexical activation ownership.

The ordinary cursor executes and cleans the actual boundary/operand/loop frames.
Native event payloads and invocation values live in one bounded root domain;
lexical activations remain on their original frames.
"""


def rooted_field(name):
    """Store the actual value only in the invocation domain, never in a shadow."""
    def get(state):
        ref = state._value_refs.get(name)
        return None if ref is None else state._owned_domain.resolve(ref, state.owner, state.epoch)
    def set_(state, value):
        old = state._value_refs.get(name)
        new = None if value is None else state._owned_domain.pin(value)
        try:
            if old is not None: state._owned_domain.release(old, state.owner, state.epoch)
        except BaseException:
            if new is not None: state._owned_domain.release(new, state.owner, state.epoch)
            raise
        state._value_refs[name] = new
    return property(get, set_)


class FlowState:
    ROOTED_FIELDS = ('function', 'scope', 'result', '_entry_env', 'pending_cursor', 'pending_request', 'invalid_definition')
    function = rooted_field('function')
    scope = rooted_field('scope')
    result = rooted_field('result')
    _entry_env = rooted_field('_entry_env')
    pending_cursor = rooted_field('pending_cursor')
    pending_request = rooted_field('pending_request')
    invalid_definition = rooted_field('invalid_definition')
    PHASES = frozenset(('dispatch','checkpoint','host','closed'))

    def __init__(self, plan, owner, function, scope, *, in_stack=True):
        from .agent_tools import current_caps
        from .jit_flow import validate_program
        self.owner = owner; self.epoch = owner.suspension.epoch
        self.program = plan.flow
        if in_stack is not True or validate_program(self.program) != self.program.layout_seal:
            self.malformed()
        from .native_values import RootDomain
        from .flow_abi import EventTable
        self.domain = self._owned_domain = RootDomain(owner, self.epoch)
        self._domain_id = self.domain.id
        self._value_refs = {}
        try:
            self.events = self._owned_events = EventTable(self.domain, self.program.instructions)
            self.function = function; self.scope = scope
        except BaseException:
            self.domain.close()
            raise
        self.in_stack = True
        self.compile_seal = self.program.layout_seal
        self.layout = (id(self.program.instructions),id(self.program.effects),self.program.flag_count,id(self.program.node_ids))
        # Protocol flavor belongs to this invocation, not its consumer Task.
        # StreamResumeFrame may borrow a different caller on every operation.
        self.flavor = function.flavor
        self.asynchronous = self.flavor in ('async_fn','async_gen_fn')
        self.generating = self.flavor in ('gen_fn','async_gen_fn')
        self.capabilities = current_caps(owner)
        self.pc = 0; self.flags = [0] * self.program.flag_count
        self.frames = []; self._owned_activations = []; self._transfers = []
        self.env = scope; self.result = None; self.previous = owner.current_span
        self.portable = self.program.kernel is None
        self.native_counted = self.portable_counted = False
        self.invalid_definition = None
        self._unwind_error = None
        self.phase = 'dispatch'; self.closed = False
        self.pending_cursor = self.pending_request = self.pending_authority = self.pending_pc = self.pending_kind = None

    @property
    def env(self):
        if self._owned_activations:
            boundary=self._owned_activations[-1]
            operation=getattr(boundary,'flow_operation',None)
            if operation is not None:
                from .execution_loops import LoopFrame
                if type(operation) is LoopFrame and operation.phase=='for_body':return operation.scope
                from .execution_protection import TryFrame
                from .execution_group_handlers import GroupTryFrame
                if type(operation) in (TryFrame,GroupTryFrame) and operation.phase in ('guard','handler') and operation.scope is not None:return operation.scope
                from .execution_patterns import PatternSyntaxFrame
                if type(operation)is PatternSyntaxFrame and operation.phase in('guard','body')and operation.scope is not None:return operation.scope
                from .execution_resources import ContextFrame
                from .execution_scopes import PromptScopeFrame,CancelShieldFrame,TaskGroupFrame
                if type(operation)in(ContextFrame,PromptScopeFrame,CancelShieldFrame,TaskGroupFrame)and operation.scope is not None:return operation.scope
                return operation.env
            return boundary.env
        return getattr(self,'_entry_env',None)

    @env.setter
    def env(self,value):
        if self._owned_activations:
            boundary=self._owned_activations[-1]
            operation=getattr(boundary,'flow_operation',None)
            if operation is not None:
                from .execution_loops import LoopFrame
                from .execution_protection import TryFrame
                from .execution_group_handlers import GroupTryFrame
                if type(operation) is LoopFrame and operation.phase=='for_body' or type(operation) in (TryFrame,GroupTryFrame) and operation.phase in ('guard','handler') and operation.scope is not None:operation.scope=value
                else:
                    from .execution_resources import ContextFrame
                    from .execution_scopes import PromptScopeFrame,CancelShieldFrame,TaskGroupFrame
                    from .execution_patterns import PatternSyntaxFrame
                    if type(operation)is PatternSyntaxFrame and operation.phase in('guard','body')and operation.scope is not None:operation.scope=value
                    elif type(operation)in(ContextFrame,PromptScopeFrame,CancelShieldFrame,TaskGroupFrame)and operation.scope is not None:operation.scope=value
                    else:operation.env=value
            else:boundary.env=value
        else:self._entry_env=value

    def malformed(self):
        from .language import LanguageError
        raise LanguageError('Malformed compiled flow invocation',code='ownership_error')

    def validate(self, *, full=False, expected_owner=None):
        from .compiled_flow_frames import validate_state
        return validate_state(self,expected_owner,full)

    def attach(self, capture):
        if capture is None:return
        if capture.owner is not self.owner or getattr(capture,'compiled_flow_state',None) is not None:self.malformed()
        capture.compiled_flow_state = self

    def detach(self, capture):
        if capture is None:return
        if getattr(capture,'compiled_flow_state',None) is not self:self.malformed()
        del capture.compiled_flow_state

    def roots(self):
        from .compiled_flow_frames import roots
        return roots(self)

    def clear_pending(self):
        # Validation already selected any corruption error. A forged reference
        # must not replace hard control during cleanup or release foreign pins.
        from .language import LanguageError
        if type(self._value_refs) is not dict:
            self._value_refs = {}
        for name in ('pending_cursor', 'pending_request'):
            ref = self._value_refs.pop(name, None)
            if ref is not None:
                try: self._owned_domain.release(ref, self.owner, self.epoch)
                except LanguageError as error:
                    if error.code != 'ownership_error': raise
        self.pending_pc = self.pending_authority = self.pending_kind = None

    def finish(self):
        # Retiring roots must succeed even after malformed handles or an epoch
        # change. Do not resolve/release individual untrusted slots on cleanup.
        self.frames = []
        self._owned_domain.close()
        self._value_refs = {}
        self._owned_events.domain = self._owned_domain
        self._owned_events.close()
        self.domain = self._owned_domain
        self.events = self._owned_events
        self.pending_pc = self.pending_authority = self.pending_kind = None
        self.phase = 'closed'; self.closed = True
        self._cursor = self._root_boundary = self._plan = self._regions = None
        self._transfers = []; self._unwind_error = None
