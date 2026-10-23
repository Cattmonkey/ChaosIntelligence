"""Versioned mode definitions remain ordinary owned language values and modules."""
import copy
import hashlib
import json
import unittest
from dataclasses import FrozenInstanceError

from chaos import Framework, Settings
from chaos.extensions import Registry, Final
from chaos.language import Evaluator, LanguageError, ResourceLimit, serializable
from chaos.modes import (Plan, Goal, Skill, make, validate_definition, definition_hash,
                         mode_source_definition, skill_source_definition)


PLAN={'kind':'plan','name':'answer','steps':[{'id':'answer','call':'solve'}]}
GOAL={'kind':'goal','objective':'reach42','planner':'planner','verify':'verify','max_iterations':2}
SKILL={'kind':'skill','actions':[{'tool':'text.normalize','input':' first '}],
       'parameters':[{'name':'text','step':0,'path':[]}]}


def artifact(definition=PLAN,revision=1,key='library',status='ready'):
    definition=validate_definition(definition)
    return {'key':key,'kind':definition['kind'],'revision':revision,'status':status,
            'definition':definition,'checkpoint':None,'sourceHash':None,'runId':None,
            'origin':{'projectId':'project','key':key,'revision':revision,'definitionHash':definition_hash(definition)}}


class ModeAuthoringTests(unittest.TestCase):
    def evaluate(self,source,value=None,**kwargs):return Evaluator(**kwargs).run(source,{'input':value})
    def start(self,app,project,value=None):return app.start(project['id'],project['version'],value,mode='source',auto_optimize=False)

    def test_explicit_import_all_kinds_and_metadata(self):
        for definition,kind in [(PLAN,'Plan'),(GOAL,'Goal'),(SKILL,'Skill')]:
            with self.subTest(kind=kind):
                result=self.evaluate('let v=mode_from(input);[type_of(v),mode_info(v),mode_metadata(v)]',definition)
                self.assertEqual(result,[kind,validate_definition(definition),{'kind':definition['kind'],'definitionHash':definition_hash(definition),'origin':None}])

    def test_canonical_hash_normalizes_keys_and_optional_needs(self):
        reordered={'steps':[{'needs':[],'call':'solve','id':'answer'}],'name':'answer','kind':'plan'}
        self.assertEqual(definition_hash(PLAN),definition_hash(reordered))
        expected=hashlib.sha256(json.dumps(validate_definition(PLAN),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.assertEqual(definition_hash(PLAN),expected)
        self.assertNotEqual(expected,definition_hash({**PLAN,'name':'other'}))
        a=make(PLAN,None);b=make(reordered,None)
        self.assertEqual(a.definition_json,b.definition_json);self.assertEqual(serializable(a),serializable(b))
        self.assertEqual(serializable(a)['definitionHash'],expected)

    def test_import_is_copied_immutable_and_does_not_revive_markers(self):
        value=copy.deepcopy(PLAN);ev=Evaluator();mode=ev.run('mode_from(input)',{'input':value});value['name']='changed'
        self.assertEqual(mode.definition['name'],'answer')
        with self.assertRaises(FrozenInstanceError):mode.origin_json='{}'
        with self.assertRaises(FrozenInstanceError):mode.definition_json='{}'
        for bad in [{'$mode':'plan','definitionHash':definition_hash(PLAN)}, {**PLAN,'origin':{'projectId':'fake'}}, {**PLAN,'authority':['browser.control']}]:
            with self.subTest(bad=bad),self.assertRaises(LanguageError):self.evaluate('mode_from(input)',bad)
        self.assertEqual(self.evaluate('type_of(parse_json(json_stringify(mode_from(input))))',PLAN),'Record')

    def test_import_rejects_callable_and_invalid_graph_or_skill_before_effects(self):
        seen=[]
        bad=[{**PLAN,'steps':[]},{**PLAN,'steps':[{'id':'a','call':'f','needs':['missing']}]},
             {**SKILL,'parameters':[{'name':'x','step':0,'path':['missing']}]}]
        for definition in bad:
            with self.subTest(definition=definition),self.assertRaises(LanguageError):self.evaluate('mode_from(input)',definition,tool_handler=lambda *args:seen.append(args))
        with self.assertRaises(LanguageError):self.evaluate('mode_from({kind:"plan",name:"x",steps:[{id:"x",call:fn(x){x}}]})')
        with self.assertRaises(ResourceLimit):self.evaluate('mode_from(input)',{**PLAN,'name':'x'*250001})
        self.assertEqual(seen,[])

    def test_imported_values_keep_owner_and_explicit_json_transfer(self):
        owner=Evaluator();value=owner.run('mode_from(input)',{'input':PLAN})
        for source in ['mode_info(input)','mode_metadata(input)','mode_source(input)','await input(41,{solve:fn(ctx){42}})']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.evaluate(source,value)
        self.assertEqual(self.evaluate('await mode_from(input)(41,{solve:fn(ctx){ctx.input+1}})',value.definition),{'answer':42})

    def test_pure_export_all_kinds_and_module_import_have_no_effects(self):
        seen=[]
        for definition,kind in [(PLAN,'Plan'),(GOAL,'Goal'),(SKILL,'Skill')]:
            with self.subTest(kind=kind):
                exported=mode_source_definition(definition,'library')
                result=Evaluator(tool_handler=lambda *args:seen.append(args),agent_handler=lambda *args:seen.append(args)).run(
                    'import "library.chaos" as L;[type_of(L.library),mode_info(L.library),mode_metadata(L.library)]',files={'library.chaos':exported})
                self.assertEqual(result[0],kind);self.assertEqual(result[1],validate_definition(definition));self.assertIsNone(result[2]['origin'])
        self.assertEqual(seen,[])

    def test_value_module_plan_composes_with_partial_apply_and_compose(self):
        exported=mode_source_definition(PLAN)
        source='import "lib.chaos" as L;let run=partial(L.saved_mode,bindings={solve:fn(ctx){ctx.input+1}});await apply(compose(fn(t){t},run),[41])'
        self.assertEqual(Evaluator().run(source,files={'lib.chaos':exported}),{'answer':42})

    def test_value_module_goal_uses_typed_verifier_and_lifetime_limit(self):
        source='import "g.chaos" as G;let p=plan([{id:"answer",call:"solve"}]);await G.saved_mode(40,{planner:fn(ctx){p},solve:fn(ctx){ctx.input+1},verify:fn(ctx){{done:ctx.results.answer==42,state:ctx.results.answer,evidence:ctx.results}}})'
        value=Evaluator().run(source,files={'g.chaos':mode_source_definition(GOAL)})
        self.assertTrue(value['done']);self.assertEqual(value['iterations'],2);self.assertEqual(value['state'],42)

    def test_value_skill_module_replays_real_tools_and_legacy_function_still_works(self):
        calls=[]
        def tool(name,value):calls.append((name,value));return value.strip()
        source='import "value.chaos" as V;import "function.chaos" as F;[await V.saved_mode({text:" value "}),F.recorded({text:" legacy "})]'
        result=Evaluator(tool_handler=tool).run(source,files={'value.chaos':mode_source_definition(SKILL),'function.chaos':skill_source_definition(SKILL)})
        self.assertEqual(result,[['value'],['legacy']]);self.assertEqual(calls,[('text.normalize',' value '),('text.normalize',' legacy ')])

    def test_source_escapes_literals_and_rejects_unsafe_export_names(self):
        definition={**SKILL,'actions':[{'tool':'text.normalize','input':'x";tool("bad",0);\\\n汉字'}],'parameters':[]}
        exported=mode_source_definition(definition,'portable')
        self.assertEqual(Evaluator().run('import "s.chaos" as S;mode_info(S.portable)',files={'s.chaos':exported}),validate_definition(definition))
        for name in ['fn','mode_from','a.b','a-b','x;tool("bad",0)','', 'a'*129]:
            with self.subTest(name=name),self.assertRaises(LanguageError):mode_source_definition(PLAN,name)

    def test_source_language_helper_matches_host_and_discards_origin(self):
        row=artifact();seen=[]
        result=self.evaluate('let m=await load_mode("library");[mode_source(m),mode_metadata(m)]',mode_handler=lambda op,payload:(seen.append((op,payload)),row)[1])
        self.assertEqual(result[0],mode_source_definition(PLAN));self.assertEqual(result[1]['origin'],row['origin'])
        imported=Evaluator().run('import "s.chaos" as S;mode_metadata(S.saved_mode)',files={'s.chaos':result[0]})
        self.assertIsNone(imported['origin']);self.assertEqual(imported['definitionHash'],row['origin']['definitionHash'])

    def test_load_save_remain_cold_and_resolve_latest_only_when_awaited(self):
        seen=[]
        def host(op,payload):seen.append((op,payload));return artifact(revision=3)
        self.assertEqual(self.evaluate('load_mode("library");save_mode(plan([{id:"x",call:"f"}]),"new");42',mode_handler=host),42);self.assertEqual(seen,[])
        result=self.evaluate('mode_metadata(await load_mode("library"))',mode_handler=host)
        self.assertEqual(result['origin']['revision'],3);self.assertEqual(seen,[('artifact.load',{'key':'library'})])

    def test_optional_revision_payload_and_save_result_abi(self):
        seen=[]
        def host(op,payload):seen.append((op,payload));return artifact(revision=2)
        result=self.evaluate('let p=mode_from(input);[await save_mode(p,"library",revision=1),mode_metadata(await load_mode("library",revision=2)),mode_metadata(p)]',PLAN,mode_handler=host)
        self.assertEqual(result[0]['revision'],2);self.assertEqual(result[1]['origin']['revision'],2);self.assertIsNone(result[2]['origin'])
        self.assertEqual(seen[0][1]['revision'],1);self.assertEqual(seen[1],('artifact.load',{'key':'library','revision':2}))

    def test_invalid_revisions_fail_before_host_effect(self):
        for revision in [True,False,0,-1,1.0,'1',[],{}]:
            for source in ['await load_mode("library",revision=input)','await save_mode(plan([{id:"x",call:"f"}]),"library",revision=input)']:
                seen=[]
                with self.subTest(revision=revision,source=source),self.assertRaises(LanguageError):self.evaluate(source,revision,mode_handler=lambda *args:seen.append(args))
                self.assertEqual(seen,[])

    def test_load_validates_resolved_origin_and_fixed_revision(self):
        bad=[]
        for update in [{'origin':None},{'revision':2}]:bad.append({**artifact(),**update})
        for key,value in [('key','other'),('revision',2),('definitionHash','0'*64),('projectId','')]:
            row=artifact();row['origin'][key]=value;bad.append(row)
        for row in bad:
            with self.subTest(row=row),self.assertRaises(LanguageError):self.evaluate('await load_mode("library",revision=1)',mode_handler=lambda *args:row)

    def test_non_ready_skill_never_loads_even_from_trusted_host_callback(self):
        for status in ['recording','incomplete','failed','interrupted','pending','completed',None]:
            for revision in [None,1]:
                with self.subTest(status=status,revision=revision),self.assertRaises(LanguageError):self.evaluate('await load_mode("library",revision=input)',revision,mode_handler=lambda *args:artifact(SKILL,status=status))
        self.assertEqual(self.evaluate('type_of(await load_mode("library"))',mode_handler=lambda *args:artifact(SKILL)),'Skill')

    def test_origin_copy_and_frozen_storage_cannot_change_metadata(self):
        row=artifact();ev=Evaluator(mode_handler=lambda *args:row);value=ev.run('await load_mode("library")')
        row['origin']['revision']=999;copy=value.origin;copy['key']='other'
        self.assertEqual(value.origin['revision'],1);self.assertEqual(value.origin['key'],'library')
        with self.assertRaises(FrozenInstanceError):value.origin_json='{}'
        metadata=ev.run('mode_metadata(input)',{'input':value});metadata['origin']['revision']=100
        self.assertEqual(ev.run('mode_metadata(input)',{'input':value})['origin']['revision'],1)

    def test_real_language_cas_pinned_load_and_mode_loaded_event(self):
        source='let first=await save_mode(plan([{id:"answer",call:"solve"}],"v1"),"library");let old=await load_mode("library",revision=first.revision);let second=await save_mode(plan([{id:"answer",call:"solve"}],"v2"),"library",revision=mode_metadata(old).origin.revision);let pinned=await load_mode("library",revision=1);[first.revision,second.revision,mode_info(pinned).name,mode_metadata(pinned),mode_info(await load_mode("library")).name]'
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            project=app.create_project('library',source=source);run=self.start(app,project);result=run.result(5)
            self.assertEqual(result[:3],[1,2,'v1']);self.assertEqual(result[4],'v2');self.assertEqual(result[3]['origin']['projectId'],project['id'])
            loaded=[e for e in app.events(project['id'],run_id=run.id)['items'] if e['type']=='mode.loaded'];self.assertEqual(len(loaded),3)
            self.assertEqual(len(app.mode_history(project['id'],'library')['items']),2)

    def test_real_cas_conflict_preserves_artifact(self):
        source='await save_mode(plan([{id:"x",call:"f"}],"one"),"library");await save_mode(plan([{id:"x",call:"f"}],"two"),"library",revision=1);try{await save_mode(plan([{id:"x",call:"f"}],"wrong"),"library",revision=1)}catch e{null};mode_info(await load_mode("library"))'
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            project=app.create_project('library',source=source);result=self.start(app,project).result(5)
            self.assertEqual(result['name'],'two');self.assertEqual(app.mode(project['id'],'library')['revision'],2)

    def test_authored_skill_rechecks_permissions_and_does_not_claim_recording(self):
        source='let s=mode_from(input);[mode_metadata(s),await replay_skill(s,{text:" denied "})]'
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            project=app.create_project('library',source=source,capabilities=[]);self.assertEqual(self.start(app,project,SKILL).wait(5)['status'],'failed')
            self.assertEqual(app.modes(project['id'])['items'],[])

    def test_imported_plan_binding_uses_actual_agent_runtime(self):
        registry=Registry();requests=[]
        @registry.provider('fixture')
        def provider(request,ctx):requests.append(request.input);return Final(request.input['input']+1)
        source='agent solve{provider:"fixture",tools:[]};await mode_from(input.plan)(input.value,{solve:solve})'
        with Framework(':memory:',settings=Settings(':memory:'),registry=registry) as app:
            project=app.create_project('library',source=source,capabilities=[]);result=self.start(app,project,{'plan':PLAN,'value':41}).result(5)
            self.assertEqual(result,{'answer':42});self.assertEqual(requests,[{'input':41,'results':{}}])


if __name__=='__main__':unittest.main()
