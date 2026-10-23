"""Independent executable artifact admission, relocation and runtime oracles."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from chaos.compilation_artifact import compile_artifact, load_artifact
from chaos.compiler import compile_bundle
from chaos.language import Evaluator, LanguageError, Native, Node, ResourceLimit
from chaos.modules import ProgramBundle
from chaos.jit import compile_info
from chaos.jit_flow import info as flow_info
import chaos.compilation_artifact as artifacts
import chaos.jit_flow as flow
import _generator_flow208 as streams


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def rehash(value):
    for template in value['templates']:
        template['templateHash'] = digest({k: v for k, v in template.items() if k != 'templateHash'})
    value['artifactHash'] = digest({k: v for k, v in value.items() if k != 'artifactHash'})
    return value


SIMPLE = 'fn raw(x){if x>0{x+1}else{x-1}};let f=compile_function(raw,target="auto-flow");remember(f);[f(41),f(0)]'
FOUR = '''fn plain(x){if x>0{x+1}else{x-1}};
async fn waiting(x){await sleep(0);x+2};
fn* streaming(x){yield x;return x+3};
async fn* both(x){await sleep(0);yield x;return x+4};
let p=compile_function(plain,target="auto-flow");let a=compile_function(waiting,target="auto-flow");
let g=compile_function(streaming,target="auto-flow");let ag=compile_function(both,target="auto-flow");
remember(p,a,g,ag);[p(41),await a(40),collect(g(42)),await acollect(ag(42))]'''


def run(test, source, bundle=None, remaining=None, cancel=None):
    effects = []; functions = []; cold = []; base = [None]
    e = Evaluator(step_limit=30000, timeout=30,
        tool_handler=lambda name, value: effects.append((name, copy.deepcopy(value))) or value)
    def remember(*values):
        functions.extend(values)
        cold.extend(flow_info(e, f)['resumeCalls'] for f in values)
    def mark():
        base[0] = e.steps
        if remaining is not None:
            e.step_limit = e.steps + remaining
    e.env.bind('remember', Native('remember', remember, e, captures=()))
    e.env.bind('mark', Native('mark', mark, e, captures=()))
    if cancel is not None:
        e.cancelled = lambda: base[0] is not None and e.steps - base[0] >= cancel
    with streams.observe_states() as states:
        try:
            value = ('value', e.run(source if bundle is None else bundle))
        except LanguageError as failure:
            value = ('error', failure.record())
            if isinstance(failure, ResourceLimit) and '超过计算步数限制' in failure.message:
                test.assertEqual(failure.message, '超过计算步数限制 ' + str(e.step_limit))
    test.assertFalse(e.suspension.active); test.assertIsNone(e.suspension.pool)
    test.assertEqual(e.depth, 0); test.assertEqual(e.frames, [])
    test.assertTrue(all(s.closed and not s.frames and not s._owned_activations
        and not s._transfers and s.pending_cursor is None and s.pending_request is None for s in states))
    return (value, e.steps, effects, e.native_kernel_count), e, functions, states, cold


class CompilationArtifactRuntime219(unittest.TestCase):
    def assert_bad(self, value, *, resource=False):
        with self.assertRaises(LanguageError) as caught:
            load_artifact(value)
        if resource:
            self.assertTrue(isinstance(caught.exception, ResourceLimit)
                or caught.exception.code == 'artifact_error', caught.exception.record())
        else:
            self.assertEqual(caught.exception.code, 'artifact_error', caught.exception.record())

    def test_data_only_cold_roundtrip_and_existing_report_identity(self):
        source = 'tool("must_not_run",1);fn f(x=tool("default",2)){x+1};42'
        with patch.object(Evaluator, 'run', side_effect=AssertionError('compile/load executed source')):
            data = compile_artifact(source)
            restored = json.loads(json.dumps(data, sort_keys=True))
            bundle = load_artifact(restored)
        self.assertIs(type(data), dict); self.assertEqual(data, restored)
        self.assertEqual(data['schema'], 'chaos.compiled-artifact/1')
        self.assertEqual(data['abi'], 'chaos.portable-flow-template/1')
        self.assertEqual(data['artifactHash'], digest({k: v for k, v in data.items() if k != 'artifactHash'}))
        self.assertIs(type(bundle), ProgramBundle)
        self.assertEqual(compile_bundle(bundle)['artifactHash'], compile_bundle(ProgramBundle(source))['artifactHash'])
        todo = [data]
        while todo:
            value = todo.pop()
            self.assertIn(type(value), (dict, list, str, int, float, bool, type(None)))
            if type(value) is dict:
                self.assertTrue(all(type(k) is str for k in value)); todo.extend(value.values())
            elif type(value) is list:
                todo.extend(value)
        self.assertTrue(data['templates'])

    def test_saved_decoded_rows_really_execute_all_four_flavors_without_relower(self):
        data = compile_artifact(FOUR); bundle = load_artifact(data)
        decoded = []; original = artifacts._decode_template
        def decoding(template, body):
            program = original(template, body); decoded.append(program); return program
        with patch.object(artifacts, '_decode_template', decoding), patch.object(flow, 'lower',
                side_effect=AssertionError('matching artifact function was re-lowered at execution')):
            row, e, functions, states, cold = run(self, FOUR, bundle)
        self.assertEqual(row[0], ('value', [42, 42, [42], [42]]))
        self.assertEqual(cold, [0, 0, 0, 0]); self.assertEqual(len(functions), 4)
        self.assertEqual({f.flavor for f in functions}, {'fn', 'async_fn', 'gen_fn', 'async_gen_fn'})
        self.assertTrue(states)
        for f in functions:
            program = f.compiled_plan.flow
            self.assertTrue(any(program is item for item in decoded))
            self.assertIs(program._artifact_decoded, True)
            self.assertEqual(program._artifact_hash, data['artifactHash'])
            self.assertTrue(program._artifact_template_id)
            self.assertGreater(flow_info(e, f)['resumeCalls'], 0)
            self.assertEqual(len(flow_info(e, f)), 11); self.assertEqual(len(compile_info(e, f)), 12)

    def test_optional_native_saved_template_uses_real_kernel_and_original_charge(self):
        from test_native_jit import NATIVE
        if not NATIVE: self.skipTest('Optional LLVM backend unavailable')
        source = SIMPLE.replace('auto-flow', 'native-flow')
        bundle = load_artifact(compile_artifact(source))
        with patch.object(flow, 'lower', side_effect=AssertionError('native saved template re-lowered')):
            row, e, functions, states, cold = run(self, source, bundle)
        self.assertEqual(row[0], ('value', [42, -1])); self.assertEqual(cold, [0])
        f = functions[0]; self.assertTrue(f.compiled_plan.flow._artifact_decoded)
        self.assertIsNotNone(f.compiled_plan.flow.kernel)
        self.assertGreater(row[3], 0); self.assertGreater(flow_info(e, f)['nativeResumes'], 0)
        self.assertIn('switch i32', str(f.compiled_plan.flow.kernel._module))

    def test_repeated_loads_keep_defaults_cells_owners_and_profiles_fresh(self):
        source = '''fn make(seed,value=tool("outer-default",40)){
var cell=seed;fn child(delta=tool("inner-default",1)){cell=cell+delta;value+cell};child};
let original=make(0);let f=compile_function(original,target="auto-flow");remember(original,f);[f(),f()]'''
        data = compile_artifact(source); before = copy.deepcopy(data); observations = []
        for _ in range(2):
            row, e, functions, states, cold = run(self, source, load_artifact(data))
            self.assertEqual(row[0], ('value', [41, 42]))
            self.assertEqual(row[2], [('outer-default', 40), ('inner-default', 1)])
            self.assertEqual(cold, [0, 0]); original, compiled = functions
            self.assertIsNone(original.compiled_plan)
            self.assertIs(original.env.resolve_cell('cell'), compiled.env.resolve_cell('cell'))
            self.assertIs(compiled.owner, e); self.assertTrue(compiled.compiled_plan.flow._artifact_decoded)
            observations.append((e, compiled, compiled.env.resolve_cell('cell')))
        self.assertIsNot(observations[0][0], observations[1][0])
        self.assertIsNot(observations[0][1].body, observations[1][1].body)
        self.assertIsNot(observations[0][1].compiled_plan.flow, observations[1][1].compiled_plan.flow)
        self.assertIsNot(observations[0][2], observations[1][2])
        self.assertEqual(data, before)

    def test_cold_function_and_generator_formation_does_not_implicitly_compile(self):
        source = '''fn outer(value=tool("default",40)){fn inner(){value+2};inner};
let f=outer();fn* g(){tool("unconsumed",1);yield 42};async fn a(){tool("unawaited",1);42};
[type_of(f),compile_flow_info(f).status,compile_flow_info(g).status,compile_flow_info(a).status]'''
        data = compile_artifact(source); bundle = load_artifact(data)
        with patch.object(flow, 'lower', side_effect=AssertionError('cold definition compiled')):
            row, e, functions, states, cold = run(self, source, bundle)
        self.assertEqual(row[0], ('value', ['Function', 'not_compiled', 'not_compiled', 'not_compiled']))
        self.assertEqual(row[2], [('default', 40)]); self.assertEqual(e.native_kernel_count, 0)
        self.assertEqual(states, []); self.assertEqual(functions, [])

    def test_sorted_json_preserves_orphan_order_macro_hygiene_and_import_effects(self):
        source = 'import "lib.chaos" as L;let raw=L.make!(40);let f=compile_function(raw,target="auto-flow");remember(f);[f(2),f(3)]'
        files = {'z.chaos': 'macro z(){quote{let saved=1;saved}};z!()',
                 'a.chaos': 'macro a(){quote{let saved=2;saved}};a!()',
                 'lib.chaos': 'tool("library",1);macro make(x){quote{fn(value){let saved=unquote(x);saved+value}}};export{make}',
                 'main.chaos': source}
        data = compile_artifact(source, files=files)
        self.assertEqual(data['fileOrder'], list(files))
        loaded = load_artifact(json.loads(json.dumps(data, sort_keys=True)))
        self.assertEqual(list(loaded.files), list(files))
        plain = ProgramBundle(source, files=files)
        self.assertEqual(compile_bundle(loaded)['artifactHash'], compile_bundle(plain)['artifactHash'])
        expected, *_ = run(self, source, plain); actual, e, functions, states, _ = run(self, source, loaded)
        self.assertEqual(actual, expected); self.assertEqual(actual[0], ('value', [42, 43]))
        self.assertEqual(actual[2], [('library', 1)])
        self.assertTrue(functions[0].compiled_plan.flow._artifact_decoded)

    def test_recomputed_hash_never_authorizes_changed_cfg_effects_or_node_paths(self):
        source = 'fn raw(x){try{match x{0=>1;_=>2}}finally{tool("cleanup",0)}};compile_function(raw,target="auto-flow")(0)'
        original = compile_artifact(source); self.assertTrue(original['templates'])
        def changed(mode):
            data = copy.deepcopy(original); template = data['templates'][0]; code = template['instructions']
            if mode == 'opcode': code[0][0] = 'done'; code[0] = ['done']
            elif mode == 'branch':
                row = next(row for row in code if row[0] == 'branch'); row[2], row[3] = row[3], row[2]
            elif mode == 'effects': template['effects'] = [[] for _ in code]
            elif mode == 'flags': template['flagCount'] = True
            elif mode == 'node-path':
                row = next(row for row in code if len(row) > 1 and type(row[1]) is dict and 'node' in row[1]); row[1]['node'] = [999999]
            elif mode == 'route':
                row = next(row for row in code if row[0] == 'jump'); row[1] = 0
            return rehash(data)
        for mode in ('opcode', 'branch', 'effects', 'flags', 'node-path', 'route'):
            with self.subTest(mode=mode): self.assert_bad(changed(mode))

    def test_source_identity_abi_closure_and_stale_stdlib_reject_after_rehash(self):
        import chaos.modules as modules
        source = 'import "std/math" as m;fn f(x){x+1};compile_function(f,target="auto-flow")(41)'
        data = compile_artifact(source)
        for mode in ('version', 'flow-abi', 'source', 'dependencies', 'expanded', 'file-order'):
            bad = copy.deepcopy(data)
            if mode == 'version': bad['compiler']['version'] = 'invalid-version'
            elif mode == 'flow-abi': bad['compiler']['flowABI'] = 'pc-host-events/unknown'
            elif mode == 'source': bad['files'][bad['entry']] += ';tool("forged",1)'
            elif mode == 'dependencies': bad['dependencies'][bad['entry']] = {}
            elif mode == 'expanded': bad['expandedHashes'][bad['entry']] = '0' * 64
            else: bad['fileOrder'] = bad['fileOrder'] * 2
            with self.subTest(mode=mode): self.assert_bad(rehash(bad))
        with patch.dict(modules.STDLIB, {'std/math': modules.STDLIB['std/math'] + '\n'}):
            self.assert_bad(data)

    def test_malformed_cyclic_deep_and_foreign_host_data_are_bounded(self):
        data = compile_artifact('fn f(){42};42')
        self.assert_bad(None); self.assert_bad({'schema': data['schema']})
        bad = copy.deepcopy(data); bad['cycle'] = bad; self.assert_bad(bad, resource=True)
        deep = None
        for _ in range(300): deep = [deep]
        bad = copy.deepcopy(data); bad['deep'] = deep; self.assert_bad(bad, resource=True)
        class Host:
            def __iter__(self): raise AssertionError('foreign host iterator executed')
            def __repr__(self): raise AssertionError('foreign host repr executed')
        bad = copy.deepcopy(data); bad['host'] = Host(); self.assert_bad(bad)

    def test_loaded_body_mutation_keeps_root_identity_but_falls_back_to_new_lowering(self):
        source = 'fn raw(){40+2};let f=compile_function(raw,target="auto-flow");remember(f);f()'
        bundle = load_artifact(compile_artifact(source)); fn = bundle.expanded_trees[bundle.entry][1][0][2]
        body = fn[3]; self.assertEqual(body[0], 'block')
        body[1][0] = Node(('literal', 99)); self.assertIs(fn[3], body)
        original = flow.lower; calls = []
        def lower(function): calls.append(function); return original(function)
        with patch.object(flow, 'lower', lower): row, e, functions, states, _ = run(self, source, bundle)
        self.assertEqual(row[0], ('value', 99)); self.assertTrue(any(f.body is functions[0].body and f.owner is e and f.flavor == functions[0].flavor for f in calls))
        self.assertFalse(getattr(functions[0].compiled_plan.flow, '_artifact_decoded', False))

    def test_same_shape_dynamic_code_function_keeps_original_lower_fallback(self):
        source = '''let code=code_from("fn raw(x){x+1}");let raw=function_from(code);
let f=compile_function(raw,target="auto-flow");remember(f);f(41)'''
        bundle = load_artifact(compile_artifact(source)); original = flow.lower; calls = []
        def lower(function): calls.append(function); return original(function)
        with patch.object(flow, 'lower', lower): row, e, functions, states, _ = run(self, source, bundle)
        self.assertEqual(row[0], ('value', 42)); self.assertTrue(any(f.body is functions[0].body and f.owner is e and f.flavor == functions[0].flavor for f in calls))
        self.assertFalse(getattr(functions[0].compiled_plan.flow, '_artifact_decoded', False))

    def test_plain_bundle_compile_path_and_query_shapes_stay_compatible(self):
        with patch.object(flow, 'lower', wraps=flow.lower) as lower:
            row, e, functions, states, cold = run(self, SIMPLE)
        self.assertEqual(row[0], ('value', [42, -1])); self.assertTrue(lower.called)
        self.assertFalse(getattr(functions[0].compiled_plan.flow, '_artifact_decoded', False))
        self.assertEqual(cold, [0]); self.assertEqual(len(compile_info(e, functions[0])), 12)
        self.assertEqual(len(flow_info(e, functions[0])), 11)

    def test_actual_cumulative_execution_budgets_and_cancel_match_without_counter_reset(self):
        source = 'fn raw(x){try{if x>0{tool("body",x);x+1}else{x-1}}finally{tool("cleanup",0)}};let f=compile_function(raw,target="auto-flow");remember(f);mark();[f(1),f(0)]'
        data = compile_artifact(source)
        baseline, *_ = run(self, source)
        actual, *_ = run(self, source, load_artifact(data))
        self.assertEqual(actual, baseline)
        # All boundaries in this finite body; setup counters remain actual.
        for remaining in range(1, 55):
            with self.subTest(remaining=remaining):
                expected, *_ = run(self, source, remaining=remaining)
                observed, *_ = run(self, source, load_artifact(data), remaining=remaining)
                self.assertEqual(observed, expected)
        for cancel in (1, 3, 8, 16, 32):
            with self.subTest(cancel=cancel):
                expected, *_ = run(self, source, cancel=cancel)
                observed, *_ = run(self, source, load_artifact(data), cancel=cancel)
                self.assertEqual(observed, expected)


    def test_recursive_environment_links_fresh_original_cells_without_implicit_compile(self):
        source = 'rec{fn even(n){if n==0{true}else{odd(n-1)}};fn odd(n){if n==0{false}else{even(n-1)}}};let f=compile_function(even,target="auto-flow");remember(even,odd,f);[f(6),odd(5)]'
        row, e, functions, states, cold = run(self, source, load_artifact(compile_artifact(source)))
        self.assertEqual(row[0], ('value', [True, True]))
        even, odd, compiled = functions
        self.assertIs(even.env, odd.env); self.assertIs(compiled.env, even.env)
        self.assertIsNone(even.compiled_plan); self.assertIsNone(odd.compiled_plan)
        self.assertTrue(compiled.compiled_plan.flow._artifact_decoded)
        self.assertEqual(cold, [0, 0, 0])

    def artifact_host(self, method_name):
        import test_pattern_flow218_surfaces as previous
        original = Evaluator.run; loaded = []
        def admission(evaluator, source, bindings=None, *, files=None, entry='main.chaos'):
            if type(source) is str:
                data = compile_artifact(source, files=files, entry=entry)
            elif type(source) is ProgramBundle:
                data = compile_artifact(source.files[source.entry], files=source.files,
                    entry=source.entry, language_versions=source.language_versions)
            else:
                return original(evaluator, source, bindings, files=files, entry=entry)
            bundle = load_artifact(data); loaded.append(bundle)
            return original(evaluator, bundle, bindings)
        fixture = previous.PatternSurfaces218(methodName=method_name)
        with patch.object(Evaluator, 'run', admission), streams.observe_states() as states:
            getattr(fixture, method_name)()
        self.assertTrue(loaded); self.assertTrue(states)
        self.assertTrue(all(s.program._artifact_decoded for s in states))
        self.assertTrue(all(s.closed and not s.frames and not s._owned_activations
            and not s._transfers and s.pending_cursor is None and s.pending_request is None for s in states))

    def test_loaded_artifacts_preserve_real_agent_tool_team_and_bot_once_effects(self):
        self.artifact_host('test_agent_tool_team_bot_callbacks_keep_subject_guard_and_body_effects_once')

    def test_loaded_artifacts_preserve_real_software_robot_admission_and_duplicate_rejection(self):
        self.artifact_host('test_robot_admission_once_and_duplicate_command_rejection_survive_pattern_callback')


if __name__ == '__main__': unittest.main()
