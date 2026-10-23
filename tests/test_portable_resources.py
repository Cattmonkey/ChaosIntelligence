from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from chaos import resources
from chaos.server import static_path


class PortableResourceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root = Path(temp.name).resolve()
        self.package = self.root / 'chaos';self.package.mkdir()

    def test_foreign_project_assets_are_not_fallback_resources(self):
        for name in ('web', 'docs', 'examples'):(self.root / name).mkdir()
        with patch.object(resources, 'PACKAGE_ROOT', self.package):
            for name in ('web', 'docs', 'examples'):
                with self.subTest(name=name), self.assertRaises(FileNotFoundError):resources.resource_directory(name)

    def test_only_explicit_matching_development_checkout_can_use_sibling_assets(self):
        (self.root / 'web').mkdir();(self.root / 'setup.py').write_text('');(self.root / 'pyproject.toml').write_text('')
        cfg = self.root / 'setup.cfg';cfg.write_text('[metadata]\nname = other-project\n')
        with patch.object(resources, 'PACKAGE_ROOT', self.package):
            with self.assertRaises(FileNotFoundError):resources.resource_directory('web')
            cfg.write_text('[metadata]\nname = chaos-intelligence\n');self.assertEqual(resources.resource_directory('web'), self.root / 'web')

    def test_package_directory_is_chosen_once_missing_file_never_uses_parent(self):
        (self.package / 'web').mkdir();(self.root / 'web').mkdir();(self.root / 'web/index.html').write_text('foreign')
        with patch.object(resources, 'PACKAGE_ROOT', self.package):
            self.assertEqual(static_path('index.html'), self.package / 'web/index.html')
            self.assertFalse(static_path('index.html').exists())
