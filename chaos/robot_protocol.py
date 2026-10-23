"""OpenBot's pinned c/h/v/s/w wire dialect, independent of language syntax."""
import re
from .robots import validate_channels

PROFILE = 'openbot-serial/1'
ACTUATORS = {name: {'minimum': -1, 'maximum': 1, 'unit': 'normalized'} for name in ('left', 'right')}
MAX_TX_FRAME = 59
MAX_RX_FRAME = 128
NUMBER = re.compile(r'-?(?:[0-9]+)(?:\.[0-9]+)?\Z')


def control_frame(channels):
    values = validate_channels(channels, ACTUATORS)
    # Java's integer cast in Vehicle.sendControl truncates toward zero.
    frame = ('c%d,%d\n' % (int(values['left'] * 255), int(values['right'] * 255))).encode('ascii')
    assert len(frame) <= MAX_TX_FRAME
    return frame


def interval_frame(kind, milliseconds):
    if kind not in ('h', 'v', 's', 'w') or type(milliseconds) is not int or not 1 <= milliseconds <= 10000:
        raise ValueError('Invalid OpenBot interval')
    frame = ('%s%d\n' % (kind, milliseconds)).encode('ascii')
    assert len(frame) <= MAX_TX_FRAME
    return frame


def _number(text, low, high):
    from .robots import finite
    if not NUMBER.fullmatch(text):
        raise ValueError('Malformed OpenBot telemetry number')
    value = float(text)
    if not finite(value) or not low <= value <= high:
        raise ValueError('OpenBot telemetry is outside finite bounds')
    return value


def telemetry_line(line):
    """Return only one real packet; startup/debug lines never become samples."""
    if type(line) not in (bytes, bytearray) or len(line) > MAX_RX_FRAME:
        raise ValueError('Invalid OpenBot receive frame')
    try:
        text = bytes(line).decode('ascii').rstrip('\r')
    except UnicodeError:
        raise ValueError('OpenBot frames must be ASCII') from None
    if text == 'r':
        return 'reset', None
    if not text or text.startswith(('vmin', 'vlow', 'vmax')):
        return None
    if text[0] == 'v':
        value = _number(text[1:], 0, 1000)
        return 'telemetry', {'voltage': {'value': value, 'unit': 'V'}}
    if text[0] == 's':
        payload = text[1:]
        if not payload.isascii() or not payload.isdecimal() or len(payload) > 5:
            raise ValueError('Malformed OpenBot sonar reading')
        value = int(payload)
        if not 0 <= value <= 65535:
            raise ValueError('OpenBot sonar reading is outside bounds')
        return 'telemetry', {'sonar': {'value': None if value == 65535 else value, 'unit': 'cm'}}
    if text[0] == 'w':
        fields = text[1:].split(',')
        if len(fields) != 2:
            raise ValueError('Malformed OpenBot wheel reading')
        left, right = (_number(value, -1000000, 1000000) for value in fields)
        return 'telemetry', {'leftRpm': {'value': left, 'unit': 'rpm'}, 'rightRpm': {'value': right, 'unit': 'rpm'}}
    return None
