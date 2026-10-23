"""Actual ordinary CLI runs over pipes and real PTYs; no replacement driver."""
import json,os,re,select,signal,sqlite3,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from test_process_terminal import PtySession

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/_run_terminal_fixture230.py'
HELD='await progress_emit("progress",{summary:"held\\u001b[31m\\rpayload"});tool("fixture.block",null);42'
BOT='''agent gate{provider:"fixture.planner",tools:[]};var plans=0;
fn plan(s){plans+=1;if length(s.guidance)>0{s.guidance[-1].input}else{gate("held")}};
fn execute(p,s){tool("fixture.effect",p)};
fn verify(s){{done:true,context:null,output:s.evidence[-1]}};
let actual=await bot("guided",plan,execute,verify)();[actual.output,plans]'''

def environment(work):
    env={name:os.environ[name]for name in('PATH','HOME','LANG','LC_ALL','SYSTEMROOT','WINDIR','TMPDIR')if name in os.environ}
    env.update(PYTHONPATH=str(ROOT),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',
               CHAOS_CONFIG=str(work/'settings.json'),XDG_CONFIG_HOME=str(work/'config'),TERM='xterm-256color')
    return env

def command(work,source,flags=(),filename=None):
    file=work/'main.chaos';file.write_text(source)
    return [sys.executable,'-B',str(FIXTURE),str(work),'run',str(file)if filename is None else filename,
            '--db',str(work/'runtime.sqlite'),'--capability','fixture.block','--capability','fixture.effect',*flags]

def wait_file(file,timeout=6):
    deadline=time.monotonic()+timeout
    while not file.exists():
        if time.monotonic()>=deadline:raise AssertionError('Missing actual fixture frontier: '+str(file))
        time.sleep(.01)

def rows(data):return [json.loads(line)for line in data.decode().splitlines()if line.strip()]

def result_envelopes(data,kind='run.result'):
    text=data.decode('utf-8');decoder=json.JSONDecoder();result=[]
    for match in re.finditer(r'\{',text):
        try:value,_=decoder.raw_decode(text[match.start():])
        except ValueError:continue
        if type(value)is dict and value.get('type')==kind:result.append(value)
    return result

def queued(work):
    db=sqlite3.connect((work/'runtime.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    try:return db.execute("SELECT count(*) FROM events WHERE type='guidance.queued'").fetchone()[0]
    finally:db.close()

class RunPty(PtySession):
    def __init__(self,work,source,filename=None):
        import pty,termios,fcntl,struct
        self.master,self.slave=pty.openpty();fcntl.ioctl(self.slave,termios.TIOCSWINSZ,struct.pack('HHHH',24,100,0,0))
        self.before=termios.tcgetattr(self.slave);self.data=bytearray();self.observations=[];self.work=work
        self.process=subprocess.Popen(command(work,source,['--interactive'],filename),stdin=self.slave,
                                      stdout=self.slave,stderr=self.slave,cwd=work,env=environment(work),start_new_session=True)
    def finished(self,timeout=7):
        import termios
        deadline=time.monotonic()+timeout
        while self.process.poll()is None and time.monotonic()<deadline:self.pump(.03)
        if self.process.poll()is None:raise AssertionError('Owned CLI did not exit/drain: '+repr(bytes(self.data[-4000:])))
        code=self.process.wait(timeout=1)
        while select.select([self.master],[],[],0)[0]:
            before=len(self.data);self.pump(0)
            if len(self.data)==before:break
        if termios.tcgetattr(self.slave)!=self.before:raise AssertionError('Original terminal attributes were not restored')
        return code

class RunTerminal230(unittest.TestCase):
    def audit(self,work,*,interactive=False,status='completed'):
        audit=json.loads((work/'audit.json').read_text())
        self.assertTrue(audit['appsClosed'],audit);self.assertEqual(audit['activeRuns'],0,audit);self.assertEqual(audit['activeOperations'],0,audit)
        self.assertEqual([r['status']for r in audit['runs']],[status],audit)
        if interactive:
            self.assertEqual(len(audit['consoleWorkers']),2,audit)
            self.assertTrue(all(row['joined']and not row['alive']for row in audit['consoleWorkers']),audit)
        return audit
    def child(self,work,source,flags=()):
        process=subprocess.Popen(command(work,source,flags),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=work,env=environment(work),start_new_session=True)
        self.addCleanup(self.stop_child,process);return process
    @staticmethod
    def stop_child(process):
        if process.poll()is None:
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=3)
    def test_default_result_json_is_unchanged_in_real_cli_subprocess(self):
        with tempfile.TemporaryDirectory()as tmp:
            work=Path(tmp);process=self.child(work,'40+2');out,err=process.communicate(timeout=15)
            self.assertEqual(process.returncode,0,err);self.assertEqual(out,b'42\n');self.assertEqual(err,b'');self.audit(work)
    def test_stream_emits_persisted_events_before_held_runtime_and_same_run_result(self):
        with tempfile.TemporaryDirectory()as tmp:
            work=Path(tmp);process=self.child(work,HELD,['--stream']);wait_file(work/'entered.json');prefix=bytearray();deadline=time.monotonic()+5
            while b'bot.progress'not in prefix and time.monotonic()<deadline:
                if select.select([process.stdout],[],[],.1)[0]:prefix.extend(os.read(process.stdout.fileno(),65536))
            self.assertIn(b'bot.progress',prefix);self.assertIsNone(process.poll());self.assertFalse((work/'release').exists())
            (work/'release').touch();tail,err=process.communicate(timeout=10);out=bytes(prefix)+tail
            self.assertEqual(process.returncode,0,err);actual=rows(out);audit=self.audit(work)
            self.assertEqual(actual[:-1],audit['events']);self.assertEqual(actual[-1],{'type':'run.result','runId':audit['runs'][0]['id'],'output':42})
            self.assertTrue(all(row['run_id']==actual[-1]['runId']for row in actual[:-1]));self.assertNotIn(b'\x1b',out);self.assertNotIn(b'\r',out)
            self.assertIn(b'\\u001b[31m',out);self.assertTrue(audit['gateExited'])
    @unittest.skipUnless(os.name=='posix','real POSIX PTY required')
    def test_interactive_utf8_guidance_changes_next_bot_decision_once_and_restores_tty(self):
        with tempfile.TemporaryDirectory()as tmp:
            work=Path(tmp);tty=RunPty(work,BOT);self.addCleanup(tty.close);tty.read_until('guide> ');tty.wait_for(lambda:(work/'entered.json').exists())
            tty.write('/detach\r');text='请核对 👋 <script>literal</script>';encoded=text.encode()
            tty.write(encoded[:2]);tty.write(encoded[2:]);tty.read_until(encoded)
            self.assertEqual(queued(work),0,'Partial draft or forbidden local detach became guidance')
            tty.write('\r');tty.read_until('"receipt"');self.assertEqual(queued(work),1);(work/'release').touch()
            self.assertEqual(tty.finished(),0);audit=self.audit(work,interactive=True)
            self.assertEqual(audit['effects'],[{'text':text}]);self.assertEqual(audit['providerCalls'],['held'])
            self.assertEqual(audit['runs'][0]['output'],[{'text':text},2])
            self.assertEqual(result_envelopes(bytes(tty.data)),[{'type':'run.result','runId':audit['runs'][0]['id'],'output':[{'text':text},2]}])
            receipts=[e for e in audit['events']if e['type']=='guidance.queued'];adopted=[e for e in audit['events']if e['type']=='bot.context.applied']
            self.assertEqual(len(receipts),1);self.assertEqual(len(adopted),1)
            self.assertEqual(receipts[0]['payload']['messageId'],adopted[0]['payload']['messageId'])
            self.assertRegex(receipts[0]['payload']['messageId'],r'^guide-[0-9a-f]{32}$')
            self.assertIn(json.dumps({'text':text},ensure_ascii=False).encode(),bytes(tty.data))
            self.assertIn(b'"run.result"',tty.data);self.assertIn(audit['runs'][0]['id'].encode(),tty.data)
    @unittest.skipUnless(os.name=='posix','real POSIX PTY required')
    def test_ctrl_c_ctrl_d_and_cancel_drain_local_run_and_join_terminal_workers(self):
        for terminator,expected in [('\x03',130),('\x04',130),('/cancel\r',130)]:
            with self.subTest(terminator=repr(terminator)),tempfile.TemporaryDirectory()as tmp:
                work=Path(tmp);tty=RunPty(work,HELD);self.addCleanup(tty.close);tty.read_until('guide> ');tty.wait_for(lambda:(work/'entered.json').exists())
                tty.write(terminator);self.assertEqual(tty.finished(),expected);audit=self.audit(work,interactive=True,status='cancelled')
                self.assertTrue(audit['gateExited']);self.assertEqual(audit['effects'],[])
                self.assertEqual(result_envelopes(bytes(tty.data),'run.exit'),[{'type':'run.exit','runId':audit['runs'][0]['id'],'status':'cancelled'}])
                self.assertEqual(result_envelopes(bytes(tty.data)),[])
                self.assertNotIn(b'\x1b[31m',tty.data);self.assertNotIn(b'\x1b',bytes(tty.data).replace(b'\x1b[2K',b''))
    def test_invalid_non_tty_interactive_rejects_before_framework_or_effect(self):
        with tempfile.TemporaryDirectory()as tmp:
            work=Path(tmp);process=self.child(work,'tool("fixture.effect",42)',['--interactive']);out,err=process.communicate(timeout=10)
            self.assertNotEqual(process.returncode,0);self.assertIn(b'terminal',err.lower());audit=json.loads((work/'audit.json').read_text())
            self.assertEqual(audit['counters'],{'constructed':0,'saved':0,'started':0});self.assertEqual(audit['effects'],[]);self.assertFalse((work/'runtime.sqlite').exists())
    @unittest.skipUnless(os.name=='posix','real POSIX PTY required')
    def test_interactive_stdin_source_is_rejected_before_read_or_effect(self):
        with tempfile.TemporaryDirectory()as tmp:
            work=Path(tmp);tty=RunPty(work,'tool("fixture.effect",42)',filename='-');self.addCleanup(tty.close)
            self.assertNotEqual(tty.finished(timeout=4),0);self.assertIn(b'stdin',bytes(tty.data).lower());audit=json.loads((work/'audit.json').read_text())
            self.assertEqual(audit['counters'],{'constructed':0,'saved':0,'started':0});self.assertEqual(audit['effects'],[])
    def test_stream_failure_drains_owned_execution_without_result_envelope(self):
        with tempfile.TemporaryDirectory()as tmp:
            work=Path(tmp);process=self.child(work,'await progress_emit("progress",{summary:"before failure"});throw "expected"',['--stream']);out,err=process.communicate(timeout=15)
            self.assertNotEqual(process.returncode,0);self.assertIn(b'"error"',err);audit=self.audit(work,status='failed')
            self.assertEqual(rows(out),audit['events']);self.assertNotIn(b'"run.result"',out)
    def test_broken_pipe_cancels_and_drains_owned_runtime(self):
        with tempfile.TemporaryDirectory()as tmp:
            work=Path(tmp);source=HELD[:-2]+'await progress_emit("progress",{summary:"after hold"});await sleep(30);42'
            process=self.child(work,source,['--stream']);wait_file(work/'entered.json');self.assertTrue(select.select([process.stdout],[],[],5)[0]);line=os.read(process.stdout.fileno(),65536);self.assertTrue(line)
            process.stdout.close();(work/'release').touch();process.wait(timeout=10);err=process.stderr.read()
            self.assertNotEqual(process.returncode,0,err);audit=self.audit(work,status='cancelled');self.assertTrue(audit['gateExited'])

if __name__=='__main__':unittest.main()
