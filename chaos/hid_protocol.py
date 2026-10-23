"""Small dependency-free HID wire schema shared by the host and CircuitPython.

Copy this exact file to the bridge as hid_protocol.py. No text/key-layout guesses.
"""
PROTOCOL = 'chaos-hid/1'
MAX_FRAME = 1024


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def identity(value):
    return (type(value) is str and 1 <= len(value) <= 128
            and all(c in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in value))


def validate_action(spec):
    if type(spec) is not dict:
        raise ValueError('HID action must be a record')
    action = spec.get('action')
    if action == 'key_tap':
        if not {'action', 'keys'} <= set(spec) or set(spec) - {'action', 'keys', 'modifiers'}:
            raise ValueError('Invalid key_tap fields')
        keys, modifiers = spec['keys'], spec.get('modifiers', 0)
        if (type(keys) is not list or not 1 <= len(keys) <= 6
                or any(not integer(k, 1, 231) for k in keys)
                or len(set(keys)) != len(keys) or not integer(modifiers, 0, 255)):
            raise ValueError('Invalid HID keyboard usages or modifier mask')
        return {'action': action, 'keys': list(keys), 'modifiers': modifiers}
    if action == 'mouse_move':
        if not {'action', 'x', 'y'} <= set(spec) or set(spec) - {'action', 'x', 'y', 'wheel'}:
            raise ValueError('Invalid mouse_move fields')
        values = [spec['x'], spec['y'], spec.get('wheel', 0)]
        if any(not integer(v, -127, 127) for v in values):
            raise ValueError('HID relative coordinates must be -127..127')
        return dict(zip(('action', 'x', 'y', 'wheel'), [action] + values))
    if action == 'mouse_click':
        if set(spec) != {'action', 'button'} or not integer(spec['button'], 1, 16) or spec['button'] not in (1, 2, 4, 8, 16):
            raise ValueError('Invalid HID mouse button')
        return dict(spec)
    if action == 'release' and set(spec) == {'action'}:
        return dict(spec)
    raise ValueError('Unknown or invalid HID action')


def validate_request(value):
    if (type(value) is not dict or set(value) != {'protocol', 'session', 'id', 'spec'}
            or value['protocol'] != PROTOCOL or not identity(value['session']) or not identity(value['id'])):
        raise ValueError('Invalid HID request envelope')
    return {'protocol': PROTOCOL, 'session': value['session'], 'id': value['id'],
            'spec': validate_action(value['spec'])}
