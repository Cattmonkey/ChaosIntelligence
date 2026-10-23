import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile

from chaos.portable import PackageError, build_portable, extract_portable, verify_portable


def wheel(root, mutate=None):
    prefix = 'chaos_intelligence-0.79.0.dist-info'
    files = {'chaos/__init__.py': b'', 'chaos/__main__.py': b'',
             'chaos/version.py': b'__version__ = "0.79.0"\n',
             'chaos/stdlib/swarm.chaos': b'42', 'chaos/ui_catalog.json': b'{}',
             'chaos/docs/catalog.json': b'{}', 'chaos/web/index.html': b'original',
             'chaos/examples/example.chaos': b'42', 'chaos/vendor/fixture/LICENSE': b'license',
             prefix + '/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
             prefix + '/METADATA': b'Metadata-Version: 2.1\nName: chaos-intelligence\nVersion: 0.79.0\nRequires-Python: >=3.9\nRequires-Dist: example>=1\nRequires-Dist: native>=1; extra == "native"\nRequires-Dist: platform>=1; sys_platform == "extra"\nProvides-Extra: native\n'}
    rows = [[n, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode(), str(len(data))] for n, data in files.items()]
    rows.append([prefix + '/RECORD', '', '']);out = io.StringIO();csv.writer(out).writerows(rows)
    files[prefix + '/RECORD'] = out.getvalue().encode()
    if mutate:mutate(files)
    path = root / 'input.whl'
    with zipfile.ZipFile(path, 'w') as archive:
        for n, data in files.items():archive.writestr(n, data)
    return path


class PortablePackageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root = Path(temp.name)

    def bundle(self):
        output = self.root / 'package.zip';build_portable(wheel(self.root), output);return output

    def test_exact_wheel_payload_metadata_and_deterministic_output(self):
        w = wheel(self.root);a = self.root / 'a.zip';b = self.root / 'b.zip'
        build_portable(w, a);build_portable(w, b);self.assertEqual(a.read_bytes(), b.read_bytes())
        with zipfile.ZipFile(w) as original, zipfile.ZipFile(a) as portable:
            for n in original.namelist():self.assertEqual(original.read(n), portable.read(n))
            m = json.loads(portable.read('manifest.json'))
            self.assertEqual(m['baseRequirements'], ['example>=1', 'platform>=1; sys_platform == "extra"'])
            self.assertEqual(m['extras'], ['native']);self.assertIn('native>=1; extra == "native"', m['requirements'])
        info = extract_portable(a, self.root / 'new');self.assertTrue(info['verified'])

    def test_record_corruption_fails_before_creating_output(self):
        w = wheel(self.root, lambda f:f.update({'chaos/web/index.html':b'changed'}))
        with self.assertRaises(PackageError):build_portable(w, self.root / 'never.zip')
        self.assertFalse((self.root / 'never.zip').exists())

    def test_traversal_and_case_collision_are_rejected(self):
        for n in ('../escape', '/escape', 'chaos/../escape', 'chaos\\escape', 'chaos/CON.txt', 'CHAOS/version.py', *(f'chaos/bad{c}.py' for c in '<>"|?*')):
            with self.subTest(path=n):
                w = wheel(self.root, lambda f:f.update({n:b'bad'}))
                with self.assertRaises(PackageError):build_portable(w, self.root / 'never.zip')
        w = wheel(self.root, lambda f:f.update({'chaos/café.py':b'a', 'chaos/cafe\u0301.py':b'b'}))
        with self.assertRaises(PackageError):build_portable(w, self.root / 'never.zip')
        if hasattr(os, 'mkfifo'):
            fifo = self.root / 'input-fifo';os.mkfifo(fifo)
            with self.assertRaises(PackageError):build_portable(fifo, self.root / 'never.zip')
        with self.assertRaises(PackageError):build_portable(self.root, self.root / 'never.zip')
        self.assertFalse((self.root / 'never.zip').exists())

    def test_existing_empty_nonempty_and_symlink_targets_are_preserved(self):
        bundle = self.bundle();empty = self.root / 'empty';empty.mkdir()
        full = self.root / 'full';full.mkdir();(full / 'host.py').write_text('original')
        link = self.root / 'link';link.symlink_to(full, target_is_directory=True)
        for target in (empty, full, link):
            with self.subTest(target=target), self.assertRaises(PackageError):extract_portable(bundle, target)
        self.assertEqual(list(empty.iterdir()), []);self.assertEqual((full / 'host.py').read_text(), 'original');self.assertTrue(link.is_symlink())

    def test_existing_archive_and_payload_tampering_are_rejected(self):
        bundle = self.bundle();old = bundle.read_bytes()
        with self.assertRaises(PackageError):build_portable(self.root / 'input.whl', bundle)
        self.assertEqual(bundle.read_bytes(), old)
        dest = self.root / 'new';extract_portable(bundle, dest);(dest / 'chaos/web/index.html').write_text('changed')
        with self.assertRaises(PackageError):verify_portable(dest)

    def test_corrupt_manifest_fails_before_claiming_destination(self):
        bundle = self.bundle();bad = self.root / 'bad.zip'
        with zipfile.ZipFile(bundle) as original, zipfile.ZipFile(bad, 'w') as altered:
            for name in original.namelist():altered.writestr(name, b'[]' if name == 'manifest.json' else original.read(name))
        with self.assertRaises(PackageError):extract_portable(bad, self.root / 'never')
        self.assertFalse((self.root / 'never').exists())

    def test_concurrent_extraction_has_one_owner_and_no_overlay(self):
        bundle = self.bundle();dest = self.root / 'new';barrier = threading.Barrier(2);results = []
        def run():
            barrier.wait()
            try:results.append(extract_portable(bundle, dest)['verified'])
            except PackageError:results.append(False)
        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(5);self.assertFalse(t.is_alive())
        self.assertEqual(sorted(results), [False, True]);self.assertTrue(verify_portable(dest)['verified'])

    def test_added_unrecorded_file_is_rejected_and_cache_does_not_change_integrity(self):
        dest = self.root / 'new';extract_portable(self.bundle(), dest)
        cache = dest / 'chaos/__pycache__';cache.mkdir();(cache / 'fixture.pyc').write_bytes(b'cache')
        self.assertTrue(verify_portable(dest)['verified']);(dest / 'chaos/unrecorded.py').write_text('42')
        with self.assertRaises(PackageError):verify_portable(dest)
