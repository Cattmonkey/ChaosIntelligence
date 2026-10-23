"""Browser session ownership, freshness and real optional Chromium coverage."""
import copy
import importlib.util
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from chaos.execution import BrowserBroker, BrowserSession, ExecutionError


class Cancelled(Exception):
    pass


class BrokerTests(unittest.TestCase):
    def test_one_worker_and_preserved_session_across_callers(self):
        records = []
        class Session:
            def __init__(self, workspace, timeout):
                self.count = 0
                records.append(('open', threading.get_ident()))
            def call(self, value, checkpoint):
                checkpoint(); self.count += 1
                records.append(('call', threading.get_ident()))
                time.sleep(.005)
                return self.count
            def close(self): records.append(('close', threading.get_ident()))
        broker = BrowserBroker('.', session_factory=Session)
        values = []
        threads = [threading.Thread(target=lambda: values.append(broker.call({}))) for _ in range(8)]
        try:
            for t in threads: t.start()
            for t in threads: t.join(2); self.assertFalse(t.is_alive())
        finally: broker.close()
        self.assertEqual(sorted(values), list(range(1, 9)))
        self.assertEqual(len({x[1] for x in records}), 1)
        self.assertNotEqual(records[0][1], threading.get_ident())
        self.assertEqual([x[0] for x in records].count('open'), 1)
        self.assertEqual(records[-1][0], 'close')
        broker.close()
        with self.assertRaises(ExecutionError): broker.call({})

    def test_cancel_waits_for_inflight_effect_then_closes_on_owner(self):
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        records, errors = [], []
        class Session:
            def __init__(self, workspace, timeout): self.owner = threading.get_ident()
            def call(self, value, checkpoint):
                entered.set(); release.wait(2); records.append('effect-settled'); checkpoint()
            def close(self): records.append(('closed', self.owner == threading.get_ident()))
        def checkpoint():
            if cancelled.is_set(): raise Cancelled('task cancelled')
        broker = BrowserBroker('.', session_factory=Session)
        def run():
            try: broker.call({}, checkpoint)
            except BaseException as e: errors.append(e)
        thread = threading.Thread(target=run); thread.start()
        try:
            self.assertTrue(entered.wait(1)); cancelled.set(); time.sleep(.06)
            self.assertTrue(thread.is_alive(), 'cancel must drain actual operation')
            release.set(); thread.join(2); self.assertFalse(thread.is_alive())
            self.assertIsInstance(errors[0], Cancelled)
        finally: release.set(); thread.join(2); broker.close()
        self.assertEqual(records, ['effect-settled', ('closed', True)])

    def test_cancelled_queued_request_never_executes(self):
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        calls, errors = [], []
        class Session:
            def __init__(self, workspace, timeout): pass
            def call(self, value, checkpoint):
                calls.append(value['id']); entered.set(); release.wait(2); checkpoint()
                return value
            def close(self): pass
        broker = BrowserBroker('.', session_factory=Session)
        def run(value, check=lambda: None):
            try: broker.call(value, check)
            except BaseException as e: errors.append(e)
        def check():
            if cancelled.is_set(): raise Cancelled('queued task cancelled')
        first = threading.Thread(target=run, args=({'id': 1},)); second = threading.Thread(target=run, args=({'id': 2}, check))
        first.start(); self.assertTrue(entered.wait(1)); second.start()
        try:
            time.sleep(.04); cancelled.set(); time.sleep(.04); release.set()
            first.join(2); second.join(2)
            self.assertFalse(first.is_alive() or second.is_alive())
            self.assertEqual(calls, [1]); self.assertTrue(any(isinstance(e, Cancelled) for e in errors))
        finally: release.set(); first.join(2); second.join(2); broker.close()

    def test_worker_exception_propagates_and_later_request_can_run(self):
        class Session:
            def __init__(self, workspace, timeout): pass
            def call(self, value, checkpoint):
                if value.get('fail'): raise KeyboardInterrupt('host stop')
                return value
            def close(self): pass
        broker = BrowserBroker('.', session_factory=Session)
        try:
            with self.assertRaises(KeyboardInterrupt): broker.call({'fail': True})
            self.assertEqual(broker.call({'ok': True}), {'ok': True})
        finally: broker.close()

    def test_close_drains_current_effect_and_rejects_queued_effects(self):
        entered, release = threading.Event(), threading.Event()
        calls, errors, closed = [], [], []
        class Session:
            def __init__(self, workspace, timeout): pass
            def call(self, value, checkpoint):
                calls.append(value); entered.set(); release.wait(2); checkpoint()
            def close(self): closed.append(True)
        broker = BrowserBroker('.', session_factory=Session)
        def run(value):
            try: broker.call(value)
            except ExecutionError as e: errors.append(e)
        a=threading.Thread(target=run,args=(1,));b=threading.Thread(target=run,args=(2,))
        a.start();self.assertTrue(entered.wait(1));b.start();time.sleep(.03)
        closer=threading.Thread(target=broker.close);closer.start();time.sleep(.03)
        self.assertTrue(closer.is_alive());release.set()
        for t in (a,b,closer): t.join(2);self.assertFalse(t.is_alive())
        self.assertEqual(calls,[1]);self.assertEqual(len(errors),2);self.assertEqual(closed,[True])


class Target:
    def __init__(self): self.identity = self; self.matches = 1; self.clicks = 0; self.text = ''; self.fail = False
    def count(self): return self.matches
    def evaluate(self, code, handle=None):
        if handle is None: return {'x': 0, 'y': 0}
        return handle is self.identity
    def scroll_into_view_if_needed(self, **kwargs): pass
    def bounding_box(self): return {'x': 0, 'y': 0, 'width': 2, 'height': 4}
    def click(self, **kwargs):
        self.clicks += 1
        if self.fail: raise ExecutionError('action timed out')
    def fill(self, text, **kwargs): self.text = text
    def dispose(self): pass


class Page:
    viewport_size = {'width': 100, 'height': 100}
    url = 'https://example.invalid/'
    def __init__(self): self.target = Target()
    def title(self): return 'Example'
    def locator(self, value): return self.target
    def get_by_role(self, *args, **kwargs): return self.target
    def get_by_label(self, *args, **kwargs): return self.target
    def get_by_test_id(self, *args, **kwargs): return self.target


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.session = BrowserSession(self.temp.name)
        self.page = self.session._page = Page()
        self.session.snapshot_id = 4
        self.session._refs = {'e1': ({'kind': 'css', 'value': '#run'}, self.page.target.identity)}
        def snapshot(page, check):
            self.session._invalidate(); return {'snapshot': 'snapshot', 'targets': []}
        self.session._snapshot = snapshot
        self.ref = {'action': 'click', 'sessionId': self.session.session_id, 'controlEpoch': 1, 'snapshotId': 4, 'ref': 'e1'}
    def tearDown(self): self.session.close(); self.temp.cleanup()
    def test_reference_action_returns_replay_locator_and_invalidates(self):
        result = self.session.call(self.ref)
        self.assertEqual(result['targeting'], 'snapshot-ref')
        self.assertEqual(result['replayInput'], {'action': 'click', 'locator': {'kind': 'css', 'value': '#run'}})
        self.assertEqual(self.page.target.clicks, 1)
        with self.assertRaises(ExecutionError): self.session.call(self.ref)
    def test_foreign_session_epoch_snapshot_unknown_and_partial_fail_before_click(self):
        for key, value in [('sessionId', 'foreign'), ('controlEpoch', 2), ('snapshotId', 3), ('snapshotId', True), ('ref', 'e2')]:
            with self.subTest(key=key, value=value), self.assertRaises(ExecutionError): self.session.call({**self.ref, key: value})
        partial = dict(self.ref); del partial['sessionId']
        with self.assertRaises(ValueError): self.session.call(partial)
        self.assertEqual(self.page.target.clicks, 0)
    def test_navigation_or_replaced_element_invalidates_reference(self):
        self.session._navigation += 1
        with self.assertRaises(ExecutionError): self.session.call(self.ref)
        self.session._navigation = 0; self.page.target.identity = object()
        with self.assertRaises(ExecutionError): self.session.call(self.ref)
        self.assertEqual(self.page.target.clicks, 0)
    def test_reference_dispatch_keeps_saved_handle_after_identity_check(self):
        original = self.page.target
        replacement = Target()
        def checked(code, handle=None):
            if handle is None: return {'x': 0, 'y': 0}
            self.page.target = replacement
            return handle is original
        original.evaluate = checked
        self.session.call(self.ref)
        self.assertEqual(original.clicks, 1)
        self.assertEqual(replacement.clicks, 0)
    def test_unique_locator_required_and_temporary_locator_rejected(self):
        self.page.target.matches = 2
        with self.assertRaises(ExecutionError): self.session.call({'action': 'click', 'locator': {'kind': 'css', 'value': '.many'}})
        with self.assertRaises(ValueError): self.session.call({'action': 'click', 'locator': {'kind': 'css', 'value': '[data-chaos-target="0"]'}})
        with self.assertRaises(ValueError): self.session.call({**self.ref, 'selector': '#run'})
        self.assertEqual(self.page.target.clicks, 0)
    def test_legacy_selector_works_with_explicit_weak_targeting(self):
        result = self.session.call({'action': 'click', 'selector': '#run'})
        self.assertEqual(result['targeting'], 'legacy-selector')
        self.assertEqual(result['replayInput']['locator'], {'kind': 'css', 'value': '#run'})
    def test_failed_action_invalidates_refs_and_text_is_checked_before_effect(self):
        with self.assertRaises(ValueError): self.session.call({**self.ref, 'action': 'type', 'text': 42})
        self.assertEqual(self.session.snapshot_id, 4)
        self.page.target.fail = True
        with self.assertRaises(ExecutionError): self.session.call(self.ref)
        self.assertGreater(self.session.snapshot_id, 4)
    def test_close_requires_owner_and_changes_epoch(self):
        errors = []
        def close():
            try: self.session.close()
            except ExecutionError as e: errors.append(e)
        t = threading.Thread(target=close); t.start(); t.join(1)
        self.assertEqual(len(errors), 1); self.session.close(); self.assertEqual(self.session.control_epoch, 2)


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional browser extra unavailable')
class RealBrowserTests(unittest.TestCase):
    def test_reference_replaced_between_validation_and_dispatch_never_clicks_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            session = BrowserSession(directory, timeout=.25)
            try:
                try:
                    page = session._open()
                except Exception as error:
                    if "Executable doesn't exist" in str(error): self.skipTest('Chromium is not installed')
                    raise
                page.set_content('<button id="run" onclick="window.clicked=true">Run</button>')
                snapshot = session.call({'action':'snapshot'})
                target = snapshot['targets'][0]
                original_target = session._target
                def replaced(page, value):
                    result = original_target(page, value)
                    page.evaluate('document.querySelector("button").outerHTML = document.querySelector("button").outerHTML')
                    return result
                session._target = replaced
                action = {k:snapshot[k] for k in ('sessionId','controlEpoch','snapshotId')}
                action.update(action='click',ref=target['ref'])
                with self.assertRaises(Exception): session.call(action)
                self.assertFalse(page.evaluate('Boolean(window.clicked)'))
            finally: session.close()

    def test_real_chromium_fresh_refs_cross_effect_context_and_replay(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body=b'<title>Fixture</title><label>Query<input id="query"></label><button id="go" onclick="document.querySelector(\'output\').textContent=document.querySelector(\'input\').value">Submit</button><output></output>'
                self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers(); self.wfile.write(body)
            def log_message(self, *args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        with tempfile.TemporaryDirectory() as directory:
            broker=BrowserBroker(directory,timeout=3)
            try:
                try:
                    first=broker.call({'action':'navigate','url':'http://127.0.0.1:'+str(server.server_port)})
                except Exception as error:
                    if "Executable doesn't exist" in str(error): self.skipTest('Chromium is not installed')
                    raise
                target=next(t for t in first['targets'] if t['tag']=='input')
                action={k:first[k] for k in ('sessionId','controlEpoch','snapshotId')}; action.update(action='type',ref=target['ref'],text='first')
                typed=broker.call(action)
                with self.assertRaises(ExecutionError): broker.call(action)
                changed=broker.call({**typed['replayInput'],'text':'second'})
                button=next(t for t in changed['targets'] if t['tag']=='button')
                click={k:changed[k] for k in ('sessionId','controlEpoch','snapshotId')}; click.update(action='click',ref=button['ref'])
                result=broker.call(click)
                self.assertIn('second',result['snapshot']); self.assertEqual(result['sessionId'],first['sessionId'])
                self.assertEqual(result['replayInput']['locator'],button['locator'])
            finally: broker.close(); server.shutdown(); server.server_close(); thread.join(2)


if __name__ == '__main__': unittest.main()
