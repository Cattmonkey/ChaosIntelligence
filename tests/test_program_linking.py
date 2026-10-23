"""Pure package composition and native Program/module semantics share one compiler."""
import copy
import json
import unittest
from unittest.mock import patch

from chaos.language import Evaluator, LanguageError, ResourceLimit, ExecutionControl, serializable
from chaos.modules import ProgramBundle
from chaos.program_linking import link_program
from chaos.program_values import Program, builtins, canonical_hash, validate_definition


def definition(source='null',files=None,entry='main.chaos',versions=None):
    files=dict(files or {});files[entry]=source
    return {'kind':'program','entry':entry,'files':files,
            'languageVersions':dict(versions) if versions is not None else {name:2 for name in files}}


def run_definition(value,**options):
    return Evaluator(**options).run(ProgramBundle(value['files'][value['entry']],value['files'],value['entry'],language_versions=value['languageVersions']))


class ProgramLinkingTests(unittest.TestCase):
    def test_sources_and_versions_are_preserved_and_inputs_are_persistent(self):
        root=definition('import "vendor/math/main.chaos" as M;M.answer()')
        library=definition('// preserved spacing\r\nimport "./helper.chaos" as H;fn answer(){H.inc(41)};export{answer}',{'helper.chaos':'fn inc(x){ x + 1 };export {inc}'})
        before=copy.deepcopy((root,library));linked=link_program(root,{'vendor/math':library})
        self.assertEqual(run_definition(linked),42)
        self.assertEqual((root,library),before)
        self.assertEqual(linked['files']['vendor/math/main.chaos'],library['files']['main.chaos'])
        self.assertEqual(linked['languageVersions']['vendor/math/helper.chaos'],2)
        linked['files']['main.chaos']='0';self.assertEqual(root,before[0])

    def test_root_draft_may_refer_to_mounts_and_library_must_be_closed(self):
        root=definition('import "pkg/main.chaos" as P;P.x')
        with self.assertRaises(LanguageError):validate_definition(root)
        library=definition('let x=42;export{x}')
        self.assertEqual(run_definition(link_program(root,{'pkg':library})),42)
        unclosed=definition('import "../../shared.chaos" as X;export{}')
        with self.assertRaises(LanguageError):link_program(definition('null',{'shared.chaos':'export{}'}),{'pkg/sub':unclosed})

    def test_empty_libraries_validate_the_original_closed_definition(self):
        root=definition('42');self.assertEqual(link_program(root,{}),validate_definition(root))
        with self.assertRaises(LanguageError):link_program(definition('import "missing.chaos" as M;null'),{})

    def test_all_library_files_are_relocated_not_only_entry(self):
        library=definition('let x=0;export{x}',{'api.chaos':'let x=42;export{x}'},entry='entry.chaos')
        root=definition('import "pkg/api.chaos" as P;P.x')
        linked=link_program(root,{'pkg':library})
        self.assertEqual(set(linked['files']),{'main.chaos','pkg/entry.chaos','pkg/api.chaos'})
        self.assertEqual(run_definition(linked),42)

    def test_relative_parent_import_and_std_import_keep_original_resolver(self):
        library=definition('import "../helper.chaos" as H;import "std/math" as M;fn answer(){M.abs(H.value)};export{answer}',{'helper.chaos':'let value=-42;export{value}'},entry='inner/api.chaos')
        root=definition('import "pkg/inner/api.chaos" as P;P.answer()')
        linked=link_program(root,{'pkg':library})
        self.assertEqual(run_definition(linked),42)
        self.assertEqual(linked['files']['pkg/inner/api.chaos'],library['files']['inner/api.chaos'])
        self.assertFalse(any(name.startswith('std/') for name in linked['files']))

    def test_repeated_internal_names_and_nested_composition(self):
        a=definition('let value=20;export{value}');b=definition('let value=22;export{value}')
        middle=link_program(definition('import "one/main.chaos" as A;import "two/main.chaos" as B;let total=A.value+B.value;export{total}'),{'one':a,'two':b})
        linked=link_program(definition('import "middle/main.chaos" as M;M.total'),{'middle':middle})
        self.assertEqual(run_definition(linked),42)
        self.assertIn('middle/one/main.chaos',linked['files'])

    def test_dialect_map_is_moved_without_upgrading_source(self):
        old=definition('fn item(xs){xs[-1]};export{item}',versions={'main.chaos':1})
        modern=definition('fn item(xs){xs[-1]};export{item}',versions={'main.chaos':2})
        linked=link_program(definition('import "old/main.chaos" as O;import "new/main.chaos" as N;[try{O.item([1,42])}catch error{error.code},N.item([1,42])]'),{'old':old,'new':modern})
        self.assertEqual(run_definition(linked),['runtime_error',42])
        self.assertEqual(linked['files']['old/main.chaos'],linked['files']['new/main.chaos'])
        self.assertEqual([linked['languageVersions']['old/main.chaos'],linked['languageVersions']['new/main.chaos']],[1,2])

    def test_exported_macros_expand_across_mount_without_runtime_initialization(self):
        calls=[]
        library=definition('tool("init",1);macro inc(x){quote{unquote(x)+1}};export{inc}')
        root=definition('import "mac/main.chaos" as M;M.inc!(41)')
        linked=link_program(root,{'mac':library})
        self.assertEqual(calls,[])
        self.assertEqual(run_definition(linked,tool_handler=lambda *args:calls.append(args)),42)
        self.assertEqual(calls,[('init',1)])

    def test_invalid_library_and_root_macros_fail_before_runtime(self):
        for root,library in [(definition('null'),definition('macro bad(x){tool("effect",x)};bad!(0)')),
                             (definition('import "lib/main.chaos" as L;L.bad!(1)'),definition('macro bad(x){tool("effect",x)};export{bad}'))]:
            with self.subTest(root=root),self.assertRaises(LanguageError):link_program(root,{'lib':library})

    def test_mount_path_rules_reject_without_rewriting(self):
        library=definition('null')
        for mount in ['', '.', '..', '../lib','/lib','./lib','lib/','lib//nested','a/../b','std','std/math','C:lib','a\\b','bad\x00name',1]:
            with self.subTest(mount=mount),self.assertRaises(LanguageError):link_program(definition(),{mount:library})
        for mount in ['vendor/math','lib.chaos','Std','库']:
            with self.subTest(mount=mount):self.assertIn(mount+'/main.chaos',link_program(definition(),{mount:library})['files'])

    def test_mount_ancestors_conflict_even_with_lexical_interloper(self):
        for mounts in [['a','a/b'],['a','a-b','a/b'],['a/b','a/b/c'],['lib','lib/sub/pkg']]:
            with self.subTest(mounts=mounts),self.assertRaises(LanguageError):link_program(definition(),{name:definition() for name in mounts})

    def test_root_cannot_enter_mount_namespace_and_file_directory_conflicts(self):
        for files,mount in [({'pkg/user.chaos':'null'},'pkg'),({'pkg.chaos':'null'},'pkg.chaos'),({'pkg.chaos':'null'},'pkg.chaos/sub')]:
            with self.subTest(files=files),self.assertRaises(LanguageError):link_program(definition(files=files),{mount:definition()})
        for files in [{'a.chaos':'null','a.chaos/b.chaos':'null'},
                      {'a.chaos':'null','a.chaos-b.chaos':'null','a.chaos/b.chaos':'null'}]:
            with self.subTest(files=files),self.assertRaises(LanguageError):link_program(definition(files=files),{})
        with self.assertRaises(LanguageError):link_program(definition(),{'lib':definition(files={'a.chaos':'null','a.chaos/b.chaos':'null'})})

    def test_case_and_unicode_names_keep_exact_logical_identity(self):
        libraries={name:definition('let x='+str(number)+';export{x}') for name,number in [('Lib',1),('lib',2),('é',3),('e\u0301',4)]}
        root=definition('import "Lib/main.chaos" as A;import "lib/main.chaos" as B;import "é/main.chaos" as C;import "é/main.chaos" as D;[A.x,B.x,C.x,D.x]')
        self.assertEqual(run_definition(link_program(root,libraries)),[1,2,3,4])

    def test_strict_schema_and_no_host_protocols(self):
        class Host:
            def __eq__(self,other):raise AssertionError('host equality')
            def __str__(self):raise AssertionError('host stringify')
        class Dict(dict):pass
        invalid=[None,[],{},Dict(definition()),dict(definition(),kind=Host()),dict(definition(),extra=0),dict(definition(),languageVersions={'main.chaos':True}),dict(definition(),files={'main.chaos':Host()})]
        for value in invalid:
            with self.subTest(kind=type(value).__name__),self.assertRaises(LanguageError):link_program(value,{})
        for value in [[],None,Dict({})]:
            with self.subTest(kind=type(value).__name__),self.assertRaises(LanguageError):link_program(definition(),value)
        for value in [dict(definition(),entry='x\ud800.chaos'),definition(files={'x\ud800.chaos':'null'}),definition('\ud800')]:
            with self.assertRaises(LanguageError):link_program(value,{})

    def test_aggregate_limits_precede_compilation(self):
        library=definition(files={str(i)+'.chaos':'null' for i in range(63)})
        with patch('chaos.program_values.validate_definition',side_effect=AssertionError('must not compile')):
            with self.assertRaises(ResourceLimit):link_program(definition(),{'lib':library})
        text='//'+'x'*99985+'\nnull'
        library=definition(text,{str(i)+'.chaos':text for i in range(9)})
        with patch('chaos.program_values.validate_definition',side_effect=AssertionError('must not compile')):
            with self.assertRaises(ResourceLimit):link_program(definition('//'+('y'*200)+'\nnull'),{'lib':library})
        with self.assertRaises(ResourceLimit):link_program(definition(),{str(i):definition() for i in range(65)})
        with self.assertRaises(LanguageError):link_program(definition(),{'x'*240:definition()})

    def test_shared_compilation_depth_and_orphan_validation_remain_enforced(self):
        library=definition('export{}',{'orphan.chaos':'fn ('})
        with self.assertRaises(LanguageError):link_program(definition(),{'pkg':library})
        files={str(i)+'.chaos':('import "'+str(i+1)+'.chaos" as N;export{}' if i<31 else 'export{}') for i in range(32)}
        library={'kind':'program','entry':'0.chaos','files':files,'languageVersions':{name:2 for name in files}}
        validate_definition(library)
        with self.assertRaises(ResourceLimit):link_program(definition('import "deep/0.chaos" as D;null'),{'deep':library})

    def test_cancellation_and_failure_leave_all_inputs_unchanged(self):
        root=definition('import "lib/main.chaos" as L;L.x');libs={'lib':definition('let x=42;export{x}')};before=copy.deepcopy((root,libs));ticks=[]
        link_program(root,libs,checkpoint=lambda:ticks.append(1));self.assertGreater(len(ticks),10)
        for at in [1,5,20]:
            count=[0]
            def stop():
                count[0]+=1
                if count[0]==at:raise ExecutionControl('stop')
            with self.subTest(at=at),self.assertRaises(ExecutionControl):link_program(root,libs,checkpoint=stop)
            self.assertEqual((root,libs),before)
        with self.assertRaises(ResourceLimit):link_program(root,libs,checkpoint=lambda:(_ for _ in ()).throw(ResourceLimit('budget')))

    def test_native_accepts_owned_program_only_and_discards_origin(self):
        e=Evaluator();api=builtins(e);d=definition('let x=42;export{x}');origin={'projectId':'p','version':1,'definitionHash':canonical_hash(d)}
        program=Program(json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(',',':')),e,json.dumps(origin))
        linked=api['program_link'](definition(),{'lib':program})
        self.assertIsNone(linked.origin);self.assertEqual(program.origin,origin);self.assertEqual(program.definition,d)
        for value in [d,serializable(program),builtins(Evaluator())['program_from'](d)]:
            with self.subTest(value=type(value).__name__),self.assertRaises(LanguageError):api['program_link'](definition(),{'lib':value})
        self.assertIs(linked.owner,e)

    def test_all_live_owners_are_checked_before_library_compile(self):
        e=Evaluator();api=builtins(e);local=api['program_from'](definition());foreign=builtins(Evaluator())['program_from'](definition())
        with patch('chaos.program_values.validate_definition',side_effect=AssertionError('must not compile')):
            with self.assertRaises(LanguageError) as caught:api['program_link'](definition(),{'a':local,'b':foreign})
        self.assertEqual(caught.exception.code,'ownership_error')

    def test_native_link_and_module_identity_initialization_cache(self):
        library=definition('tool("init",null);class Box{};data Option{Some(value)};var n=0;fn count(){n=n+1;n};export{Box,Some,count}')
        root=definition('import "a/main.chaos" as A;import "a/main.chaos" as Again;import "b/main.chaos" as B;let same=A.Box==Again.Box;let other=A.Box==B.Box;let first=A.count;let second=Again.count;let third=B.count;let make=A.Some;export{same,other,first,second,third,make}')
        calls=[];e=Evaluator(tool_handler=lambda *args:calls.append(args))
        source='let lib=program_from(input.library);let p=program_link(input.root,{a:lib,b:lib});let before=length(input.events);let m=module_from(p);[m.same,m.other,m.first(),m.second(),m.third(),match m.make(42){m.make(x)=>x},program_metadata(p).origin]'
        self.assertEqual(e.run(source,{'input':{'library':library,'root':root,'events':[]}}),[True,False,1,2,1,42,None])
        self.assertEqual(calls,[('init',None),('init',None)])

    def test_native_async_generator_and_borrowed_callback_use_original_runtime(self):
        library=definition('async fn a(x){await sleep(0);callback(x)};fn* g(x){yield callback(x)};async fn* ag(x){await sleep(0);yield callback(x)};export{a,g,ag}')
        root=definition('import "lib/main.chaos" as L;let a=L.a;let g=L.g;let ag=L.ag;export{a,g,ag}')
        source='fn callback(x){x+1};let p=program_link(input.root,{lib:program_from(input.library)});let m=module_from(p,{callback:callback});[await m.a(41),collect(m.g(41)),await acollect(m.ag(41))]'
        self.assertEqual(Evaluator().run(source,{'input':{'root':root,'library':library}}),[42,[42],[42]])

    def test_owned_but_lazily_checked_program_is_revalidated(self):
        e=Evaluator();api=builtins(e);bad=definition('macro bad(x){tool("bad",x)};bad!(1)')
        forged=Program(json.dumps(bad),e)
        with self.assertRaises(LanguageError):api['program_link'](definition(),{'lib':forged})

    def test_root_entry_and_canonical_hash_do_not_depend_on_mount_record_order(self):
        a=definition('let x=20;export{x}');b=definition('let x=22;export{x}')
        root=definition('import "../a/main.chaos" as A;import "../b/main.chaos" as B;A.x+B.x',entry='app/start.chaos')
        first=link_program(root,{'b':b,'a':a});second=link_program(root,{'a':a,'b':b})
        self.assertEqual(first['entry'],'app/start.chaos');self.assertEqual(canonical_hash(first),canonical_hash(second));self.assertEqual(run_definition(first),42)

    def test_native_aggregate_failure_decodes_only_until_bound_is_known(self):
        e=Evaluator();api=builtins(e)
        source=definition(files={str(i)+'.chaos':'null' for i in range(63)})
        library=api['program_from'](source);reads=[]
        original=Program.definition.fget
        def read(value):reads.append(value);return original(value)
        with patch.object(Program,'definition',property(read)):
            with self.assertRaises(ResourceLimit):api['program_link'](definition(),{str(i):library for i in range(64)})
        self.assertEqual(reads,[library])

if __name__=='__main__':unittest.main()
