"""Observable incremental parsing contracts without sharing language state."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import threading
import unittest
from unittest import mock

from chaos.compilation_cache import CompilationCache, unit_identity
from chaos.language import Evaluator, LanguageError, ResourceLimit
from chaos.modules import ProgramBundle, STDLIB


class CompilationCacheTests(unittest.TestCase):
    def setUp(self):
        self.cache = CompilationCache()

    def bundle(self, source='40+2', files=None, **options):
        return ProgramBundle(source, files, compiler_cache=self.cache, **options)

    def statuses(self, bundle):
        return {name: item['cacheStatus'] for name, item in bundle.compilation_units.items()}

    def test_repeated_bundle_reuses_parse_and_stable_identity(self):
        first = self.bundle(); second = self.bundle()
        self.assertEqual(self.statuses(first), {'main.chaos': 'miss'})
        self.assertEqual(self.statuses(second), {'main.chaos': 'hit'})
        self.assertEqual(first.compilation_units['main.chaos']['unitId'], second.compilation_units['main.chaos']['unitId'])
        self.assertEqual(Evaluator().run(first), 42)
        self.assertEqual(Evaluator().run(second), 42)
        self.assertEqual(self.cache.info()['entries'], 1)
        self.assertEqual(self.cache.info()['hits'], 1)

    def test_mutable_ast_children_and_spans_are_private_on_both_paths(self):
        first = self.bundle('let x=[40,2];x')
        expected_tree = copy.deepcopy(first.trees['main.chaos'])
        first.trees['main.chaos'][1].clear()
        first.trees['main.chaos'].span['file'] = 'poison.chaos'
        second = self.bundle('let x=[40,2];x')
        self.assertEqual(second.trees['main.chaos'], expected_tree)
        self.assertEqual(second.trees['main.chaos'].span['file'], 'main.chaos')
        second.trees['main.chaos'][1][0].span['line'] = 999
        second.trees['main.chaos'][1][0].macro_bindings = {'poison': [1]}
        second.expanded_trees['main.chaos'][1].clear()
        third = self.bundle('let x=[40,2];x')
        self.assertFalse(hasattr(third.trees['main.chaos'][1][0], 'macro_bindings'))
        self.assertEqual(third.trees['main.chaos'][1][0].span['line'], 1)
        self.assertEqual(Evaluator().run(third), [40, 2])

    def test_only_changed_dependency_reparses(self):
        source = 'import "./lib.chaos" as lib;lib.answer'
        first = self.bundle(source, {'lib.chaos': 'let answer=41;export {answer};'})
        second = self.bundle(source, {'lib.chaos': 'let answer=42;export {answer};'})
        self.assertEqual(self.statuses(second), {'main.chaos': 'hit', 'lib.chaos': 'miss'})
        self.assertNotEqual(first.compilation_units['lib.chaos']['unitId'], second.compilation_units['lib.chaos']['unitId'])
        self.assertEqual(Evaluator().run(second), 42)

    def test_entry_mirror_remains_authoritative(self):
        self.bundle('41', {'main.chaos': '999'})
        changed = self.bundle('42', {'main.chaos': '41'})
        self.assertEqual(Evaluator().run(changed), 42)
        self.assertEqual(changed.files['main.chaos'], '42')
        self.assertEqual(self.statuses(changed)['main.chaos'], 'miss')

    def test_filename_and_dialect_are_identity_components(self):
        a = self.bundle('42', entry='a.chaos', language_versions={'a.chaos': 1})
        b = self.bundle('42', entry='b.chaos', language_versions={'b.chaos': 1})
        c = self.bundle('42', entry='a.chaos', language_versions={'a.chaos': 2})
        units = [next(iter(x.compilation_units.values())) for x in (a, b, c)]
        self.assertEqual(len({x['sourceHash'] for x in units}), 1)
        self.assertEqual(len({x['unitId'] for x in units}), 3)
        self.assertTrue(all(x['cacheStatus'] == 'miss' for x in units))
        self.assertEqual(b.trees['b.chaos'].span['file'], 'b.chaos')

    def test_abi_invalidates_retained_units(self):
        first = self.bundle()
        with mock.patch('chaos.compilation_cache.COMPILER_ABI', 'test.abi/2'):
            second = self.bundle()
        self.assertEqual(self.statuses(second)['main.chaos'], 'miss')
        self.assertNotEqual(first.compilation_units['main.chaos']['unitId'], second.compilation_units['main.chaos']['unitId'])
        self.assertEqual(self.statuses(self.bundle())['main.chaos'], 'hit')

    def test_stdlib_actual_content_invalidates(self):
        source = 'import "std/math" as math;math.pi'
        first = self.bundle(source)
        with mock.patch.dict(STDLIB, {'std/math': 'let pi=42;export {pi};'}):
            second = self.bundle(source)
        self.assertEqual(self.statuses(second), {'main.chaos': 'hit', 'std/math': 'miss'})
        self.assertNotEqual(first.compilation_units['std/math']['sourceHash'], second.compilation_units['std/math']['sourceHash'])
        self.assertEqual(Evaluator().run(second), 42)

    def test_macro_expansion_rebuilds_for_changed_import(self):
        source = 'import "./lib.chaos" as lib;lib.answer!()'
        self.bundle(source, {'lib.chaos': 'macro answer(){quote {41}};export {answer};'})
        second = self.bundle(source, {'lib.chaos': 'macro answer(){quote {42}};export {answer};'})
        self.assertEqual(self.statuses(second)['main.chaos'], 'hit')
        self.assertEqual(Evaluator().run(second), 42)

    def test_global_macro_hygiene_rescans_unchanged_units(self):
        source = 'macro make(){quote {let temp=42;temp}};make!()'
        first = self.bundle(source)
        second = self.bundle(source, {'orphan.chaos': 'let __chaos_macro_1_temp=99;'})
        self.assertEqual(self.statuses(second)['main.chaos'], 'hit')
        self.assertNotEqual(first.expanded_trees['main.chaos'], second.expanded_trees['main.chaos'])
        self.assertEqual(Evaluator().run(second), 42)

    def test_invalid_orphan_and_cycle_still_rejected_on_warm_parse(self):
        with self.assertRaises(LanguageError):
            self.bundle('42', {'orphan.chaos': 'export {missing};'})
        before = self.cache.info()['hits']
        with self.assertRaises(LanguageError):
            self.bundle('42', {'orphan.chaos': 'export {missing};'})
        self.assertEqual(self.cache.info()['hits'] - before, 2)
        source = 'import "./lib.chaos" as lib;42'
        files = {'lib.chaos': 'import "./main.chaos" as root;'}
        for _ in range(2):
            with self.assertRaisesRegex(LanguageError, '循环导入'):
                self.bundle(source, files)

    def test_parse_failures_never_enter_cache(self):
        for _ in range(2):
            with self.assertRaises(LanguageError): self.bundle('let =')
        self.assertEqual(self.cache.info()['entries'], 0)
        self.assertEqual(self.cache.info()['misses'], 2)
        self.assertEqual(self.cache.info()['hits'], 0)

    def test_explicit_none_disables_reuse_without_touching_default(self):
        result = ProgramBundle('42', compiler_cache=None)
        self.assertEqual(self.statuses(result), {'main.chaos': 'disabled'})
        self.assertEqual(Evaluator().run(result), 42)

    def test_checkpoint_bypasses_reads_writes_and_preserves_callback_count(self):
        source = 'macro answer(){quote {42}};answer!()'
        self.bundle(source)
        before = self.cache.info()
        counts = []
        for cache in (None, self.cache):
            calls = []
            result = ProgramBundle(source, compiler_cache=cache, checkpoint=lambda: calls.append(1))
            self.assertEqual(self.statuses(result), {'main.chaos': 'checkpoint'})
            self.assertEqual(Evaluator().run(result), 42)
            counts.append(len(calls))
        self.assertEqual(counts[0], counts[1])
        self.assertGreater(counts[0], 20)
        self.assertEqual(self.cache.info(), before)

    def test_checkpoint_cancellation_matches_uncached_error_and_never_retains(self):
        source = 'let values=[1,2,3];values'
        self.bundle(source)
        before = self.cache.info()
        errors = []
        for cache in (None, self.cache):
            count = [0]
            def checkpoint():
                count[0] += 1
                if count[0] == 15: raise ResourceLimit('cancelled')
            with self.assertRaises(ResourceLimit) as caught:
                ProgramBundle(source, compiler_cache=cache, checkpoint=checkpoint)
            errors.append(caught.exception.record())
        self.assertEqual(errors[0], errors[1])
        self.assertEqual(self.cache.info(), before)

    def test_lru_order_and_all_three_retention_bounds(self):
        cache = CompilationCache(max_entries=2, max_source_bytes=20, max_ast_units=1000)
        def compile(source): return ProgramBundle(source, compiler_cache=cache)
        compile('1'); compile('2'); compile('1'); compile('3')
        self.assertEqual(self.statuses(compile('1'))['main.chaos'], 'hit')
        self.assertEqual(self.statuses(compile('2'))['main.chaos'], 'miss')
        info = cache.info()
        self.assertEqual(info['entries'], 2)
        self.assertGreaterEqual(info['evictions'], 2)
        self.assertLessEqual(info['sourceBytes'], 20)
        self.assertLessEqual(info['astUnits'], 1000)

    def test_aggregate_byte_and_ast_budgets_evict_without_entry_limit(self):
        byte_cache = CompilationCache(max_source_bytes=4)
        for source in ('10', '20', '30'):
            ProgramBundle(source, compiler_cache=byte_cache)
        self.assertEqual(byte_cache.info()['entries'], 2)
        self.assertEqual(byte_cache.info()['evictions'], 1)
        self.assertEqual(byte_cache.info()['sourceBytes'], 4)
        sample = CompilationCache()
        ProgramBundle('10', compiler_cache=sample)
        units = sample.info()['astUnits']
        ast_cache = CompilationCache(max_ast_units=units * 2 - 1)
        for source in ('10', '20'):
            ProgramBundle(source, compiler_cache=ast_cache)
        self.assertEqual(ast_cache.info()['entries'], 1)
        self.assertEqual(ast_cache.info()['evictions'], 1)
        self.assertLessEqual(ast_cache.info()['astUnits'], units * 2 - 1)

    def test_warm_path_skips_parser_but_changed_module_is_parsed(self):
        from chaos.language import Parser
        original = Parser.program
        parsed = []
        def tracked(parser):
            parsed.append(parser.filename)
            return original(parser)
        source = 'import "./lib.chaos" as lib;lib.value'
        with mock.patch.object(Parser, 'program', tracked):
            self.bundle(source, {'lib.chaos': 'let value=41;export {value};'})
            self.bundle(source, {'lib.chaos': 'let value=41;export {value};'})
            self.bundle(source, {'lib.chaos': 'let value=42;export {value};'})
        self.assertEqual(parsed, ['main.chaos', 'lib.chaos', 'lib.chaos'])

    def test_oversized_ast_and_source_compile_normally_without_retention(self):
        for cache in (CompilationCache(max_entries=0), CompilationCache(max_source_bytes=1),
                      CompilationCache(max_ast_units=1)):
            result = ProgramBundle('42', compiler_cache=cache)
            self.assertEqual(Evaluator().run(result), 42)
            self.assertEqual(self.statuses(result), {'main.chaos': 'oversize'})
            self.assertEqual(cache.info()['entries'], 0)
            self.assertEqual(cache.info()['oversize'], 1)

    def test_hard_limits_and_readonly_configuration(self):
        for options in ({'max_entries': 257}, {'max_source_bytes': 4000001},
                        {'max_ast_units': 400001}, {'max_entries': True},
                        {'max_entries': -1}, {'max_source_bytes': 1.0}):
            with self.assertRaises(ValueError): CompilationCache(**options)
        with self.assertRaises(AttributeError): self.cache.max_entries = 999999
        with self.assertRaises(TypeError): ProgramBundle('42', compiler_cache={})

    def test_clear_resets_counters_and_retention(self):
        self.bundle(); self.bundle(); self.cache.clear()
        info = self.cache.info()
        self.assertTrue(all(info[key] == 0 for key in ('entries', 'sourceBytes', 'astUnits', 'hits', 'misses', 'evictions', 'oversize')))
        self.assertEqual(self.statuses(self.bundle())['main.chaos'], 'miss')

    def test_cache_info_exposes_neither_source_nor_syntax(self):
        self.bundle('"private-source-marker"')
        info = self.cache.info()
        self.assertNotIn('private-source-marker', json.dumps(info))
        info['limits']['maxEntries'] = 999
        self.assertEqual(self.cache.info()['limits']['maxEntries'], 128)

    def test_concurrent_compiles_have_private_trees_and_bounded_totals(self):
        barrier = threading.Barrier(8)
        def compile(index):
            barrier.wait()
            result = self.bundle('let xs=[40,2];xs')
            value = Evaluator().run(result)
            result.trees['main.chaos'].span['worker'] = index
            result.trees['main.chaos'][1].append(('literal', index))
            return value
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(list(executor.map(compile, range(8))), [[40, 2]] * 8)
        clean = self.bundle('let xs=[40,2];xs')
        self.assertNotIn('worker', clean.trees['main.chaos'].span)
        self.assertEqual(len(clean.trees['main.chaos'][1]), 2)
        info = self.cache.info()
        self.assertEqual(info['entries'], 1)
        self.assertEqual(info['hits'] + info['misses'], 9)
        self.assertLessEqual(info['astUnits'], info['limits']['maxAstUnits'])

    def test_clear_during_parse_prevents_later_publication(self):
        from chaos import compilation_cache
        original = compilation_cache._parse
        entered, release = threading.Event(), threading.Event()
        def paused(*args, **kwargs):
            entered.set(); release.wait(5)
            return original(*args, **kwargs)
        with mock.patch.object(compilation_cache, '_parse', paused), ThreadPoolExecutor(max_workers=1) as executor:
            task = executor.submit(self.bundle)
            self.assertTrue(entered.wait(5))
            self.cache.clear(); release.set()
            self.assertEqual(Evaluator().run(task.result()), 42)
        self.assertEqual(self.cache.info()['entries'], 0)

    def test_cold_warm_effects_results_steps_and_trace_equal(self):
        source = 'import "./lib.chaos" as lib;tool("entry",lib.value);lib.value+1'
        files = {'lib.chaos': 'let value=tool("dependency",41);export {value};',
                 'orphan.chaos': 'tool("orphan",0)'}
        observations = []
        for _ in range(2):
            calls, traces, debug = [], [], []
            bundle = self.bundle(source, files)
            self.assertEqual(calls, [])
            evaluator = Evaluator(tool_handler=lambda name, value: calls.append((name, value)) or value,
                                  trace_handler=lambda event: traces.append(event),
                                  debug_hook=lambda node, env, frames: debug.append((node[0], dict(node.span))))
            result = evaluator.run(bundle)
            observations.append((result, evaluator.steps, calls, traces, debug))
        self.assertEqual(observations[0], observations[1])
        self.assertEqual(observations[0][2], [('dependency', 41), ('entry', 41)])

    def test_cold_warm_runtime_errors_keep_filename_location_and_budget(self):
        source = 'import "./lib.chaos" as lib;lib.fail()'
        files = {'lib.chaos': 'fn fail(){\n  1/0\n};export {fail};'}
        observations = []
        for _ in range(2):
            evaluator = Evaluator()
            with self.assertRaises(LanguageError) as caught:
                evaluator.run(self.bundle(source, files))
            observations.append((caught.exception.record(), evaluator.steps))
        self.assertEqual(observations[0], observations[1])
        self.assertEqual(observations[0][0]['location']['file'], 'lib.chaos')
        self.assertEqual(observations[0][0]['location']['line'], 2)

    def test_escaped_surrogate_literal_and_filename_preserve_old_parse_contract(self):
        source = '"\\ud800"'
        for entry in ('main.chaos', 'a\ud800.chaos'):
            cold = ProgramBundle(source, entry=entry, compiler_cache=None)
            first = self.bundle(source, entry=entry)
            second = self.bundle(source, entry=entry)
            self.assertEqual(cold.trees, first.trees)
            self.assertEqual(cold.trees, second.trees)
            self.assertEqual(self.statuses(second)[entry], 'hit')

    def test_hit_clone_recursion_failure_reparses_without_rejecting_program(self):
        self.bundle()
        with mock.patch('chaos.compilation_cache._clone', side_effect=RecursionError):
            result = self.bundle()
        self.assertEqual(Evaluator().run(result), 42)
        self.assertEqual(self.statuses(result)['main.chaos'], 'oversize')
        self.assertEqual(self.cache.info()['entries'], 0)

    def test_unit_identity_does_not_contain_source(self):
        value = unit_identity('"private-source-marker"', 'main.chaos', 2)
        self.assertEqual(set(value), {'sourceHash', 'unitId'})
        self.assertTrue(all(len(item) == 64 for item in value.values()))
        self.assertNotIn('private-source-marker', json.dumps(value))


if __name__ == '__main__': unittest.main()
