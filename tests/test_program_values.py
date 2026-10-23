"""Program values are immutable source, never live interpreter environments."""
import copy
import json
import threading
import unittest
from dataclasses import FrozenInstanceError

from chaos.language import Evaluator, LanguageError, ResourceLimit, ExecutionControl, Parser, serializable
from chaos.modules import ProgramBundle
from chaos.program import plain
from chaos.program_values import (Program, ProgramResult, builtins, canonical_hash, configuration_schema,
    definition_hash, definition_from_bundle, source_definition, validate_definition, validate_cases)
from chaos.suspension import TaskCancelled


def definition(source='input+1', files=None, versions=None):
    files=dict(files or {});files['main.chaos']=source
    return {'kind':'program','entry':'main.chaos','files':files,
            'languageVersions':versions or {key:2 for key in files}}


class ProgramValueTests(unittest.TestCase):
    def evaluate(self,source,value=None,**options):
        return Evaluator(**options).run(source,{'input':definition() if value is None else value})

    def host(self,operation,payload):
        if operation=='run':
            d=payload['definition']
            return Evaluator().run(ProgramBundle(d['files'][d['entry']],d['files'],d['entry'],language_versions=d['languageVersions']),{'input':payload['input']})
        return {'receipt':payload} if operation=='adopt' else payload

    def test_nominal_callable_identity_and_marker(self):
        out=self.evaluate('let p:Program=program_from(input);fn use(f:Function){type_of(f)};[use(p),p==p,p==program_from(input),type_of(parse_json(json_stringify(p)))]')
        self.assertEqual(out,['Program',True,False,'Record'])
        with self.assertRaises(LanguageError):self.evaluate('let p:Program=input;p')
        with self.assertRaises(LanguageError):self.evaluate('await input(1)',serializable(builtins(Evaluator())['program_from'](definition())))

    def test_definition_is_fresh_frozen_and_canonical(self):
        e=Evaluator();api=builtins(e);d=definition();p=api['program_from'](d)
        d['files']['main.chaos']='0';info=api['program_info'](p).value;info['files']['main.chaos']='1'
        self.assertEqual(p.definition,definition())
        with self.assertRaises(FrozenInstanceError):p.owner=None
        self.assertEqual(p.definition_hash,definition_hash(definition()))
        self.assertEqual(canonical_hash(dict(reversed(list(definition().items())))),p.definition_hash)
        self.assertNotEqual(definition_hash(definition('input + 1')),p.definition_hash)

    def test_strict_definition_schema_and_no_origin_import(self):
        bad=[None,[],{},dict(definition(),kind='other'),dict(definition(),origin={}),dict(definition(),capabilities=[]),dict(definition(),languageVersions={}),dict(definition(),entry='missing.chaos')]
        for field in ('kind','entry','files','languageVersions'):
            d=definition();del d[field];bad.append(d)
        for version in (True,0,3,'2'):
            d=definition();d['languageVersions']['main.chaos']=version;bad.append(d)
        class Sub(dict):pass
        bad.append(Sub(definition()))
        for d in bad:
            with self.subTest(value=str(d)[:100]),self.assertRaises(LanguageError):validate_definition(d)
        schema=configuration_schema();schema['properties']['kind']['const']='bad'
        self.assertEqual(configuration_schema()['properties']['kind']['const'],'program')

    def test_closed_bundle_orphans_imports_and_macro_validation(self):
        self.assertEqual(validate_definition(definition('import "lib.chaos" as L;L.x',{'lib.chaos':'let x=42;export {x}'}))['files']['lib.chaos'],'let x=42;export {x}')
        for d in [definition('import "missing.chaos" as M;1'),definition('1',{'orphan.chaos':'let ='}),definition('1',{'../escape.chaos':'0'}),definition('import "a.chaos" as A;1',{'a.chaos':'import "main.chaos" as M;0'}),definition('macro bad(x){tool("x",x)}bad!(1)')]:
            with self.subTest(value=d),self.assertRaises(LanguageError):validate_definition(d)

    def test_current_program_actual_bundle_and_trusted_origin(self):
        source='let p=current_program();[program_info(p),program_metadata(p)]'
        d=definition(source,{'unused.chaos':'1'}, {'main.chaos':2,'unused.chaos':1})
        origin={'projectId':'workspace','version':3,'definitionHash':canonical_hash(d)}
        e=Evaluator(program_origin=origin);origin['version']=9
        result=e.run(ProgramBundle(source,d['files'],language_versions=d['languageVersions']))
        self.assertEqual(result[0],d);self.assertEqual(result[1]['origin']['version'],3)
        result=Evaluator(program_origin={'projectId':'workspace','version':3,'definitionHash':'0'*64}).run(source)
        self.assertIsNone(result[1]['origin'])
        self.assertIsNone(self.evaluate('program_metadata(program_from(input)).origin'))

    def test_ir_edit_is_immutable_and_upgrades_only_edited_legacy_file(self):
        d=definition('input["x"]',{'other.chaos':'input["x"]'},{'main.chaos':1,'other.chaos':1})
        e=Evaluator();api=builtins(e);p=api['program_from'](d)
        ir=api['program_ir'](p).value
        self.assertEqual(ir['root']['statements'][0]['kind'],'index')
        q=api['program_edit'](p,'main.chaos',[])
        self.assertEqual(q.definition['languageVersions'],{'main.chaos':2,'other.chaos':1})
        self.assertIn('.[',q.definition['files']['main.chaos'])
        self.assertEqual(p.definition,d)
        self.assertEqual(plain(ProgramBundle(d['files']['main.chaos'],d['files'],language_versions=d['languageVersions']).trees['main.chaos']),plain(ProgramBundle(q.definition['files']['main.chaos'],q.definition['files'],language_versions=q.definition['languageVersions']).trees['main.chaos']))

    def test_structural_edit_literal_and_validation(self):
        e=Evaluator();api=builtins(e);p=api['program_from'](definition('41'))
        node=api['program_ir'](p).value['root']['statements'][0]
        q=api['program_edit'](p,'main.chaos',[{'op':'set','id':node['id'],'field':'value','value':42}])
        self.assertEqual(Evaluator().run(q.definition['files']['main.chaos']),42)
        with self.assertRaises(LanguageError):api['program_edit'](p,'main.chaos',[{'op':'remove','id':'missing'}])
        with self.assertRaises(LanguageError):api['program_ir'](p,'other.chaos')

    def test_edit_inserts_modern_item_into_legacy_module(self):
        e=Evaluator();api=builtins(e)
        p=api['program_from'](definition('input[0]',versions={'main.chaos':1}))
        node=api['program_ir'](p).value['root']['statements'][0]
        replacement=copy.deepcopy(node);replacement['kind']='subscript';replacement['index']['value']=-1
        q=api['program_edit'](p,'main.chaos',[{'op':'replace','id':node['id'],'node':replacement}])
        d=q.definition
        self.assertEqual(d['languageVersions']['main.chaos'],2)
        self.assertEqual(Evaluator().run(ProgramBundle(d['files']['main.chaos'],language_versions=d['languageVersions']),{'input':[10,20]}),20)

    def test_program_inside_hygienic_macro_and_typed_function(self):
        source='macro make(ctor,x){quote{unquote(ctor)(unquote(x))}};fn identity(value:Program)->Program{value};await identity(make!(program_from,input))(41)'
        self.assertEqual(self.evaluate(source,program_handler=self.host),42)

    def test_source_roundtrip_and_binding_validation(self):
        d=definition('input+1');source=source_definition(d,'候选')
        result=Evaluator().run(source+'program_info(候选)')
        self.assertEqual(result,d)
        for name in ['x;tool("x",1)','fn','program_from','x.y','',1]:
            with self.subTest(name=name),self.assertRaises(LanguageError):source_definition(d,name)
        with self.assertRaises(ResourceLimit):source_definition(definition('//'+('x'*99000)+'\n1',{'other.chaos':'//'+('y'*99000)+'\n2'}))

    def test_host_required_but_construct_and_cold_task_allowed(self):
        self.assertEqual(self.evaluate('let p=program_from(input);[type_of(p(41)),type_of(program_probe(p,[{input:1,expect:2}]))]'),['Task','Task'])
        for call in ['program_from(input)(41)','run_program(program_from(input),41)','program_probe(program_from(input),[{input:1,expect:2}])']:
            seen=[]
            with self.subTest(call=call),self.assertRaises(LanguageError):self.evaluate('await '+call,effect_handler=lambda *args:seen.append(args))
            self.assertEqual(seen,[])

    def test_actual_host_fallback_and_copied_json(self):
        self.assertEqual(self.evaluate('await run_program(program_from(input),41)',program_handler=self.host),42)
        source='let p=program_from(input);class C{let run=staticmethod(p)};await C.run(input=41)'
        self.assertEqual(self.evaluate(source,program_handler=self.host),42)
        source='let p=program_from(input);let f=compose(fn(t){t},partial(p));await apply(f,[41])'
        self.assertEqual(self.evaluate(source,program_handler=self.host),42)
        with self.assertRaises(LanguageError):self.evaluate('await run_program(program_from(input),fn(x){x})',program_handler=self.host)
        with self.assertRaises(LanguageError):self.evaluate('await run_program(program_from(input),1)',program_handler=lambda *_:Evaluator().run('fn(x){x}'))

    def test_parallel_calls_use_independent_json_payloads(self):
        barrier=threading.Barrier(2);seen=[]
        def host(op,payload):
            seen.append(threading.get_ident());barrier.wait(2)
            payload['definition']['files']['main.chaos']='0'
            return payload['input']
        result=self.evaluate('let p=program_from(input);let out=await gather([p(40),p(42)]);[out,program_info(p).files["main.chaos"]]',program_handler=host)
        self.assertEqual(result,[[40,42],'input+1']);self.assertEqual(len(set(seen)),2)

    def test_owned_values_and_nested_descriptors_do_not_cross_contexts(self):
        p=builtins(Evaluator())['program_from'](definition())
        for source in ['program_info(input)','program_metadata(input)','await input(1)','staticmethod(input)','property(null,input)','type_of(input)','tool("x",{p:input})']:
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator(tool_handler=lambda *_:None,program_handler=self.host).run(source,{'input':p})
        for source in ['let p=program_from(input);property(null,p)','let p=program_from(input);property(null,staticmethod(p))']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.evaluate(source)

    def test_program_marker_host_serialization_has_no_source(self):
        seen=[]
        result=self.evaluate('let p=program_from(input);tool("capture",{p:p});json_stringify(p)',tool_handler=lambda n,v:seen.append(v))
        self.assertEqual(json.loads(result),seen[0]['p']);self.assertEqual(set(seen[0]['p']),{'$program','definitionHash'})

    def test_probe_cases_strict_and_alternate_entry(self):
        d=definition('input',{'test.chaos':'input+1'})
        self.assertEqual(validate_cases([{'input':1,'expect':2,'entry':'test.chaos'}],d)[0]['entry'],'test.chaos')
        for cases in [[],[{}],[{'input':1}],[{'input':1,'expect':2,'extra':0}],[{'input':1,'expect':2,'entry':'missing.chaos'}],[{'input':1,'expect':2}]*17]:
            with self.subTest(cases=cases),self.assertRaises(LanguageError):validate_cases(cases,d)

    def test_adopt_requires_owned_unmodified_origin_and_snapshots_payload(self):
        source='let base=current_program();let candidate=program_edit(base,"main.chaos",[]);await program_adopt(base,candidate,[{input:null,expect:42}],"verified")'
        d=definition(source);origin={'projectId':'workspace','version':1,'definitionHash':canonical_hash(d)}
        payload=Evaluator(program_origin=origin,program_handler=self.host).run(source)
        self.assertEqual(payload['base'],origin);self.assertEqual(payload['reason'],'verified')
        source='let base=current_program();let changed=program_edit(base,"main.chaos",[]);program_adopt(changed,base,[{input:null,expect:42}])'
        with self.assertRaises(LanguageError):Evaluator(program_origin={'projectId':'workspace','version':1,'definitionHash':canonical_hash(definition(source))}).run(source)
        with self.assertRaises(LanguageError):self.evaluate('let p=program_from(input);program_adopt(p,p,[{input:1,expect:2}])')

    def test_compile_checkpoints_charge_parser_and_macro_expansion(self):
        ticks=[];ProgramBundle('macro twice(x){quote{unquote(x)+unquote(x)}};twice!(21)',checkpoint=lambda:ticks.append(1))
        self.assertGreater(len(ticks),50)
        with self.assertRaises(ResourceLimit):self.evaluate('program_from(input)',definition('macro twice(x){quote{unquote(x)+unquote(x)}};twice!(21)'),step_limit=50)
        def cancel():raise TaskCancelled('compile cancelled')
        with self.assertRaises(TaskCancelled):validate_definition(definition(),checkpoint=cancel)
        self.assertEqual(plain(Parser('input+1').program()),plain(Parser('input+1',checkpoint=lambda:None).program()))

    def test_hard_host_errors_propagate_without_catch_conversion(self):
        for error in [ResourceLimit('limit'),TaskCancelled('cancel')]:
            def host(*args):raise error
            with self.subTest(error=type(error).__name__),self.assertRaises(type(error)):
                self.evaluate('try{await program_from(input)(41)}catch e{0}',program_handler=host)

    def test_payload_is_snapshotted_at_cold_task_creation(self):
        source='let p=program_from(input);var r={n:1};let t=p(r);r=merge(r,{n:2});await t'
        self.assertEqual(self.evaluate(source,program_handler=lambda op,payload:payload['input']),{'n':1})

    def test_large_bundle_opaque_reflection_and_generic_output_limit(self):
        d=definition('//'+('x'*90000)+'\n1',{'b.chaos':'//'+('x'*90000)+'\n2','c.chaos':'//'+('x'*90000)+'\n3'})
        e=Evaluator();p=builtins(e)['program_from'](d)
        self.assertEqual(len(builtins(e)['program_info'](p).value['files']),3)
        self.assertEqual(serializable(p)['$program'],'main.chaos')
        with self.assertRaises(ResourceLimit):serializable(p.definition)
        self.assertEqual(e.run('length(keys(program_info(input).files))',{'input':p}),3)

    def test_recording_refusal_poison_survives_catch(self):
        events=[]
        def mode(op,payload):
            events.append(op)
            if op=='recording.begin':return {'scopeId':'scope-1'}
            if op=='recording.abort':return None
            if op=='recording.finish':raise LanguageError('incomplete')
            raise AssertionError(op)
        calls=[]
        source='let p=program_from(input);async fn action(x){try{await p(41)}catch e{null};42};await record_skill(action)'
        with self.assertRaises(LanguageError):self.evaluate(source,mode_handler=mode,program_handler=lambda *args:calls.append(args))
        self.assertEqual(calls,[]);self.assertIn('recording.abort',events)

    def test_compact_adoption_receipt_ignores_large_project_result(self):
        source='let p=current_program();await program_adopt(p,program_from(input),[{input:null,expect:42}])'
        origin={'projectId':'workspace','version':1,'definitionHash':canonical_hash(definition(source))}
        result=self.evaluate(source,definition('42'),program_origin=origin,
            program_handler=lambda *_:{'project':{'source':'x'*300000},'receipt':{'version':2}})
        self.assertEqual(result,{'version':2})


if __name__=='__main__':unittest.main()
