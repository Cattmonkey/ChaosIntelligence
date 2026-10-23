"""Logical checkpoint observations stay stable across native/typed delegation.

This is an additive regression. Existing iteration tests and their observer are
used unchanged; no class or CHECKPOINT is filtered by either observation layer.
"""
from contextlib import nullcontext
import unittest
from unittest.mock import patch
from chaos.execution_frames import ExecutionCursor
from chaos.suspension import CHECKPOINT
import test_iteration_execution_frames as iteration_tests
from _suspension_reference201 import reference
from test_suspension_syntax_frames201 import observe

SOURCES = (
    'var seen=[];fn* inner(){try{yield 42}finally{seen=append(seen,"closed")}}fn* outer(){yield from inner()}let g=outer();next(g);close(g);close(g);seen',
    'fn* inner(){let x=yield 1;try{yield x}catch e{yield e.value};return 99}fn* outer(){let r=yield from inner();return r}let g=outer();[next(g),send(g,42),throw_into(g,"caught"),next(g)]',
)

class SuspensionCheckpointBoundary201(unittest.TestCase):
    def record(self,source,legacy_syntax,legacy_iteration,**options):
        observed_types=[];original=ExecutionCursor._advance
        def observed(cursor,value,error):
            request=original(cursor,value,error)
            if request is CHECKPOINT:observed_types.append(type(cursor).__name__)
            return request
        with reference()if legacy_syntax else nullcontext()as reached,observe()as seen,patch.object(ExecutionCursor,'_advance',observed):
            result=iteration_tests.snapshot(source,legacy=legacy_iteration,**options)
        if legacy_syntax:
            self.assertEqual(seen['steps'],{})
            self.assertGreater(reached['yield'],0)
            self.assertGreater(reached['yield_from'],0)
        elif not options:
            self.assertGreater(seen['steps'].get('yield',0),0)
            self.assertGreater(seen['steps'].get('yield_from',0),0)
        return result,observed_types

    def test_logical_checkpoint_observer_preserves_delegation_send_throw_close_and_control_prefixes(self):
        for source in SOURCES:
            with self.subTest(source=source):
                baseline,base_types=self.record(source,True,True)
                self.assertTrue(baseline[5]);self.assertTrue(base_types)
                for syntax,iteration in ((True,False),(False,True),(False,False)):
                    actual,types=self.record(source,syntax,iteration)
                    self.assertEqual(actual,baseline)
                    self.assertEqual(types,base_types)
                    self.assertNotIn('SuspensionCloseCursor',types)
                # The checkpoint primitive preserves real error/cancellation
                # prefixes too; successful checkpoint equality alone is not enough.
                boundaries=sorted({baseline[5][0][0],baseline[5][len(baseline[5])//2][0],baseline[1]-1})
                for option in ('limit','cancel'):
                    for boundary in boundaries:
                        expected=iteration_tests.snapshot(source,legacy=True,**{option:boundary})
                        actual=iteration_tests.snapshot(source,legacy=False,**{option:boundary})
                        self.assertEqual(actual,expected)
