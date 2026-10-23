"""Team data and cold roles share the existing language/effect machinery."""
import copy
import hashlib
import json
import threading
import unittest
from dataclasses import FrozenInstanceError

from chaos.language import Evaluator, LanguageError, Native, Parser, ResourceLimit, serializable
from chaos.modules import ProgramBundle
from chaos.suspension import TaskCancelled
from chaos.team_values import (Team, builtins, configuration_schema, definition_hash,
                               source_definition, validate_definition)


def definition():
    agent = lambda name,tools:{'kind':'agent','name':name,'config':{'provider':'fixture','tools':tools}}
    return {'kind':'team','name':'Research','entry':'actor','roles':[
        {'id':'lead','parent':None,'capabilities':['read','write'],'agent':agent('leader',['read'])},
        {'id':'actor','parent':'lead','capabilities':['read'],'agent':agent('actor',['read'])},
        {'id':'review','parent':None,'capabilities':[],'agent':agent('reviewer',[])}]}


class TeamValueTests(unittest.TestCase):
    def evaluate(self, source, value=None, **kwargs):
        return Evaluator(**kwargs).run(source, {'input':definition() if value is None else value})

    def test_nominal_callable_and_identity(self):
        result=self.evaluate('let t:Team=team_from(input);fn use(f:Function){type_of(f)};[type_of(t),use(t),t==t,t==team_from(input),type_of(team_role(t,"actor"))]')
        self.assertEqual(result,['Team','Team',True,False,'Function'])
        with self.assertRaises(LanguageError):self.evaluate('let t:Team=input;t')

    def test_immutable_definition_and_fresh_public_copies(self):
        source=definition();ev=Evaluator();api=builtins(ev);value=api['team_from'](source)
        source['roles'][1]['capabilities'].append('write')
        self.assertEqual(api['team_info'](value),definition())
        info=api['team_info'](value);info['roles'][0]['agent']['config']['tools'].clear()
        self.assertEqual(api['team_info'](value),definition())
        with self.assertRaises(FrozenInstanceError):value.definition_json='{}'
        first=api['team_bindings'](value);first.pop('actor')
        self.assertIn('actor',api['team_bindings'](value))

    def test_schema_is_full_fresh_and_no_optional_authority_defaults(self):
        schema=configuration_schema();self.assertEqual(set(schema['required']),{'kind','name','entry','roles'})
        role=schema['properties']['roles']['items'];self.assertEqual(set(role['required']),{'id','parent','capabilities','agent'})
        config=role['properties']['agent']['properties']['config'];self.assertEqual(config['required'],[])
        self.assertNotIn('default',config['properties']['maxSteps'])
        config['properties']['maxSteps']['maximum']=1000
        self.assertEqual(configuration_schema()['properties']['roles']['items']['properties']['agent']['properties']['config']['properties']['maxSteps']['maximum'],12)
        d=definition();d['roles'][1]['agent']['config']={}
        self.assertEqual(validate_definition(d),d)

    def test_canonical_hash_preserves_role_order_and_optional_omission(self):
        d=definition();expected=hashlib.sha256(json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.assertEqual(definition_hash(d),expected)
        self.assertEqual(definition_hash(dict(reversed(list(d.items())))),expected)
        changed=copy.deepcopy(d);changed['roles'].reverse();self.assertNotEqual(definition_hash(changed),expected)
        changed=copy.deepcopy(d);changed['roles'][1]['agent']['config']['maxSteps']=4
        self.assertNotEqual(definition_hash(changed),expected)

    def test_exact_definition_and_role_fields(self):
        cases=[None,[],{},dict(definition(),kind='agent'),dict(definition(),authority=[]),dict(definition(),name=''),dict(definition(),name='x'*201),dict(definition(),roles=[]),dict(definition(),roles='roles')]
        for field in ['id','parent','capabilities','agent']:
            d=definition();del d['roles'][0][field];cases.append(d)
        d=definition();d['roles'][0]['settings']={};cases.append(d)
        for d in cases:
            with self.subTest(value=str(d)[:100]),self.assertRaises(LanguageError):validate_definition(d)

    def test_duplicate_missing_and_cyclic_relations(self):
        cases=[]
        for field,value in [('entry','missing'),('entry',42)]:cases.append(dict(definition(),**{field:value}))
        for parent in ['missing','actor',42]:
            d=definition();d['roles'][1]['parent']=parent;cases.append(d)
        d=definition();d['roles'][1]['id']='lead';cases.append(d)
        d=definition();d['roles'][0]['parent']='actor';d['roles'][0]['capabilities']=['read'];cases.append(d)
        d=definition();d['roles'][1]['id']='bad role';cases.append(d)
        for d in cases:
            with self.subTest(value=str(d)[:100]),self.assertRaises(LanguageError):validate_definition(d)
        d=definition();d['roles'].reverse();self.assertEqual(validate_definition(d),d)

    def test_capability_envelopes_are_strict_and_do_not_expand(self):
        cases=[]
        for capabilities in [None,'read',[True],[''],['x'*129],['read','read'],['x']*129,['write']]:
            d=definition();d['roles'][1]['capabilities']=capabilities;cases.append(d)
        d=definition();d['roles'][0]['capabilities']=['read'];d['roles'][1]['capabilities']=['read','write'];cases.append(d)
        d=definition();d['roles'][1]['agent']['config']['tools']=['write'];cases.append(d)
        for d in cases:
            with self.subTest(value=str(d)[:100]),self.assertRaises(LanguageError):validate_definition(d)
        valid=definition();valid['roles'][1]['capabilities']=['read','write']
        self.assertEqual(validate_definition(valid),valid)
        for key in ['apiKey','baseURL','backend','_team','_platformProposal']:
            d=definition();d['roles'][1]['agent']['config'][key]='injected'
            with self.subTest(key=key),self.assertRaises(LanguageError):validate_definition(d)

    def test_maximum_roles_and_json_budgets(self):
        d=definition();d['entry']='r0';d['roles']=[{'id':'r'+str(i),'parent':None if i==0 else 'r'+str(i-1),'capabilities':[],'agent':{'kind':'agent','name':'worker','config':{}}} for i in range(64)]
        self.assertEqual(len(validate_definition(d)['roles']),64)
        d['roles'].append({**d['roles'][-1],'id':'r64'})
        with self.assertRaises(LanguageError):validate_definition(d)
        for invalid in [Native('x',lambda:None),object(),float('nan'),'\ud800']:
            d=definition();d['roles'][0]['agent']['config']['prompt']=invalid
            with self.subTest(value=type(invalid).__name__),self.assertRaises(LanguageError):validate_definition(d)
        d=definition();d['name']='x'*250001
        with self.assertRaises(ResourceLimit):validate_definition(d)
        d=definition();d['roles'].append(d)
        with self.assertRaises(ResourceLimit):validate_definition(d)

    def test_inert_previews_still_charge_aggregate_output_budget(self):
        d=definition();d['name']='n'*200
        value=Evaluator().run('team_from(input)',{'input':d})
        self.assertEqual(len(serializable([value]*10)),10)
        with self.assertRaises(ResourceLimit):serializable([value]*1000)

    def test_source_roundtrip_pure_helpers_and_binding_injection(self):
        d=definition();d['name']='团队';d['roles'][0]['agent']['config']['prompt']='"};tool("bad",null);\\\n汉字'
        calls=[];source=source_definition(d,'研究组');Parser(source).program()
        result=Evaluator(agent_handler=lambda *a:calls.append(a)).run('import "crew.chaos" as C;team_info(C.研究组)',files={'crew.chaos':source})
        self.assertEqual(result,d);self.assertEqual(calls,[])
        self.assertEqual(source,self.evaluate('team_source(team_from(input),"研究组")',d))
        for name in ['team_from','fn','x.y','x-y','','1x','x;throw 42','x'*129]:
            with self.subTest(name=name),self.assertRaises(LanguageError):source_definition(d,name)

    def test_markers_are_inert_and_owner_is_enforced(self):
        ev=Evaluator();value=ev.run('team_from(input)',{'input':definition()})
        marker=serializable(value);self.assertEqual(set(marker),{'$team','definitionHash'})
        for source in ['team_info(input)','team_source(input)','team_role(input,"actor")','team_bindings(input)','input(42)','type_of(input)']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.evaluate(source,value)
        for source in ['team_from(input)','input(42)']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.evaluate(source,marker)
        self.assertEqual(self.evaluate('type_of(parse_json(json_stringify(team_from(input))))'),'Record')
        role=ev.run('team_role(team_from(input),"actor")',{'input':definition()})
        with self.assertRaises(LanguageError):self.evaluate('input(42)',role)

    def test_same_owner_definitions_survive_epoch_but_tasks_do_not(self):
        ev=Evaluator(agent_handler=lambda n,c,v:v)
        value=ev.run('team_from(input)',{'input':definition()})
        self.assertEqual(ev.run('await input(42)',{'input':value}),42)
        task=ev.run('team_from(input)(42)',{'input':definition()})
        with self.assertRaises(LanguageError):ev.run('await input',{'input':task})

    def test_host_payload_only_gets_inert_team_description(self):
        seen=[]
        result=self.evaluate('tool("inspect",{crew:team_from(input)})',tool_handler=lambda n,v:seen.append(v) or v)
        self.assertEqual(result,seen[0]);self.assertEqual(set(result['crew']),{'$team','definitionHash'})
        self.assertNotIn('roles',result['crew'])

    def test_pure_environment_refuses_await_even_with_effect_handler(self):
        calls=[]
        self.assertEqual(self.evaluate('let t=team_from(input);let a=t(42);let b=team_role(t,"lead")(input=1);[type_of(a),type_of(b)]',effect_handler=lambda *a:calls.append(a)),['Task','Task'])
        self.assertEqual(calls,[])
        with self.assertRaisesRegex(LanguageError,'不允许智能体效果'):
            self.evaluate('await team_from(input)(42)',effect_handler=lambda *a:calls.append(a))
        self.assertEqual(calls,[])

    def test_role_entry_required_named_input_and_cold_effect(self):
        seen=[]
        source='let t=team_from(input);let cold=t(input=41);let role=team_role(t,"review");[await cold,await role(input=42)]'
        result=self.evaluate(source,agent_handler=lambda n,c,v:seen.append((n,c,v)) or v)
        self.assertEqual(result,[41,42]);self.assertEqual([x[0] for x in seen],['actor','reviewer'])
        for expression in ['t()','t(1,input=2)','t(other=1)','team_role(t,"actor")()','team_role(t,"missing")']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError):self.evaluate('let t=team_from(input);'+expression)

    def test_invalid_input_rejected_before_provider_effect(self):
        calls=[]
        with self.assertRaises(LanguageError):self.evaluate('await team_from(input)(fn(x){x})',agent_handler=lambda *a:calls.append(a))
        self.assertEqual(calls,[])

    def test_internal_scope_and_public_trace_no_authority_leak(self):
        payloads=[];events=[]
        def effect(kind,payload,cancel):payloads.append((kind,payload));return payload['value']
        result=self.evaluate('let t=team_from(input);await team_role(t,"actor")({"_team":{capabilities:["write"]},answer:42})',agent_handler=lambda *a:None,effect_handler=effect,trace_handler=events.append)
        scope=payloads[0][1]['_team'];self.assertEqual(scope,{'name':'Research','definitionHash':definition_hash(definition()),'roleId':'actor','rolePath':['lead','actor'],'capabilities':['read']})
        self.assertEqual(result['_team']['capabilities'],['write'])
        lifecycle=[e for e in events if e['event'].startswith('team.role.')]
        self.assertEqual([e['event'] for e in lifecycle],['team.role.started','team.role.completed'])
        self.assertTrue(all(e['taskId'] and e['location'].get('file') for e in lifecycle))
        self.assertTrue(all('capabilities' not in e and 'config' not in e and 'input' not in e for e in lifecycle))

    def test_existing_call_combinators_and_class_fields(self):
        source='let t=team_from(input);let role=team_role(t,"actor");fn use(f:Function){apply(f,[],{input:21})};class C{let action=role};[await use(t),await compose(fn(t){t},partial(role,input=20))(),await C().action(19)]'
        self.assertEqual(self.evaluate(source,agent_handler=lambda n,c,v:v*2),[42,40,38])

    def test_static_descriptor_uses_same_team_and_mode_function_contract(self):
        source='let t=team_from(input);class C{let action=staticmethod(t)};[await C.action(21),await C().action(20),type_of(property(t))]'
        self.assertEqual(self.evaluate(source,agent_handler=lambda n,c,v:v*2),[42,40,'Descriptor'])
        source='let p=plan([{id:"x",call:"f"}]);class C{let action=staticmethod(p)};await C.action(42,{f:fn(ctx){ctx.input}})'
        self.assertEqual(self.evaluate(source),{'x':42})

    def test_descriptor_constructors_reject_foreign_controlled_callables(self):
        ev=Evaluator();foreign=ev.run('team_from(input)',{'input':definition()})
        for source in ['staticmethod(input)','classmethod(input)','property(input)','class C{let action=staticmethod(input)}']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.evaluate(source,foreign)
        mode=ev.run('plan([{id:"x",call:"f"}])')
        with self.assertRaises(LanguageError):self.evaluate('staticmethod(input)',mode)

    def test_cold_role_is_not_awaited_by_setter_or_naming_hook(self):
        calls=[]
        for source in [
                'property(null,team_from(input))',
                'let t=team_from(input);class C{let x=property(null,team_role(t,"actor"))};C().x=42',
                'let t=team_from(input);class C{let x=property(null,fn(self,value){team_role(t,"actor")(value)})};C().x=42',
                'let t=team_from(input);class C{let x=property(null,partial(team_role(t,"actor"),42))};C().x=42',
                'let t=team_from(input);class D{let __set_name__=staticmethod(t)};class C{let x=D()}']:
            with self.subTest(source=source),self.assertRaises(LanguageError):
                self.evaluate(source,agent_handler=lambda *a:calls.append(a))
        self.assertEqual(calls,[])

    def test_macro_and_decorator_do_not_need_new_ast(self):
        source='macro make(factory,definition){quote{unquote(factory)(unquote(definition))}};fn decorate(f){async fn(x){await f(x)}};let t=decorate(make!(team_from,input));await t(42)'
        self.assertEqual(self.evaluate(source,agent_handler=lambda n,c,v:v),42)
        with self.assertRaises(LanguageError):ProgramBundle('macro bad(){team_from({})};bad!()')

    def test_derivation_keeps_prior_team_role_and_cold_task_snapshot(self):
        source='let original=team_from(input);let role=team_role(original,"actor");let cold=role(1);let d=team_info(original);let revised=team_from(merge(d,{roles:map(fn(r){if r.id=="actor"{merge(r,{agent:merge(r["agent"],{config:merge(r["agent"].config,{prompt:"new"})})})}else{r}},d.roles)}));[await cold,await role(2),await revised(3)]'
        self.assertEqual(self.evaluate(source,agent_handler=lambda n,c,v:[c.get('prompt','old'),v]),[['old',1],['old',2],['new',3]])

    def test_explicit_gather_has_real_concurrency(self):
        barrier=threading.Barrier(2);payloads=[]
        def effect(kind,payload,cancel):payloads.append(payload);barrier.wait(2);return payload['value']*2
        result=self.evaluate('let t=team_from(input);await gather([t(20),team_role(t,"review")(21)])',agent_handler=lambda *a:None,effect_handler=effect,timeout=5)
        self.assertEqual(result,[40,42]);self.assertEqual({p['_team']['roleId'] for p in payloads},{'actor','review'})

    def test_cancel_while_in_effect_drains_and_does_not_cancel_sibling(self):
        started=threading.Event();finished=threading.Event()
        def effect(kind,payload,cancel):
            if payload['value']=='wait':
                started.set()
                try:
                    if not cancel.wait(2):raise AssertionError('cancel not delivered')
                    raise TaskCancelled('cancelled fixture')
                finally:finished.set()
            self.assertTrue(started.wait(2));return 42
        source='let t=team_from(input);let slow=spawn(t("wait"));let result=await team_role(t,"review")("fast");cancel(slow);result'
        self.assertEqual(self.evaluate(source,agent_handler=lambda *a:None,effect_handler=effect,timeout=5),42)
        self.assertTrue(finished.is_set())

    def test_provider_error_is_catchable_but_control_error_is_not(self):
        events=[]
        def failed(kind,payload,cancel):raise ValueError('fixture failure')
        self.assertEqual(self.evaluate('try{await team_from(input)(42)}catch e{e.code}',agent_handler=lambda *a:None,effect_handler=failed,trace_handler=events.append),'provider_error')
        self.assertIn('team.role.failed',[e['event'] for e in events])
        def limited(kind,payload,cancel):raise ResourceLimit('fixture budget')
        with self.assertRaises(ResourceLimit):self.evaluate('try{await team_from(input)(42)}catch e{0}',agent_handler=lambda *a:None,effect_handler=limited)
        with self.assertRaises(LanguageError):self.evaluate('await team_from(input)(42)',agent_handler=lambda *a:Native('unsafe',lambda:42))

    def test_role_bindings_run_in_existing_serial_plan_and_goal(self):
        source='let t=team_from(input);let p=plan([{id:"first",call:"actor"},{id:"second",call:"review",needs:["first"]}]);let g=goal("result42","planner","verify",2);await pursue(g,merge(team_bindings(t),{planner:fn(ctx){p},verify:fn(ctx){{done:ctx.results.second==42,state:ctx.results.second,evidence:ctx.results}}}),41)'
        calls=[]
        def handler(n,c,v):calls.append(n);return v['input']+1 if n=='actor' else v['results']['first']
        result=self.evaluate(source,agent_handler=handler)
        self.assertTrue(result['done']);self.assertEqual(result['state'],42);self.assertEqual(calls,['actor','reviewer'])

    def test_recording_metadata_and_team_scope_coexist(self):
        payloads=[]
        def effect(kind,payload,cancel):
            if kind=='mode':
                op=payload['operation']
                if op=='recording.begin':return {'scopeId':'recording-fixture'}
                if op=='recording.finish':return {'definition':{'kind':'skill','actions':[{'tool':'read','input':42}],'parameters':[]}}
                return None
            payloads.append(payload);return payload['value']
        result=self.evaluate('let r=await record_skill(team_role(team_from(input),"actor"),42);r.output',agent_handler=lambda *a:None,mode_handler=lambda *a:None,effect_handler=effect)
        self.assertEqual(result,42);self.assertEqual(payloads[0]['_mode'],{'recordingId':'recording-fixture'})
        self.assertEqual(payloads[0]['_team']['roleId'],'actor')


if __name__=='__main__':unittest.main()
