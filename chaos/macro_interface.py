"""Portable raw macro declarations; phase data never carries a live environment.

The ordinary grammar is shared with executable objects, with explicit staging
contexts. Pure evaluation, effects rejection and expansion budgets remain owned
by the original Expander when a client actually calls a macro.
"""
from types import SimpleNamespace
from .executable_ast import _decode_tree, _validate_grammar, encode_tree, error


def _validate(tree):
    from .language import Node
    from .macros import Expander
    _validate_grammar(tree, phase=True)
    names = set()
    for declaration in tree[1]:
        if declaration[0] != 'macro' or declaration[1] in names:
            error('Macro phase interfaces contain unique macro declarations only')
        names.add(declaration[1])
        # Quotes defer insertion-context control checks to expansion. This is
        # a bounded grammar projection, never evaluation or source reparsing.
        def scope_tree(value):
            if isinstance(value, tuple):
                if value and value[0] == 'quote': return ('literal', None)
                return tuple(scope_tree(child) for child in value)
            if type(value) is list: return [scope_tree(child) for child in value]
            return value
        checker = SimpleNamespace(tick=lambda *args: None,
            fail=lambda message, node=None: error(message))
        try:
            Expander.validate_scope(checker, scope_tree(declaration[3]))
        except Exception as failure:
            from .language import LanguageError, ResourceLimit
            if isinstance(failure, ResourceLimit): raise
            if isinstance(failure, LanguageError): error('Invalid macro declaration scope: ' + failure.message)
            raise


def encode_phase(tree, modules):
    _validate(tree)
    return encode_tree(tree, modules)


def decode_phase(data, modules):
    return _decode_tree(data, modules, _validate)
