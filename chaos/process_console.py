"""Shared terminal observation; Process attaches remotely, CLI runs are locally owned."""
import codecs
import json
import os
import queue
import select
import shutil
import sys
import threading
import time
import uuid
from .framework import timeout_deadline,RunFailed
from .client import Client
from .extensions import TERMINAL_STATES


def _require_terminal(message):
    if os.name != 'posix' or not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError(message)
    import termios
    # Check the real descriptor before callers create or start an owned run.
    termios.tcgetattr(sys.stdin.fileno())


def validate_interactive(file):
    if file == '-':
        raise ValueError('--interactive requires a source file; stdin belongs to terminal guidance')
    _require_terminal('--interactive requires a POSIX stdin/stdout terminal; use --stream for redirected output')


class _ProcessAdapter:
    owned=False
    def __init__(self,client,project_id,key,target):
        self.project_id,self.key,self.target=project_id,key,target
        if target is not None:
            from .guidance_targets import validate_target
            target=validate_target(target)
            if target['kind']!='processRole':raise ValueError('Process attach needs a persistent role target')
            self.target=target
        self.client=client
        self.observer=Client(client.base_url,timeout=min(1,client.timeout))
        self.sender=Client(client.base_url,timeout=min(1,client.timeout))
    def page(self,after):return self.client.events(self.project_id,process_key=self.key,after=after,limit=1)
    def watch(self,after,stopped):return self.observer.watch_process(self.project_id,self.key,after=after,timeout=None,stop_event=stopped)
    def guide(self,envelope):
        extra={} if 'target'not in envelope else {'target':envelope['target']}
        return self.sender.guide_process(self.project_id,self.key,envelope['input'],message_id=envelope['messageId'],**extra)
    def finish(self):pass
    def banner(self):return 'Attached to '+self.project_id+'/'+self.key+'; /detach closes observation. /help shows controls.'
    completed='Process completed; durable event tail drained'
    timed_out='Observation timed out; Process was not stopped'
    receipt_wait='; awaiting durable receipt'
    help='Natural text posts durable Process guidance. /retry reuses the last unconfirmed envelope. /detach, Ctrl-D, Ctrl-C only close this connection. Use chaos process pause/resume/stop PROJECT KEY --revision REV for explicit remote controls; reconcile requires state JSON and evidence.'
    def footer(self,after):return {'detached':True,'projectId':self.project_id,'processKey':self.key,'after':after}


def _cancel_and_drain(run):
    record=run.get()
    if record['status']not in TERMINAL_STATES:
        run.cancel()
    return run.wait(timeout=None)


class _RunAdapter:
    owned=True
    target=None
    def __init__(self,run):self.run=run;self.record=None
    def page(self,after):return self.run.backend.events(self.run.project_id,run_id=self.run.id,after=after,limit=1)
    def watch(self,after,stopped):return self.run.backend.watch(self.run.project_id,run_id=self.run.id,after=after,timeout=None,stop_event=stopped)
    def guide(self,envelope):
        try:return self.run.guide(envelope['input'],message_id=envelope['messageId'])
        except (ValueError,KeyError)as error:raise RuntimeError(str(error))from error
    def finish(self):self.record=_cancel_and_drain(self.run)
    def banner(self):return 'Attached to local run '+self.run.id+'; /cancel, Ctrl-D or Ctrl-C cancels and drains this owned run. /detach is unavailable.'
    completed='Run terminal; persisted event tail drained'
    timed_out='Observation timed out; local run will be cancelled and drained'
    receipt_wait='; awaiting run receipt'
    help='Natural text posts guidance to this same run. /retry reuses the last unconfirmed envelope. /cancel, Ctrl-D and Ctrl-C cancel and drain the locally owned run. /detach is unavailable because this command owns its runtime.'
    def footer(self,after):return None


def stream_run(run):
    """Print actual persisted rows, then the successful same-run result envelope."""
    try:
        for event in run.backend.watch(run.project_id,run_id=run.id,timeout=None):
            print(json.dumps(event,ensure_ascii=False,allow_nan=False),flush=True)
        output=run.result(timeout=None)
        print(json.dumps({'type':'run.result','runId':run.id,'output':output},ensure_ascii=False,allow_nan=False),flush=True)
        return 0
    except BaseException:
        _cancel_and_drain(run)
        raise


def attach_run(run,*,after=0,timeout=None):
    adapter=_RunAdapter(run)
    try:
        _require_terminal('--interactive requires a POSIX stdin/stdout terminal; use --stream for redirected output')
        code=_terminal(adapter,after=after,timeout=timeout)
    except BaseException:
        if adapter.record is None:adapter.finish()
        raise
    record=adapter.record
    if record['status']=='completed':
        print(json.dumps({'type':'run.result','runId':run.id,'output':record['output']},ensure_ascii=False,allow_nan=False),flush=True)
    elif record['status']in ('cancelled','interrupted'):
        print(json.dumps({'type':'run.exit','runId':run.id,'status':record['status']},ensure_ascii=False,allow_nan=False),flush=True)
        return 130
    else:
        raise RunFailed(record)
    return code


def attach(client,project_id,key,*,after=0,timeout=None,target=None):
    _require_terminal('attach requires a POSIX terminal; use process watch for redirected output')
    return _terminal(_ProcessAdapter(client,project_id,key,target),after=after,timeout=timeout)


def _terminal(adapter,*,after=0,timeout=None):
    import termios
    import tty
    first_page=adapter.page(after)
    deadline = timeout_deadline(timeout)
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    stopped = threading.Event()
    notifications = queue.Queue(maxsize=64)
    requests = queue.Queue(maxsize=8)
    draft = ''
    displayed_cursor = after
    pending = None
    retry = None
    completed = False
    decoder = codecs.getincrementaldecoder('utf-8')()
    prompt = 'guide> '

    def put(item):
        while not stopped.is_set():
            try:
                notifications.put(item, timeout=.1)
                return
            except queue.Full:
                pass

    def poll():
        cursor = after
        disconnected = False
        while not stopped.is_set():
            try:
                for event in adapter.watch(cursor,stopped):
                    if stopped.is_set():
                        return
                    if disconnected:
                        put(('status', 'Reconnected after #' + str(cursor)))
                        disconnected = False
                    put(('event', event))
                    cursor = event['id']
                if not stopped.is_set():
                    put(('completed',))
                return
            except (OSError, RuntimeError) as error:
                if not disconnected:
                    put(('status', 'Observation disconnected; reconnecting after #' + str(cursor)))
                    disconnected = True
                stopped.wait(.3)
            except BaseException as error:
                put(('fatal',error))
                return

    def send():
        while not stopped.is_set():
            try:
                envelope = requests.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                receipt = adapter.guide(envelope)
                put(('receipt', envelope, receipt))
            except (OSError, RuntimeError) as error:
                # Public SDK error message only. Never log unrestricted request arguments.
                put(('failed', envelope, str(error)))
            except BaseException as error:
                put(('fatal',error))
                return

    def redraw():
        columns = max(20, shutil.get_terminal_size((80, 24)).columns)
        visible = draft[-max(1, columns - len(prompt) - 2):]
        sys.stdout.write('\r\x1b[2K' + prompt + visible)
        sys.stdout.flush()

    def show(text):
        # JSON encoding ensures host progress cannot inject terminal control bytes.
        sys.stdout.write('\r\x1b[2K' + text + '\n')
        redraw()

    def submit(envelope):
        nonlocal pending
        if pending is not None:
            show('Guidance acknowledgement pending; draft retained')
            return False
        pending = envelope
        requests.put_nowait(envelope)
        show('Queued local request ' + envelope['messageId'] + adapter.receipt_wait)
        return True

    workers = [threading.Thread(target=poll), threading.Thread(target=send)]
    exit_code = 0
    try:
        tty.setraw(fd)
        for worker in workers:
            worker.start()
        show(adapter.banner())
        if first_page.get('legacyHistoryIncluded') is False:
            show('Trusted Process history starts at #' + str(first_page.get('historyStartEventId')) + '; older events are not attributed')
        while not stopped.is_set():
            while True:
                try:
                    item = notifications.get_nowait()
                except queue.Empty:
                    break
                if item[0] == 'event':
                    event = item[1]
                    if event['id'] > displayed_cursor:
                        show(json.dumps(event, ensure_ascii=False, allow_nan=False))
                        displayed_cursor = event['id']
                elif item[0] == 'receipt':
                    envelope, receipt = item[1:]
                    show(json.dumps({'receipt': receipt}, ensure_ascii=False, allow_nan=False))
                    if pending is envelope:
                        pending = None
                    if retry is envelope:
                        retry = None
                elif item[0] == 'failed':
                    envelope, message = item[1:]
                    retry = envelope
                    if pending is envelope:
                        pending = None
                    show(json.dumps({'messageId': envelope['messageId'], 'delivery': 'unconfirmed', 'error': message, 'retry': '/retry'}, ensure_ascii=False))
                elif item[0] == 'completed':
                    completed = True
                elif item[0] == 'fatal':
                    raise RuntimeError('Terminal worker failed') from item[1]
                else:
                    show(item[1])
            if completed and pending is None:
                show(adapter.completed)
                stopped.set()
            if stopped.is_set():
                break
            if deadline is not None and time.monotonic() >= deadline:
                show(adapter.timed_out)
                break
            readable, _, _ = select.select([fd], [], [], .05)
            if not readable:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            for ch in decoder.decode(chunk):
                if ch == '\x03':
                    exit_code = 130
                    stopped.set()
                    break
                if ch == '\x04':
                    stopped.set()
                    break
                if ch in '\x7f\b':
                    draft = draft[:-1]
                elif ch in '\r\n':
                    text = draft
                    if text == '/detach':
                        if adapter.owned:
                            show('Cannot detach a locally owned run; use /cancel to cancel and drain it')
                            draft = ''
                            continue
                        stopped.set()
                        break
                    if text == '/cancel' and adapter.owned:
                        stopped.set()
                        break
                    if text == '/help':
                        show(adapter.help)
                        draft = ''
                    elif text == '/retry':
                        if retry is None:
                            show('No unconfirmed request retained')
                        elif submit(retry):
                            draft = ''
                    elif text.strip():
                        if len(text) > 20000:
                            show('Guidance exceeds 20000 characters; draft retained')
                        else:
                            # Copy values exactly once. The sender only receives this envelope.
                            envelope = {'messageId': 'guide-' + uuid.uuid4().hex, 'input': {'text': text}}
                            if adapter.target is not None: envelope['target'] = dict(adapter.target)
                            if submit(envelope):
                                draft = ''
                elif ch.isprintable():
                    # Keep a little space for edits after the budget is reached, but bound RAM.
                    if len(draft) < 20001:
                        draft += ch
                redraw()
    except KeyboardInterrupt:
        exit_code = 130
    finally:
        stopped.set()
        try:
            try:
                for worker in workers:
                    if worker.ident is not None:
                        worker.join()
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, original)
                pendin = getattr(termios, 'PENDIN', 0)
                if sys.platform == 'darwin' and pendin and not (original[3] & pendin):
                    if termios.tcgetattr(fd)[3] & pendin:
                        # Preserve the original Darwin raw-to-canonical bookkeeping.
                        import array
                        import fcntl
                        fcntl.ioctl(fd, termios.FIONREAD, array.array('i', [0]), True)
            footer=adapter.footer(displayed_cursor)
            sys.stdout.write('\r\x1b[2K'+(json.dumps(footer,ensure_ascii=False)+'\n' if footer is not None else ''))
            sys.stdout.flush()
        finally:
            # Local ownership is resolved only after observer/sender stop and TTY restore.
            # Process.finish is inert: remote detach never cancels or controls its owner.
            adapter.finish()
    return exit_code
