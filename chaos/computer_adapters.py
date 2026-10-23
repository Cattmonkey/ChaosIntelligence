"""Trusted visual drivers and bounded packets for registered Computers.

Registration and validation perform no device or transport I/O. Driver objects,
endpoint configuration and physical resource identities remain host-private.
"""
import base64
import binascii
import copy
from dataclasses import dataclass
from io import BytesIO
import math
import warnings


MAX_IMAGE_BYTES = 8_000_000
MAX_IMAGE_DIMENSION = 16384
MAX_IMAGE_PIXELS = 16_777_216
IMAGE_LIMITS = {'preview': (180000, 1280), 'model': (16000, 480)}
IMAGE_FORMATS = {'image/png': 'PNG', 'image/jpeg': 'JPEG', 'image/webp': 'WEBP'}
DEFAULT_ACTIONS = ('navigate', 'click', 'type', 'press', 'scroll')
REGISTERED_ACTIONS = DEFAULT_ACTIONS + ('input', 'launch', 'button', 'swipe')


@dataclass(frozen=True)
class ComputerSpec:
    name: str
    factory: object
    description: str = ''
    profile: str = 'registered/1'
    resource_id: object = None
    actions: tuple = DEFAULT_ACTIONS

    def __post_init__(self):
        from .extensions import valid_name
        valid_name(self.name)
        if not callable(self.factory):
            raise ValueError('Computer factory must be callable')
        if type(self.description) is not str or len(self.description) > 4000:
            raise ValueError('Invalid Computer description')
        if type(self.profile) is not str or not 1 <= len(self.profile) <= 128:
            raise ValueError('Invalid Computer profile')
        if self.resource_id is not None and (type(self.resource_id) is not str or not 1 <= len(self.resource_id) <= 1024):
            raise ValueError('Invalid Computer resource identity')
        if (type(self.actions) is not tuple or not 1 <= len(self.actions) <= len(REGISTERED_ACTIONS) or
                any(type(action) is not str or action not in REGISTERED_ACTIONS for action in self.actions) or
                len(set(self.actions)) != len(self.actions)):
            raise ValueError('Computer actions must be a unique supported tuple')

    def describe(self):
        return {'name': self.name, 'description': self.description, 'profile': self.profile,
                'actions': list(self.actions)}


def _invalid(field):
    raise ValueError('Invalid registered Computer ' + field)


def _text(value, maximum, *, minimum=0, identity=False):
    if type(value) is not str or not minimum <= len(value) <= maximum:
        return False
    return not identity or all(ord(character) >= 32 and ord(character) != 127 for character in value)


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _finite(value, minimum, maximum):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        return False


def _pillow():
    try:
        from PIL import Image
    except ImportError as error:
        raise ValueError('Registered Computer images require the optional Pillow package') from error
    return Image


def _decode_image(raw, mime_type):
    """Decode bounded real pixels without trusting dimensions or MIME labels."""
    if (type(raw) is not bytes or not 1 <= len(raw) <= MAX_IMAGE_BYTES or
            type(mime_type) is not str or mime_type not in IMAGE_FORMATS):
        _invalid('image bytes or MIME type')
    Image = _pillow()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                width, height = image.size
                if (image.format != IMAGE_FORMATS[mime_type] or
                        not _integer(width, 1, MAX_IMAGE_DIMENSION) or
                        not _integer(height, 1, MAX_IMAGE_DIMENSION) or
                        width * height > MAX_IMAGE_PIXELS or getattr(image, 'n_frames', 1) != 1):
                    _invalid('image format or dimensions')
                image.load()
                return image.convert('RGB')
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError('Invalid registered Computer encoded image') from error


def prepare_computer_image(raw, mime_type, *, checkpoint=None):
    """Return real JPEG preview/model images within the existing base64 limits.

Scale is relative to original screenshot pixels, not logical device coordinates.
Pillow is loaded only when an image is requested; no browser is opened.
"""
    check = checkpoint if checkpoint is not None else lambda: None
    check()
    original = _decode_image(raw, mime_type)
    try:
        Image = _pillow()
        result = {}
        for variant, (maximum, width_limit) in IMAGE_LIMITS.items():
            factor, quality = min(1.0, width_limit / original.width), 70
            for _ in range(32):
                check()
                width, height = max(1, math.floor(original.width * factor)), max(1, math.floor(original.height * factor))
                with original.resize((width, height), Image.Resampling.LANCZOS) as resized:
                    output = BytesIO()
                    resized.save(output, format='JPEG', quality=quality)
                encoded = base64.b64encode(output.getvalue()).decode('ascii')
                if len(encoded) <= maximum:
                    result[variant] = {'data': encoded, 'mimeType': 'image/jpeg', 'width': width,
                                       'height': height, 'scale': width / original.width}
                    break
                factor *= .8
                quality = max(30, quality - 5)
            else:
                _invalid('image capture budget')
        check()
        return result
    finally:
        original.close()


def _images(value):
    if value is None:
        return
    if type(value) is not dict or set(value) != set(IMAGE_LIMITS):
        _invalid('image variants')
    for variant, (maximum, width_limit) in IMAGE_LIMITS.items():
        image = value[variant]
        if type(image) is not dict or set(image) != {'data', 'mimeType', 'width', 'height', 'scale'}:
            _invalid('image fields')
        if (not _text(image['data'], maximum, minimum=1) or
                not _integer(image['width'], 1, width_limit) or
                not _integer(image['height'], 1, MAX_IMAGE_DIMENSION) or
                not _finite(image['scale'], 0, 1) or image['scale'] == 0 or
                type(image['mimeType']) is not str or image['mimeType'] not in IMAGE_FORMATS):
            _invalid('image metadata')
        try:
            raw = base64.b64decode(image['data'], validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError('Invalid registered Computer image base64') from error
        decoded = _decode_image(raw, image['mimeType'])
        try:
            if decoded.size != (image['width'], image['height']):
                _invalid('image pixel dimensions')
        finally:
            decoded.close()


def _identity(packet):
    if (type(packet) is not dict or not _text(packet.get('sessionId'), 128, minimum=1, identity=True) or
            not _integer(packet.get('controlEpoch'), 1, 2**63 - 1) or
            not _integer(packet.get('snapshotId'), 1, 2**63 - 1)):
        _invalid('snapshot identity')
    return packet['sessionId'], packet['controlEpoch'], packet['snapshotId']


def validate_computer_packet(packet, previous=None):
    """Copy one closed registered-driver packet before journal/image exposure.

The host owns all run, source, Computer and record identities. ``previous`` is
the host's last accepted packet or its trusted three-field snapshot identity.
New control epochs may reset snapshot counters; the session stays lease-bound.
Logical viewport/target coordinates are independent of screenshot pixel density.
"""
    required = {'sessionId', 'controlEpoch', 'snapshotId', 'url', 'title', 'viewport',
                'cursor', 'truncated', 'snapshot', 'targets'}
    if type(packet) is not dict or not required <= set(packet) or set(packet) - required - {'_images'}:
        _invalid('packet fields')
    session, epoch, snapshot = _identity(packet)
    if previous is not None:
        old_session, old_epoch, old_snapshot = _identity(previous)
        if session != old_session or epoch < old_epoch or epoch == old_epoch and snapshot <= old_snapshot:
            _invalid('stale or changed snapshot identity')
    if (not _text(packet['url'], 2048) or not _text(packet['title'], 2000) or
            not _text(packet['snapshot'], 12000)):
        _invalid('observation text')
    viewport, cursor, truncated = packet['viewport'], packet['cursor'], packet['truncated']
    if (type(viewport) is not dict or set(viewport) != {'width', 'height'} or
            any(not _integer(viewport[key], 1, MAX_IMAGE_DIMENSION) for key in ('width', 'height'))):
        _invalid('viewport')
    if (type(cursor) is not dict or set(cursor) != {'x', 'y'} or
            not _finite(cursor['x'], 0, viewport['width']) or cursor['x'] == viewport['width'] or
            not _finite(cursor['y'], 0, viewport['height']) or cursor['y'] == viewport['height']):
        _invalid('cursor')
    if (type(truncated) is not dict or set(truncated) != {'text', 'targets'} or
            any(type(value) is not bool for value in truncated.values())):
        _invalid('truncation metadata')
    targets = packet['targets']
    if type(targets) is not list or len(targets) > 40:
        _invalid('targets')
    refs = set()
    for target in targets:
        if (type(target) is not dict or not {'ref', 'tag', 'text', 'bbox'} <= set(target) or
                set(target) - {'ref', 'tag', 'text', 'bbox', 'role'} or
                not _text(target['ref'], 128, minimum=1, identity=True) or target['ref'] in refs or
                not _text(target['tag'], 128, minimum=1, identity=True) or not _text(target['text'], 1000) or
                'role' in target and not _text(target['role'], 128, minimum=1, identity=True)):
            _invalid('target fields')
        refs.add(target['ref'])
        box = target['bbox']
        if box is not None:
            if (type(box) is not dict or set(box) != {'x', 'y', 'width', 'height'} or
                    any(not _finite(box[key], -1000000, 1000000) for key in ('x', 'y')) or
                    any(not _finite(box[key], 0, 1000000) or box[key] == 0 for key in ('width', 'height')) or
                    box['x'] >= viewport['width'] or box['y'] >= viewport['height'] or
                    box['x'] + box['width'] <= 0 or box['y'] + box['height'] <= 0):
                _invalid('target bounds')
    _images(packet.get('_images'))
    return copy.deepcopy(packet)
