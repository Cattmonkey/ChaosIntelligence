"""Package-owned assets, with an explicit development-checkout adapter."""
import configparser
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent


def import_root():
    """The current canonical chaos package's filesystem import root."""
    return PACKAGE_ROOT.parent


def _checkout(root):
    if not all((root / name).is_file() for name in ('setup.py', 'setup.cfg', 'pyproject.toml')):
        return False
    if (root / 'chaos').resolve() != PACKAGE_ROOT:
        return False
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(root / 'setup.cfg', encoding='utf-8')
        return config.get('metadata', 'name', fallback='') == 'chaos-intelligence'
    except (OSError, configparser.Error, UnicodeError):
        return False


def resource_directory(name):
    if name not in ('docs', 'web', 'examples', 'vendor', 'stdlib'):
        raise ValueError('Unknown Chaos resource directory')
    packaged = PACKAGE_ROOT / name
    if packaged.is_dir():
        return packaged
    root = import_root()
    if name != 'stdlib' and _checkout(root) and (root / name).is_dir():
        return root / name
    raise FileNotFoundError('Chaos package is missing bundled resource directory: ' + name)
