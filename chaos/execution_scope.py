"""Scheduler-local authority and trusted role provenance for deferred execution."""
from contextlib import contextmanager


def current(evaluator):
    runner=evaluator.suspension.current
    return runner.team_scope if runner is not None else None


@contextmanager
def scope(evaluator,capabilities=None,team=None):
    from .agent_tools import scope as authority_scope
    runner=evaluator.suspension.current
    previous=runner.team_scope if runner is not None else None
    with authority_scope(evaluator,capabilities):
        if runner is not None and team is not None:runner.team_scope=team
        try:yield
        finally:
            if runner is not None:runner.team_scope=previous
