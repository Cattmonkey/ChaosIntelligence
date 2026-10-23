"""Immutable executable run snapshots; project authority is never code identity."""
from .executable_image import _data, load_executable
from .language import LanguageError


def freeze_image(image):
    image = _data(image)
    load_executable(image)  # Whole closure before run admission or a queued event.
    return {'kind': 'executable', 'imageHash': image['imageHash'],
            'entry': image['entry'], 'image': image}


def load_execution(execution):
    if (type(execution) is not dict or set(execution) != {'kind', 'imageHash', 'entry', 'image'}
            or execution['kind'] != 'executable' or type(execution['imageHash']) is not str
            or type(execution['entry']) is not str or type(execution['image']) is not dict):
        raise LanguageError('Invalid executable run snapshot', code='executable_error')
    bundle = load_executable(execution['image'])
    if execution['imageHash'] != bundle._executable_image_hash or execution['entry'] != bundle.entry:
        raise LanguageError('Executable run identity differs from its saved image', code='executable_error')
    return bundle


def descriptor(execution):
    return {'kind': 'executable', 'imageHash': execution['imageHash'],
            'entry': execution['entry'], 'sourceAvailable': False}
