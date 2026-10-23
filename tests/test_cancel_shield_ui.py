"""Shield debugger cards render bounded snapshots from the actual interpreter."""
from workbench_actions import command as workbench_command, button as workbench_button, view as workbench_view, run_options as workbench_run_options
import importlib.util
import json
import os
import tempfile
import threading
import time
from pathlib import Path
import shutil
import subprocess
import unittest
from chaos.language import Evaluator
from chaos.suspension import TaskCancelled

ROOT = Path(__file__).resolve().parent.parent


class CancelShieldUI(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is unavailable')
    def test_real_deferred_snapshot_bounds_frames_and_resume(self):
        pending = []; completed = []; evaluator = Evaluator(timeout=5)
        def hook(*args):
            snapshot = evaluator.suspension.snapshot()
            if any(task['cancellationDeferred'] and task['shieldDepth'] == 2 for task in snapshot['tasks']):
                pending.append(snapshot)
            if snapshot.get('shields') and snapshot['shields'][0]['state'] == 'closed':
                completed.append(snapshot)
        evaluator.debug_hook = hook
        source = '''let shields=map(fn(n){cancel_shield()},range(105));
async fn work(){with shields[0] as outer{async with shields[1] as inner{
 while !cancel_shield_info(inner).cancellationDeferred{await sleep(0)};
 await sleep(0);42
}}}
let worker=spawn(work());while task_info(worker).shieldDepth!=2{null};cancel(worker);
while task_info(worker).state!="cancelled"{null};task_info(worker)'''
        try: evaluator.run(source)
        except TaskCancelled: pass  # The final unobserved local cancellation remains a hard signal.
        self.assertTrue(pending); self.assertTrue(completed)
        snapshot = pending[-1]
        self.assertEqual(snapshot['shieldTotal'], 105)
        self.assertTrue(snapshot['shieldsTruncated'])
        self.assertEqual(len(snapshot['shields']), 100)
        first, second = snapshot['shields'][:2]
        self.assertEqual((first['depth'], second['depth']), (1, 2))
        self.assertEqual(first['taskId'], second['taskId'])
        self.assertTrue(first['cancellationDeferred']); self.assertTrue(second['cancellationDeferred'])
        task = next(task for task in snapshot['tasks'] if task['id'] == first['taskId'])
        self.assertTrue(task['cancellationRequested']); self.assertTrue(task['cancellationDeferred'])
        self.assertEqual(task['shieldDepth'], 2)
        result = subprocess.run([shutil.which('node'), str(ROOT/'tests/cancel_shield_controls.cjs')],
                                input=json.dumps({'pending': snapshot, 'completed': completed[-1]}),
                                text=True, capture_output=True, cwd=ROOT, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'passed')


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright extra unavailable')
class CancelShieldBrowser(unittest.TestCase):
    def test_http_paused_shield_cards_semantic_source_and_continue(self):
        from playwright.sync_api import sync_playwright
        from chaos import Framework, Settings
        from chaos.server import make_server
        source = '\n'.join([
            'async fn protected(){',
            ' with cancel_shield() as protection {',
            '  while !cancel_shield_info(protection).cancellationDeferred{await sleep(0)};',
            '  let receipt=cancel_shield_info(protection);',
            '  42',
            ' }',
            '}',
            'let worker=spawn(protected());',
            'while task_info(worker).shieldDepth!=1{null};',
            'cancel(worker);await worker',
        ])
        with tempfile.TemporaryDirectory(prefix='chaos-shield-browser-') as folder, Framework(Path(folder)/'runtime.sqlite', settings=Settings(':memory:')) as app:
            project = app.create_project('shield-browser', source=source, capabilities=[])
            server = make_server('127.0.0.1', 0, framework=app, state_dir=Path(folder)/'control', update_home=Path(folder)/'updates')
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            origin = 'http://127.0.0.1:' + str(server.server_port)
            try:
                run = app.start_debug(project['id'], project['version'], pause_on_entry=False,
                                      breakpoints=[{'file': 'main.chaos', 'line': 4}])
                deadline = time.monotonic()+5
                while time.monotonic() < deadline:
                    state = run.debugger()
                    if state['status'] == 'paused': break
                    time.sleep(.005)
                self.assertEqual(state['status'], 'paused')
                snapshot = state['taskSnapshot']; shield = snapshot['shields'][0]
                self.assertEqual(shield['state'], 'active'); self.assertTrue(shield['cancellationDeferred'])
                errors = []; external = []
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    try:
                        context = browser.new_context(viewport={'width': 1600, 'height': 1450})
                        def route(request):
                            if request.request.url.startswith(origin+'/'): request.continue_()
                            else: external.append(request.request.url); request.abort()
                        context.route('**/*', route)
                        page = context.new_page(); page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(origin+'/?project='+project['id'])
                        page.locator('.cm-editor').wait_for(state='attached')
                        workbench_view(page,'source')
                        page.evaluate('(run)=>showRun(run)', run.get())
                        page.wait_for_function('()=>ChaosDebugger.state.data?.status==="paused"')
                        card = page.locator('#debugShields [data-shield-id="'+shield['id']+'"]')
                        card.wait_for(state='visible')
                        self.assertIn('CancelShield · active', card.inner_text())
                        self.assertIn('取消请求：是 · 取消延期：是', card.inner_text())
                        self.assertIn('活动深度：1', card.inner_text())
                        task = page.locator('#debugTasks [data-task-id="'+shield['taskId']+'"]')
                        self.assertIn('取消延期：是 · 屏蔽深度：1', task.inner_text())
                        self.assertEqual(card.get_attribute('data-active-frame'), 'true')
                        page.wait_for_function('()=>document.querySelector("#debugSource")?.dataset.highlight==="ready"')
                        self.assertTrue(page.locator('#debugSource .chaos-token-function').filter(has_text='cancel_shield_info').count())
                        self.assertEqual(page.evaluate('JSON.parse(stringifyJSON(ChaosDebugger.state.data.taskSnapshot))'), snapshot)
                        self.assertEqual(page.evaluate('dirty'), False)
                        output = os.environ.get('CHAOS_SHIELD_AUDIT')
                        if output:
                            output = Path(output); output.mkdir(parents=True, exist_ok=True)
                            page.locator('#debugPanel').screenshot(path=str(output/'cancel-shield-paused.png'))
                            card.screenshot(path=str(output/'cancel-shield-card.png'))
                            task.screenshot(path=str(output/'cancel-shield-task.png'))
                        page.locator('[data-debug-command="continue"]').click()
                        page.wait_for_function('()=>ChaosDebugger.state.data?.status==="failed"')
                        self.assertEqual(run.wait(5)['status'], 'failed')
                        self.assertEqual(page.locator('#debugShields .debug-shield').count(), 0)
                        self.assertEqual(page.locator('#debugTasks .debug-task').count(), 0)
                        self.assertEqual(app.get_project(project['id'])['source'], source)
                        self.assertEqual(errors, []); self.assertEqual(external, [])
                        if output:
                            (output/'browser-result.json').write_text(json.dumps({'status': 'passed', 'sourceRoot': str(ROOT), 'runId': run.id, 'version': project['version'], 'pausedSnapshot': snapshot, 'finalStatus': 'failed', 'sourceUnchanged': True, 'semanticSourceHighlight': True, 'externalHTTP': [], 'pageErrors': []}, indent=2)+'\n')
                    finally: browser.close()
            finally:
                server.shutdown(); thread.join(3); server.server_close()


if __name__ == '__main__': unittest.main()
