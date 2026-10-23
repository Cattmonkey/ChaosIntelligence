import copy
import unittest
from chaos.harness import NativeHarness
from chaos.session_protocol import encode_messages,validate_messages
from tests.test_harness_runtime import CONFIG,FakeClient,message,call
from tests.test_computer_protocol import IMAGE,FINAL

class ComputerHarnessTests(unittest.TestCase):
    def harness(self,responses):
        client=FakeClient(responses);return NativeHarness(CONFIG,client_factory=lambda _:client),client
    def test_explicit_stateless_visual_override_is_native_parts(self):
        h,c=self.harness([message('saw image')]);self.assertEqual(h.run('system',None,user_message=IMAGE),'saw image')
        self.assertEqual(c.requests[0]['messages'][1],IMAGE);self.assertTrue(c.closed)
        # Model request owns its copied data, caller cannot mutate recorded source.
        c.requests[0]['messages'][1]['content'][0]['text']='changed';self.assertEqual(IMAGE['content'][0]['text'],'Actual observation')
    def test_plain_json_never_becomes_native_parts(self):
        h,c=self.harness([message('text')]);h.run('system',IMAGE)
        self.assertIsInstance(c.requests[0]['messages'][1]['content'],str)
    def test_session_journals_exact_visual_and_second_turn_sends_it(self):
        journal=[];h,c=self.harness([message('first')]);result=h.run_session('',None,[],user_message=IMAGE,journal=lambda batch:journal.extend(copy.deepcopy(batch)))
        self.assertEqual(journal,result['messages']);self.assertEqual(journal[0],IMAGE);validate_messages(journal)
        second,client=self.harness([message('second')]);second.run_session('','next',journal)
        self.assertEqual(client.requests[0]['messages'][1:-1],encode_messages(journal))
    def test_bad_override_and_overflow_before_client_or_journal(self):
        for override in [[],{},FINAL,{'role':'system','content':'bad'},{'role':'user','content':'x'*40000}]:
            for session in (False,True):
                h,c=self.harness([message('must not run')]);journal=[]
                with self.assertRaises((ValueError,TypeError)):
                    if session:h.run_session('',None,[],user_message=override,journal=lambda batch:journal.extend(batch))
                    else:h.run('',None,user_message=override)
                self.assertEqual(c.requests,[]);self.assertEqual(journal,[])
    def test_visual_tools_share_existing_loop(self):
        effects=[];h,c=self.harness([message(calls=[call('tool_0','{"input":42}')]),message('done')])
        self.assertEqual(h.run('',None,[{'name':'app.echo','wrapped':True}],lambda n,v:effects.append(v)or v,user_message=IMAGE),'done')
        self.assertEqual(effects,[42]);self.assertEqual(c.requests[-1]['messages'][1],IMAGE)
