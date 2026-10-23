import copy
import unittest
from chaos.harness import NativeHarness,HarnessError
from chaos.session_protocol import tool_alias,encode_messages,validate_messages
from chaos.suspension import TaskCancelled
from tests.test_harness_runtime import FakeClient,CONFIG,message,call

class SessionHarnessTests(unittest.TestCase):
    def harness(self,responses,**options):
        client=FakeClient(responses);return NativeHarness({**CONFIG,**options},client_factory=lambda _:client),client
    def test_invalid_none_history_never_downgrades_to_stateless_run(self):
        for history in [None,{},(),True]:
            h,c=self.harness([message('must not run')]);journal=[]
            with self.assertRaises(ValueError):h.run_session('','',history,journal=lambda b:journal.extend(b))
            self.assertEqual(c.requests,[]);self.assertEqual(journal,[])

    def test_actual_second_request_uses_prior_native_assistant_tools_final(self):
        spec={'name':'app.echo','wrapped':True,'parameters':{'type':'object'}};journal=[];effects=[]
        h,c=self.harness([message(calls=[call(tool_alias('app.echo'),'{"input":42}')]),message('first')]);one=h.run_session('system',{'task':1},[],[spec],lambda n,v:effects.append(v)or v,journal=lambda batch:journal.extend(copy.deepcopy(batch)))
        self.assertEqual(one['messages'],journal);self.assertEqual(effects,[42]);self.assertEqual(one['messages'][-1]['content'],'first');h2,c2=self.harness([message('second')]);two=h2.run_session('system','next',one['messages'],[spec],lambda *a:None)
        self.assertEqual(c2.requests[0]['messages'][1:-1],encode_messages(journal));self.assertEqual(c2.requests[0]['messages'][-1],{'role':'user','content':'next'});self.assertEqual(two['output'],'second');self.assertEqual(len(two['messages']),2);self.assertTrue(c.closed and c2.closed)
    def test_journal_full_batch_precedes_any_tool(self):
        events=[];h,c=self.harness([message(calls=[call(tool_alias('x'),ident='1'),call(tool_alias('y'),ident='2')]),message('done')]);h.run_session('','',[],[{'name':'x'},{'name':'y'}],lambda n,v:events.append(('tool',n)),journal=lambda b:events.append(('journal',copy.deepcopy(b))))
        batch=events[1][1][0];self.assertEqual([v['name']for v in batch['tool_calls']],['x','y']);self.assertEqual(events[2],('tool','x'));self.assertEqual(events[3][1][0]['role'],'tool')
    def test_journal_failure_blocks_first_tool_and_keeps_client_closed(self):
        h,c=self.harness([message(calls=[call(tool_alias('x'))])]);seen=[]
        def journal(batch):
            if batch[0]['role']=='assistant':raise RuntimeError('storage failed')
        with self.assertRaisesRegex(RuntimeError,'storage failed'):h.run_session('','',[],[{'name':'x'}],lambda*a:seen.append(a),journal=journal)
        self.assertEqual(seen,[]);self.assertTrue(c.closed)
    def test_revoked_tool_history_never_rebinds_or_enables_calls(self):
        h,c=self.harness([message(calls=[call(tool_alias('old'))]),message('done')]);history=h.run_session('','',[],[{'name':'old'}],lambda*a:42)['messages']
        new,client=self.harness([message(calls=[call(tool_alias('old'))])]);seen=[]
        with self.assertRaises(HarnessError):new.run_session('','next',history,[{'name':'new'}],lambda*a:seen.append(a))
        self.assertEqual(client.requests[0]['messages'][2]['tool_calls'][0]['function']['name'],tool_alias('old'));self.assertEqual(client.requests[0]['tools'][0]['function']['name'],tool_alias('new'));self.assertEqual(seen,[])
    def test_invalid_history_and_overflow_make_no_model_request(self):
        for history,value,limit in [([{'role':'user','content':'incomplete'}],'x',100000),([], 'x'*990,1000)]:
            h,c=self.harness([message('unused')])
            with self.assertRaises(ValueError):h.run_session('',value,history,context_limit=limit)
            self.assertEqual(c.requests,[])
    def test_actual_result_overflow_preserves_intent_but_no_final(self):
        h,c=self.harness([message(calls=[call(tool_alias('x'))]),message('unused')]);j=[];effects=[]
        with self.assertRaises(ValueError):h.run_session('','',[],[{'name':'x'}],lambda*a:effects.append(1)or 'x'*40000,journal=lambda b:j.extend(b))
        self.assertEqual(effects,[1]);self.assertEqual([m['role']for m in j],['user','assistant']);self.assertEqual(len(c.requests),1)
    def test_media_bounded_and_no_image_tool_still_runs(self):
        for result in [{'text':'DOM only'},{'image':'data:image/png;base64,YQ=='}]:
            h,c=self.harness([message(calls=[call(tool_alias('browser'))]),message('done')]);r=h.run_session('','',[],[{'name':'browser','imageOutput':True}],lambda*a:result);validate_messages(r['messages']);self.assertEqual(r['output'],'done')
            if 'image'in result:self.assertEqual(c.requests[-1]['messages'][-1]['content'][1]['image_url']['url'],result['image'])
        h,c=self.harness([message(calls=[call(tool_alias('browser'))])]);
        with self.assertRaises(ValueError):h.run_session('','',[],[{'name':'browser','imageOutput':True}],lambda*a:{'image':'data:image/png;base64,'+'a'*40000})
    def test_cancellation_and_cleanup_error_preserve_hard_exception(self):
        h,c=self.harness([message(calls=[call(tool_alias('x'))])]);j=[]
        def tool(*a):raise TaskCancelled('stop')
        def close():c.closed=True;raise RuntimeError('cleanup')
        c.close=close
        with self.assertRaises(TaskCancelled):h.run_session('','',[],[{'name':'x'}],tool,journal=lambda b:j.extend(b))
        self.assertEqual([m['role']for m in j],['user','assistant']);self.assertTrue(c.closed)
    def test_fallback_keeps_completed_tool_once(self):
        error=RuntimeError('hidden');error.status_code=503;h,c=self.harness([message(calls=[call(tool_alias('x'))]),error,message('done')],candidates=['b']);seen=[]
        r=h.run_session('','',[],[{'name':'x'}],lambda*a:seen.append(1));self.assertEqual(seen,[1]);self.assertEqual(r['output'],'done');self.assertEqual([x['model']for x in c.requests],['model-a','model-a','b'])
    def test_entire_response_batch_validation_precedes_journal_effect(self):
        h,c=self.harness([message(calls=[call(tool_alias('x')),call('invalid',ident='2')])]);j=[];effects=[]
        with self.assertRaises(HarnessError):h.run_session('','',[],[{'name':'x'}],lambda*a:effects.append(1),journal=lambda b:j.extend(b))
        self.assertEqual(effects,[]);self.assertEqual([m['role']for m in j],['user'])
