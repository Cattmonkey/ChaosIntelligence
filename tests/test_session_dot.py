"""Shipped self-programming dot using real OpenAI SDK native durable roles."""
from pathlib import Path
import copy
import json
import time
import unittest
from chaos import Framework
from tests.test_session_integration import NativeFixture

class SessionDot(unittest.TestCase):
    def files(self):return {p.name:p.read_text()for p in (Path(__file__).resolve().parents[1]/'examples/session-dot').glob('*.chaos')}
    def fixture(self,bad=False):
        def respond(body):
            value=json.loads([m for m in body['messages']if m['role']=='user'][-1]['content'])
            if body['model']=='fixture-reviewer':output={'approve':True,'reason':'Run actual executable checks'}
            else:
                output=copy.deepcopy(value['definition']);n=-1 if bad else value['increment']
                output['files']['behavior.chaos']='fn answer(value:Int)->Int{value+'+str(n)+'};export{answer};'
            return {'role':'assistant','content':json.dumps(output)}
        f=NativeFixture(respond);self.addCleanup(f.close)
        f.settings.update({'roles':{'planner':{'provider':'text','model':'fixture-programmer','autoUpgrade':False},'reviewer':{'provider':'text','model':'fixture-reviewer','autoUpgrade':False}}},f.settings.public()['version'])
        return f
    def wait(self,app,status):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            item=app.get_process('dot','dot')
            if item['status']==status:return item
            if item['status']=='needs_reconciliation'and status!='needs_reconciliation':self.fail(str(item))
            time.sleep(.01)
        self.fail(str(item))
    def test_complete_language_guide_example_uses_actual_native_transport(self):
        f=NativeFixture();self.addCleanup(f.close)
        markdown=(Path(__file__).resolve().parents[1]/'docs/SESSIONS.md').read_text()
        source=markdown.split('```chaos\n',1)[1].split('```',1)[0]
        with Framework(settings=f.settings)as app:
            app.create_project('guide',source=source,capabilities=['session.manage'])
            result=app.start('guide',1,{'fact':42},mode='source').result()
            self.assertEqual(result[:2],['reply-1','reply-2'])
            self.assertEqual(result[2],app.agent_session('guide','assistant')['revision'])
            self.assertEqual([m['role']for m in f.requests[-1]['messages']],['system','user','assistant','user'])

    def test_three_turns_two_adoptions_preserve_both_native_role_conversations(self):
        f=self.fixture();files=self.files()
        with Framework(settings=f.settings)as app:
            app.create_project('dot',source=files['main.chaos'],files=files,capabilities=['process.manage','program.adopt','session.manage'])
            app.start('dot',1,None,mode='source').result();done=self.wait(app,'completed')
            self.assertEqual(done['turn'],3);self.assertEqual(done['state']['generation'],2)
            self.assertEqual(done['output'],{'value':43,'version':3});self.assertEqual(app.get_project('dot')['version'],3)
            self.assertEqual([r['model']for r in f.requests],['fixture-programmer','fixture-reviewer','fixture-programmer','fixture-reviewer'])
            for request in f.requests[2:]:self.assertEqual([m['role']for m in request['messages']],['system','user','assistant','user'])
            self.assertIn('value+2',json.loads(f.requests[2]['messages'][-1]['content'])['definition']['files']['behavior.chaos'])
            for key in ('dot_programmer','dot_reviewer'):
                self.assertEqual(app.agent_session('dot',key)['turn'],2)
                self.assertEqual([r['messageId']for r in app.agent_session_history('dot',key)['items']],["generation_0","generation_0","generation_1","generation_1"])
            adopted=[e for e in app.events('dot',limit=300)['items']if e['type']=='program.adopted'];self.assertEqual(len(adopted),2)
            for expected,event in zip((41,42),adopted):
                result=app.get_run(event['run_id'])['output']['output'];self.assertEqual(result['before'],expected);self.assertEqual(result['afterCommitSameFunction'],expected)
    def test_review_approval_cannot_replace_checks_and_session_commit_is_independent(self):
        f=self.fixture(bad=True);files=self.files()
        with Framework(settings=f.settings)as app:
            app.create_project('dot',source=files['main.chaos'],files=files,capabilities=['process.manage','program.adopt','session.manage'])
            app.start('dot',1,None,mode='source').result();failed=self.wait(app,'needs_reconciliation')
            self.assertEqual(failed['turn'],0);self.assertEqual(failed['state']['generation'],0);self.assertEqual(app.get_project('dot')['version'],1)
            self.assertEqual(app.agent_session('dot','dot_programmer')['turn'],1);self.assertEqual(app.agent_session('dot','dot_reviewer')['turn'],1)
            self.assertFalse(any(e['type']=='program.adopted'for e in app.events('dot')['items']))
