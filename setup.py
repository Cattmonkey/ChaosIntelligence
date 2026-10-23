from pathlib import Path
from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def run(self):
        super().run()
        self.copy_tree('docs', str(Path(self.build_lib) / 'chaos' / 'docs'))
        self.copy_tree('examples', str(Path(self.build_lib) / 'chaos' / 'examples'))
        self.copy_tree('web', str(Path(self.build_lib) / 'chaos' / 'web'))
        self.copy_tree('vendor/deepseek', str(Path(self.build_lib) / 'chaos' / 'vendor' / 'deepseek'))
        if Path('vendor/openui').is_dir():
            self.copy_tree('vendor/openui', str(Path(self.build_lib) / 'chaos' / 'vendor' / 'openui'))


setup(cmdclass={'build_py': BuildPy})
