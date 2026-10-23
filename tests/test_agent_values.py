"""Explicit Agent definitions retain the existing callable and effect boundaries."""
import copy
import hashlib
import json
import threading
import unittest

from chaos.agent_values import (builtins, configuration_schema, definition_hash,
                                source_definition, validate_definition)
from chaos.language import Agent, Evaluator, LanguageError, Native, Parser, ResourceLimit, serializable
from chaos.modules import ProgramBundle


DEFINITION = {'kind': 'agent', 'name': 'worker',
              'config': {'provider': 'fixture', 'prompt': 'double', 'tools': []}}


class AgentValueTests(unittest.TestCase):
    def evaluate(self, source, value=None, *, files=None, **kwargs):
        return Evaluator(**kwargs).run(source, {'input': value}, files=files)

    def test_explicit_creation_info_and_function_identity(self):
        value = self.evaluate('let a=agent_from(input);let b=agent_from(input);[type_of(a),agent_info(a),a==a,a==b]', DEFINITION)
        self.assertEqual(value, ['Function', DEFINITION, True, False])
        owner = Evaluator(); agent = owner.run('agent_from(input)', {'input': DEFINITION})
        self.assertIsInstance(agent, Agent); self.assertIs(agent.owner, owner)
        self.assertEqual(serializable(agent), {'$callable': 'worker'})

    def test_helpers_are_pure_and_invocation_needs_agent_handler(self):
        calls = []
        self.evaluate('let a=agent_from(input);[agent_info(a),agent_source(a)]', DEFINITION,
                      tool_handler=lambda *args:calls.append(args), agent_handler=lambda *args:calls.append(args))
        self.assertEqual(calls, [])
        with self.assertRaisesRegex(LanguageError, '不允许智能体效果'):
            self.evaluate('agent_from(input)(42)', DEFINITION)

    def test_definition_schema_single_source_and_no_defaults(self):
        schema = configuration_schema()
        self.assertEqual(set(schema['properties']), {'provider','prompt','tools','maxSteps','mode','policy','level','role','route'})
        self.assertEqual(schema['required'], []); self.assertFalse(schema['additionalProperties'])
        self.assertTrue(all('default' not in entry for entry in schema['properties'].values()))
        schema['properties']['maxSteps']['maximum'] = 999
        self.assertEqual(configuration_schema()['properties']['maxSteps']['maximum'], 12)
        empty = {'kind':'agent','name':'empty','config':{}}
        self.assertEqual(self.evaluate('agent_info(agent_from(input))', empty), empty)
        self.assertNotEqual(definition_hash(empty), definition_hash({**empty,'config':{'maxSteps':4}}))

    def test_all_public_fields_roundtrip_without_provider_resolution(self):
        definition = {'kind':'agent','name':'审阅者_2','config':{
            'provider':'not.registered','prompt':'','tools':['not.granted','not.granted'],
            'maxSteps':12,'mode':'review','policy':'','level':'coordinator','role':'reviewer','route':'named.route'}}
        self.assertEqual(self.evaluate('agent_info(agent_from(input))', definition), definition)
        self.assertEqual(validate_definition(definition), definition)

    def test_copies_and_derive_do_not_alias_original_definition(self):
        source = copy.deepcopy(DEFINITION); evaluator = Evaluator(); helpers = builtins(evaluator)
        original = helpers['agent_from'](source)
        source['config']['prompt'] = 'mutated'; source['config']['tools'].append('x')
        self.assertEqual(helpers['agent_info'](original), DEFINITION)
        info = helpers['agent_info'](original); info['config']['tools'].append('x')
        self.assertEqual(helpers['agent_info'](original), DEFINITION)
        clone = helpers['agent_from'](helpers['agent_info'](original))
        clone.config['tools'].append('host-mutation')
        self.assertEqual(original.config['tools'], [])
        result = self.evaluate('let a=agent_from(input);let d=agent_info(a);let b=agent_from(merge(d,{config:merge(d.config,{prompt:"revised"})}));[agent_info(a).config.prompt,agent_info(b).config.prompt]', DEFINITION)
        self.assertEqual(result, ['double','revised'])

    def test_canonical_hash_preserves_omission_and_matches_sorted_json(self):
        reordered = {'config':{'tools':[],'prompt':'double','provider':'fixture'},'name':'worker','kind':'agent'}
        expected = hashlib.sha256(json.dumps(DEFINITION,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.assertEqual(definition_hash(DEFINITION), expected)
        self.assertEqual(definition_hash(reordered), expected)
        self.assertNotEqual(definition_hash({**DEFINITION,'config':{**DEFINITION['config'],'maxSteps':4}}), expected)

    def test_exact_top_level_and_only_public_configuration(self):
        bad = [None, [], {'$callable':'worker'}, {**DEFINITION,'kind':'plan'},
               {**DEFINITION,'owner':'fake'}, {**DEFINITION,'config':[]},
               {**DEFINITION,'config':{'provider':42}}, {**DEFINITION,'config':{'prompt':None}}]
        for key in ['_platformProposal','apiKey','baseURL','backend','model','authority','owner','origin','timeout','maxConcurrent']:
            bad.append({**DEFINITION,'config':{key:'injected'}})
        for definition in bad:
            with self.subTest(definition=definition), self.assertRaises(LanguageError):
                validate_definition(definition)

    def test_strict_field_types_bounds_and_known_levels(self):
        invalid = [('maxSteps',v) for v in [True,False,0,13,1.0,'4',None]]
        invalid += [('tools',v) for v in ['a',[1],[''],['x'*129],['x']*129]]
        invalid += [('level',v) for v in ['admin','',None]]
        invalid += [(name,v) for name in ['provider','mode','role','route'] for v in ['',False,'x'*129]]
        invalid += [(name,'x'*100001) for name in ['prompt','policy']]
        for name, value in invalid:
            with self.subTest(name=name,value=repr(value)[:40]),self.assertRaises(LanguageError):
                validate_definition({**DEFINITION,'config':{name:value}})
        for level in ['worker','coordinator','platform']:
            self.assertEqual(validate_definition({**DEFINITION,'config':{'level':level}})['config']['level'],level)

    def test_non_json_live_values_cycles_and_aggregate_budget(self):
        ev = Evaluator()
        function = ev.run('fn(x){x}')
        for value in [function, Native('x',lambda:None), float('nan'), float('inf'), object()]:
            with self.subTest(value=type(value).__name__),self.assertRaises(LanguageError):
                validate_definition({**DEFINITION,'config':{'prompt':value}})
        with self.assertRaises(LanguageError): validate_definition({**DEFINITION,'config':{'prompt':'\ud800'}})
        cyclic = [];cyclic.append(cyclic)
        with self.assertRaises(ResourceLimit): validate_definition({**DEFINITION,'config':{'tools':cyclic}})
        with self.assertRaises(ResourceLimit): validate_definition({**DEFINITION,'config':{'prompt':'x'*250001}})
        with self.assertRaises(ResourceLimit): validate_definition({**DEFINITION,'config':{'maxSteps':1<<14001}})

    def test_unicode_identifiers_follow_actual_language_lexer(self):
        for name in ['审阅者','λ角色_2','_worker','await','async','finally']:
            with self.subTest(name=name):
                definition = {**DEFINITION,'name':name}
                self.assertEqual(validate_definition(definition)['name'],name)
                result = self.evaluate('import "role.chaos" as R;agent_info(R.角色)', files={'role.chaos':source_definition(definition,'角色')})
                self.assertEqual(result,definition)
        for name in ['', '1agent','a.b','a-b','fn','return','a'*129,'x;tool("bad",0)']:
            with self.subTest(name=name),self.assertRaises(LanguageError):validate_definition({**DEFINITION,'name':name})

    def test_source_real_module_roundtrip_has_zero_effects(self):
        calls = []
        source = source_definition(DEFINITION)
        self.assertEqual(source,self.evaluate('agent_source(agent_from(input))',DEFINITION))
        tree = Parser(source).program();self.assertEqual(tree[0],'block')
        result = self.evaluate('import "agent.chaos" as A;agent_info(A.saved_agent)',files={'agent.chaos':source},agent_handler=lambda *args:calls.append(args))
        self.assertEqual(result,DEFINITION);self.assertEqual(calls,[])

    def test_source_literal_and_binding_injection_are_not_executed(self):
        prompt = '"};tool("bad",null);\\\n汉字\u2028'
        definition = {**DEFINITION,'config':{'prompt':prompt}}
        source = source_definition(definition,'角色')
        self.assertEqual(self.evaluate('import "s.chaos" as S;agent_info(S.角色)',files={'s.chaos':source}),definition)
        for name in ['agent_from','fn','a.b','a-b','', 'x;throw 42','x'*129]:
            with self.subTest(name=name),self.assertRaises(LanguageError):source_definition(DEFINITION,name)
        with self.assertRaises(ResourceLimit): source_definition({**DEFINITION,'config':{'prompt':'x'*100000}})

    def test_markers_remain_data_and_source_crosses_owners_explicitly(self):
        first = Evaluator();agent = first.run('agent_from(input)',{'input':DEFINITION})
        for source in ['agent_info(input)','agent_source(input)','input(42)']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.evaluate(source,agent)
        for marker in [serializable(agent),{'kind':'agent','name':'worker'}]:
            with self.subTest(marker=marker),self.assertRaises(LanguageError):self.evaluate('agent_from(input)',marker)
        second = Evaluator();other = second.run(source_definition(DEFINITION)+'\nsaved_agent')
        self.assertIs(other.owner,second);self.assertIsNot(other,agent)
        self.assertEqual(self.evaluate('type_of(parse_json(str(agent_from(input))))',DEFINITION),'Record')

    def test_legacy_declarations_and_low_level_constructor_abi(self):
        calls=[]
        def handler(name,config,value):calls.append((name,config,value));return value
        self.assertEqual(self.evaluate('agent a{custom:"legacy",_platformProposal:true};a(42)',agent_handler=handler),42)
        self.assertEqual(calls[0][1],{'custom':'legacy','_platformProposal':True})
        with self.assertRaises(LanguageError):self.evaluate('agent a{custom:"legacy"};agent_info(a)')
        old=Agent('trusted',{'provider':'fixture'})
        self.assertEqual(self.evaluate('input(input=42)',old,agent_handler=handler),42)
        self.assertEqual(self.evaluate('agent_info(input)',old),{'kind':'agent','name':'trusted','config':{'provider':'fixture'}})
        self.assertEqual(self.evaluate('fn factory(p){agent local{prompt:p};local};agent_info(factory("dynamic"))'),{'kind':'agent','name':'local','config':{'prompt':'dynamic'}})

    def test_public_info_rejects_wrappers_and_non_json_legacy_config(self):
        for source in ['agent_info(fn(x){x})','agent_info(partial(fn(x){x}))','agent a{prompt:fn(x){x}};agent_info(a)','agent_info({"$callable":"a"})']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.evaluate(source)

    def test_partial_apply_compose_function_contract_and_named_input(self):
        calls=[]
        def handler(name,config,value):calls.append((name,config,value));return value*2
        result=self.evaluate('let a=agent_from(input);fn use(f:Function){apply(f,[],{input:21})};[use(a),compose(fn(x){x+1},partial(a,input=20))()]',DEFINITION,agent_handler=handler)
        self.assertEqual(result,[42,41]);self.assertEqual(len(calls),2)
        for bad in ['a()', 'a(1,input=2)', 'a(other=42)']:
            before=len(calls)
            with self.subTest(bad=bad),self.assertRaises(LanguageError):self.evaluate('let a=agent_from(input);'+bad,DEFINITION,agent_handler=handler)
            self.assertEqual(len(calls),before)

    def test_class_agent_field_does_not_inject_self(self):
        source='class C{let action=agent_from(input);fn run(self,x){self.action(x)}};C().run(21)'
        self.assertEqual(self.evaluate(source,DEFINITION,agent_handler=lambda n,c,v:v*2),42)

    def test_macro_and_user_decorator_compose_without_new_ast(self):
        source='''macro make(factory,definition){quote{unquote(factory)(unquote(definition))}}
fn wrap(action){fn(x){action(x)+1}}
let a=wrap(make!(agent_from,input));a(20)'''
        self.assertEqual(self.evaluate(source,DEFINITION,agent_handler=lambda n,c,v:v*2),41)
        with self.assertRaises(LanguageError):ProgramBundle('macro bad(){agent_from({kind:"agent",name:"x",config:{}})};bad!()')

    def test_async_agent_effects_share_existing_scheduler(self):
        barrier=threading.Barrier(2);calls=[]
        def effect(kind,payload,cancel):
            self.assertEqual(kind,'agent');calls.append(payload);barrier.wait(timeout=2);return payload['value']*2
        source='let a=agent_from(input);async fn invoke(x){a(x)};await gather([invoke(20),invoke(21)])'
        result=self.evaluate(source,DEFINITION,agent_handler=lambda *a:None,effect_handler=effect,timeout=5)
        self.assertEqual(result,[40,42]);self.assertEqual(len(calls),2)
        self.assertTrue(all(item['config']==DEFINITION['config'] for item in calls))

    def test_stream_consumption_is_demand_driven(self):
        calls=[]
        def effect(name,config,value):calls.append(value);return value*2
        source='let a=agent_from(input);fn* source(){yield a(20);yield a(21)};let g=source();let first=next(g);[first,close(g)]'
        self.assertEqual(self.evaluate(source,DEFINITION,agent_handler=effect),[{'done':False,'value':40},None]);self.assertEqual(calls,[20])

    def test_cancellation_uses_existing_task_scope_and_drains_agent_effect(self):
        entered=threading.Event();finished=threading.Event()
        def effect(kind,payload,cancel):
            self.assertEqual(kind,'agent')
            if payload['value']==1:
                entered.set();cancel.wait(2);finished.set();return 100
            self.assertTrue(entered.wait(2));return 42
        evaluator=Evaluator(agent_handler=lambda *args:None,effect_handler=effect,timeout=5)
        source='let a=agent_from(input);async fn work(x){a(x)}let one=spawn(work(1));let two=spawn(work(2));cancel(one);await two'
        self.assertEqual(evaluator.run(source,{'input':DEFINITION}),42)
        self.assertTrue(finished.is_set());self.assertFalse(evaluator.suspension.active)
        self.assertIsNone(evaluator.suspension.pool);self.assertEqual(evaluator.frames,[])

    def test_imported_agent_is_a_real_plan_binding(self):
        source='let a=agent_from(input);await run_plan(plan([{id:"first",call:"work"},{id:"next",call:"work",needs:["first"]}]),{work:a},20)'
        seen=[]
        def handler(name,config,value):
            seen.append((name,config,value))
            return value['results'].get('first',value['input'])+1
        self.assertEqual(self.evaluate(source,DEFINITION,agent_handler=handler),{'first':21,'next':22})
        self.assertEqual([row[2]['results'] for row in seen],[{}, {'first':21}])
        self.assertTrue(all(row[:2]==('worker',DEFINITION['config']) for row in seen))

    def test_helper_bindings_can_be_shadowed_as_ordinary_names(self):
        self.assertEqual(self.evaluate('let agent_from=fn(x){x+1};agent_from(41)'),42)
        self.assertEqual(self.evaluate('let agent_info=fn(x){x};agent_info(42)'),42)


if __name__ == '__main__': unittest.main()
