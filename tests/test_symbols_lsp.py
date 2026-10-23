"""Whole-package LSP references and transactional versioned rename proposals."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from chaos import Framework
from chaos.language import LanguageError
from chaos.lsp import Job,Server
from chaos.lsp_workspace import Workspace,TextMap,changed_text
from tests.test_lsp_integration import StdioClient
from tests.test_lsp_protocol import Connection
from tests.test_symbols_surfaces import SOURCE,LIBRARY,REVERSE,FILES

class SymbolLSP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        for name,text in FILES.items():(self.root/name).write_text(text)
    def test_whole_package_reverse_files_overlays_and_old_dependency_loader(self):
        w=Workspace([self.root]);uri=(self.root/'lib.chaos').as_uri();w.open({'uri':uri,'version':7,'languageId':'chaos','text':LIBRARY+'\n// unsaved'})
        self.assertEqual(set(w.snapshot(uri).load()[0]),{'lib.chaos'})
        files,entry=w.snapshot(uri).load_package();self.assertEqual(set(files),set(FILES));self.assertTrue(files['lib.chaos'].endswith('// unsaved'));self.assertEqual(w.snapshot(uri).document_versions,{'lib.chaos':7})
        (self.root/'escape').symlink_to(self.root.parent,target_is_directory=True)
        with self.assertRaises(ValueError):w.snapshot(uri).load_package()
    def test_package_limits_and_checkpoint_cancellation(self):
        w=Workspace([self.root]);snapshot=w.snapshot((self.root/'lib.chaos').as_uri())
        with self.assertRaisesRegex(RuntimeError,'cancelled'):snapshot.load_package(lambda:(_ for _ in ()).throw(RuntimeError('cancelled')))
        for index in range(65):(self.root/('other'+str(index)+'.chaos')).write_text('null')
        with self.assertRaises(ValueError):snapshot.load_package()
    def test_real_stdio_prepare_references_and_versioned_edits_do_not_write(self):
        client=StdioClient(self.root);self.addCleanup(client.close)
        self.assertTrue(client.capabilities['referencesProvider']);self.assertTrue(client.capabilities['renameProvider']['prepareProvider'])
        library=LIBRARY+'\n// overlay';reverse=REVERSE+'\n// reverse overlay'
        for name,text,version in [('lib.chaos',library,8),('reverse.chaos',reverse,4)]:client.send('textDocument/didOpen',{'textDocument':{'uri':(self.root/name).as_uri(),'languageId':'chaos','version':version,'text':text}})
        position=TextMap(library).position(len(library[:library.index('answer')].encode('utf-16-le'))//2);params={'textDocument':{'uri':(self.root/'lib.chaos').as_uri()},'position':position}
        prepared=client.request('textDocument/prepareRename',params);self.assertEqual(prepared['placeholder'],'answer')
        refs=client.request('textDocument/references',{**params,'context':{'includeDeclaration':False}});self.assertEqual(len(refs),3)
        result=client.request('textDocument/rename',{**params,'newName':'respond'});changes=result['documentChanges'];self.assertEqual(len(changes),3)
        files={**FILES,'lib.chaos':library,'reverse.chaos':reverse}
        for change in changes:
            name=Path(change['textDocument']['uri']).name;self.assertEqual(change['textDocument']['version'],{'lib.chaos':8,'reverse.chaos':4}.get(name))
            edits=[{'range':edit['range'],'text':edit['newText']}for edit in reversed(change['edits'])];files[name]=changed_text(files[name],edits)
        expected=Framework.rename(library,file='lib.chaos',files={**FILES,'lib.chaos':library,'reverse.chaos':reverse},position=TextMap(library).units[library.index('answer')],new_name='respond')['files']
        self.assertEqual(files,expected)
        self.assertEqual({name:(self.root/name).read_text()for name in FILES},{name:text.replace('\r\n','\n')for name,text in FILES.items()})
    def test_stale_overlay_rename_reply_is_rejected(self):
        connection=Connection(self.root);self.addCleanup(connection.close);uri=(self.root/'main.chaos').as_uri()
        connection.send('textDocument/didOpen',{'textDocument':{'uri':uri,'languageId':'chaos','version':1,'text':SOURCE}})
        original=connection.server._calculate;started=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def blocked(job):
            result=original(job)
            if job.method=='textDocument/rename':started.set();release.wait(5)
            return result
        connection.server._calculate=blocked
        position=TextMap(SOURCE).position(TextMap(SOURCE).units[SOURCE.index('L.answer')+2])
        connection.send('textDocument/rename',{'textDocument':{'uri':uri},'position':position,'newName':'respond'},'old-rename');self.assertTrue(started.wait(3))
        other=(self.root/'reverse.chaos').as_uri();connection.send('textDocument/didOpen',{'textDocument':{'uri':other,'languageId':'chaos','version':2,'text':REVERSE+'\n// new'}});connection.request('unknown');release.set()
        response=connection.take(lambda value:value.get('id')=='old-rename');self.assertEqual(response['error']['code'],-32801)
    def test_incomplete_references_are_explicit_error_not_partial_lsp_array(self):
        w=Workspace([self.root]);uri=(self.root/'main.chaos').as_uri();job=Job(1,'textDocument/references',{'position':{'line':0,'character':0}},w.snapshot(uri))
        with patch('chaos.symbols.references',return_value={'target':None,'references':[],'complete':False,'limitations':['dynamic use']}):
            with self.assertRaisesRegex(LanguageError,'dynamic use'):Server(None,None)._calculate(job)
