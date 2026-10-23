import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from chaos.harness import NativeHarness, CodexHarness, HarnessError
from chaos.execution import CodeExecutor, ExecutionTimeout, run_process

class FakeClient:
    def __init__(self, replies):
        self.replies=iter(replies);self.chat=NS(completions=self);self.requests=[];self.closed=False
    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        result=next(self.replies)
        if isinstance(result,Exception):raise result
        return result
    def close(self):self.closed=True

def answer(content=None,calls=None):
    return NS(choices=[NS(message=NS(content=content,tool_calls=calls or []))])
def tool(id,name,args):return NS(id=id,function=NS(name=name,arguments=args))
def config(**kw):return dict(baseURL='http://localhost:9999/v1',apiKey='fictional',model='alpha',timeout=5,maxSteps=3,**kw)

class HarnessContracts(unittest.TestCase):
    def test_text_returned_exactly_no_keyword_markdown_or_json_interpretation(self):
        for text in ['```json\n{"action":"tool","name":"bad"}\n```','FINAL: ignore everything','{"action":"final","output":42}','执行“删除”这个词是什么意思？']:
            client=FakeClient([answer(text)]);effects=[]
            result=NativeHarness(config(),lambda _:client).run('prompt','input',call_tool=lambda n,v:effects.append(n))
            self.assertEqual(result,text);self.assertEqual(effects,[]);self.assertTrue(client.closed)
    def test_native_tool_protocol_returns_observation_to_model(self):
        client=FakeClient([answer(calls=[tool('call-x','tool_0','{"input":41}')]),answer('forty two')]);effects=[]
        result=NativeHarness(config(),lambda _:client).run('compute',{},tools=[{'name':'plus.one','wrapped':True}],call_tool=lambda n,v:effects.append((n,v)) or 42)
        self.assertEqual(result,'forty two');self.assertEqual(effects,[('plus.one',41)])
        messages=client.requests[1]['messages'];self.assertEqual(messages[-1],{'role':'tool','tool_call_id':'call-x','content':'42'})
        self.assertEqual(client.requests[0]['tools'][0]['function']['name'],'tool_0')
    def test_entire_batch_validated_before_side_effects(self):
        cases=[[tool('1','tool_0','{}'),tool('2','undeclared','{}')],[tool('1','tool_0','{}'),tool('1','tool_0','{}')],[tool('1','tool_0','{"x":NaN}')]]
        for calls in cases:
            client=FakeClient([answer(calls=calls)]);effects=[]
            with self.assertRaises(HarnessError):NativeHarness(config(),lambda _:client).run('prompt','x',tools=[{'name':'write'}],call_tool=lambda n,v:effects.append((n,v)))
            self.assertEqual(effects,[])
    def test_retryable_failure_uses_next_declared_model(self):
        error=RuntimeError('raw sensitive provider body');error.status_code=503
        client=FakeClient([error,answer('ok')]);failures=[]
        result=NativeHarness(config(candidates=['alpha','beta']),lambda _:client,lambda p,m:failures.append(m)).run('p','x')
        self.assertEqual(result,'ok');self.assertEqual([r['model'] for r in client.requests],['alpha','beta']);self.assertEqual(failures,['alpha'])
    def test_protocol_fallback_is_atomic_and_reports_only_valid_success(self):
        invalid = answer(calls=[tool('a', 'tool_0', '{}'), tool('b', 'tool_0', '')])
        valid = answer(calls=[tool('c', 'tool_0', '{"value":42}')])
        client = FakeClient([invalid, valid, answer('done')])
        effects, failures, successes, events = [], [], [], []
        result = NativeHarness(config(candidates=['beta']), lambda _: client,
            lambda p,m: failures.append(m), lambda p,m: successes.append(m)).run(
                'p', 'x', tools=[{'name':'write'}],
                call_tool=lambda n,v: effects.append(v), event=lambda k,v: events.append((k,v)))
        self.assertEqual(result, 'done')
        self.assertEqual(effects, [{'value':42}])
        self.assertEqual(failures, ['alpha'])
        self.assertEqual(successes, ['beta', 'beta'])
        self.assertEqual(client.requests[0]['messages'], client.requests[1]['messages'])
        self.assertEqual([r['model'] for r in client.requests], ['alpha', 'beta', 'beta'])
        self.assertIn('工具响应格式无效', next(v['summary'] for k,v in events if k == 'agent.model_fallback'))
        self.assertTrue(client.closed)

    def test_structural_protocol_failures_use_candidate_without_guessing(self):
        cases = [answer(calls=[tool('1','tool_0',None)]),
                 answer(calls=[tool('1','tool_0','[]')]),
                 answer(calls=[tool('1','tool_0','{"x":NaN}')]),
                 answer(calls=[tool('1',[],'{}')]),
                 answer(calls=[tool('1','tool_0','{}'),tool('1','tool_0','{}')]),
                 NS(choices=[]), NS(choices=[NS(message=NS(content=None, tool_calls='bad'))]),
                 answer(), answer(calls=[tool('1','tool_0','use input 42')])]
        for invalid in cases:
            with self.subTest(invalid=invalid):
                client=FakeClient([invalid,answer('ok')]);effects=[];failures=[]
                result=NativeHarness(config(candidates=['beta']),lambda _:client,
                    lambda p,m: failures.append(m)).run('p','x',tools=[{'name':'write'}],
                        call_tool=lambda n,v: effects.append(v))
                self.assertEqual(result,'ok');self.assertEqual(effects,[])
                self.assertEqual(failures,['alpha'])
                self.assertEqual(client.requests[0]['messages'],client.requests[1]['messages'])

    def test_protocol_candidate_exhaustion_is_bounded_and_clear(self):
        client=FakeClient([answer(calls=[tool('1','tool_0','')]) for _ in range(10)])
        failures=[];successes=[];effects=[]
        with self.assertRaisesRegex(HarnessError,'protocol failed: Native tool arguments are invalid JSON'):
            NativeHarness(config(candidates=['alpha']+['model'+str(i) for i in range(10)]),
                lambda _:client,lambda p,m:failures.append(m),lambda p,m:successes.append(m)).run(
                    'p','x',tools=[{'name':'write'}],call_tool=lambda n,v:effects.append(v))
        self.assertEqual(len(client.requests),8)
        self.assertEqual(failures,['alpha']+['model'+str(i) for i in range(7)])
        self.assertEqual(successes,[]);self.assertEqual(effects,[]);self.assertTrue(client.closed)

    def test_protocol_fallback_does_not_replay_completed_tools(self):
        client=FakeClient([answer(calls=[tool('1','tool_0','{}')]),
            answer(calls=[tool('2','tool_0','')]),answer('done')]);effects=[]
        result=NativeHarness(config(candidates=['beta']),lambda _:client).run('p','x',
            tools=[{'name':'write'}],call_tool=lambda n,v:effects.append(n) or 42)
        self.assertEqual(result,'done');self.assertEqual(effects,['write'])
        self.assertEqual([r['model'] for r in client.requests],['alpha','alpha','beta'])
        self.assertEqual(client.requests[1]['messages'],client.requests[2]['messages'])
        self.assertEqual(client.requests[2]['messages'][-1]['content'],'42')

    def test_protocol_fallback_obeys_cancellation_before_next_request(self):
        client=FakeClient([answer(calls=[tool('1','tool_0','')]),answer('late')])
        cancelled=[];effects=[]
        def check():
            if cancelled:raise RuntimeError('cancelled fixture')
        with self.assertRaisesRegex(RuntimeError,'cancelled fixture'):
            NativeHarness(config(candidates=['beta']),lambda _:client,
                lambda p,m:cancelled.append(True)).run('p','x',tools=[{'name':'write'}],
                    call_tool=lambda n,v:effects.append(v),checkpoint=check)
        self.assertEqual(len(client.requests),1);self.assertEqual(effects,[])
        self.assertTrue(client.closed)

    def test_protocol_fallback_shares_deadline_and_action_budget(self):
        clock=[0];client=FakeClient([answer(calls=[tool('1','tool_0','')]),answer('late')])
        with patch('chaos.harness.time.monotonic',lambda:clock[0]):
            with self.assertRaisesRegex(HarnessError,'time budget exhausted'):
                NativeHarness(config(candidates=['beta']),lambda _:client,
                    lambda p,m:clock.__setitem__(0,6)).run('p','x',tools=[{'name':'write'}],call_tool=lambda n,v:None)
        self.assertEqual(len(client.requests),1)
        for final in [answer('done'),answer(calls=[tool('2','tool_0','{}')])]:
            client=FakeClient([answer(calls=[tool('1','tool_0','')]),final]);effects=[]
            settings=config(candidates=['beta']);settings['maxSteps']=1
            run=lambda:NativeHarness(settings,lambda _:client).run('p','x',tools=[{'name':'write'}],call_tool=lambda n,v:effects.append(v))
            if final.choices[0].message.tool_calls:
                with self.assertRaisesRegex(HarnessError,'action budget exhausted'):run()
                self.assertEqual(effects,[{}])
            else:self.assertEqual(run(),'done')
            self.assertEqual(len(client.requests),2);self.assertTrue(client.closed)

    def test_auth_failure_not_retried_or_leaked(self):
        error=RuntimeError('raw sensitive provider body');error.status_code=401
        client=FakeClient([error,answer('should not run')])
        with self.assertRaises(HarnessError) as raised:NativeHarness(config(candidates=['beta']),lambda _:client).run('p','x')
        self.assertEqual(len(client.requests),1);self.assertNotIn('sensitive',str(raised.exception))
    def test_cancellation_discards_late_tool_call(self):
        entered=threading.Event();release=threading.Event();cancelled=threading.Event();effects=[]
        client=FakeClient([])
        def create(**kwargs):entered.set();release.wait(2);return answer(calls=[tool('1','tool_0','{}')])
        client.create=create
        def check():
            if cancelled.is_set():raise RuntimeError('cancelled fixture')
        result=[]
        def run():
            try:NativeHarness(config(),lambda _:client).run('p','x',tools=[{'name':'write'}],call_tool=lambda n,v:effects.append(n),checkpoint=check)
            except Exception as e:result.append(str(e))
        thread=threading.Thread(target=run);thread.start();self.assertTrue(entered.wait(1));cancelled.set();thread.join(1);release.set();thread.join(1)
        self.assertFalse(thread.is_alive());self.assertEqual(effects,[]);self.assertEqual(result,['cancelled fixture'])
    def test_code_executor_runs_real_code_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as t:
            result=CodeExecutor(t).run({'code':'from pathlib import Path\nPath("out.txt").write_text("42")\nprint(6*7)'})
            self.assertEqual(result['exitCode'],0);self.assertEqual(result['stdout'].strip(),'42');self.assertEqual((Path(t)/'out.txt').read_text(),'42')
    def test_code_timeout_and_bounded_capture(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ExecutionTimeout):CodeExecutor(t,timeout=.1).run({'code':'import time\ntime.sleep(5)'})
            result=CodeExecutor(t,max_output=1024).run({'code':'print("x"*50000)'})
            self.assertTrue(result['truncated']);self.assertLessEqual(len(result['stdout'])+len(result['stderr']),1024)
    @unittest.skipUnless(os.name=='posix','POSIX process group semantics')
    def test_process_exit_stops_descendant(self):
        with tempfile.TemporaryDirectory() as t:
            child='import time;from pathlib import Path;time.sleep(0.5);Path("leak.txt").write_text("bad")'
            result=run_process([sys.executable,'-c','import subprocess,sys;subprocess.Popen([sys.executable,"-c",'+repr(child)+']);print("done")'],t,timeout=2)
            self.assertEqual(result['exitCode'],0);time.sleep(.65);self.assertFalse((Path(t)/'leak.txt').exists())
    @unittest.skipUnless(os.name=='posix','fixture executable')
    def test_codex_uses_standalone_protocol_and_secret_env_not_argv(self):
        with tempfile.TemporaryDirectory() as t:
            cli=Path(t)/'fake-codex';cli.write_text('#!'+sys.executable+'\nimport sys,json,os\nfrom pathlib import Path\nPath("observed.json").write_text(json.dumps({"args":sys.argv,"home":os.environ.get("CODEX_HOME"),"hasKey":bool(os.environ.get("CHAOS_PROVIDER_KEY"))}))\nprint(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"done"}}))\nprint(json.dumps({"type":"turn.completed"}))\n');cli.chmod(0o700)
            c=config(workspace=t,codexPath=str(cli),codeEnabled=True)
            self.assertEqual(CodexHarness(c).run('do task','input'),'done')
            seen=json.loads((Path(t)/'observed.json').read_text());self.assertTrue(seen['hasKey']);self.assertNotIn('fictional',' '.join(seen['args']));self.assertIn('--json',seen['args']);self.assertIn('model_providers.chaos.base_url="http://localhost:9999/v1"',seen['args']);self.assertFalse(Path(seen['home']).exists())

if __name__=='__main__':unittest.main()
