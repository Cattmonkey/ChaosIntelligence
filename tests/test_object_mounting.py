"""Namespace relocation preserves executable semantics, never source text."""
import copy
import json
import unittest
from unittest.mock import patch

from chaos import Evaluator, LanguageError
from chaos.executable_image import (compile_objects, compile_executable,
    mount_objects, link_objects, load_executable)
from chaos.language import Node, ResourceLimit, serializable
from chaos.executable_ast import encode_tree, decode_tree
from chaos.separate_compilation import admit_libraries, validate_objects, _promote
from test_executable231 import no_frontend
from test_separate_compilation234 import evaluate, digest, no_library_parser


def rehash(row):
    row['objectHash'] = digest({k: v for k, v in row.items() if k != 'objectHash'})


def nodes(tree):
    pending = [tree]
    while pending:
        row = pending.pop()
        if row[0] not in ('node', 'tuple', 'list'):
            continue
        pending.extend(row[1])
        if row[0] == 'node':
            yield row


def metadata_modules(tree):
    result = []
    def span(value):
        if 'file' in value: result.append(value['file'])
        if 'macroOrigin' in value:
            origin = value['macroOrigin']; result.append(origin['definitionModule'])
            span(origin['definition']); span(origin['origin'])
    for node in nodes(tree):
        metadata = node[2]
        if 'span' in metadata: span(metadata['span'])
        if 'macro_module' in metadata: result.append(metadata['macro_module'])
    return result


class ObjectMounting(unittest.TestCase):
    def library(self, source, *, version=2, files=None):
        options = {} if version == 1 else {'libraries': []}
        if version == 3: options['macro_interfaces'] = True
        return compile_objects(source, files, entry='lib.chaos', **options)

    def test_two_same_path_libraries_v1_v2_v3_have_independent_cells(self):
        source = 'import "private/base.chaos" as B;var count=0;fn bump(){count+=B.step();count};export{bump}'
        for version in (1, 2, 3):
            with self.subTest(version=version):
                left = self.library(source, version=version, files={'private/base.chaos': 'fn step(){1};export{step}'})
                right = self.library(source, version=version, files={'private/base.chaos': 'fn step(){10};export{step}'})
                before = copy.deepcopy([left, right])
                with no_frontend():
                    a = mount_objects(left, 'vendor/one'); b = mount_objects(right, 'vendor/two')
                self.assertEqual([left, right], before)
                self.assertEqual({x['schema'] for x in a}, {'chaos.module-object/3' if version == 3 else 'chaos.module-object/2'})
                with no_library_parser({'lib.chaos', 'private/base.chaos', 'vendor/one/lib.chaos', 'vendor/two/lib.chaos', 'vendor/one/private/base.chaos', 'vendor/two/private/base.chaos'}):
                    image = compile_executable('import "vendor/one/lib.chaos" as A;import "vendor/two/lib.chaos" as B;[A.bump(),B.bump(),A.bump(),B.bump()]', libraries=a+b)
                self.assertEqual(evaluate(image), [1, 10, 2, 20])

    def test_v3_private_and_transitive_macro_helpers_keep_definition_site(self):
        source = 'import "helper.chaos" as H;macro hidden(x){quote{H.inc!(unquote(x))}};macro answer(x){quote{hidden!(hidden!(unquote(x)))}};export{answer}'
        library = self.library(source, version=3, files={'helper.chaos': 'macro inc(x){quote{unquote(x)+1}};export{inc}'})
        with no_frontend(): mounted = mount_objects(library, 'pkg')
        source = 'import "pkg/lib.chaos" as L;macro hidden(x){quote{unquote(x)+100}};let H=0;let temporary=10;[L.answer!(40),temporary]'
        self.assertEqual(evaluate(compile_executable(source, libraries=mounted)), [42, 10])
        for row in mounted:
            self.assertTrue(all(name.startswith('pkg/') for name in metadata_modules(row['tree'])))
            if row.get('phaseCapsule'):
                self.assertTrue(all(name.startswith('pkg/') for name in metadata_modules(row['phaseCapsule']['definitions'])))

    def test_runtime_macro_provenance_literals_aliases_and_intrinsic_captures_are_precise(self):
        source = 'macro value(){syntax_literal(complex(fraction(1,3),1))};macro make(x){quote{fn(argument,*,bonus=1){unquote(x)+argument+bonus}}};fn exact(){value!()};fn literal(){"lib.chaos"};let f=make!(40);export{exact,literal,f,value}'
        objects = self.library(source, version=3); before = copy.deepcopy(objects)
        mounted = mount_objects(objects, 'isolated')
        def captures(rows):
            return [(node[2].get('numeric_capture'), node[2].get('condition_capture'), node[2].get('macro_bindings'))
                    for row in rows for node in nodes(row['tree'])
                    if set(node[2]) & {'numeric_capture', 'condition_capture', 'macro_bindings'}]
        self.assertTrue(captures(before)); self.assertEqual(captures(before), captures(mounted))
        self.assertTrue(any('macroOrigin' in n[2].get('span', {}) for row in before for n in nodes(row['tree'])))
        source = 'import "isolated/lib.chaos" as L;[L.exact()==complex(fraction(1,3),1),L.value!()==complex(fraction(1,3),1),L.literal(),L.f(argument=1,bonus=1)]'
        self.assertEqual(evaluate(compile_executable(source, libraries=mounted)), [True, True, 'lib.chaos', 42])
        self.assertEqual(objects, before)
        self.assertEqual([x['sourceHash'] for x in mounted], [x['sourceHash'] for x in before])

    def test_encoded_DAG_reference_numbering_and_literal_path_are_unchanged(self):
        objects = self.library('42'); row = objects[0]
        shared = Node(('literal', 'lib.chaos'), {'file': 'lib.chaos', 'line': 1})
        row['tree'] = encode_tree(Node(('block', [shared, shared])), frozenset({'lib.chaos'})); rehash(row)
        before = copy.deepcopy(row['tree']); self.assertTrue(any(child[0] == 'ref' for child in before[1][1][1]))
        mounted = mount_objects(objects, 'mounted')
        tree = decode_tree(mounted[0]['tree'], frozenset({'mounted/lib.chaos'}))
        self.assertIs(tree[1][0], tree[1][1]); self.assertEqual(tree[1][0][1], 'lib.chaos')
        self.assertEqual(tree[1][0].span['file'], 'mounted/lib.chaos')
        self.assertEqual(evaluate(link_objects(mounted, entry='mounted/lib.chaos')), 'lib.chaos')

    def test_standard_objects_remain_exact_and_coalesce_across_distinct_mounts(self):
        objects = self.library('import "std/math" as M;fn answer(){M.floor(42.5)};export{answer}', version=3)
        admitted = admit_libraries(objects); standard = next(row for row in admitted if row['module'] == 'std/math')
        a = mount_objects(objects, 'a'); b = mount_objects(objects, 'b')
        self.assertEqual(next(row for row in a if row['module'] == 'std/math'), standard)
        self.assertEqual(next(row for row in b if row['module'] == 'std/math'), standard)
        image = compile_executable('import "a/lib.chaos" as A;import "b/lib.chaos" as B;[A.answer(),B.answer()]', libraries=a+b)
        self.assertEqual(sum(row['module'] == 'std/math' for row in image['objects']), 1)
        self.assertEqual(evaluate(image), [42, 42])

    def test_standard_reverse_import_or_project_provenance_is_rejected_after_original_admission(self):
        local = self.library('42')[0]
        std = next(row for row in self.library('import "std/math" as M;42') if row['module'] == 'std/math')
        for mode in ('import', 'runtime-provenance', 'phase-provenance'):
            with self.subTest(mode=mode):
                rows = copy.deepcopy([local, std]); row = rows[1]; modules = frozenset(x['module'] for x in rows)
                if mode == 'import':
                    tree = decode_tree(row['tree'], modules)
                    row['tree'] = encode_tree(Node(('block', [Node(('import', '../lib.chaos', 'Library'))]+list(tree[1]))), modules)
                    row['imports'] = {'../lib.chaos': 'lib.chaos'}; row['requires'] = {'lib.chaos': local['objectHash']}
                elif mode == 'runtime-provenance':
                    row['tree'][2]['span'] = {'file': 'lib.chaos', 'line': 1}
                else:
                    rows = list(_promote({x['module']: x for x in rows}).values())
                    row = next(x for x in rows if x['module'] == 'std/math')
                    phase = self.library('macro helper(x){quote{unquote(x)}};42', version=3)[0]['phaseCapsule']
                    row['phaseCapsule'] = copy.deepcopy(phase)
                rehash(row); validate_objects(rows)
                with no_frontend(), self.assertRaises(LanguageError) as caught: mount_objects(rows, 'mounted')
                self.assertEqual(caught.exception.code, 'executable_error')

    def test_same_named_data_types_are_namespace_distinct(self):
        objects = self.library('data Box { Boxed(value) };fn make(x){Boxed(x)};export{Box,Boxed,make}')
        a = mount_objects(objects, 'a'); b = mount_objects(objects, 'b')
        image = compile_executable('import "a/lib.chaos" as A;import "b/lib.chaos" as B;[A.make(42)==B.make(42),A.make(42)==A.make(42)]', libraries=a+b)
        self.assertEqual(evaluate(image), [False, True])
        values = evaluate(compile_executable('import "a/lib.chaos" as A;import "b/lib.chaos" as B;[A.make(42),B.make(42)]', libraries=a+b))
        self.assertEqual([value['$type'] for value in values], ['a/lib.chaos::Box', 'b/lib.chaos::Box'])

    def test_legacy_valid_data_or_import_without_file_provenance_is_mount_barrier(self):
        for kind in ('data', 'import'):
            source = 'data Box { Boxed(value) };42' if kind == 'data' else 'import "helper.chaos" as H;42'
            original = self.library(source, files=None if kind == 'data' else {'helper.chaos': '42'})
            for mode in ('missing-file', 'tuple-node'):
                rows = copy.deepcopy(original); row = next(x for x in rows if x['module'] == 'lib.chaos')
                tree = decode_tree(row['tree'], frozenset(x['module'] for x in rows)); statement = tree[1][0]
                if mode == 'missing-file': statement.span.pop('file', None)
                else: tree[1][0] = tuple(statement)
                row['tree'] = encode_tree(tree, frozenset(x['module'] for x in rows)); rehash(row)
                validate_objects(rows)
                with self.subTest(kind=kind, mode=mode), no_frontend(), self.assertRaises(LanguageError) as caught:
                    mount_objects(rows, 'mounted')
                self.assertEqual(caught.exception.code, 'executable_error')

    def test_prefix_type_normalization_namespace_unicode_and_length_bounds(self):
        objects = self.library('42')
        for prefix in (None, True, 1, [], '', '.', '..', '/a', 'a/', 'a//b', 'a/./b', 'a/../b', 'std', 'std/pkg', 'x\\y', 'x:y', 'x\x00y', '\ud800', 'x'*240):
            with self.subTest(prefix=repr(prefix)), no_frontend(), self.assertRaises(LanguageError) as caught:
                mount_objects(objects, prefix)
            self.assertEqual(caught.exception.code, 'executable_error')
        mounted = mount_objects(objects, '库/版本')
        self.assertEqual(mounted[0]['module'], '库/版本/lib.chaos')
        self.assertEqual(evaluate(link_objects(mounted, entry='库/版本/lib.chaos')), 42)

    def test_same_namespace_never_merges_conflicting_implementations(self):
        a = mount_objects(self.library('fn answer(){1};export{answer}'), 'same')
        b = mount_objects(self.library('fn answer(){2};export{answer}'), 'same')
        with self.assertRaises(LanguageError):
            compile_executable('import "same/lib.chaos" as L;L.answer()', libraries=a+b)
        self.assertEqual(a, mount_objects(self.library('fn answer(){1};export{answer}'), 'same'))

    def test_incomplete_stale_or_rehashed_malformed_orphan_rejects_before_frontend(self):
        original = self.library('import "helper.chaos" as H;H.answer()', files={'helper.chaos': 'fn answer(){42};export{answer}'})
        for mode in ('missing', 'stale', 'orphan'):
            rows = copy.deepcopy(original)
            if mode == 'missing': rows = [row for row in rows if row['module'] != 'helper.chaos']
            elif mode == 'stale': rows[0]['objectHash'] = '0'*64
            else:
                bad = self.library('1')[0]; bad['module'] = 'orphan.chaos'; bad['tree'] = ['host-authority', 'never']; rehash(bad); rows.append(bad)
            before = copy.deepcopy(rows)
            with self.subTest(mode=mode), no_frontend(), self.assertRaises(LanguageError): mount_objects(rows, 'mounted')
            self.assertEqual(rows, before)

    def test_input_and_output_are_isolated_and_mount_is_pure_deterministic(self):
        objects = self.library('fn answer(){42};export{answer}', version=3); before = copy.deepcopy(objects)
        with no_frontend(), patch.object(Evaluator, 'run', side_effect=AssertionError('Mount executed code')), patch('chaos.macros.Expander.pure', side_effect=AssertionError('Mount expanded macros')):
            first = mount_objects(objects, 'one'); second = mount_objects(objects, 'one')
        self.assertEqual(first, second); self.assertEqual(objects, before)
        first[0]['tree'][2]['span'] = {'file': 'changed'}
        self.assertEqual(objects, before); self.assertNotEqual(first, second)

    def test_aggregate_objects_and_path_growth_obey_existing_limits(self):
        row = self.library('42')[0]
        with self.assertRaises(ResourceLimit): mount_objects([copy.deepcopy(row) for _ in range(97)], 'pkg')
        self.assertEqual(mount_objects([], 'valid'), [])

    def test_valid_input_rejects_relocated_output_byte_growth_without_mutation(self):
        objects = self.library('fn answer(){42};export{answer}'); before = copy.deepcopy(objects)
        prefix = 'namespace_' + 'x'*80
        image = compile_executable('import "'+prefix+'/lib.chaos" as Library;Library.answer()', libraries=mount_objects(objects, prefix))
        self.assertEqual(evaluate(image), 42)
        original_size = len(json.dumps(objects, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8'))
        with patch('chaos.executable_image.MAX_EXECUTABLE_BYTES', original_size+32), no_frontend():
            validate_objects(admit_libraries(objects))
            with self.assertRaises(ResourceLimit): mount_objects(objects, prefix)
        self.assertEqual(objects, before)


if __name__ == '__main__': unittest.main()
