"""Explicitly trusted host execution and isolated, optional headless browser tools.

The working directory is a project boundary, NOT a security sandbox. Executed
Python/shell code has the server account's OS permissions. Browser sessions use
an independent headless context; they never move the system mouse.
"""
import base64
import json
import os
import copy
import queue
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from .tool_schemas import browser_schema


class ExecutionError(RuntimeError):
    pass


class ExecutionTimeout(ExecutionError):
    pass


def _stop_process(proc):
    try:
        if os.name == 'posix':
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run_process(argv, cwd, *, timeout=30, max_output=65536, checkpoint=lambda: None,
                env=None, stdin=None, on_line=None):
    """Drain both pipes, bound retained bytes, and kill the process group on exit.

    on_line receives at most max_output bytes in total. A detached child cannot
    keep pipes open forever: its group is terminated after the main child exits.
    """
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
        raise ValueError('Process timeout must be in (0, 3600] seconds')
    if type(max_output) is not int or not 1024 <= max_output <= 4_000_000:
        raise ValueError('Invalid output budget')
    checkpoint()
    started = time.monotonic()
    kwargs = {'start_new_session': True} if os.name == 'posix' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    truncated = [False]
    callbacks = []
    lock = threading.Lock()

    def drain(name, pipe):
        pending = b''
        try:
            while True:
                data = os.read(pipe.fileno(), 4096)
                if not data:
                    break
                with lock:
                    remaining = max_output - sum(map(len, output.values()))
                    kept = data[:max(0, remaining)]
                    output[name].extend(kept)
                    if len(kept) < len(data):
                        truncated[0] = True
                    if on_line is not None and name == 'stdout':
                        pending += kept
                        lines = pending.split(b'\n')
                        pending = lines.pop()
                        callbacks.extend(line.decode('utf-8', 'replace') for line in lines)
            if on_line is not None and pending:
                with lock:
                    callbacks.append(pending.decode('utf-8', 'replace'))
        finally:
            pipe.close()

    threads = [threading.Thread(target=drain, args=(name, getattr(proc, name)), daemon=True)
               for name in ('stdout', 'stderr')]
    for thread in threads:
        thread.start()
    # Input can exceed a pipe buffer. Feed it independently so cancellation and
    # deadlines remain active even if the child never reads stdin.
    def feed():
        try:
            proc.stdin.write(stdin.encode('utf-8') if isinstance(stdin, str) else stdin)
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            proc.stdin.close()
    if stdin is not None:
        threading.Thread(target=feed, daemon=True).start()
    try:
        while proc.poll() is None:
            checkpoint()
            if time.monotonic() - started >= timeout:
                raise ExecutionTimeout('Process exceeded %.2f seconds' % timeout)
            if on_line:
                with lock:
                    batch, callbacks[:] = callbacks[:], []
                for line in batch:
                    on_line(line)
            time.sleep(.025)
        checkpoint()
    finally:
        # Always clean up descendants, including after normal parent exit.
        _stop_process(proc)
        for thread in threads:
            thread.join(timeout=1)
    if on_line:
        with lock:
            batch = callbacks[:]
        for line in batch:
            on_line(line)
    return {'exitCode': proc.returncode,
            'stdout': bytes(output['stdout']).decode('utf-8', 'replace'),
            'stderr': bytes(output['stderr']).decode('utf-8', 'replace'),
            'truncated': truncated[0], 'duration': round(time.monotonic() - started, 3)}


class CodeExecutor:
    def __init__(self, workspace, timeout=30, max_output=65536):
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        if not self.workspace.is_dir():
            raise ValueError('Execution workspace must be a directory')
        self.timeout, self.max_output = timeout, max_output

    def run(self, value, checkpoint=lambda: None):
        if not isinstance(value, dict) or set(value) - {'language', 'code', 'timeout'}:
            raise ValueError('Code input requires language, code and optional timeout')
        language, code = value.get('language', 'python'), value.get('code')
        if language not in ('python', 'shell') or not isinstance(code, str) or len(code) > 250000:
            raise ValueError('Expected python/shell code of at most 250000 characters')
        timeout = value.get('timeout', self.timeout)
        if type(timeout) not in (int, float) or not 0 < timeout <= self.timeout:
            raise ValueError('Tool timeout must not exceed the configured timeout')
        suffix = '.py' if language == 'python' else '.sh'
        with tempfile.TemporaryDirectory(prefix='.chaos-exec-', dir=str(self.workspace)) as temporary:
            script = Path(temporary) / ('script' + suffix)
            script.write_text(code, encoding='utf-8')
            executable = sys.executable if language == 'python' else shutil.which('sh')
            if not executable:
                raise ExecutionError('A POSIX shell is unavailable on this host')
            argv = [executable, '-u', str(script)] if language == 'python' else [executable, str(script)]
            environment = None
            if language == 'python':
                from .resources import import_root
                environment = os.environ.copy()
                paths = [str(import_root())]
                if environment.get('PYTHONPATH'):
                    paths.append(environment['PYTHONPATH'])
                environment['PYTHONPATH'] = os.pathsep.join(paths)
            result = run_process(argv, self.workspace, timeout=timeout, max_output=self.max_output, checkpoint=checkpoint, env=environment)
        return {**result, 'workspace': str(self.workspace), 'isolation': 'trusted-host'}



def validate_browser_input(value, *, internal=False):
    if type(value)is not dict or type(value.get('action'))is not str:raise ValueError('Browser input requires an action object')
    action=value['action'];private={'_observation','_capture','image'}if internal else set()
    targets={'selector','locator','sessionId','controlEpoch','snapshotId','ref'}
    fields={'navigate':{'url'},'snapshot':set(),'screenshot':set(),'observe':{'image'},'click':targets,'type':targets|{'text'},'press':targets|{'key'},'scroll':{'deltaX','deltaY'}}
    if action not in fields or set(value)-({'action'}|fields[action]|private):raise ValueError('Invalid browser action fields')
    if action=='navigate':
        from .computer_schema import validate_url
        from .language import LanguageError
        try:validate_url(value.get('url'))
        except LanguageError as error:raise ValueError(str(error))from None
    if action in ('click','type','press'):
        refs={'sessionId','controlEpoch','snapshotId','ref'};present=refs.intersection(value)
        if present:
            if present!=refs or 'selector'in value or 'locator'in value:raise ValueError('Reference actions require all identity fields only')
            if any(type(value[k])is not str or not 1<=len(value[k])<=128 for k in ('sessionId','ref')) or any(type(value[k])is not int or value[k]<1 for k in ('controlEpoch','snapshotId')):raise ExecutionError('Invalid browser reference identity')
        elif ('selector'in value)==('locator'in value):raise ValueError('Browser action requires exactly one target')
        if 'selector'in value and (type(value['selector'])is not str or not 1<=len(value['selector'])<=1000):raise ValueError('Invalid browser selector')
        if 'locator'in value:
            spec=value['locator']
            if type(spec)is not dict:raise ValueError('Invalid stable locator')
            if spec.get('kind')=='role':
                if set(spec)!={'kind','role','name'} or any(type(spec[k])is not str or len(spec[k])>1000 for k in ('role','name')):raise ValueError('Invalid role locator')
            elif spec.get('kind')not in ('testId','label','css') or set(spec)!={'kind','value'} or type(spec.get('value'))is not str or not 1<=len(spec['value'])<=1000:raise ValueError('Invalid stable locator')
            if spec.get('kind')=='css'and 'data-chaos-target'in spec['value']:raise ValueError('Temporary snapshot selectors cannot be replay locators')
    if action=='type'and(type(value.get('text'))is not str or len(value['text'])>100000):raise ValueError('Type requires bounded text')
    if action=='press'and(type(value.get('key'))is not str or not 1<=len(value['key'])<=128):raise ValueError('Press requires bounded key')
    if action=='scroll'and any(type(value.get(k))is not int or abs(value[k])>10000 for k in ('deltaX','deltaY')):raise ValueError('Scroll requires bounded Int deltas')
    if 'image'in value and type(value['image'])is not bool:raise ValueError('Image flag must be Bool')
    return value


def browser_storage(value):
    """Private bounded Playwright storage, never a public tool result."""
    if value is None:
        return {'cookies': [], 'origins': []}
    if type(value) is not dict or set(value) != {'cookies', 'origins'} or any(type(value[k]) is not list for k in ('cookies', 'origins')):
        raise ExecutionError('Invalid browser storage checkpoint')
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    if len(encoded.encode('utf-8')) > 1048576:
        raise ExecutionError('Browser storage checkpoint exceeds 1 MiB')
    return json.loads(encoded)


class BrowserSession:
    """Thread-affine headless context; references are scoped to one snapshot."""
    def __init__(self, workspace, timeout=15, *, storage_state=None):
        self._storage_state = browser_storage(storage_state)
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.timeout = min(60, max(.1, float(timeout)))
        self._playwright = self._browser = self._context = self._page = None
        self._owner = threading.get_ident()
        self.cursor = {'x': 0, 'y': 0}
        self.session_id, self.control_epoch, self.snapshot_id = uuid.uuid4().hex, 1, 0
        self._refs = {}
        self._navigation = self._snapshot_navigation = 0

    def _open(self):
        if threading.get_ident() != self._owner:
            raise ExecutionError('BrowserSession must be used by its owning worker thread')
        if self._page is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise ExecutionError('Install the browser extra and run: playwright install chromium') from exc
            self._playwright = sync_playwright().start()
            try:
                self._browser = self._playwright.chromium.launch(headless=True)
                self._context = self._browser.new_context(viewport={'width': 1280, 'height': 800}, accept_downloads=False, storage_state=self._storage_state)
                self._page = self._context.new_page()
                self._page.set_default_timeout(self.timeout * 1000)
                self._page.on('framenavigated', self._navigated)
            except BaseException:
                self.close()
                raise
        return self._page

    def _navigated(self, frame):
        if self._page is not None and frame == self._page.main_frame:
            self._navigation += 1

    def _invalidate(self):
        self.snapshot_id += 1
        refs, self._refs = self._refs, {}
        for _, handle in refs.values():
            try:
                handle.dispose()
            except Exception:
                pass

    def _locator(self, page, spec):
        if not isinstance(spec, dict):
            raise ValueError('A stable locator object is required')
        kind = spec.get('kind')
        if kind == 'role':
            if set(spec) != {'kind', 'role', 'name'} or not all(isinstance(spec[k], str) and len(spec[k]) <= 1000 for k in ('role', 'name')):
                raise ValueError('Role locator requires bounded role and name')
            return page.get_by_role(spec['role'], name=spec['name'], exact=True)
        if kind not in ('testId', 'label', 'css') or set(spec) != {'kind', 'value'} or not isinstance(spec.get('value'), str) or not 1 <= len(spec['value']) <= 1000:
            raise ValueError('Unsupported stable locator')
        value = spec['value']
        if kind == 'testId':
            return page.get_by_test_id(value)
        if kind == 'label':
            return page.get_by_label(value, exact=True)
        if 'data-chaos-target' in value:
            raise ValueError('Temporary snapshot selectors cannot be replay locators')
        return page.locator('css=' + value)

    def _target(self, page, value):
        fields = {'sessionId', 'controlEpoch', 'snapshotId', 'ref'}
        supplied = fields.intersection(value)
        if supplied:
            if supplied != fields or 'selector' in value or 'locator' in value:
                raise ValueError('Reference actions require sessionId, controlEpoch, snapshotId and ref only')
            if (value['sessionId'] != self.session_id or type(value['controlEpoch']) is not int or value['controlEpoch'] != self.control_epoch or
                    type(value['snapshotId']) is not int or value['snapshotId'] != self.snapshot_id or self._snapshot_navigation != self._navigation):
                raise ExecutionError('Stale browser reference; take a new snapshot')
            ref = value['ref']
            if not isinstance(ref, str) or ref not in self._refs:
                raise ExecutionError('Unknown browser reference')
            spec, handle = self._refs[ref]
            target = self._locator(page, spec)
            if target.count() != 1 or not target.evaluate('(el, original) => el === original && el.isConnected', handle):
                raise ExecutionError('Browser target changed; take a new snapshot')
            # Keep identity through dispatch: a Locator could resolve a replacement
            # node after the check above. A detached ElementHandle must fail.
            return handle, spec, 'snapshot-ref'
        if 'locator' in value:
            if 'selector' in value:
                raise ValueError('Use one browser targeting mechanism')
            target = self._locator(page, value['locator'])
            if target.count() != 1:
                raise ExecutionError('Stable browser locator must resolve exactly one element')
            return target, copy.deepcopy(value['locator']), 'stable-locator'
        selector = value.get('selector')
        if not isinstance(selector, str) or not 1 <= len(selector) <= 1000:
            raise ValueError('A snapshot reference, stable locator, or legacy selector is required')
        target = page.locator(selector)
        if target.count() != 1:
            raise ExecutionError('Browser selector must resolve exactly one element')
        spec = None
        for known, handle in self._refs.values():
            if target.evaluate('(el, original) => el === original && el.isConnected', handle):
                spec = known
                break
        return target, spec, 'legacy-selector'

    def _snapshot(self, page, checkpoint, limit=200):
        self._invalidate()
        self._snapshot_navigation = self._navigation
        result = {'snapshot': page.locator('body').aria_snapshot(timeout=self.timeout * 1000)[:40000]}
        rows = page.locator('a, button, input, textarea, select, [role=button], [contenteditable=true]').evaluate_all("""elements => elements.slice(0, 200).map((el, i) => {
            el.setAttribute('data-chaos-target', String(i));
            const text=(el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') || '').slice(0,250), candidates=[];
            const testId=el.getAttribute('data-testid'); if(testId)candidates.push({kind:'testId',value:testId});
            const label=el.labels && el.labels[0] && el.labels[0].innerText.trim(); if(label)candidates.push({kind:'label',value:label});
            const role=el.getAttribute('role') || ({BUTTON:'button',A:'link',TEXTAREA:'textbox',SELECT:'combobox'}[el.tagName]);
            if(role && text)candidates.push({kind:'role',role,name:text});
            if(el.id)candidates.push({kind:'css',value:'#'+CSS.escape(el.id)});
            const parts=[]; let node=el;
            while(node && node.nodeType===1 && parts.length<16){
                const tag=node.tagName.toLowerCase(); let index=1,sib=node.previousElementSibling;
                while(sib){if(sib.tagName===node.tagName)index++;sib=sib.previousElementSibling;}
                parts.unshift(tag+':nth-of-type('+index+')');node=node.parentElement;
            }
            candidates.push({kind:'css',value:parts.join(' > ')});
            return {selector:'[data-chaos-target="'+i+'"]',tag:el.tagName.toLowerCase(),text,candidates};
        })""")
        targets = []
        for row in rows:
            checkpoint()
            original = page.locator(row['selector']).element_handle(timeout=self.timeout * 1000)
            if original is None:
                continue
            box=original.bounding_box()
            if limit==40 and (box is None or box['width']<=0 or box['height']<=0 or box['x']+box['width']<=0 or box['y']+box['height']<=0 or box['x']>=page.viewport_size['width'] or box['y']>=page.viewport_size['height']):
                original.dispose();continue
            for spec in row.pop('candidates'):
                try:
                    target = self._locator(page, spec)
                    if target.count() != 1 or not target.evaluate('(el, original) => el === original && el.isConnected', original):
                        continue
                except Exception:
                    continue
                ref = 'e' + str(len(targets) + 1)
                self._refs[ref] = (spec, original)
                targets.append({**row, 'ref': ref, 'locator': spec, 'bbox': original.bounding_box()})
                break
            else:
                original.dispose()
            if len(targets)>=limit:break
        result['targets'] = targets
        result['truncated'] = {'text':len(result['snapshot'])>=40000,'targets':len(rows)>limit}
        return result

    def _observe(self, page, image, checkpoint):
        navigation = self._navigation
        result = self._snapshot(page, checkpoint, limit=40)
        result['truncated']['text'] = len(result['snapshot']) > 12000
        result['snapshot'] = result['snapshot'][:12000]
        result['viewport'] = dict(page.viewport_size)
        # Page pixels and semantic targets are captured in the same serialized job.
        # Dynamic pages can still change; navigation during capture is rejected.
        result['_images'] = None
        if image:
            raw = page.screenshot(type='jpeg', quality=70, full_page=False, timeout=self.timeout*1000)
            data = 'data:image/jpeg;base64,' + base64.b64encode(raw).decode('ascii')
            image_page=self._context.new_page()
            def resize(maximum, width):
                return image_page.evaluate("""async ({data,maximum,width}) => {
                  const image=new Image(); image.src=data; await image.decode();
                  let scale=Math.min(1,width/image.width), quality=.7;
                  for(let i=0;i<16;i++){
                    const c=document.createElement('canvas'); c.width=Math.max(1,Math.floor(image.width*scale));c.height=Math.max(1,Math.floor(image.height*scale));
                    c.getContext('2d').drawImage(image,0,0,c.width,c.height);
                    const value=c.toDataURL('image/jpeg',quality).split(',')[1];
                    if(value.length<=maximum)return {data:value,width:c.width,height:c.height,scale:c.width/image.width,mimeType:'image/jpeg'};
                    scale*=.8;quality=Math.max(.3,quality-.05);
                  }
                  throw new Error('Browser image exceeds capture budget');
                }""", {'data':data,'maximum':maximum,'width':width})
            try:
                result['_images']={'preview':resize(180000,1280),'model':resize(16000,480)}
            finally:
                try:image_page.close()
                except Exception:pass
        checkpoint()
        if navigation != self._navigation:
            self._invalidate()
            raise ExecutionError('Navigation changed while capturing observation; observe again')
        return result

    def call(self, value, checkpoint=lambda: None, *, _computer=False):
        validate_browser_input(value,internal=_computer)
        action = value.get('action')
        if action not in ('navigate', 'snapshot', 'observe', 'click', 'type', 'press', 'scroll', 'screenshot'):
            raise ValueError('Unknown browser action')
        # Validate action fields before navigation, pointer or keyboard effects.
        if action == 'navigate':
            url=value.get('url','');parsed=urllib.parse.urlparse(url)
            if not isinstance(url,str) or len(url)>2048 or parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:raise ValueError('Browser navigation requires a bounded HTTP(S) URL without credentials')
        if action == 'type' and (type(value.get('text'))is not str or len(value['text'])>100000):raise ValueError('Type requires bounded text')
        if action == 'press' and (type(value.get('key'))is not str or not 1<=len(value['key'])<=128):raise ValueError('Press requires bounded key')
        if action == 'scroll' and any(type(value.get(k))is not int or abs(value[k])>10000 for k in ('deltaX','deltaY')):raise ValueError('Scroll requires bounded Int deltas')
        if action == 'observe' and type(value.get('image',True))is not bool:raise ValueError('observe image must be Bool')
        checkpoint()
        page = self._open()
        replay = targeting = None
        guard=value.get('_observation')
        if guard is not None:
            if guard != [self.session_id,self.control_epoch,self.snapshot_id] or self._snapshot_navigation!=self._navigation:
                raise ExecutionError('Stale computer observation; observe again')
        if action == 'navigate':
            url = value.get('url', '')
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Browser navigation requires an HTTP(S) URL without credentials')
            self._invalidate()
            page.goto(url, wait_until='domcontentloaded', timeout=self.timeout * 1000)
            replay = {'action': 'navigate', 'url': url}
        elif action in ('click', 'type', 'press'):
            target, spec, targeting = self._target(page, value)
            text = value.get('text')
            if action == 'type' and (not isinstance(text, str) or len(text) > 100000):
                raise ValueError('Type requires bounded text')
            checkpoint()
            if spec is not None:
                replay = {'action': action, 'locator': copy.deepcopy(spec)}
                if action == 'type':replay['text'] = text
                if action == 'press':replay['key'] = value['key']
            try:
                if action == 'click':
                    target.scroll_into_view_if_needed(timeout=self.timeout * 1000)
                    box = target.bounding_box()
                    if not box:raise ExecutionError('Browser target has no visible bounding box')
                    viewport=page.viewport_size
                    x=(max(0,box['x'])+min(viewport['width'],box['x']+box['width']))/2
                    y=(max(0,box['y'])+min(viewport['height'],box['y']+box['height']))/2
                    border=target.evaluate('(el) => ({x:el.clientLeft,y:el.clientTop})')
                    target.click(position={'x':x-box['x']-border['x'],'y':y-box['y']-border['y']},timeout=self.timeout * 1000)
                    self.cursor = {'x':x,'y':y}
                elif action == 'type':
                    target.fill(text, timeout=self.timeout * 1000)
                else:
                    target.press(value['key'], timeout=self.timeout * 1000)
            finally:
                self._invalidate()  # A timeout may already have produced an effect.
        elif action == 'scroll':
            try:
                page.mouse.wheel(value['deltaX'],value['deltaY'])
                page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                replay={'action':'scroll','deltaX':value['deltaX'],'deltaY':value['deltaY']}
            finally:self._invalidate()
        checkpoint()
        result = {'url': page.url, 'title': page.title()[:2000], 'cursor': dict(self.cursor), 'isolation': 'headless-context'}
        if targeting:
            result['targeting'] = targeting
        if replay is not None:
            result['replayInput'] = replay
        if action == 'screenshot':
            pixels = page.screenshot(type='jpeg', quality=45, full_page=False, timeout=self.timeout * 1000)
            folder = self.workspace / '.chaos-browser'
            folder.mkdir(exist_ok=True)
            artifact = folder / (uuid.uuid4().hex + '.jpg')
            artifact.write_bytes(pixels)
            result['artifactPath'] = str(artifact)
            if len(pixels) <= 180000:
                result['image'] = 'data:image/jpeg;base64,' + base64.b64encode(pixels).decode('ascii')
            else:
                result['notice'] = 'Image retained as an artifact; exceeds inline image budget'
        elif action=='observe' or value.get('_capture'):
            result.update(self._observe(page,value.get('image',True),checkpoint))
            if action=='observe':result['replayInput']={'action':'observe','image':False}
        else:
            result.update(self._snapshot(page, checkpoint))
        result.update(sessionId=self.session_id, controlEpoch=self.control_epoch, snapshotId=self.snapshot_id)
        checkpoint()
        return result

    def checkpoint_state(self):
        """Run on the browser worker; fence references without navigating."""
        if threading.get_ident() != self._owner:
            raise ExecutionError('BrowserSession checkpoint requires its owning worker thread')
        self._invalidate()
        storage = self._context.storage_state() if self._context is not None else self._storage_state
        storage = browser_storage(storage)
        self._storage_state = storage
        return {'storage': storage, 'url': self._page.url if self._page is not None else 'about:blank'}

    def close(self):
        if threading.get_ident() != self._owner:
            raise ExecutionError('BrowserSession must close on its owning worker thread')
        self._invalidate()
        self.control_epoch += 1
        for handle in (self._context, self._browser):
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._page = self._context = self._browser = self._playwright = None


class BrowserBroker:
    """Serialized browser, always opened, checkpointed and closed on one thread.

    Cancellation drains the bounded in-flight operation. It cannot undo effects.
    """
    def __init__(self, workspace, timeout=15, session_factory=BrowserSession, *, storage_state=None):
        self._storage_state = browser_storage(storage_state) if storage_state is not None else None
        self.workspace, self.timeout = workspace, timeout
        self._factory = session_factory
        self._queue = queue.Queue(maxsize=128)
        self._lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(target=self._serve, name='chaos-browser-' + uuid.uuid4().hex[:8], daemon=True)
        self._thread.start()

    def _serve(self):
        session = None
        try:
            while True:
                # close() rejects further admissions. When a full queue could
                # not accept its wakeup sentinel, drain pending jobs then exit.
                if self._closed and self._queue.empty():
                    break
                job = self._queue.get()
                if job is None:
                    break
                try:
                    def check():
                        if self._closed:
                            raise ExecutionError('Browser session is closing')
                        if job['cancel'].is_set():
                            raise ExecutionError('Browser request cancelled')
                        job['checkpoint']()
                    check()
                    if session is None:
                        session = self._factory(self.workspace, timeout=self.timeout, **({'storage_state':self._storage_state} if self._storage_state is not None else {}))
                    if job.get('checkpoint_state'):
                        job['result'] = session.checkpoint_state()
                    else:
                        job['result'] = session.call(job['value'], check, **({'_computer':True}if job.get('computer')else {}))
                except BaseException as exc:
                    job['error'] = exc
                finally:
                    job['done'].set()
        finally:
            if session is not None:
                session.close()

    def call(self, value, checkpoint=lambda: None, *, _computer=False):
        if threading.get_ident() == self._thread.ident:
            raise ExecutionError('Browser broker cannot be called recursively from its worker')
        return self._request({'value': copy.deepcopy(value), 'computer':_computer}, checkpoint)

    def checkpoint_state(self):
        return self._request({'checkpoint_state': True})

    def _request(self, job, checkpoint=lambda: None):
        if threading.get_ident() == self._thread.ident:
            raise ExecutionError('Browser broker cannot be called recursively from its worker')
        checkpoint()
        job.update(checkpoint=checkpoint, done=threading.Event(), cancel=threading.Event())
        with self._lock:
            if self._closed:
                raise ExecutionError('Browser session is closed')
            try:
                self._queue.put_nowait(job)
            except queue.Full:
                raise ExecutionError('Browser request queue is full')
        try:
            while not job['done'].wait(.025):
                checkpoint()
            checkpoint()
        except BaseException:
            job['cancel'].set()
            job['done'].wait()
            raise
        if 'error' in job:
            raise job['error']
        return job['result']

    def close(self, timeout=None):
        if threading.get_ident() == self._thread.ident:
            raise ExecutionError('Browser broker cannot close from its worker')
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._queue.put_nowait(None)
                except queue.Full:
                    pass  # The worker drains closed requests and exits above.
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise TimeoutError('Browser worker has not drained; ownership must remain held')
