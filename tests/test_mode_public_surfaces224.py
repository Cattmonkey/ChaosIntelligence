"""Mode algorithms through source, IR, saved plans and outside public consumers."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from contextlib import nullcontext
from chaos import Framework,compile_artifact,load_artifact,parse_program,format_program
from chaos.source_format import format_source
from chaos.compiled_flow_state import FlowState
from unittest.mock import patch
from _mode_reference224 import original_modes
from _mode_fixture224 import observe_frames
import test_robot_values as robot_fixture

ROOT=Path(__file__).resolve().parents[1]
SOURCE="var calls=0;async fn raw(value){let p=plan([{id:\"later\",call:\"end\",needs:[\"seed\"]},{id:\"seed\",call:\"seed\"}]);let out=await run_plan(p,{seed:fn(c){calls+=1;c.input},end:async fn(c){calls+=1;await sleep(0);c.results.seed+2}},value);[out,calls]};let f=compile_function(raw,target=\"auto-flow\");let pending=f(40);let before=calls;let answer=await pending;[before,answer,compile_flow_info(f).resumeCalls>0]"
EXPECTED=[0,[{"seed":40,"later":42},2],True]
ISOLATED="import json,socket,sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nsys.path.insert(0,str(Path(sys.argv[1])/'tests'))\ndef denied(*args,**kwargs):raise AssertionError('Mode test has no network authority')\nsocket.create_connection=denied\nsocket.socket.connect=denied\nsocket.socket.connect_ex=denied\nfrom chaos import Framework,load_artifact\nfrom chaos.cli import main\nfrom unittest.mock import patch\nfrom contextlib import ExitStack\nfrom chaos.execution_modes import MODE_FRAMES\nfrom chaos.compiled_flow_state import FlowState\nseen=set();states=[]\noriginal_init=FlowState.__init__\ndef entered(state,*args,**kwargs):original_init(state,*args,**kwargs);states.append(state)\nwith ExitStack()as stack:\n stack.enter_context(patch.object(FlowState,'__init__',entered))\n for cls in MODE_FRAMES:\n  original=cls.step\n  def step(frame,cursor,value,error,_original=original):\n   assert frame.owner is cursor.owner and frame in cursor.frames\n   seen.add(type(frame).__name__)\n   return _original(frame,cursor,value,error)\n  stack.enter_context(patch.object(cls,'step',step))\n if sys.argv[2]=='sdk':\n  result=Framework.evaluate(load_artifact(json.loads(Path(sys.argv[3]).read_text())))\n  assert states and all(s.closed and not s.frames and not s._owned_activations and not s._transfers and s.pending_request is None and s.pending_cursor is None for s in states)\n  assert any(s.program._artifact_decoded and s.program.resume_calls>0 for s in states)\n  assert {'ModeRunPlanFrame','ModeSerialPlanFrame','ModePlanStepFrame','ModeInvokeFrame'}<=seen\n  print(json.dumps({'result':result,'actualFrames':sorted(seen),'savedPlanDecoded':True,'closed':True}))\n else:\n  sys.argv=['chaos']+sys.argv[3:]\n  status=main()\n  if status==0 and states:\n   assert all(s.closed and not s.frames and not s._owned_activations and not s._transfers for s in states)\n   assert {'ModeRunPlanFrame','ModeSerialPlanFrame','ModeInvokeFrame'}<=seen\n  raise SystemExit(status)\n"

class ModePublicSurfaces224(unittest.TestCase):
    def test_source_IR_Code_and_saved_artifact_use_actual_Mode_frames(self):
        ir=parse_program(SOURCE);formatted=format_program(ir)
        self.assertEqual(parse_program(formatted)['semanticHash'],ir['semanticHash'])
        artifact=compile_artifact(SOURCE);encoded=json.dumps(artifact,sort_keys=True)
        for legacy in (True,False):
            for mode,text in (('source',SOURCE),('ir',formatted),('format',format_source(SOURCE)),('artifact',load_artifact(json.loads(encoded)))):
                with self.subTest(legacy=legacy,mode=mode),original_modes() if legacy else nullcontext(),observe_frames(self)as frames:
                    result=Framework.evaluate(text)
                self.assertEqual(result,EXPECTED)
                if legacy:self.assertEqual(frames,([],[]))
                else:self.assertTrue({'ModeRunPlanFrame','ModeSerialPlanFrame','ModePlanStepFrame','ModeInvokeFrame'}<={type(f).__name__ for f,c in frames[0]})
        code='let callback=function_from(code_from("fn(c){c.input+2}"));await run_plan(plan([{id:"answer",call:"f"}]),{f:callback},40)'
        for legacy in (True,False):
            with original_modes() if legacy else nullcontext(),observe_frames(self)as observed:self.assertEqual(Framework.evaluate(code),{'answer':42})
            if not legacy:self.assertTrue(any(type(f).__name__=='ModeInvokeFrame' for f,c in observed[0]))

    def test_fresh_outside_SDK_CLI_build_validate_run_saved_Mode_program(self):
        with tempfile.TemporaryDirectory(prefix='mode224-outside-')as directory:
            work=Path(directory);source=work/'main.chaos';source.write_text(SOURCE,encoding='utf-8');artifact=work/'saved.json'
            env={k:v for k,v in os.environ.items()if k in ('PATH','SYSTEMROOT','WINDIR','TMPDIR','TMP','TEMP','LANG','LC_ALL','HOME','USERPROFILE')}
            env.update(PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',CHAOS_CONFIG=str(work/'settings.json'))
            def run(mode,*args):
                child=subprocess.run([sys.executable,'-I','-S','-B','-c',ISOLATED,str(ROOT),mode,*map(str,args)],cwd=work,env=env,capture_output=True,text=True,timeout=30)
                self.assertEqual(child.returncode,0,(child.stdout,child.stderr));return json.loads(child.stdout)
            built=run('cli','artifact','build',source,'--root',work);artifact.write_text(json.dumps(built),encoding='utf-8')
            self.assertEqual(built,compile_artifact(SOURCE));self.assertTrue(run('cli','artifact','validate',artifact)['valid'])
            source.unlink();self.assertEqual(run('cli','artifact','run',artifact),EXPECTED)
            sdk=run('sdk',artifact);self.assertEqual(sdk['result'],EXPECTED);self.assertTrue(sdk['savedPlanDecoded']and sdk['closed']);self.assertIn('ModePlanStepFrame',sdk['actualFrames'])

    def test_software_Robot_action_in_Mode_is_once_and_keeps_original_authority(self):
        source='''async with await robot_open("rig") as r{let observation=await robot_observe(r);let command=robot_command(observation,{pan:0,tilt:1});let p=plan([{id:"act",call:"act"}]);let result=await run_plan(p,{act:async fn(c){let receipt=await r(command);receipt.status}},null);[result,robot_command_info(command).used]}'''
        for legacy in (True,False):
            log=[]
            with self.subTest(legacy=legacy),original_modes() if legacy else nullcontext(),observe_frames(self)as observed,robot_fixture.host(log)as app:
                result=robot_fixture.run(app,source)
            self.assertEqual(result,[{'act':'sent'},True]);self.assertEqual([row[0]for row in log],['open','observe','act','close'])
            if legacy:self.assertEqual(observed,([],[]))
            else:self.assertTrue(any(type(f).__name__=='ModeInvokeFrame' for f,c in observed[0]))
