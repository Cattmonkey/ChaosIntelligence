"""Protocol fixtures replace external responses while executing actual Chaos."""
import copy
import unittest
from unittest.mock import patch

from chaos.evaluation import evaluate,validate_fixtures,fixture_schema
from chaos.language import LanguageError,ResourceLimit
from chaos.suspension import Task
from chaos.program_values import validate_cases,cases_hash


def agent(name='a',tools=(),**config):
    return {'kind':'agent','name':name,'config':{'provider':'model','tools':list(tools),**config}}


def lane(definition=None,value=21,actions=None,times=None):
    result={'definition':definition or agent(),'input':value,'actions':actions or [{'tool':'double','input':value,'expect':42},{'final':42}]}
    if times is not None:result['times']=times
    return result


SOURCE='''agent a{provider:"model",tools:[]};var calls=0;fn double(x){calls=calls+1;x*2};
let bound=agent_bind(a,[agent_tool("double",double)]);[await bound(21),calls]'''


class AgentFixtureTests(unittest.TestCase):
    def test_real_closure_runs_without_settings_native_client_or_provider(self):
        from chaos.settings import Settings
        from chaos.harness import NativeHarness
        with patch.object(Settings,'resolve',side_effect=AssertionError('real settings used')),patch.object(NativeHarness,'_client',side_effect=AssertionError('real model used')):
            result=evaluate(SOURCE,fixtures={'agents':[lane()]})
        self.assertEqual(result['value'],[42,1]);self.assertTrue(result['fixtures']['passed'])
        self.assertEqual(result['fixtures']['agents'],[{'index':0,'expected':1,'used':1,'completed':1}])

    def test_default_pure_projection_stays_raw_and_agent_effect_denied(self):
        result=evaluate('async fn f(){1};f()')
        self.assertIsInstance(result['value'],Task);self.assertIsNone(result['fixtures'])
        with self.assertRaises(LanguageError):evaluate('agent a{provider:"model"};a(1)')

    def test_ordinary_agent_uses_same_structured_fixture_actions(self):
        fixtures={'agents':[lane(agent(tools=['text.normalize']),actions=[{'tool':'text.normalize','input':' A ','expect':'A'},{'final':'A'}])],
                  'tools':[{'name':'text.normalize','input':' A ','output':'A'}]}
        result=evaluate('agent a{provider:"model",tools:["text.normalize"]};a(21)',fixtures=fixtures,capabilities=['text.normalize'])
        self.assertEqual(result['value'],'A')

    def test_nested_bound_and_ordinary_agents_are_real_invocations(self):
        source='''agent a{provider:"model",tools:[]};agent b{provider:"model",tools:[]};
        let inner=agent_bind(b,[agent_tool("inner",fn(x){x+1})]);
        let outer=agent_bind(a,[agent_tool("double",async fn(x){await inner(x)})]);await outer(21)'''
        fixtures={'agents':[lane(actions=[{'tool':'double','input':21,'expect':22},{'final':22}]),
                            lane(agent('b'),actions=[{'tool':'inner','input':21,'expect':22},{'final':22}])]}
        self.assertEqual(evaluate(source,fixtures=fixtures)['value'],22)
        source=source.replace('await inner(x)','b(x)')
        fixtures['agents'][1]['actions']=[{'final':22}]
        self.assertEqual(evaluate(source,fixtures=fixtures)['value'],22)

    def test_parallel_identical_calls_use_times_without_lane_ambiguity(self):
        source=SOURCE.replace('[await bound(21),calls]','[await gather([bound(21),bound(21),bound(21),bound(21)]),calls]')
        result=evaluate(source,fixtures={'agents':[lane(times=4)]})
        self.assertEqual(result['value'],[[42]*4,4]);self.assertEqual(result['fixtures']['agents'][0]['completed'],4)

    def test_actual_async_handler_output_is_checked(self):
        source=SOURCE.replace('fn double(x){calls=calls+1;x*2}','async fn double(x){await sleep(0);calls=calls+1;x*2}')
        self.assertEqual(evaluate(source,fixtures={'agents':[lane()]})['value'],[42,1])
        wrong={'agents':[lane(actions=[{'tool':'double','input':21,'expect':43},{'final':42}])]}
        with self.assertRaisesRegex(LanguageError,'Actual Agent tool output'):evaluate(source,fixtures=wrong)

    def test_mismatch_stays_failed_after_language_catch(self):
        source=SOURCE.replace('[await bound(21),calls]','try{await bound(21)}catch e{42}')
        wrong={'agents':[lane(actions=[{'tool':'double','input':21,'expect':43},{'final':42}])]}
        with self.assertRaises(LanguageError) as error:evaluate(source,fixtures=wrong)
        self.assertEqual(error.exception.code,'fixture_error');self.assertFalse(error.exception.fixture_evidence['passed'])

    def test_capability_denial_in_tail_call_is_sticky(self):
        source='''agent a{provider:"model",tools:["secret"]};fn work(x){try{tool("secret",x)}catch e{1}};
        await agent_bind(a,[agent_tool("double",work)])(21)'''
        fixtures={'agents':[lane(agent(tools=['secret']),actions=[{'tool':'double','input':21,'expect':1},{'final':42}])]}
        with self.assertRaises(LanguageError):evaluate(source,fixtures=fixtures,capabilities=['secret'],tool_descriptors={'secret':{}})

    def test_undeclared_host_call_is_sticky_even_if_caught(self):
        for fixtures in ({},{'tools':[{'name':'text.normalize','input':1,'output':2}]}):
            with self.subTest(fixtures=fixtures),self.assertRaises(LanguageError):
                evaluate('try{tool("text.normalize",0)}catch e{42}',fixtures=fixtures,capabilities=['text.normalize'])

    def test_fixture_response_does_not_grant_project_capability(self):
        with self.assertRaises(LanguageError):evaluate('try{tool("secret",0)}catch e{42}',fixtures={'tools':[{'name':'secret','input':0,'output':42}]})

    def test_unused_agent_config_tools_do_not_require_responses(self):
        result=evaluate('agent a{provider:"model",tools:["text.normalize"]};a(21)',fixtures={'agents':[lane(agent(tools=['text.normalize']),actions=[{'final':42}])]},capabilities=['text.normalize'])
        self.assertEqual(result['value'],42)

    def test_all_declared_fixture_uses_are_required(self):
        for fixtures in ({'agents':[lane()]},{'tools':[{'name':'text.normalize','input':0,'output':42}]}):
            with self.subTest(fixtures=fixtures),self.assertRaisesRegex(LanguageError,'Unconsumed'):evaluate('42',fixtures=fixtures,capabilities=['text.normalize'])

    def test_extra_agent_and_tool_calls_remain_failed(self):
        source=SOURCE.replace('[await bound(21),calls]','await bound(21);try{await bound(21)}catch e{42}')
        with self.assertRaisesRegex(LanguageError,'extra'):evaluate(source,fixtures={'agents':[lane()]})
        with self.assertRaisesRegex(LanguageError,'extra'):
            evaluate('tool("text.normalize",0);try{tool("text.normalize",0)}catch e{42}',fixtures={'tools':[{'name':'text.normalize','input':0,'output':42}]},capabilities=['text.normalize'])

    def test_definition_and_input_matching_are_exact(self):
        for change in ('input','definition'):
            fixtures={'agents':[lane()]}
            if change=='input':fixtures['agents'][0]['input']=21.0
            else:fixtures['agents'][0]['definition']['config']['prompt']='changed'
            with self.subTest(change=change),self.assertRaises(LanguageError):evaluate(SOURCE,fixtures=fixtures)
        with self.assertRaises(LanguageError):evaluate('tool("text.normalize",true)',fixtures={'tools':[{'name':'text.normalize','input':1,'output':42}]},capabilities=['text.normalize'])

    def test_epoch_cleanup_effects_are_checked_before_verification(self):
        source='''var stack=null;fn work(x){stack=exit_stack();stack.callback(fn(){tool("audit","closed")});x*2};
        agent a{provider:"model",tools:["audit"]};await agent_bind(a,[agent_tool("double",work,capabilities=["audit"])])(21)'''
        fixtures={'agents':[lane(agent(tools=['audit']))],'tools':[{'name':'audit','input':'closed','output':None}]}
        self.assertTrue(evaluate(source,fixtures=fixtures,capabilities=['audit'])['fixtures']['passed'])

    def test_unsupported_effect_families_cannot_be_caught_into_success(self):
        sources=['try{workflow({},null)}catch e{42}',
                 'try{await current_program()(0)}catch e{42}',
                 'try{await record_skill(fn(x){x},0)}catch e{42}']
        for source in sources:
            with self.subTest(source=source),self.assertRaises(LanguageError):evaluate(source,fixtures={})

    def test_hard_budget_is_preserved_over_unused_fixture_failure(self):
        with self.assertRaises(ResourceLimit):evaluate('while true{null}',fixtures={'agents':[lane()]},step_limit=100)

    def test_schema_and_validator_reject_ambiguous_or_unbounded_scripts(self):
        bad=[None,[],{'unknown':[]},{'agents':[lane(),lane()]},{'tools':[{'name':'x','input':0,'output':0},{'name':'x','input':0,'output':1}]},
             {'agents':[lane(actions=[{'final':1},{'final':2}])]},{'agents':[lane(times=True)]},
             {'agents':[lane(agent(maxSteps=1))]},{'tools':[{'name':'program.adopt','input':0,'output':0}]},
             {'agents':[lane(value=i,times=64) for i in range(5)]}]
        for fixtures in bad:
            with self.subTest(fixtures=fixtures),self.assertRaises(LanguageError):validate_fixtures(fixtures)
        self.assertFalse(fixture_schema()['additionalProperties'])
        self.assertEqual(validate_fixtures({}),{})

    def test_case_hash_includes_every_fixture_input_and_expectation(self):
        definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':SOURCE},'languageVersions':{'main.chaos':2}}
        cases=[{'input':None,'expect':[42,1],'fixtures':{'agents':[lane()]}}]
        validated=validate_cases(cases,definition);before=cases_hash(validated)
        changed=copy.deepcopy(cases);changed[0]['fixtures']['agents'][0]['actions'][0]['expect']=43
        self.assertNotEqual(before,cases_hash(validate_cases(changed,definition)))
        for key in ('input','definition','actions','times'):
            self.assertIn(key,fixture_schema()['properties']['agents']['items']['properties'])

    def test_fixture_values_and_responses_are_copied(self):
        fixture={'tools':[{'name':'data.select','input':{},'output':{'items':[1]}}]}
        snapshot=copy.deepcopy(fixture);result=evaluate('tool("data.select",{})',fixtures=fixture,capabilities=['data.select'])
        result['value']['items'].append(2);self.assertEqual(fixture,snapshot)

    def test_registered_tool_contracts_apply_to_mock_inputs_and_outputs(self):
        for value,output in [('bad',42),(1,'bad')]:
            with self.subTest(value=value,output=output),self.assertRaises(LanguageError):
                evaluate('try{tool("typed",input)}catch e{42}',value,
                    fixtures={'tools':[{'name':'typed','input':value,'output':output}]},capabilities=['typed'],
                    tool_descriptors={'typed':{'inputType':'Int','outputType':'Int'}})

    def test_ordinary_caught_application_error_remains_a_legitimate_tool_result(self):
        source='''agent a{provider:"model",tools:[]};fn work(x){try{throw "ordinary"}catch e{1}};
        await agent_bind(a,[agent_tool("double",work)])(21)'''
        fixture={'agents':[lane(actions=[{'tool':'double','input':21,'expect':1},{'final':42}])]}
        self.assertEqual(evaluate(source,fixtures=fixture)['value'],42)
