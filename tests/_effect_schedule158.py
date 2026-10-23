"""Test-only fixed external completion schedules using real host workers."""
from contextlib import contextmanager
import threading
from unittest.mock import patch


@contextmanager
def effect_schedule(policy='already_done'):
    """Fix Future handoff timing, never language ticks/results/driver behavior.

    Callbacks in this oracle fixture are finite and independent of interpreter
    progress. Blocking/reentrant host integration tests use the original pool.
    """
    import chaos.suspension as suspension
    if policy not in ('already_done', 'first_wait'):
        raise ValueError('Unknown oracle effect schedule')
    original_executor, original_wait = suspension.ThreadPoolExecutor, suspension.wait
    gates = []
    stats = dict(policy=policy, submitted=0, completedBeforeSubmitReturns=0,
                 pendingBeforeSubmitReturns=0, waitCalls=0,
                 waitBoundaryReleases=0, shutdownReleases=0, realExecutors=0)

    def release(futures, reason):
        futures = tuple(futures)
        selected = []
        for future, gate in list(gates):
            if any(future is item for item in futures):
                gate.set();gates.remove((future, gate));selected.append(future)
                stats[reason] += 1
        return futures, selected

    def completed(futures):
        futures = tuple(futures)
        if not futures:return
        original_wait(futures, timeout=2)
        if not all(future.done() for future in futures):
            raise AssertionError('Finite oracle worker did not complete its bounded handoff')

    def controlled_wait(futures, *args, **kwargs):
        stats['waitCalls'] += 1
        futures, selected = release(futures, 'waitBoundaryReleases')
        # Select the valid schedule in which these real workers finish before
        # the driver's first original wait returns. Results remain unread here.
        completed(selected)
        return original_wait(futures, *args, **kwargs)

    class ScheduledExecutor(original_executor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs);stats['realExecutors'] += 1
        def submit(self, function, *args, **kwargs):
            stats['submitted'] += 1
            if policy == 'already_done':
                future = super().submit(function, *args, **kwargs)
                completed((future,));stats['completedBeforeSubmitReturns'] += 1
                return future
            gate = threading.Event()
            def held_worker():
                if not gate.wait(2):raise AssertionError('Oracle worker gate was not released')
                return function(*args, **kwargs)
            future = super().submit(held_worker);gates.append((future, gate))
            if future.done():raise AssertionError('Pending oracle worker completed before its gate')
            stats['pendingBeforeSubmitReturns'] += 1
            return future
        def shutdown(self, *args, **kwargs):
            release([future for future, gate in gates], 'shutdownReleases')
            return super().shutdown(*args, **kwargs)

    with patch.object(suspension, 'ThreadPoolExecutor', ScheduledExecutor), patch.object(suspension, 'wait', controlled_wait):
        try:yield stats
        finally:
            release([future for future, gate in gates], 'shutdownReleases')
    if stats['shutdownReleases'] or gates:
        raise AssertionError('Oracle schedule required a fallback worker release')
