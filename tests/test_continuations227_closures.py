"""Separate original closure snapshot behavior from fresh suffix-local bindings."""
import json,os,subprocess,sys,unittest
from pathlib import Path
from chaos.language import Evaluator

ORDINARY='fn work(){fn read(){later};let x=1;let later=x;read()};work()'
class ContinuationClosure227(unittest.TestCase):
    def test_original_undefined_forward_name_contract_matches_retained_baseline(self):
        candidate=Path(__file__).resolve().parents[1]
        canonical=next(p for p in Path(__file__).resolve().parents if (p/'.development').is_dir() and (p/'chaos').is_dir())
        script='import json,sys;sys.path.insert(0,sys.argv[1]);from chaos.language import Evaluator,LanguageError\ntry: Evaluator().run(sys.argv[2])\nexcept LanguageError as error: print(json.dumps({"code":error.code,"record":error.record()},ensure_ascii=False))\nelse: raise AssertionError("ordinary snapshot unexpectedly saw forward name")'
        outcomes=[]
        for target in (canonical,candidate):
            child=subprocess.run([sys.executable,'-I','-B','-c',script,str(target),ORDINARY],text=True,capture_output=True,timeout=15,check=True)
            outcomes.append(json.loads(child.stdout))
        self.assertEqual(outcomes[0],outcomes[1])
        self.assertEqual(outcomes[1]['code'],'runtime_error')
        self.assertIn('later',outcomes[1]['record']['message'])
        self.assertIn('未定义名称',outcomes[1]['record']['message'])

    def test_closures_created_after_capture_retain_each_fresh_suffix_local(self):
        source='''var k=null;let p=prompt_tag();let first=call_prompt(p,fn(){let x=capture_continuation(p,fn(c){k=c;1});let later=x;fn(){later}});let second=k(2);let third=k(3);[first(),second(),third(),first()]'''
        e=Evaluator(timeout=20,step_limit=200000)
        self.assertEqual(e.run(source),[1,2,3,1])
        self.assertEqual(e.depth,0);self.assertEqual(e.frames,[]);self.assertFalse(e.suspension.control_state.stack)
