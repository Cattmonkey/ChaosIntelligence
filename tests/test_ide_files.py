import copy
import unittest
from chaos import Framework
from chaos.ide_files import edit_project_files, transform_files
from chaos.language import LanguageError
from chaos.store import Conflict


class IDEFiles(unittest.TestCase):
    def setUp(self):
        self.app=Framework();self.addCleanup(self.app.close)
        self.project=self.app.create_project('files',source='// keep import "lib/math.chaos"\nimport "lib/math.chaos" as M;M.answer()',files={'lib/math.chaos':'import "../base.chaos" as B; fn answer(){B.base+2};export{answer}', 'base.chaos':'let base=40;export{base}'})

    def edit(self,**kw):
        result=edit_project_files(self.app,'files',{'version':self.project['version'],**kw});self.project=result;return result

    def test_create_file_and_directory_module_are_versioned(self):
        old=self.project['version'];p=self.edit(operation='create',path='extras',directory=True)
        self.assertEqual(p['files']['extras/__init__.chaos'],'export {};\n');self.assertEqual(p['version'],old+1)
        p=self.edit(operation='create',path='extras/numbers.chaos',source='let two=2;export{two}')
        self.assertIn('extras/numbers.chaos',p['files'])
        self.assertTrue(any(e['type']=='graph.saved' for e in self.app.events('files')['items']))

    def test_directory_rename_rewrites_actual_imports_preserves_comments(self):
        p=self.edit(operation='rename',path='lib',newPath='deep/lib',directory=True)
        self.assertIn('// keep import "lib/math.chaos"',p['source'])
        self.assertIn('import "deep/lib/math.chaos" as M',p['source'])
        self.assertIn('import "../../base.chaos"',p['files']['deep/lib/math.chaos'])
        self.assertEqual(Framework.evaluate(p['source'],files=p['files'],entry=p['entry']),42)

    def test_entry_move_updates_relative_imports_and_source_mirror(self):
        p=self.edit(operation='rename',path='main.chaos',newPath='app/main.chaos')
        self.assertEqual(p['source'],p['files'][p['entry']]);self.assertEqual(p['entry'],'app/main.chaos')
        self.assertEqual(Framework.evaluate(p['source'],files=p['files'],entry=p['entry']),42)

    def test_unicode_and_multiline_comment_import_locations(self):
        p=self.app.create_project('unicode',source='let 标记="🧠"; // quote "fake"\nimport \'lib/a.chaos\' as A; A.x',files={'lib/a.chaos':'let x=42;export{x}'})
        moved=edit_project_files(self.app,'unicode',{'version':p['version'],'operation':'rename','path':'lib/a.chaos','newPath':'lib/b.chaos'})
        self.assertIn('let 标记="🧠"; // quote "fake"',moved['source'])
        self.assertEqual(Framework.evaluate(moved['source'],files=moved['files']),42)

    def test_non_lf_unicode_whitespace_does_not_shift_import_rewrite(self):
        for i, separator in enumerate(('\u2028','\r','\v')):
            source='let x=1;'+separator+'// comment\nimport "lib/a.chaos" as A; A.n'
            name='unicode_sep_'+str(i)
            p=self.app.create_project(name,source=source,files={'lib/a.chaos':'let n=42;export{n}'})
            changed=edit_project_files(self.app,name,{'version':p['version'],'operation':'rename','path':'lib/a.chaos','newPath':'lib/b.chaos'})
            self.assertIn('// comment\n',changed['source'])
            self.assertEqual(Framework.evaluate(changed['source'],files=changed['files']),42)

    def test_delete_rejects_entry_and_live_dependency_without_mutating(self):
        for path,directory in [('main.chaos',False),('lib/math.chaos',False),('lib',True)]:
            before=self.app.snapshot('files')
            with self.subTest(path=path),self.assertRaises((LanguageError,ValueError)):
                self.edit(operation='delete',path=path,directory=directory)
            self.assertEqual(self.app.snapshot('files'),before)
        self.edit(operation='create',path='unused.chaos');p=self.edit(operation='delete',path='unused.chaos');self.assertNotIn('unused.chaos',p['files'])

    def test_invalid_collision_scope_and_version_are_atomic(self):
        for body in [dict(operation='create',path='../bad.chaos'),dict(operation='create',path='a/../bad.chaos'),dict(operation='create',path='std/x.chaos'),dict(operation='create',path='x.py'),dict(operation='create',path='base.chaos'),dict(operation='rename',path='lib',newPath='lib/sub',directory=True),dict(operation='rename',path='main.chaos',newPath='base.chaos'),dict(operation='create',path='bad.chaos',source='fn('),dict(operation='create',path='base.chaos/file.chaos')]:
            before=self.app.snapshot('files')
            with self.subTest(body=body),self.assertRaises((LanguageError,ValueError)):self.edit(**body)
            self.assertEqual(self.app.snapshot('files'),before)
        old=self.project['version'];self.edit(operation='create',path='new.chaos')
        with self.assertRaises(Conflict):edit_project_files(self.app,'files',{'version':old,'operation':'delete','path':'new.chaos'})
        self.assertIn('new.chaos',self.app.get_project('files')['files'])

    def test_transform_never_mutates_input_and_same_name_is_noop(self):
        old=copy.deepcopy(self.project);result=transform_files(old,'create','standalone.chaos');self.assertNotIn('standalone.chaos',old['files']);self.assertIn('standalone.chaos',result['files'])
        version=self.project['version'];self.edit(operation='rename',path='main.chaos',newPath='main.chaos');self.assertEqual(version,self.project['version'])
