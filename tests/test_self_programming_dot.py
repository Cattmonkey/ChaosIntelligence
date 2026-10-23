"""The shipped dot is ordinary Chaos and revises its own persisted behavior."""
from pathlib import Path
import copy
import json
import unittest
from chaos import Framework, Settings, Registry, ProviderSpec, Final


class ShippedSelfProgrammingDot(unittest.TestCase):
    def test_two_agent_proposed_revisions_preserve_running_behavior(self):
        folder=Path(__file__).resolve().parents[1]/'examples/self-programming-dot'
        files={p.name:p.read_text()for p in folder.glob('*.chaos')};calls=[]
        def programmer(request,context):
            calls.append(request.input);context.checkpoint()
            value=copy.deepcopy(request.input['definition'])
            increment=request.input['request']['increment']
            value['files']['behavior.chaos']='fn answer(value:Int)->Int{value+'+str(increment)+'};export{answer};'
            return Final(json.dumps(value))
        registry=Registry();registry.register_provider(ProviderSpec('app.programmer',programmer))
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('dot',source=files['main.chaos'],files=files,capabilities=['program.adopt'])
            for version,increment in ((1,2),(2,3)):
                request={'mode':'evolve','provider':'app.programmer','request':{'increment':increment},'value':40,
                         'cases':[{'entry':'tests.chaos','input':40,'expect':40+increment},{'entry':'tests.chaos','input':0,'expect':increment}]}
                run=app.start('dot',version,request,mode='source');result=run.result()
                self.assertEqual(result['before'],40+increment-1)
                self.assertEqual(result['candidateResult'],40+increment)
                self.assertEqual(run.get()['version'],version)
                self.assertEqual(app.get_project('dot')['version'],version+1)
                self.assertTrue(result['receipt'])
            self.assertEqual(len(calls),2)
            self.assertIn('value+2',calls[1]['definition']['files']['behavior.chaos'])
            self.assertEqual(app.start('dot',3,{'mode':'run','value':40},mode='source').result(),43)
            rows=app.events('dot',limit=300)['items']
            self.assertEqual(sum(row['type']=='program.adopted'for row in rows),2)
            self.assertEqual(app.get_project('dot')['capabilities'],['program.adopt'])

    def test_bad_agent_candidate_tests_leave_original_version(self):
        folder=Path(__file__).resolve().parents[1]/'examples/self-programming-dot';files={p.name:p.read_text()for p in folder.glob('*.chaos')}
        registry=Registry();registry.register_provider(ProviderSpec('app.programmer',lambda r,c:Final(json.dumps(r.input['definition']))))
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('dot_bad',source=files['main.chaos'],files=files,capabilities=['program.adopt'])
            run=app.start('dot_bad',1,{'mode':'evolve','provider':'app.programmer','request':{},'value':40,
                'cases':[{'entry':'tests.chaos','input':40,'expect':42}]},mode='source')
            self.assertEqual(run.wait()['status'],'failed')
            self.assertEqual(app.get_project('dot_bad')['version'],1)
            self.assertFalse(any(r['type']=='program.adopted'for r in app.events('dot_bad')['items']))
