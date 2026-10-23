"""Compare real implicit planning against an actual inert plan, not flag toggles.

The old parser/link/macro budget must not pay for optional optimization discovery.
All fixtures are local language values; no models, files or device effects run.
"""
import json
import unittest
from unittest.mock import patch

from chaos.compiler import compile_bundle
from chaos.language import Evaluator, LanguageError, ResourceLimit
from chaos.modules import ProgramBundle
from chaos.portable_optimizer import LiteralPlan, build_plan


def definition(source):
    return {'kind':'program','entry':'main.chaos',
            'files':{'main.chaos':source},'languageVersions':{'main.chaos':2}}


def inert_plan(trees, checkpoint=None):
    # An actual recognized empty plan; no hidden walk or forwarded callback.
    return LiteralPlan({},0,False)


def run_observed(source, inert, *, bindings=None, limit=100000, cancel=None):
    trace=[]
    evaluator=Evaluator(step_limit=limit,timeout=10,trace_handler=trace.append,
                        optimize_literals=True)
    evaluator.cancelled=lambda:cancel is not None and evaluator.steps>=cancel
    with patch('chaos.portable_optimizer.build_plan',side_effect=inert_plan) if inert else patch('chaos.portable_optimizer.build_plan',wraps=build_plan):
        try:
            result=('value',evaluator.run(source,bindings=bindings))
        except LanguageError as error:
            result=('error',error.record())
    return result,evaluator.steps,trace


class PortableCheckpoints(unittest.TestCase):
    def assert_all_thresholds(self,source,bindings=None,expected=None):
        reference=run_observed(source,True,bindings=bindings)
        actual=run_observed(source,False,bindings=bindings)
        self.assertEqual(actual,reference)
        if expected is not None:self.assertEqual(actual[0],('value',expected))
        # Small real dynamic fixtures: exhaust every original logical threshold.
        for threshold in range(reference[1]+2):
            with self.subTest(kind='budget',threshold=threshold):
                self.assertEqual(run_observed(source,False,bindings=bindings,limit=threshold),
                                 run_observed(source,True,bindings=bindings,limit=threshold))
            with self.subTest(kind='cancel',threshold=threshold):
                self.assertEqual(run_observed(source,False,bindings=bindings,cancel=threshold),
                                 run_observed(source,True,bindings=bindings,cancel=threshold))

    def test_implicit_plan_keeps_parse_macro_link_callback_count_exact(self):
        source='import "math.chaos" as M;M.answer'
        files={'math.chaos':'macro forty(){quote{6*7}};let answer=forty!();export{answer};'}
        observed=[]
        for inert in (True,False):
            calls=[]
            with patch('chaos.portable_optimizer.build_plan',side_effect=inert_plan) if inert else patch('chaos.portable_optimizer.build_plan',wraps=build_plan):
                bundle=ProgramBundle(source,files,checkpoint=lambda:calls.append(1),compiler_cache=None)
            observed.append((len(calls),bundle.summary()))
            self.assertGreater(len(calls),0)
            self.assertEqual(bundle._literal_plan.info()['candidateSites']==0,inert)
        self.assertEqual(observed[0],observed[1])

    def test_implicit_bundle_cancel_failure_occurs_at_same_legacy_callback(self):
        source='macro forty(){quote{6*7}};forty!()'
        total=[]
        with patch('chaos.portable_optimizer.build_plan',side_effect=inert_plan):
            ProgramBundle(source,checkpoint=lambda:total.append(1),compiler_cache=None)
        for threshold in range(1,len(total)+2):
            results=[]
            for inert in (True,False):
                calls=[]
                def checkpoint():
                    calls.append(1)
                    if len(calls)>=threshold:raise ResourceLimit('legacy construction cancelled')
                with patch('chaos.portable_optimizer.build_plan',side_effect=inert_plan) if inert else patch('chaos.portable_optimizer.build_plan',wraps=build_plan):
                    try:
                        ProgramBundle(source,checkpoint=checkpoint,compiler_cache=None)
                        result=('value',len(calls))
                    except LanguageError as error:result=('error',len(calls),error.record())
                results.append(result)
            self.assertEqual(results[0],results[1],threshold)

    def test_code_from_function_from_all_budget_and_cancel_thresholds(self):
        source='let f=function_from(code_from("fn(){6*7}"));f()'
        self.assert_all_thresholds(source,expected=42)

    def test_program_from_all_budget_and_cancel_thresholds(self):
        source='program_info(program_from(input)).entry'
        self.assert_all_thresholds(source,bindings={'input':definition('6*7')},expected='main.chaos')

    def test_module_from_all_budget_and_cancel_thresholds(self):
        source='let m=module_from(program_from(input));m.answer()'
        self.assert_all_thresholds(source,bindings={'input':definition('fn answer(){6*7};export{answer};')},expected=42)

    def test_dynamic_error_keeps_actual_generated_source_span_and_trace(self):
        cases=[('let f=function_from(code_from("fn(){1/0}"));f()',None,'code/'),
               ('let m=module_from(program_from(input));m.answer()',
                {'input':definition('fn answer(){1/0};export{answer};')},'module/')]
        for source,bindings,prefix in cases:
            with self.subTest(kind=prefix):
                actual=run_observed(source,False,bindings=bindings)
                self.assertEqual(actual,run_observed(source,True,bindings=bindings))
                self.assertEqual(actual[0][0],'error')
                location=actual[0][1]['location']
                self.assertTrue(location['file'].startswith(prefix),location)
                self.assertGreater(location['line'],0);self.assertGreater(location['column'],0)
                self.assertTrue(any(event['location'].get('file','').startswith(prefix) for event in actual[2]))

    def test_explicit_compiler_planner_callback_still_interrupts_actual_walk(self):
        bundle=ProgramBundle('6*7')
        def stop():raise ResourceLimit('explicit planner stopped')
        with self.assertRaisesRegex(ResourceLimit,'explicit planner stopped'):
            build_plan(bundle.expanded_trees,checkpoint=stop)
        inside=[False];seen=[]
        def observed(trees,checkpoint=None):
            inside[0]=True
            try:return build_plan(trees,checkpoint=checkpoint)
            finally:inside[0]=False
        def checkpoint():
            if inside[0]:
                seen.append('planner');raise ResourceLimit('explicit compiler planner stopped')
        with patch('chaos.portable_optimizer.build_plan',side_effect=observed):
            with self.assertRaisesRegex(ResourceLimit,'explicit compiler planner stopped'):
                compile_bundle(bundle,checkpoint=checkpoint)
        self.assertEqual(seen,['planner'])
