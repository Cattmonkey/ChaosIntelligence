"""Real PTY and pipe observers against a temporary Framework HTTP server."""
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import sys
import time
import unittest

from tests.process_stream_fixture import ROOT, StreamFixture, eventually


PTY_CHILD = '''import json,sys,threading,termios
from chaos.cli import process_main
def tty_snapshot():
    attrs=termios.tcgetattr(sys.stdin.fileno())
    return {'iflag':attrs[0],'oflag':attrs[1],'cflag':attrs[2],'lflag':attrs[3],
            'ispeed':attrs[4],'ospeed':attrs[5],
            'cc':[item[0] if isinstance(item,bytes) else item for item in attrs[6]]}
entry_tty=tty_snapshot()
started=[];joined=[]
original_start=threading.Thread.start
original_join=threading.Thread.join
def start(worker,*args,**kwargs):
    result=original_start(worker,*args,**kwargs)
    started.append(worker)
    return result
def join(worker,*args,**kwargs):
    result=original_join(worker,*args,**kwargs)
    joined.append({'ident':worker.ident,'alive':worker.is_alive()})
    return result
threading.Thread.start=start
threading.Thread.join=join
try:
    code=process_main(sys.argv[1:])
finally:
    threading.Thread.start=original_start
    threading.Thread.join=original_join
    print(json.dumps({'workerAudit':{'started':[w.ident for w in started],'joined':joined},
                      'ttyAudit':{'entry':entry_tty,'exit':tty_snapshot()}}),flush=True)
raise SystemExit(code)
'''


class PtySession:
    def __init__(self, fixture, *, timeout=12):
        import pty
        import termios
        import fcntl
        import struct
        self.master, self.slave = pty.openpty()
        # openpty does not supply useful dimensions on every supported Python.
        # The long Unicode draft is deliberately visible within this real TTY.
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
        self.before = termios.tcgetattr(self.slave)
        self.data = bytearray()
        self.observations = []
        self.process = subprocess.Popen(
            [sys.executable, '-B', '-c', PTY_CHILD, 'attach', 'p', 'worker',
             '--server', fixture.origin, '--timeout', str(timeout)],
            stdin=self.slave, stdout=self.slave, stderr=self.slave,
            cwd=fixture.path, env=fixture.child_env(), start_new_session=True,
        )

    def write(self, value):
        if isinstance(value, str):
            value = value.encode('utf-8')
        os.write(self.master, value)
        self.observations.append(('write', time.monotonic(), value))

    def read_until(self, needle, timeout=6):
        if isinstance(needle, str):
            needle = needle.encode('utf-8')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.data:
                return bytes(self.data)
            self.pump(min(.05, max(0, deadline - time.monotonic())))
        raise AssertionError('PTY did not print ' + repr(needle) + ': ' + repr(bytes(self.data[-4000:])))

    def pump(self, timeout=.05):
        ready, _, _ = select.select([self.master], [], [], timeout)
        if ready:
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                return
            self.data.extend(chunk)
            self.observations.append(('read', time.monotonic(), chunk))

    def wait_for(self, predicate, *, timeout=6, message='PTY condition did not become true'):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            # A real terminal consumer must keep reading while the sender and
            # observer work. Waiting only on a host Event can block stdout.
            self.pump(min(.05, max(0, deadline - time.monotonic())))
        raise AssertionError(message + ': ' + repr(bytes(self.data[-4000:])))

    def finished(self, timeout=3):
        import termios
        deadline = time.monotonic() + timeout
        while self.process.poll() is None and time.monotonic() < deadline:
            self.pump(min(.05, max(0, deadline - time.monotonic())))
        if self.process.poll() is None:
            raise AssertionError('PTY child did not exit while output was continuously drained: '
                                 + repr(bytes(self.data[-4000:])))
        code = self.process.wait(timeout=max(.01, deadline - time.monotonic()))
        self.read_until('workerAudit')
        audit = re.search(rb'\{"workerAudit":[^\r\n]*\}', bytes(self.data))
        if not audit:
            raise AssertionError('child did not provide actual observer worker join evidence')
        report = json.loads(audit.group(0))
        payload = report['workerAudit']
        if len(payload['started']) != 2:
            raise AssertionError(payload)
        if {item['ident'] for item in payload['joined']} != set(payload['started']):
            raise AssertionError(payload)
        if any(item['alive'] for item in payload['joined']):
            raise AssertionError(payload)
        after = termios.tcgetattr(self.slave)
        if after != self.before:
            fields = ('iflag', 'oflag', 'cflag', 'lflag', 'ispeed', 'ospeed', 'cc')
            differences = {name: {'before': repr(self.before[index]), 'after': repr(after[index])}
                           for index, name in enumerate(fields) if after[index] != self.before[index]}
            raise AssertionError('attach did not restore the original PTY attributes: '
                                 + repr({'changedFields': differences, 'child': report.get('ttyAudit')}))
        return code

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        os.close(self.master)
        os.close(self.slave)


@unittest.skipUnless(os.name == 'posix', 'real POSIX PTY required')
class ProcessTerminal(unittest.TestCase):
    def test_partial_unicode_draft_progress_retry_and_detach_are_real(self):
        with StreamFixture() as fixture:
            fixture.start()
            fixture.at_gate(1, 'progress')
            tty = PtySession(fixture)
            self.addCleanup(tty.close)
            tty.read_until('guide> ')
            text = '请核对 👋 <script>保留原样</script>'
            encoded = text.encode('utf-8')
            # Split inside a UTF-8 code point: the terminal incremental decoder
            # must retain these exact bytes while real progress is arriving.
            tty.write(encoded[:2])
            tty.write(encoded[2:])
            fixture.release(1, 'progress')
            fixture.at_gate(1, 'adoption')
            transcript = tty.read_until('public progress')
            progress_time = time.monotonic()
            self.assertIn(encoded, transcript)
            self.assertEqual(fixture.posts, [], 'partial natural text was submitted before Enter')
            progress = next(e for e in fixture.events()
                            if e['type'] == 'bot.progress' and e['payload']['kind'] == 'progress')
            self.assertIn(('"id": ' + str(progress['id'])).encode(), transcript)

            fixture.drop_next_guidance = True
            tty.write('\r')
            enter_time = tty.observations[-1][1]
            self.assertLess(progress_time, enter_time)
            tty.wait_for(fixture.dropped_guidance.is_set, timeout=5,
                         message='real guidance commit did not enter receipt-loss fixture')
            tty.read_until('unconfirmed')
            first = fixture.posts[0]
            self.assertEqual(first['input'], {'text': text})
            self.assertRegex(first['messageId'], r'^guide-[0-9a-f]{32}$')
            tty.write('/retry\r')
            tty.wait_for(lambda: len(fixture.posts) == 2,
                         message='real /retry did not reach guidance endpoint')
            tty.read_until('"receipt"')
            self.assertEqual(fixture.posts[1], first, 'retry changed the immutable envelope')
            receipt = fixture.client.guide_process('p', 'worker', first['input'], message_id=first['messageId'])
            self.assertEqual(receipt['status'], 'queued')
            fixture.release(1, 'adoption')
            row = fixture.row(1)
            self.assertEqual(row['state']['context']['latest'], {'text': text})
            self.assertEqual(fixture.effects, [{'text': text}])
            tty.read_until('process.turn.completed')
            events = fixture.events()
            self.assertEqual(sum(e['type'] == 'process.guidance.queued'
                                 and e['payload']['messageId'] == first['messageId'] for e in events), 1)
            adopted = next(e for e in events if e['type'] == 'process.bot.adopt')
            outcome = next(e for e in events if e['type'] == 'bot.action.completed')
            self.assertLess(adopted['id'], outcome['id'])
            self.assertEqual(fixture.client.guide_process('p', 'worker', first['input'],
                                                         message_id=first['messageId'])['status'], 'applied')

            before = fixture.app.get_process('p', 'worker')
            tty.write('/detach\r')
            self.assertEqual(tty.finished(), 0)
            self.assertEqual(fixture.control_requests, [])
            self.assertEqual(fixture.app.get_process('p', 'worker'), before)
            # A new real turn after viewer shutdown proves that observation
            # did not stop or cancel the persistent Process.
            fixture.client.guide_process('p', 'worker', {'text': 'after detach'}, message_id='after-detach')
            fixture.release_turn(2)
            after = fixture.row(2)
            self.assertNotEqual(before['lastRunId'], after['lastRunId'])
            self.assertEqual(after['state']['context']['latest'], {'text': 'after detach'})
            self.assertEqual(fixture.control_requests, [])

    def test_ctrl_d_ctrl_c_and_observation_timeout_restore_tty_without_remote_control(self):
        for terminator, expected in [('\x04', 0), ('\x03', 130), (None, 0)]:
            with self.subTest(terminator=repr(terminator)), StreamFixture() as fixture:
                fixture.start()
                fixture.release_turn(1)
                before = fixture.row(1)
                tty = PtySession(fixture, timeout=.5 if terminator is None else 12)
                try:
                    tty.read_until('guide> ')
                    if terminator is not None:
                        tty.write(terminator)
                    self.assertEqual(tty.finished(), expected)
                    self.assertEqual(fixture.app.get_process('p', 'worker'), before)
                    self.assertEqual(fixture.control_requests, [])
                finally:
                    tty.close()


class ProcessPipeCLI(unittest.TestCase):
    def cli(self, fixture, *args, input=None):
        return subprocess.run([sys.executable, '-B', '-m', 'chaos', 'process', *args,
                               '--server', fixture.origin], cwd=fixture.path,
                              env=fixture.child_env(), input=input,
                              capture_output=True, text=True, timeout=7)

    def test_pipe_watch_flushes_before_exit_and_spans_actual_turn_run_ids(self):
        with StreamFixture() as fixture:
            fixture.start()
            fixture.at_gate(1, 'progress')
            proc = subprocess.Popen([sys.executable, '-B', '-m', 'chaos', 'process', 'watch',
                                     'p', 'worker', '--server', fixture.origin, '--timeout', '10'],
                                    cwd=fixture.path, env=fixture.child_env(), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.addCleanup(lambda: self.close_child(proc))
            buffer, rows = bytearray(), []

            def receive(predicate):
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    while b'\n' in buffer:
                        line, _, tail = buffer.partition(b'\n')
                        buffer[:] = tail
                        rows.append(json.loads(line))
                        if predicate(rows[-1]):
                            self.assertIsNone(proc.poll(), 'watch output appeared only after process exit')
                            return rows[-1]
                    if select.select([proc.stdout], [], [], .05)[0]:
                        chunk = os.read(proc.stdout.fileno(), 65536)
                        if not chunk:
                            break
                        buffer.extend(chunk)
                self.fail('piped watcher did not flush the expected actual event')

            receive(lambda e: e['type'] == 'bot.progress' and e['payload']['kind'] == 'plan')
            fixture.release_turn(1)
            first = fixture.row(1)
            receive(lambda e: e['type'] == 'run.completed' and e['run_id'] == first['lastRunId'])
            fixture.client.guide_process('p', 'worker', {'text': 'second turn'}, message_id='pipe-second')
            fixture.release_turn(2)
            second = fixture.row(2)
            receive(lambda e: e['type'] == 'run.completed' and e['run_id'] == second['lastRunId'])
            self.assertNotEqual(first['lastRunId'], second['lastRunId'])
            self.assertTrue({first['lastRunId'], second['lastRunId']} <= {e['run_id'] for e in rows})
            ids = [e['id'] for e in rows]
            self.assertEqual(ids, sorted(set(ids)))
            self.assertTrue(all(e['processOrigin'] in ('run', 'lifecycle') for e in rows))
            self.close_child(proc)
            self.assertEqual(fixture.control_requests, [])
            self.assertEqual(fixture.app.get_process('p', 'worker'), second)

    def test_text_guide_idempotent_retry_and_redirected_attach_rejection(self):
        with StreamFixture() as fixture:
            fixture.start()
            fixture.at_gate(1, 'progress')
            text = '中文 👋 <img src=x onerror=evil()> with exact spaces  '
            args = ('guide', 'p', 'worker', '--text', text, '--message-id', 'stable-cli-guide')
            fixture.drop_next_guidance = True
            lost = self.cli(fixture, *args)
            self.assertNotEqual(lost.returncode, 0)
            retry = self.cli(fixture, *args)
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertEqual(json.loads(retry.stdout)['messageId'], 'stable-cli-guide')
            self.assertEqual(fixture.posts, [{'input': {'text': text}, 'messageId': 'stable-cli-guide'}] * 2)
            fixture.release_turn(1)
            self.assertEqual(fixture.row(1)['state']['context']['latest'], {'text': text})
            again = self.cli(fixture, *args)
            self.assertEqual(json.loads(again.stdout)['status'], 'applied')
            self.assertEqual(fixture.effects, [{'text': text}])
            rejected = self.cli(fixture, 'attach', 'p', 'worker', input='never a guide\n')
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('POSIX terminal', rejected.stderr)
            self.assertIn('watch', rejected.stderr)
            self.assertEqual(len(fixture.posts), 3)
            self.assertEqual(fixture.control_requests, [])

    @staticmethod
    def close_child(proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()


if __name__ == '__main__':
    unittest.main()
