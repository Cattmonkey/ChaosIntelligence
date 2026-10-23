"""Language-authored regression recovery from immutable history across restart."""
from pathlib import Path
import copy
import json
import tempfile
import time
import unittest
from chaos import Framework
from tests.test_session_integration import NativeFixture


class RecoveringDot(unittest.TestCase):
    def files(self):
        return {p.name:p.read_text() for p in (Path(__file__).resolve().parents[1]/'examples/recovering-dot').glob('*.chaos')}
    def fixture(self,source):
        def respond(body):
            value=json.loads([m for m in body['messages'] if m['role']=='user'][-1]['content'])
            definition=copy.deepcopy(value['definition']);definition['files']['behavior.chaos']=source
            return {'role':'assistant','content':json.dumps(definition)}
        fixture=NativeFixture(respond);self.addCleanup(fixture.close);return fixture
    def wait(self,app,turn,status='waiting'):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            item=app.get_process('dot','dot')
            if item['turn']==turn and item['status']==status:return item
            if item['status']=='needs_reconciliation':self.fail(str(item))
            time.sleep(.01)
        self.fail(str(item))
    def create(self,app):
        files=self.files()
        app.create_project('dot',source=files['main.chaos'],files=files,capabilities=['program.adopt','process.manage','session.manage'])
        app.start('dot',1,{'intervalSeconds':None},mode='source').result(timeout=8)

    def test_three_turn_native_dot_recovers_exact_program_after_restart(self):
        fixture=self.fixture('fn answer(value:Int)->Int {value*2};export {answer};')
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'dot.sqlite3'
            with Framework(db,settings=fixture.settings) as app:
                self.create(app);first=self.wait(app,1)
                self.assertEqual(first['state']['phase'],'check')
                self.assertEqual(first['output']['before'],9)
                self.assertEqual(first['output']['afterAdoptSameFunction'],9)
                original=app.program_revision('dot',1);candidate=app.program_revision('dot',2)
                self.assertNotEqual(original['origin']['definitionHash'],candidate['origin']['definitionHash'])
                self.assertEqual(candidate['definition']['files']['behavior.chaos'],'fn answer(value:Int)->Int {value*2};export {answer};')
                self.assertEqual(app.agent_session('dot','recovering_programmer')['turn'],1)
            with Framework(db,settings=fixture.settings) as app:
                app.send_process('dot','dot',None,message_id='evaluate-regression')
                second=self.wait(app,2)
                self.assertFalse(second['output']['regression']['passed'])
                self.assertEqual(second['output']['before'],6)
                self.assertEqual(second['output']['afterAdoptSameFunction'],6)
                self.assertEqual(second['output']['restoredFrom'],original['origin'])
                self.assertEqual(app.get_project('dot')['version'],3)
                self.assertEqual(app.program_revision('dot',3)['definition'],original['definition'])
                self.assertEqual(app.program_revision('dot',2),candidate)
                self.assertEqual(app.get_project('dot')['capabilities'],['process.manage','program.adopt','session.manage'])
                app.send_process('dot','dot',None,message_id='verify-restored-program')
                done=self.wait(app,3,'completed')
                self.assertEqual(done['output'],{'value':9,'version':3,'recovered':True,'historyVersions':[1,2,3]})
                events=app.events('dot',limit=300)['items']
                adopted=[row for row in events if row['type']=='program.adopted']
                self.assertEqual([row['payload']['version'] for row in adopted],[2,3])
                self.assertEqual([app.get_run(row['run_id'])['version'] for row in adopted],[1,2])
                self.assertEqual([row['kind'] for row in app.program_history('dot')['items']],['created','adopted','adopted'])
                self.assertEqual(len(fixture.requests),1)

    def test_candidate_meeting_acceptance_finishes_without_recovery(self):
        fixture=self.fixture('fn answer(value:Int)->Int {let result=value*value;result};export {answer};')
        with Framework(settings=fixture.settings) as app:
            self.create(app);self.wait(app,1)
            app.send_process('dot','dot',None,message_id='evaluate-good-candidate')
            done=self.wait(app,2,'completed')
            self.assertEqual(done['output'],{'value':9,'version':2,'recovered':False})
            self.assertEqual([row['version'] for row in app.program_history('dot')['items']],[1,2])

    def test_prompt_cannot_authorize_modifying_the_recovery_controller(self):
        def respond(body):
            value=json.loads([m for m in body['messages'] if m['role']=='user'][-1]['content'])
            definition=value['definition'];definition['files']['dot.chaos']='fn step(ctx){process_step(ctx.state,"forged",true)};export{step};'
            return {'role':'assistant','content':json.dumps(definition)}
        fixture=NativeFixture(respond);self.addCleanup(fixture.close)
        with Framework(settings=fixture.settings) as app:
            self.create(app);deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                item=app.get_process('dot','dot')
                if item['status']=='needs_reconciliation':break
                time.sleep(.01)
            self.assertEqual(item['status'],'needs_reconciliation')
            self.assertEqual(app.get_project('dot')['version'],1)
            self.assertEqual(len(app.program_history('dot')['items']),1)
            self.assertFalse(any(row['type']=='program.adopted' for row in app.events('dot')['items']))
