import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from chaos.documentation import Documentation, DocumentationError
from chaos.version import __version__


def fixture(root):
    docs=root/'docs';examples=root/'examples';web=root/'web'
    for path in (docs,examples,web):path.mkdir()
    entries=[{'id':i,'title':t,'path':p,'section':'Fixture','description':'Test documentation'}for i,t,p in [('overview','Home','README.md'),('language','Language','LANGUAGE.md')]]
    (docs/'catalog.json').write_text(json.dumps({'schemaVersion':1,'pages':entries}))
    (docs/'README.md').write_text('# Home\n\n[Language](LANGUAGE.md#functions)\n\n[Example](../examples/demo.chaos)\n\n```chaos example=increment\ninput + 1\n```\n',encoding='utf-8')
    (docs/'LANGUAGE.md').write_text('# Language\n\n## Functions\n\n## Functions\n\n| A | B |\n| --- | --- |\n| x | y |\n',encoding='utf-8')
    (docs/'examples.json').write_text(json.dumps([{'id':'increment','input':41,'expect':42}]))
    (examples/'demo.chaos').write_text('1 + 2')
    (web/'docs.html').write_text('<!doctype html><title>Docs</title><!-- DOCS_BOOTSTRAP --><script src="docs.js"></script>')
    (web/'docs.js').write_text('// fixture')
    (web/'docs.css').write_text('body {}')
    return Documentation(docs,examples,web)


class DocumentationContract(unittest.TestCase):
    def test_actual_registered_docs_and_executable_examples(self):
        result=Documentation().check()
        self.assertEqual(result['status'],'passed')
        self.assertGreaterEqual(result['pages'],18)
        self.assertGreaterEqual(result['examples'],15)

    def test_snapshot_navigation_anchors_tables_and_case_results(self):
        with tempfile.TemporaryDirectory() as temp:
            docs=fixture(Path(temp));report=docs.check();self.assertEqual(report['examples'],1)
            page=docs.page('overview');self.assertIn('#/language/h-functions',page['html'])
            self.assertIn('/docs/files/examples/demo.chaos',page['html'])
            language=docs.page('language');self.assertIn('h-functions-2',[h['id']for h in language['headings']]);self.assertIn('<table>',language['html'])
            self.assertNotIn('html',docs.catalog()['pages'][0]);self.assertIn('input + 1',docs.catalog()['pages'][0]['searchText'])
            self.assertEqual(docs.attachment('demo.chaos'),b'1 + 2')

    def test_renderer_does_not_execute_html_or_unsafe_urls(self):
        with tempfile.TemporaryDirectory() as temp:
            docs=fixture(Path(temp));(docs.root/'LANGUAGE.md').write_text('# Language\n## Functions\n<script>alert(1)</script>\n\n[bad](javascript:alert%281%29)\n\n![bad](https://external.invalid/pixel.png)')
            page=docs.page('language');self.assertNotIn('<script>',page['html']);self.assertNotIn('href="javascript:',page['html']);self.assertNotIn('<img',page['html'])

    def test_link_and_example_drift_fail_before_build(self):
        with tempfile.TemporaryDirectory() as temp:
            docs=fixture(Path(temp));(docs.root/'examples.json').write_text(json.dumps([{'id':'increment','input':41,'expect':43}]))
            with self.assertRaisesRegex(DocumentationError,'increment'):docs.check()
            (docs.root/'README.md').write_text('# Home\n[Missing](MISSING.md)')
            with self.assertRaisesRegex(DocumentationError,'Unregistered'):docs.snapshot()
            (docs.root/'README.md').write_text('# Home\n[Missing](LANGUAGE.md#absent)')
            with self.assertRaisesRegex(DocumentationError,'Missing heading'):docs.snapshot()

    def test_missing_catalog_page_and_duplicate_example_are_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            docs=fixture(Path(temp));(docs.root/'EXTRA.md').write_text('# Extra')
            with self.assertRaisesRegex(DocumentationError,'missing from catalog'):docs.check()
            (docs.root/'EXTRA.md').unlink()
            with (docs.root/'README.md').open('a') as f:f.write('\n```chaos example=increment\n42\n```\n')
            with self.assertRaisesRegex(DocumentationError,'duplicate executable'):docs.check()

    def test_paths_never_read_unregistered_or_external_files(self):
        with tempfile.TemporaryDirectory() as temp:
            docs=fixture(Path(temp));outside=Path(temp)/'private.py';outside.write_text('PRIVATE')
            for path in ('../private.py','/etc/passwd','a/../../private.py'):
                with self.assertRaises(DocumentationError):docs.attachment(path)
            (docs.examples/'link.py').symlink_to(outside)
            with self.assertRaises(DocumentationError):docs.attachment('link.py')
            catalog=json.loads((docs.root/'catalog.json').read_text());catalog['pages'][0]['path']='../private.py'
            (docs.root/'catalog.json').write_text(json.dumps(catalog))
            with self.assertRaises(DocumentationError):docs.catalog()

    def test_static_export_offline_safe_and_replaces_only_owned_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);docs=fixture(root);out=root/'site'
            (docs.root/'LANGUAGE.md').write_text('# Language\n## Functions\n```text\n</script><script>alert(1)</script>\n```\n')
            result=docs.build(out);self.assertFalse(result['published'])
            shell=(out/'index.html').read_text();self.assertIn('docs-bootstrap',shell);self.assertIn('\\u003c/script\\u003e',shell)
            self.assertIn('examples/demo.chaos',shell);self.assertEqual((out/'examples/demo.chaos').read_text(),'1 + 2')
            (out/'obsolete-generated.txt').write_text('obsolete');docs.build(out);self.assertFalse((out/'obsolete-generated.txt').exists())
            other=root/'user';other.mkdir();(other/'keep.txt').write_text('keep')
            with self.assertRaises(DocumentationError):docs.build(other)
            self.assertEqual((other/'keep.txt').read_text(),'keep')
            (other/'.chaos-docs-site.json').write_text('{}')
            with self.assertRaisesRegex(DocumentationError,'ownership marker'):docs.build(other)
            self.assertEqual((other/'keep.txt').read_text(),'keep')
            (other/'.chaos-docs-site.json').unlink();(other/'.chaos-docs-site.json').symlink_to(out/'.chaos-docs-site.json')
            with self.assertRaisesRegex(DocumentationError,'ownership marker'):docs.build(other)
            with self.assertRaises(DocumentationError):docs.build(docs.root)
            with self.assertRaises(DocumentationError):docs.build(root)


class DocumentationHTTP(unittest.TestCase):
    def test_online_docs_assets_and_existing_service_interfaces(self):
        from chaos.server import make_server
        from chaos.framework import Framework
        from chaos.client import Client,NotFoundError,APIError
        with tempfile.TemporaryDirectory() as temp,Framework() as app:
            server=make_server(port=0,framework=app,state_dir=Path(temp)/'control',update_home=Path(temp)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            client=Client('http://127.0.0.1:'+str(server.server_port))
            try:
                pages=client._request('GET','/docs');self.assertEqual(pages['version'],__version__)
                page=client._request('GET','/docs/language');self.assertIn('html',page)
                api=client._request('GET','/docs/http-api');self.assertIn('documentationCatalog',api['markdown'])
                with self.assertRaises(NotFoundError):client._request('GET','/docs/not-registered')
                with self.assertRaises(APIError) as error:client._request('POST','/docs',{})
                self.assertEqual(error.exception.status,405)
                for path in ['/docs','/docs/','/docs.js','/docs.css','/docs/docs.js','/docs/files/examples/support_assistant.py']:
                    with urllib.request.urlopen(client.base_url+path)as response:self.assertEqual(response.status,200)
                for path in ['/docs/files/examples/../../settings.py','/docs/files/examples/%2e%2e/%2e%2e/settings.py']:
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(client.base_url+path)
                    self.assertEqual(error.exception.code,404)
                self.assertEqual(client._request('GET','/health')['apiVersion'],'v1')
            finally:server.shutdown();server.server_close();thread.join(2)

if __name__=='__main__':unittest.main()
