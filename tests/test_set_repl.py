"""Actual REPL JSON output agrees with SDK values without changing native errors."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class SetRepl(unittest.TestCase):
    def run_repl(self, source):
        with tempfile.TemporaryDirectory() as directory:
            result=subprocess.run([sys.executable,'-m','chaos','repl'],input=source+'\n.quit\n',cwd=directory,env=dict(os.environ,PYTHONPATH=str(ROOT)),text=True,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()],[json.loads(line)['error'] for line in result.stderr.splitlines()]

    def test_native_set_results_errors_env_and_continuation(self):
        output,errors=self.run_repl('let choices={20,22,20}\n.env\nthrow {42}\nthrow (20,22)\n42')
        self.assertEqual(output,[{'$set':[20,22]},{'choices':{'$set':[20,22]}},42])
        self.assertEqual([e['code'] for e in errors],['user_throw','user_throw'])
        self.assertEqual([e['value'] for e in errors],[{'$set':[42]},{'$tuple':[20,22]}])
        self.assertTrue(all(set(e)=={'code','message','value','location'} for e in errors))

    def test_oversized_thrown_value_and_env_report_projection_failure_then_continue(self):
        output,errors=self.run_repl('let row=range(10000);let payload=[row,row,row];throw payload\n.env\n42')
        self.assertEqual(output,[42]);self.assertEqual(len(errors),2)
        self.assertEqual([e['code'] for e in errors],['resource_limit','resource_limit'])
        self.assertTrue(all(e['value'] is None for e in errors))

if __name__=='__main__':unittest.main()
