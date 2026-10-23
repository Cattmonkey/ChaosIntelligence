"""Native shallow-immutable product values, distinct from AST/host tuples.

Host equality and hashing retain identity. Language structural equality is
budgeted by the evaluator; it must never fall through to Python comparisons.
"""
from dataclasses import dataclass


@dataclass(frozen=True, eq=False)
class TupleValue:
    items: tuple = ()

    def __post_init__(self):
        # This constructor is internal. The language tuple() constructor instead
        # consumes the scheduler's bounded iterator protocol, never Python iter.
        from .language import LanguageError, ResourceLimit
        if type(self.items) not in (tuple, list):
            raise LanguageError('Tuple 内部项需要有界序列', code='argument_error')
        if len(self.items) > 10000: raise ResourceLimit('Tuple 超过 10000 项限制')
        object.__setattr__(self, 'items', tuple(self.items))

    def __len__(self): return len(self.items)
    def __iter__(self): return iter(self.items)
    def __getitem__(self, key):
        value = self.items[key]
        return TupleValue(value) if isinstance(key, slice) else value
