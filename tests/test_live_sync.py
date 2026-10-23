"""Independent acceptance tests for one program across UI, API and files.

Only public SDK / HTTP entry points are used unless a test explicitly exercises
Store's cross-connection transaction guarantee. All artifacts use temp dirs.
"""
import concurrent.futures
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request

from chaos import Framework, Client, Workflow, ConflictError, ValidationError
from chaos.program import format_program, parse_program
from chaos.language import LanguageError
from chaos.server import make_server
from chaos.store import Store


class RemoteFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Framework(self.root / 'runtime.sqlite3')
        self.server = make_server(port=0, framework=self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = Client('http://127.0.0.1:' + str(self.server.server_port))
        self.other = Client(self.client.base_url)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.temp.cleanup()

    def create(self, source='input', files=None):
        return self.client.create_project('shared', source=source, files=files)


class SnapshotAndWireAcceptance(RemoteFixture):
    def test_snapshot_cursor_and_runs_are_scoped_to_project(self):
        project = self.create('input + 1')
        self.other.create_project('unrelated')
        self.app._store.event('unrelated', 'other.event', {})
        first = self.client.snapshot('shared')
        self.assertEqual(first['project'], project)
        self.assertEqual(first['runs'], [])
        self.assertEqual(first['eventCursor'], self.client.events('shared')['items'][-1]['id'])
        project['source'] = 'input + 2'
        updated = self.other.save_project(project, from_view='source')
        handle = self.other.start('shared', updated['version'], 40)
        self.assertEqual(handle.result(), 42)
        current = self.client.snapshot('shared')
        self.assertEqual(current['project']['version'], updated['version'])
        self.assertEqual(current['runs'][0]['id'], handle.id)
        self.assertEqual(current['runs'][0]['status'], 'completed')
        tail = self.client.events('shared', after=first['eventCursor'])['items']
        self.assertTrue(tail)
        self.assertEqual(current['eventCursor'], tail[-1]['id'])
        self.assertTrue(all(e['project_id'] == 'shared' for e in tail))

    def test_simultaneous_clients_save_exactly_once_and_loser_keeps_draft(self):
        project = self.create('1')
        barrier = threading.Barrier(2)
        def save(client, value):
            draft = copy.deepcopy(project)
            draft['source'] = str(value)
            barrier.wait(2)
            try:
                return client.save_project(draft, from_view='source')
            except ConflictError:
                self.assertEqual(draft['source'], str(value))
                self.assertEqual(draft['version'], 1)
                return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            a = pool.submit(save, self.client, 41)
            b = pool.submit(save, self.other, 42)
            results = [a.result(), b.result()]
        self.assertEqual(sum(r is not None for r in results), 1)
        snapshot = self.client.snapshot('shared')
        self.assertEqual(snapshot['project']['version'], 2)
        self.assertIn(snapshot['project']['source'], ('41', '42'))
        saved = [e for e in self.client.events('shared')['items'] if e['type'] == 'graph.saved']
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['payload']['version'], 2)

    def test_event_reconnect_after_snapshot_does_not_lose_committed_save(self):
        project = self.create('1')
        snapshot = self.client.snapshot('shared')
        project['source'] = '42'
        project = self.other.save_project(project, from_view='source')
        path = self.client.base_url + '/api/v1/events/stream?projectId=shared&after=' + str(snapshot['eventCursor'])
        with urllib.request.urlopen(path, timeout=2) as response:
            event_id = int(response.readline().decode().strip().split(': ', 1)[1])
            event = json.loads(response.readline().decode().split(': ', 1)[1])
        self.assertGreater(event_id, snapshot['eventCursor'])
        self.assertEqual(event['payload']['version'], project['version'])
        self.assertEqual(self.client.snapshot('shared')['project']['source'], '42')

    def test_json_text_input_preserves_numeric_type_and_large_integer(self):
        project = self.create('input')
        values = [('9007199254740993', 9007199254740993, int),
                  ('1.0', 1.0, float), ('false', False, bool),
                  ('{"nested":[9007199254740993,1.0]}', {'nested': [9007199254740993, 1.0]}, dict)]
        for raw, expected, expected_type in values:
            with self.subTest(raw=raw):
                created = self.client._request('POST', '/runs', {'projectId':'shared', 'version':project['version'], 'inputJson':raw})
                run = self.client.wait(created['id'])
                self.assertEqual(run['status'], 'completed')
                self.assertIs(type(run['output']), expected_type)
                self.assertEqual(run['output'], expected)
                self.assertEqual(json.loads(run['outputJson']), expected)
                self.assertEqual(json.loads(run['inputJson']), expected)
                events = self.client.events('shared', run_id=run['id'])['items']
                terminal = events[-1]
                self.assertEqual(terminal['type'], 'run.completed')
                self.assertEqual(json.loads(terminal['payload']['outputJson']), expected)
                if raw == '1.0':
                    self.assertIs(type(json.loads(run['outputJson'])), float)
                if isinstance(expected, dict):
                    self.assertIs(type(json.loads(run['outputJson'])['nested'][1]), float)

    def test_response_mirrors_do_not_modify_user_graph_records(self):
        values = [{'run_id':'user-data','type':'user-data','payload':None},
                  {'run_id':'user-data','type':'user-data','payload':{'output':42}},
                  {'snapshot':{},'status':'custom','input':42}]
        for index, value in enumerate(values):
            with self.subTest(value=value):
                graph = Workflow.identity().build()
                graph['nodes'][0]['config'].update(value)
                project = self.client.create_project('shape_'+str(index),graph=graph)
                self.assertEqual(project['graph']['nodes'][0]['config'],value)
                fetched = self.other.get_project(project['id'])
                self.assertEqual(fetched['graph']['nodes'][0]['config'],value)
                self.assertEqual(fetched['source'],project['source'])

    def test_ir_json_roundtrip_keeps_float_contracts_and_large_ints(self):
        self.create('[9007199254740993,1.0,-0.0,1e-300]')
        program = self.client.program('shared')['program']
        def browser_numbers(value):
            if type(value) is int:
                return int(float(value))
            if type(value) is float and value.is_integer():
                return int(value)
            if isinstance(value, list):
                return [browser_numbers(x) for x in value]
            if isinstance(value, dict):
                return {k:browser_numbers(v) for k,v in value.items()}
            return value
        encoded = json.loads(json.dumps(browser_numbers(program)))
        project = self.client.edit_program('shared',1,program=encoded)
        result = self.other.start('shared',project['version'],None).result()
        self.assertEqual(result,[9007199254740993,1.0,-0.0,1e-300])
        self.assertIs(type(result[0]),int)
        self.assertTrue(all(type(value) is float for value in result[1:]))
        import math
        self.assertEqual(math.copysign(1,result[2]),-1)

    def test_legacy_persisted_ir_exposes_lossless_numbers_without_changing_ids(self):
        self.create('[9007199254740993,1.0]')
        current = self.client.program('shared')['program']
        legacy = copy.deepcopy(current)
        def native_numbers(value):
            if isinstance(value, dict):
                if value.get('kind') in ('literal','p_literal') and isinstance(value.get('value'),dict):
                    value['value']=json.loads(value['value']['$number'])
                for child in value.values():
                    native_numbers(child)
            elif isinstance(value, list):
                for child in value:
                    native_numbers(child)
        native_numbers(legacy)
        # Simulates the 0.3 persisted representation, before exact JSON numbers.
        with self.app._store.write():
            self.app._store.db.execute('UPDATE projects SET program=? WHERE id=?',(json.dumps(legacy),'shared'))
        returned = self.client.program('shared')['program']
        self.assertEqual(returned,current)
        self.assertEqual(self.client.get_project('shared')['program'],current)
        self.assertEqual(self.client.snapshot('shared')['project']['program'],current)
        self.assertEqual(self.client.get_project('shared')['version'],1)

    def test_invalid_exact_number_wrapper_cannot_commit(self):
        self.create('1')
        original = self.client.program('shared')['program']
        literal = original['root']['statements'][0]
        for value in [{'$number':'NaN'},{'$number':'1e999'},{'$number':'false'},
                      {'$number':'12.0x'},{'$number':'1','extra':1}]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.client.edit_program('shared',1,operations=[{'op':'set','id':literal['id'],'field':'value','value':value}])
        self.assertEqual(self.other.get_project('shared')['version'],1)
        self.assertEqual(self.other.get_project('shared')['source'],'1')

    def test_json_text_input_invalid_forms_have_no_run_or_event_side_effect(self):
        project = self.create('input')
        before = self.client.snapshot('shared')
        for extra in [{'inputJson':'NaN'}, {'inputJson':'Infinity'}, {'inputJson':'{'},
                      {'inputJson':42}, {'input':0, 'inputJson':'1'}, {'inputJson':'1e999'}]:
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                self.client._request('POST', '/runs', {'projectId':'shared','version':project['version'],**extra})
        after = self.client.snapshot('shared')
        self.assertEqual(after['runs'], before['runs'])
        self.assertEqual(after['eventCursor'], before['eventCursor'])

    def test_nonentry_module_structural_ids_survive_successive_patches(self):
        self.create('import "lib.chaos" as L;L.value', {'lib.chaos':'let value=1;export{value}'})
        first = self.client.program('shared', file='lib.chaos')['program']
        def literals(value):
            if isinstance(value, dict):
                if value.get('kind') == 'literal':
                    yield value
                for child in value.values():
                    yield from literals(child)
            elif isinstance(value, list):
                for child in value:
                    yield from literals(child)
        literal_id = next(literals(first))['id']
        for version, value in [(1, 2), (2, 3)]:
            changed = self.client.edit_program('shared', version, file='lib.chaos',
                operations=[{'op':'set','id':literal_id,'field':'value','value':value}])
            after = self.other.program('shared', file='lib.chaos')['program']
            self.assertEqual(next(literals(after))['id'], literal_id)
            self.assertEqual(self.other.start('shared',changed['version'],None).result(),value)

    def test_ir_file_identity_cannot_disagree_with_target_module(self):
        self.create('1')
        foreign = parse_program('8', 'wrong.chaos')
        try:
            project = self.client.edit_program('shared', 1, program=foreign)
        except ValidationError:
            self.assertEqual(self.client.get_project('shared')['version'], 1)
        else:
            self.assertEqual(project['program']['file'], project['entry'])
            def locations(value):
                if isinstance(value, dict):
                    if 'span' in value:
                        yield value['span']
                    for child in value.values():
                        yield from locations(child)
                elif isinstance(value, list):
                    for child in value:
                        yield from locations(child)
            self.assertTrue(all(span['file'] == project['entry'] for span in locations(project['program'])))

    def test_source_ir_graph_and_api_runs_share_one_program_version(self):
        graph = Workflow().input().function('plus', 'input + 1').output().connect('input','plus').connect('plus','output')
        project = self.client.create_project('shared', graph=graph)
        self.assertEqual(self.client.start('shared',1,41,mode='source').result(),42)
        self.assertEqual(self.other.start('shared',1,41,mode='graph').result(),42)
        project['source'] = 'var n = 0; for x in range(1, 7) { n = n + x }; n'
        project = self.client.save_project(project, from_view='source')
        ir = self.other.program('shared')['program']
        self.assertEqual(parse_program(project['source'])['semanticHash'], ir['semanticHash'])
        self.assertEqual(project['graph'], {'nodes':[], 'edges':[]})
        self.assertEqual(project['files'][project['entry']], project['source'])
        self.assertEqual(self.other.start('shared',project['version'],None,mode='graph').result(),21)
        self.assertEqual(self.client.snapshot('shared')['project']['program'], ir)


class AtomicSnapshotAcceptance(unittest.TestCase):
    def test_snapshot_is_a_sqlite_transaction_across_store_connections(self):
        with tempfile.TemporaryDirectory() as folder:
            a = Store(Path(folder) / 'runtime.sqlite')
            b = Store(Path(folder) / 'runtime.sqlite')
            p = a.create_project('shared', 'shared', {'nodes':[],'edges':[]}, '1')
            errors = []
            done = threading.Event()
            def writer():
                try:
                    for i in range(2, 62):
                        q = b.project('shared')
                        b.save('shared', q['version'], q['graph'], str(i), q['name'], from_view='source')
                except BaseException as error:
                    errors.append(error)
                finally:
                    done.set()
            worker = threading.Thread(target=writer)
            try:
                worker.start()
                for i in range(150):
                    snap = a.snapshot('shared')
                    history = b.events('shared', limit=300)
                    at_cursor = [e for e in history if e['id'] <= snap['eventCursor']]
                    expected_version = max(e['payload']['version'] for e in at_cursor)
                    self.assertEqual(snap['project']['version'], expected_version)
                    self.assertEqual(int(snap['project']['source']), expected_version)
                    if done.is_set():
                        break
                worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(a.snapshot('shared')['project']['version'], 61)
            finally:
                worker.join(5)
                a.close()
                b.close()


class FileWorkspaceAcceptance(RemoteFixture):
    def checkout(self, source='input', files=None, name='checkout'):
        from chaos import Workspace
        self.create(source, files)
        return Workspace.checkout(self.client, 'shared', self.root / name)

    def test_nested_modules_checkout_and_new_remote_directory_pull(self):
        ws = self.checkout('import "lib/math.chaos" as M;M.answer', {'lib/math.chaos':'let answer=42;export{answer}'})
        root = self.root/'checkout'
        self.assertEqual((root/'lib'/'math.chaos').read_text(),'let answer=42;export{answer}')
        self.assertEqual(ws.run(None),42)
        remote = self.other.get_project('shared')
        remote['files']['new/deep/value.chaos']='let value=21;export{value}'
        remote['source']='import "new/deep/value.chaos" as V;V.value*2'
        self.other.save_project(remote,from_view='source')
        self.assertEqual(ws.sync()['status'],'pulled')
        self.assertEqual((root/'new'/'deep'/'value.chaos').read_text(),'let value=21;export{value}')
        self.assertEqual(ws.run(None),42)

    def test_checkout_push_then_second_client_edit_pulls(self):
        from chaos import Workspace
        ws = self.checkout('import "lib.chaos" as L; L.f(input)', {'lib.chaos':'fn f(x){x + 1}; export {f}'})
        path = self.root / 'checkout'
        self.assertEqual((path/'lib.chaos').read_text(), 'fn f(x){x + 1}; export {f}')
        self.assertEqual(ws.sync()['status'], 'unchanged')
        (path/'lib.chaos').write_text('fn f(x){x + 2}; export {f}')
        pushed = Workspace.open(path, client=self.client).sync()
        self.assertEqual(pushed, {'status':'pushed', 'version':2})
        self.assertEqual(self.other.start('shared',2,40).result(),42)
        remote = self.other.get_project('shared')
        remote['source'] = 'input + 3'
        remote = self.other.save_project(remote, from_view='source')
        self.assertEqual(ws.sync(), {'status':'pulled','version':remote['version']})
        self.assertEqual((path/'main.chaos').read_text(), 'input + 3')
        self.assertEqual(ws.run(39),42)

    def test_dual_edit_conflict_preserves_both_drafts_and_original_base(self):
        from chaos import WorkspaceConflict
        ws = self.checkout('1')
        path = self.root/'checkout'
        before = json.loads((path/'.chaos-workspace.json').read_text())
        (path/'main.chaos').write_text('41')
        remote = self.other.get_project('shared')
        remote['source'] = '42'
        remote = self.other.save_project(remote, from_view='source')
        with self.assertRaises(WorkspaceConflict) as failure:
            ws.sync()
        self.assertEqual(failure.exception.remote['source'], '42')
        self.assertEqual((path/'main.chaos').read_text(), '41')
        self.assertEqual(self.client.get_project('shared')['source'], '42')
        self.assertEqual(json.loads((path/'.chaos-workspace.json').read_text()),before)
        self.assertTrue((path/'.chaos-conflict.json').exists())
        with self.assertRaises(WorkspaceConflict):
            ws.run(None)
        self.assertEqual(self.client.runs('shared')['items'], [])

    def test_push_uses_cas_if_remote_changes_after_snapshot(self):
        from chaos import WorkspaceConflict
        ws = self.checkout('1')
        path = self.root/'checkout'
        (path/'main.chaos').write_text('41')
        original = self.client.save_project
        def racing_save(project, **kwargs):
            remote = self.other.get_project('shared')
            remote['source'] = '42'
            self.other.save_project(remote, from_view='source')
            return original(project, **kwargs)
        with patch.object(self.client, 'save_project', side_effect=racing_save), self.assertRaises(WorkspaceConflict):
            ws.sync()
        self.assertEqual((path/'main.chaos').read_text(), '41')
        self.assertEqual(self.other.get_project('shared')['source'],'42')

    def test_edit_while_push_is_in_flight_stays_local_for_next_sync(self):
        ws = self.checkout('1')
        source = self.root/'checkout'/'main.chaos'
        source.write_text('41')
        original = self.client.save_project
        def editing_save(project, **kwargs):
            saved = original(project, **kwargs)
            source.write_text('42')
            return saved
        with patch.object(self.client,'save_project',side_effect=editing_save):
            self.assertEqual(ws.sync()['status'],'pushed')
        self.assertEqual(source.read_text(),'42')
        self.assertEqual(self.other.get_project('shared')['source'],'41')
        self.assertTrue(ws.status()['localChanged'])
        self.assertEqual(ws.sync()['status'],'pushed')
        self.assertEqual(self.other.get_project('shared')['source'],'42')
        self.assertFalse(ws.status()['localChanged'])

    def test_edit_during_remote_fetch_prevents_pull_overwrite(self):
        from chaos import WorkspaceConflict
        ws = self.checkout('1')
        source = self.root/'checkout'/'main.chaos'
        remote = self.other.get_project('shared')
        remote['source']='42'
        self.other.save_project(remote,from_view='source')
        original = self.client.get_project
        def editing_fetch(project_id):
            result = original(project_id)
            source.write_text('41')
            return result
        with patch.object(self.client,'get_project',side_effect=editing_fetch), self.assertRaises(WorkspaceConflict):
            ws.sync()
        self.assertEqual(source.read_text(),'41')
        self.assertEqual(self.other.get_project('shared')['source'],'42')

    def test_late_editor_write_before_file_replacement_is_recovered(self):
        from chaos import WorkspaceConflict
        from chaos import workspace as module
        ws = self.checkout('1')
        source = self.root/'checkout'/'main.chaos'
        remote = self.other.get_project('shared')
        remote['source']='42'
        self.other.save_project(remote,from_view='source')
        original = module.os.replace
        edited = []
        def editing_replace(src, dst):
            if Path(src).resolve() == source.resolve() and not edited:
                source.write_text('314159265')
                edited.append(True)
            return original(src,dst)
        with patch.object(module.os,'replace',side_effect=editing_replace), self.assertRaises(WorkspaceConflict):
            ws.sync()
        self.assertTrue(edited)
        self.assertEqual(source.read_text(),'314159265')
        self.assertEqual(self.other.get_project('shared')['source'],'42')
        recovery = json.loads((source.parent/'.chaos-before-pull.json').read_text())
        self.assertEqual((Path(recovery['recoveryDirectory'])/'main.chaos').read_text(),'314159265')

    def test_editor_recreation_after_old_file_moved_is_never_replaced(self):
        from chaos import WorkspaceConflict
        from chaos import workspace as module
        ws = self.checkout('1')
        source = self.root/'checkout'/'main.chaos'
        remote = self.other.get_project('shared')
        remote['source']='42'
        self.other.save_project(remote,from_view='source')
        original = module.os.replace
        edited = []
        def editing_replace(src, dst):
            result = original(src,dst)
            if Path(src).resolve() == source.resolve() and not edited:
                source.write_text('314159265')
                edited.append(True)
            return result
        with patch.object(module.os,'replace',side_effect=editing_replace), self.assertRaises(WorkspaceConflict):
            ws.sync()
        self.assertTrue(edited)
        self.assertEqual(source.read_text(),'314159265')
        self.assertEqual(self.other.get_project('shared')['source'],'42')

    def test_deleting_unimported_local_module_is_pushed(self):
        ws = self.checkout('42', {'unused.chaos':'1'})
        path = self.root/'checkout'
        (path/'unused.chaos').unlink()
        self.assertEqual(ws.sync()['status'],'pushed')
        self.assertNotIn('unused.chaos',self.other.get_project('shared')['files'])
        self.assertFalse((path/'unused.chaos').exists())

    def test_remote_removed_module_is_removed_only_when_local_clean(self):
        ws = self.checkout('42', {'unused.chaos':'1'})
        path = self.root/'checkout'
        remote = self.other.get_project('shared')
        del remote['files']['unused.chaos']
        self.other.save_project(remote, from_view='source')
        self.assertEqual(ws.sync()['status'],'pulled')
        self.assertFalse((path/'unused.chaos').exists())
        self.assertEqual((path/'main.chaos').read_text(),'42')

    def test_invalid_local_program_does_not_publish_or_erase_draft(self):
        ws = self.checkout('42')
        path = self.root/'checkout'
        (path/'main.chaos').write_text('fn broken(')
        with self.assertRaises((LanguageError, ValueError, ValidationError)):
            ws.sync()
        self.assertEqual((path/'main.chaos').read_text(),'fn broken(')
        self.assertEqual(self.other.get_project('shared')['version'],1)
        self.assertEqual(self.other.get_project('shared')['source'],'42')

    def test_checkout_refuses_existing_files_and_sync_rejects_symlink_escape(self):
        from chaos import Workspace
        self.create('42')
        occupied = self.root/'occupied'
        occupied.mkdir()
        (occupied/'notes.txt').write_text('keep')
        with self.assertRaises((OSError, ValueError)):
            Workspace.checkout(self.client,'shared',occupied)
        self.assertEqual((occupied/'notes.txt').read_text(),'keep')
        ws = Workspace.checkout(self.client,'shared',self.root/'checkout')
        outside = self.root/'outside.chaos'
        outside.write_text('99')
        (self.root/'checkout'/'main.chaos').unlink()
        (self.root/'checkout'/'main.chaos').symlink_to(outside)
        with self.assertRaises((OSError, ValueError)):
            ws.sync()
        self.assertEqual(outside.read_text(),'99')
        self.assertEqual(self.other.get_project('shared')['version'],1)

    def test_cli_watch_survives_incomplete_source_then_synchronizes_fix(self):
        self.checkout('1')
        directory = self.root/'checkout'
        source = directory/'main.chaos'
        source.write_text('fn incomplete(')
        # The server can commit before the CLI receives its response and writes
        # its local manifest/receipt. Wait for that actual receipt before stopping
        # the watcher. Separate file opens keep readers from moving child offsets.
        output_path = self.root/'watch.stdout'
        error_path = self.root/'watch.stderr'
        with output_path.open('w') as output, error_path.open('w') as errors:
            process = subprocess.Popen([sys.executable,'-m','chaos','workspace','sync',str(directory),'--watch','--interval','0.1'],
                stdout=output,stderr=errors,text=True)
            try:
                # Require evidence that the child saw the invalid source before
                # fixing it, even when interpreter startup is slow.
                deadline = time.monotonic()+5
                waiting = None
                while time.monotonic()<deadline:
                    self.assertIsNone(process.poll(),'Watch exited on an ordinary incomplete editing state')
                    rows = error_path.read_text().splitlines(keepends=True)
                    notices = [json.loads(line) for line in rows if line.endswith('\n')]
                    waiting = next((row for row in notices if 'main.chaos' in row.get('waiting','')),None)
                    if waiting is not None:break
                    time.sleep(.05)
                self.assertIsNotNone(waiting,'Watch did not report the incomplete source before synchronization')
                self.assertEqual(self.other.get_project('shared')['source'],'1')
                source.write_text('42')
                deadline = time.monotonic()+5
                receipt = None
                while time.monotonic()<deadline:
                    remote = self.other.get_project('shared')
                    # Ignore only an unfinished trailing write; complete malformed
                    # output still fails JSON decoding instead of hiding a defect.
                    rows = output_path.read_text().splitlines(keepends=True)
                    notices = [json.loads(line) for line in rows if line.endswith('\n')]
                    receipt = next((row for row in notices if row.get('status')=='pushed'),None)
                    if remote['source']=='42' and receipt is not None:break
                    self.assertIsNone(process.poll(),'Watch terminated before acknowledging valid source')
                    time.sleep(.05)
                self.assertEqual(remote['source'],'42')
                self.assertIsNotNone(receipt,'Watch published remotely but did not acknowledge synchronization')
                self.assertEqual(receipt['version'],remote['version'])
                self.assertIsNone(process.poll())
            finally:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=5)
                    raise
        stdout,stderr=output_path.read_text(),error_path.read_text()
        self.assertIn('main.chaos',stderr)
        self.assertIn('pushed',stdout)

    def test_cli_checkout_sync_run_interoperates_with_remote_sdk(self):
        self.create('input + 1')
        directory = self.root/'cli-checkout'
        base = [sys.executable,'-m','chaos','workspace']
        args = ['--server', self.client.base_url, '--project','shared']
        result = subprocess.run(base+['checkout',str(directory)]+args, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        (directory/'main.chaos').write_text('input + 2')
        result = subprocess.run(base+['sync',str(directory)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.other.get_project('shared')['version'],2)
        result = subprocess.run(base+['run',str(directory),'--input-json','40'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),42)
        self.assertEqual(self.other.runs('shared')['items'][0]['version'],2)


if __name__ == '__main__':
    unittest.main()
