import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from chaos.server import make_server
from chaos.settings import Settings
from chaos.client import Client, APIError, ConflictError
from chaos.background import BackgroundManager
from chaos import managed

class UpdateService(unittest.TestCase):
    def test_additive_api_cas_trust_and_unmanaged_preserve_programming(self):
        with tempfile.TemporaryDirectory() as folder:
            server=make_server(port=0,db=Path(folder)/'app.db',settings=Settings(':memory:'),state_dir=Path(folder)/'tasks',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            client=Client('http://127.0.0.1:'+str(server.server_port))
            try:
                status=client.updates();self.assertFalse(status['configured']);self.assertFalse(status['managed'])
                revision=status['version']
                updated=client.configure_updates({'version':revision,'autoCheck':False,'intervalSeconds':120})
                self.assertEqual(updated['config']['intervalSeconds'],120)
                with self.assertRaises(ConflictError):client.configure_updates({'version':revision,'autoCheck':True})
                with self.assertRaises(APIError) as denied:client.configure_updates({'version':updated['version'],'publicKey':'ab'*32})
                self.assertEqual(denied.exception.status,403)
                with self.assertRaises(APIError):client.apply_update()
                project=client.create_project('same-api',source='input + 1')
                self.assertEqual(client.start(project['id'],project['version'],41,mode='source').result(),42)
                self.assertIn('/updates',client._request('GET','/openapi.json')['paths'])
                with patch.dict('os.environ',{'CHAOS_INSTANCE_ID':'instance-fixture'}):
                    self.assertEqual(client._request('GET','/health')['instanceId'],'instance-fixture')
                client.check_updates()
                deadline=time.monotonic()+2
                while client.updates()['busy'] and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(client.updates()['error']['code'],'not_configured')
                self.assertFalse(client.updates()['maintenance'])
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_pause_preserves_active_and_queued_work(self):
        with tempfile.TemporaryDirectory() as folder:
            entered=threading.Event();release=threading.Event()
            def worker(*args):entered.set();release.wait(3);return 42
            manager=BackgroundManager(Path(folder)/'tasks.db',worker,max_workers=1)
            try:
                a=manager.start('first',iterations=1);self.assertTrue(entered.wait(2))
                manager.pause_dispatch();b=manager.start('second',iterations=1)
                self.assertFalse(manager.is_idle());release.set()
                deadline=time.monotonic()+2
                while not manager.is_idle() and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(manager.get(a['id'])['status'],'completed')
                self.assertEqual(manager.get(b['id'])['status'],'queued')
                manager.pause_dispatch(False)
                deadline=time.monotonic()+2
                while manager.get(b['id'])['status']!='completed' and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(manager.get(b['id'])['status'],'completed')
            finally:release.set();manager.close(wait=True)

class ManagedLauncher(unittest.TestCase):
    def test_rollback_failed_startup_and_cleanup_on_interrupt(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for version in ('0.6.0','0.7.0'):
                python=managed.release_python(root,version);python.parent.mkdir(parents=True);python.touch()
            managed.atomic_json(root/'current.json',{'version':'0.7.0','previousVersion':'0.6.0'})
            processes=[]
            class Process:
                def __init__(self,*a,**kw):self.returncode=None;self.terminated=False;processes.append(self)
                def poll(self):return self.returncode
                def terminate(self):self.terminated=True;self.returncode=1
                def wait(self,timeout=None):self.returncode=0 if self.returncode is None else self.returncode;return self.returncode
            with patch.object(managed.subprocess,'Popen',Process),patch.object(managed,'_healthy',side_effect=[False,True]):
                self.assertEqual(managed.supervise(root,['serve']),0)
            self.assertEqual(managed.current(root)['version'],'0.6.0');self.assertTrue(processes[0].terminated)
            with patch.object(managed.subprocess,'Popen',Process),patch.object(managed,'_healthy',side_effect=KeyboardInterrupt):
                self.assertEqual(managed.supervise(root,['serve']),130)
            self.assertTrue(processes[-1].terminated)
    def test_launcher_preserves_arguments_and_excludes_source_shadow(self):
        self.assertTrue(managed._server_args(['serve','--port','8765']))
        self.assertFalse(managed._server_args(['update','check']))
        self.assertFalse(managed._server_args(['--version']))
        self.assertFalse(managed._server_args(['serve','--version']))
        self.assertFalse(managed._server_args(['serve','--help']))
        self.assertEqual(managed._health_url(['serve','--host=0.0.0.0','--port','8899']),'http://127.0.0.1:8899/api/v1/health')

if __name__=='__main__':unittest.main()
