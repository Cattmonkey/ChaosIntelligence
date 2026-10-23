"""Computer authority aliases preserve one lease and one action admission."""
import json
import unittest
from types import SimpleNamespace
from chaos.language import Evaluator, Native, LanguageError
from chaos.computer_values import Computer, ComputerAction, Observation, ObservedInput, project, request, resolve
from chaos.capture_graph import seal
from test_computer_values import ComputerFixture


class ComputerAuthority(unittest.TestCase):
    def evaluator(self):
        host=ComputerFixture();e=host.evaluator(agent_handler=lambda *args:'ordinary')
        e.env.bind('limited',Native('limited',lambda f:e.invoke(f,[]),e,capabilities=frozenset()))
        e.env.bind('browser_scope',Native('browser_scope',lambda f:e.invoke(f,[]),e,capabilities=frozenset({'browser.control'})))
        return e,host

    def test_nested_alias_denial_precedes_consumption_and_original_remains_wide(self):
        e,host=self.evaluator()
        result=e.run('''let c=await computer_open();let o=await computer_observe(c);
let a=computer_action(o,{action:"click",ref:"e1"});let alias=limited(fn(){[{c:c,a:a}]})[0];
let denied=try{await alias.c(alias.a)}catch e{e.code};let unused=action_info(a).used;
let before=computer_info(c).status;await c(a);[denied,unused,before,action_info(alias.a).used]''')
        self.assertEqual(result,['capability_error',False,'open',True])
        self.assertEqual([op for op,_ in host.calls],['open','observe','act'])

    def test_narrow_observation_cannot_mint_wider_action(self):
        e,host=self.evaluator()
        result=e.run('''let c=await computer_open();let o=await computer_observe(c);
let narrow=limited(fn(){o});let a=computer_action(narrow,{action:"click",ref:"e1"});
[try{await c(a)}catch e{e.code},action_info(a).used,observation_info(narrow).id]''')
        self.assertEqual(result,['capability_error',False,'observation-1'])
        self.assertEqual([op for op,_ in host.calls],['open','observe'])

    def test_deferred_request_retains_construction_ceiling_and_capture_roots(self):
        e,host=self.evaluator()
        result=e.run('''let c=await computer_open();let task=limited(fn(){computer_observe(c)});
let code=try{await task}catch e{e.code};[code,computer_info(c).status,task,c]''')
        self.assertEqual(result[:2],['capability_error','open']);self.assertEqual([op for op,_ in host.calls],['open'])
        task=result[2];self.assertTrue(task.capture_roots)
        self.assertEqual(task.capture_roots[0].capabilities,frozenset())
        self.assertFalse(result[3].busy)

    def test_shared_lease_constructor_properties_and_close_alias(self):
        e,host=self.evaluator()
        result=e.run('let c=await computer_open();let alias=browser_scope(fn(){c});await computer_close(alias);[computer_info(c).status,computer_info(alias).status,c,alias]')
        self.assertEqual(result[:2],['closed','closed'])
        c=result[2];alias=result[3];self.assertIs(c.lease,alias.lease)
        self.assertIsNone(c.capabilities);self.assertEqual(alias.capabilities,frozenset({'browser.control'}))
        alias.busy=True;self.assertTrue(c.busy);c.busy=False
        alias.context_active=True;self.assertTrue(c.context_active)
        alias.observation_id='shared';self.assertEqual(c.observation_id,'shared')
        legacy=Computer(e,{'id':'legacy'},e.suspension.epoch,True,'observation',True)
        self.assertTrue(legacy.busy);self.assertEqual(legacy.observation_id,'observation');self.assertTrue(legacy.context_active)

    def test_permitted_action_aliases_share_single_consumption(self):
        e,host=self.evaluator()
        result=e.run('''let c=await computer_open();let o=await computer_observe(c);let a=computer_action(o,{action:"click",ref:"e1"});
let alias=browser_scope(fn(){a});await c(alias);[action_info(a).used,try{await c(a)}catch e{e.message},a,alias]''')
        self.assertTrue(result[0]);self.assertIn('consumed',result[1])
        self.assertIs(result[2].admission,result[3].admission)
        self.assertEqual(sum(op=='act' for op,_ in host.calls),1)

    def test_alias_observation_updates_shared_freshness_without_consuming_stale_action(self):
        e,host=self.evaluator()
        result=e.run('''let c=await computer_open();let o=await computer_observe(c);let a=computer_action(o,{action:"click",ref:"e1"});
let alias=browser_scope(fn(){c});await computer_observe(alias);
[try{await c(a)}catch e{e.message},action_info(a).used]''')
        self.assertIn('stale',result[0]);self.assertFalse(result[1])
        self.assertEqual([op for op,_ in host.calls],['open','observe','observe'])

    def test_projected_alias_cannot_enter_an_already_active_context(self):
        e,host=self.evaluator()
        result=e.run('''let c=await computer_open();let alias=browser_scope(fn(){c});
let result=async with c as original{try{async with alias as duplicate{42}}catch e{e.code}};
[result,computer_info(c).status,computer_info(alias).status]''')
        self.assertEqual(result,['computer_busy','closed','closed'])
        self.assertEqual([op for op,_ in host.calls],['open','close'])

    def test_descriptor_stored_field_and_generated_function_preserve_ceiling(self):
        for expression,consume in (
            ('c','computer_close(saved)'),
            ('staticmethod(c)','C.call=saved;let o=await computer_observe(c);C.call(computer_action(o,{action:"click",ref:"e1"}))'),
            ('fn(){c}','computer_close(saved())'),
        ):
            e,host=self.evaluator()
            source='let c=await computer_open();class C{var call=null};let saved=limited(fn(){'+expression+'});try{await '+consume+'}catch e{e.code}'
            if expression=='staticmethod(c)':
                source='let c=await computer_open();class C{var call=null};let saved=limited(fn(){'+expression+'});C.call=saved;let o=await computer_observe(c);try{await C.call(computer_action(o,{action:"click",ref:"e1"}))}catch e{e.code}'
            with self.subTest(expression=expression):
                self.assertEqual(e.run(source),'capability_error')
                self.assertFalse(any(op in ('act','close') for op,_ in host.calls))

    def test_compiled_higher_order_calls_cannot_widen_projected_computer(self):
        e,host=self.evaluator()
        result=e.run('''let c=await computer_open();let o=await computer_observe(c);let a=computer_action(o,{action:"click",ref:"e1"});
let narrow=limited(fn(){c});let dispatch=compile_function(fn(target,value){target(value)},target="auto-flow");
[try{await dispatch(narrow,a)}catch e{e.code},action_info(a).used]''')
        self.assertEqual(result,['capability_error',False])
        self.assertEqual([op for op,_ in host.calls],['open','observe'])

    def test_channel_transfer_retains_narrowed_computer(self):
        e,host=self.evaluator()
        result=e.run('''let c=await computer_open();let channel=channel(1);let narrow=limited(fn(){c});
await channel_send(channel,narrow);let value=(await channel_receive(channel)).value;
try{await computer_close(value)}catch e{e.code}''')
        self.assertEqual(result,'capability_error');self.assertEqual([op for op,_ in host.calls],['open'])

    def test_visual_read_only_input_still_reaches_agent(self):
        e,host=self.evaluator()
        result=e.run('''agent vision{provider:"model",tools:[]};let c=await computer_open();let o=await computer_observe(c);
let image=limited(fn(){observation_input(o,{question:"look"})});let answer=vision(image);[answer,image]''')
        self.assertEqual(result[0],'visual sync');self.assertEqual(len(host.agents),1)
        self.assertEqual(result[1].capabilities,frozenset())

    def test_reconcile_request_keeps_observation_ceiling_and_capture(self):
        e,host=self.evaluator()
        c,o=e.run('let c=await computer_open();let o=await computer_observe(c);[c,o]')
        o=project(e,o,SimpleNamespace(capabilities=frozenset()))
        descriptor=request(e,'reconcile',{'id':c.snapshot['id'],'observationId':o.id,'evidence':'inspected','location':{}},c,observation=o)
        self.assertIs(descriptor.observation,o);self.assertEqual(descriptor.capabilities,frozenset())
        task=resolve(e,descriptor);self.assertEqual(task.capture_roots,(descriptor,))

    def test_capture_graph_validates_computer_observation_and_request_owners(self):
        first,host=self.evaluator();c,o=first.run('let c=await computer_open();let o=await computer_observe(c);[c,o]')
        other=Evaluator()
        for value in (c,o,request(first,'observe',{},c),ObservedInput(first,o,'null')):
            with self.subTest(kind=type(value).__name__),self.assertRaises(LanguageError):seal(other,[value],None)


if __name__=='__main__':unittest.main()
