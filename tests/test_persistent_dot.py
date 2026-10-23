"""The shipped Chaos dot evolves across automatic fresh program turns."""
from pathlib import Path
import copy
import json
import time
import unittest
from chaos import Framework,Settings,Registry,ProviderSpec,Final


class PersistentDot(unittest.TestCase):
    def files(self):
        return {p.name:p.read_text()for p in (Path(__file__).resolve().parents[1]/'examples/persistent-dot').glob('*.chaos')}
    def provider(self,seen,bad=False):
        def respond(request,context):
            context.checkpoint();seen.append((request.name,copy.deepcopy(request.input)))
            if request.name=='reviewer':return Final({'approve':True,'reason':'candidate returned for real executable checks'})
            definition=copy.deepcopy(request.input['definition']);n=-1 if bad else request.input['increment']
            definition['files']['behavior.chaos']='fn answer(value:Int)->Int{value+'+str(n)+'};export{answer};'
            return Final(json.dumps(definition))
        return respond
    def wait(self,app,status):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            item=app.get_process('dot','dot')
            if item['status']==status:return item
            if item['status']=='needs_reconciliation' and status!='needs_reconciliation':self.fail(str(item))
            time.sleep(.01)
        self.fail(str(item))
    def test_three_automatic_turns_two_self_revisions_with_team_and_state(self):
        seen=[];registry=Registry();registry.register_provider(ProviderSpec('test.dot',self.provider(seen)));files=self.files()
        with Framework(settings=Settings(':memory:'),registry=registry)as app:
            app.create_project('dot',source=files['main.chaos'],files=files,capabilities=['process.manage','program.adopt'])
            boot=app.start('dot',1,{'provider':'test.dot'},mode='source');boot.result()
            done=self.wait(app,'completed')
            self.assertEqual(done['turn'],3);self.assertEqual(done['state']['generation'],2);self.assertEqual(done['state']['lastObserved'],43)
            self.assertEqual(done['output'],{'value':43,'version':3});self.assertEqual(app.get_project('dot')['version'],3)
            self.assertEqual([name for name,_ in seen],['programmer','reviewer','programmer','reviewer'])
            self.assertIn('value+2',seen[2][1]['definition']['files']['behavior.chaos'])
            rows=app.events('dot',limit=300)['items'];adoptions=[e for e in rows if e['type']=='program.adopted'];self.assertEqual(len(adoptions),2)
            self.assertNotEqual(adoptions[0]['run_id'],adoptions[1]['run_id'])
            for expected,event in zip((41,42),adoptions):
                run=app.get_run(event['run_id']);self.assertEqual(run['status'],'completed')
                out=run['output'];payload=out['output']if type(out)is dict and 'state'in out else out
                self.assertEqual(payload['before'],expected);self.assertEqual(payload['afterCommitSameFunction'],expected)
            self.assertTrue(any(e['type']=='agent.completed'and e['payload'].get('team',{}).get('roleId')=='reviewer'for e in rows))
            self.assertEqual(app.get_project('dot')['capabilities'],['process.manage','program.adopt'])
    def test_approving_reviewer_cannot_replace_actual_checks(self):
        seen=[];registry=Registry();registry.register_provider(ProviderSpec('test.dot',self.provider(seen,bad=True)));files=self.files()
        with Framework(settings=Settings(':memory:'),registry=registry)as app:
            app.create_project('dot',source=files['main.chaos'],files=files,capabilities=['process.manage','program.adopt'])
            app.start('dot',1,{'provider':'test.dot'},mode='source').result()
            failed=self.wait(app,'needs_reconciliation');self.assertEqual(failed['turn'],0);self.assertEqual(failed['state']['generation'],0)
            self.assertEqual(app.get_project('dot')['version'],1)
            self.assertFalse(any(e['type']=='program.adopted'for e in app.events('dot')['items']))
