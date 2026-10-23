"""IDE routes and SDK remain one versioned program across files and execution."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from chaos import Client, Framework, Settings
from chaos.client import ConflictError, ValidationError
from chaos.server import make_server


class IDEHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Framework(settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server(port=0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close_server)
        self.client=Client('http://127.0.0.1:'+str(self.server.server_port))

    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def test_analysis_format_and_offline_editor_assets(self):
        text='// preserve 🧠\nfn add(x:Int,by:Int=2)->Int{x+by};add(40)'
        info=self.client.analyze(text,position=len(text));self.assertIn('tokens',info);self.assertIn('diagnostics',info)
        formatted=self.client.format_source(text);self.assertIn('// preserve 🧠',formatted)
        self.assertEqual(Framework.evaluate(formatted),42)
        self.assertEqual(Framework.format_source(text),formatted)
        self.assertEqual(Framework.analyze(text)['diagnostics'],info['diagnostics'])
        for file in ['/','/ide.js','/ide.css','/vendor/codemirror.js']:
            with urllib.request.urlopen(self.client.base_url+file)as response:
                body=response.read();self.assertGreater(len(body),30);self.assertEqual(response.status,200)
                self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
        with self.assertRaises(ValidationError):self.client.format_source('fn broken(')

    def test_file_edit_sdk_events_and_immutable_run_snapshot(self):
        p=self.client.create_project('ide',source='import "lib/a.chaos" as A;A.n',files={'lib/a.chaos':'let n=42;export{n}'})
        run=self.client.start('ide',p['version'],None,mode='source');self.assertEqual(run.result(),42)
        snapshot=self.client.snapshot('ide');p=self.client.edit_files('ide',p['version'],'rename','lib',new_path='math',directory=True)
        self.assertIn('math/a.chaos',p['files']);self.assertIn('math/a.chaos',self.app.get_project('ide')['source'])
        self.assertEqual(self.client.start('ide',p['version'],None,mode='source').result(),42)
        self.assertEqual(run.get()['snapshot']['source'],'import "lib/a.chaos" as A;A.n')
        events=self.client.events('ide',after=snapshot['eventCursor'])['items'];self.assertTrue(any(e['payload'].get('version')==p['version']for e in events))
        with self.assertRaises(ConflictError):self.client.edit_files('ide',p['version']-1,'delete','math/a.chaos')
        with self.assertRaises(ValidationError):self.client.edit_files('ide',p['version'],'delete','math/a.chaos')
        self.assertEqual(self.client.assistant_history('ide'),[])
        spec=self.client._request('GET','/openapi.json')
        for path in ['/language/analyze','/language/format-source','/projects/{projectId}/files','/projects/{projectId}/assistant/{jobId}/apply']:
            self.assertIn(path,spec['paths'])
