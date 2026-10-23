import json
import unittest
from unittest.mock import patch

from chaos.compiler import compile_program, compile_bundle
from chaos.compilation_cache import CompilationCache
from chaos.language import Evaluator, LanguageError, ResourceLimit
from chaos.modules import ProgramBundle, STDLIB


class CompilerTests(unittest.TestCase):
    def test_stable_identity_and_actual_incremental_units(self):
        cache = CompilationCache()
        files = {'main.chaos': 'import "./math.chaos" as m; m.answer',
                 'math.chaos': 'let answer = 42; export {answer};'}
        cold = compile_program(files['main.chaos'], files, compiler_cache=cache)
        warm = compile_program(files['main.chaos'], files, compiler_cache=cache)
        self.assertEqual(cold['artifactHash'], warm['artifactHash'])
        self.assertEqual(cold['summary']['cacheMisses'], 2)
        self.assertEqual(warm['summary']['cacheHits'], 2)
        files['math.chaos'] = 'let answer = 43; export {answer};'
        changed = compile_program(files['main.chaos'], files, compiler_cache=cache)
        byname = {row['file']: row for row in changed['modules']}
        before = {row['file']: row for row in warm['modules']}
        self.assertEqual(byname['main.chaos']['unitId'], before['main.chaos']['unitId'])
        self.assertNotEqual(byname['main.chaos']['dependencyHash'], before['main.chaos']['dependencyHash'])
        self.assertEqual(changed['summary']['cacheHits'], 1)
        self.assertNotEqual(changed['artifactHash'], cold['artifactHash'])

    def test_identity_includes_orphans_dialect_stdlib_and_compiler(self):
        source = 'import "std/math" as m; m.pi'
        first = compile_program(source, {'orphan.chaos':'1'}, compiler_cache=None)
        orphan = compile_program(source, {'orphan.chaos':'2'}, compiler_cache=None)
        dialect = compile_program(source, {'orphan.chaos':'1'}, language_versions={'orphan.chaos':1}, compiler_cache=None)
        with patch.dict(STDLIB, {'std/math': STDLIB['std/math']+'\n'}):
            std = compile_program(source, {'orphan.chaos':'1'}, compiler_cache=None)
        with patch('chaos.compiler.__version__', '0.58.1'):
            compiler = compile_program(source, {'orphan.chaos':'1'}, compiler_cache=None)
        self.assertEqual(len({x['artifactHash'] for x in (first,orphan,dialect,std,compiler)}), 5)

    def test_report_contains_no_source_or_effect_execution(self):
        marker = 'do-not-echo-this-literal'
        source = 'let secret = "'+marker+'"; agent Worker {provider:"local"}; Worker(secret)'
        report = compile_program(source)
        text = json.dumps(report)
        self.assertNotIn(marker, text)
        self.assertIn('call', report['summary']['effects'])
        self.assertIn('unknown', report['summary']['effects'])
        self.assertEqual(report['target'], 'portable-ast')

    def test_shadowable_calls_never_inferred_pure(self):
        for source in ('sqrt(4)', 'let sqrt=fn(x){throw x};sqrt(4)', 'fn f(){42}; f()'):
            with self.subTest(source=source):
                self.assertIn('unknown', compile_program(source)['summary']['effects'])

    def test_pipeline_is_dynamic_call_and_boolean_operators_are_control(self):
        report = compile_program('input |> worker')
        self.assertIn('call', report['summary']['effects'])
        self.assertIn('unknown', report['summary']['effects'])
        self.assertIn('control', compile_program('true && false')['summary']['effects'])

    def test_only_function_body_is_latent(self):
        report = compile_program('async fn worker(value = get_default()) { await perform(value) }; 42')
        row = report['modules'][0]['analysis']
        self.assertIn('call', row['effects'])
        self.assertNotIn('suspend', row['effects'])
        self.assertIn('suspend', row['latentEffects'])
        self.assertIn('call', row['latentEffects'])

    def test_class_decorators_defaults_and_protocols_are_barriers(self):
        source = 'class Box {var value = initialize(); fn read(self){self.value}}; Box()[0]'
        analysis = compile_program(source)['summary']
        self.assertIn('call', analysis['effects'])
        self.assertIn('protocol', analysis['effects'])
        self.assertIn('protocol', analysis['latentEffects'])

    def test_macro_expansion_is_included_and_always_validated(self):
        source = 'macro later(x){quote{await unquote(x)}};later!(task)'
        result = compile_program(source)
        self.assertIn('suspend', result['summary']['effects'])
        self.assertIn('await', result['modules'][0]['analysis']['kinds'])

    def test_compile_bundle_does_not_expand_again(self):
        bundle = ProgramBundle('macro answer(){quote{42}};answer!()')
        with patch('chaos.macros.expand_trees', side_effect=AssertionError('expanded again')):
            self.assertEqual(compile_bundle(bundle)['entry'], 'main.chaos')

    def test_checkpoint_compile_bypasses_and_remains_cancellable(self):
        cache = CompilationCache(); compile_program('42', compiler_cache=cache)
        before = cache.info(); ticks = []
        result = compile_program('42', compiler_cache=cache, checkpoint=lambda:ticks.append(1))
        self.assertEqual(cache.info(), before)
        self.assertEqual(result['summary']['cacheBypasses'], 1)
        self.assertGreater(len(ticks), 1)
        def cancelled(): raise ResourceLimit('stopped')
        with self.assertRaises(ResourceLimit): compile_program('42', compiler_cache=cache, checkpoint=cancelled)

    def test_analysis_has_hard_structural_bound(self):
        from chaos.compiler import _analysis
        from chaos.language import Node
        # This deliberately malformed host tree is never a source program.
        loop = []; loop.append(loop)
        with self.assertRaises(ResourceLimit): _analysis(Node(('list',loop)))

    def test_async_comprehension_sources_mark_suspension(self):
        for source in ('[x async for x in stream]', '{x async for x in stream}', '{x:x async for x in stream}'):
            with self.subTest(source=source):
                self.assertIn('suspend', compile_program(source)['summary']['effects'])

    def test_source_mirror_and_legacy_summary_contract(self):
        bundle = ProgramBundle('42', {'main.chaos':'unknown()'})
        self.assertEqual(Evaluator().run(bundle),42)
        self.assertEqual(set(bundle.summary()),{'valid','entry','files','dependencies','exports','macroExports'})
        self.assertEqual(compile_bundle(bundle)['summary']['effects'], ['control'])

    def test_escaped_unicode_and_legacy_module_path_remain_compilable(self):
        source = r'"\ud800"'
        result = compile_program(source, entry='a\ud800.chaos')
        self.assertEqual(result['entry'], 'a\ud800.chaos')
        self.assertEqual(len(result['artifactHash']), 64)

    def test_invalid_orphan_not_hidden_by_warm_cache(self):
        cache = CompilationCache(); compile_program('42',compiler_cache=cache)
        with self.assertRaises(LanguageError): compile_program('42',{'orphan.chaos':'let ='},compiler_cache=cache)


if __name__ == '__main__': unittest.main()
