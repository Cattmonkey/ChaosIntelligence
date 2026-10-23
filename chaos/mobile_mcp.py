"""Mobile MCP backend for the existing owned Computer/Observation protocol.

Protocol vocabulary is pinned to mobile-next/mobile-mcp commit
 d4a83fa45b9ce7551dba1d1f5e7bdf7400b1b023. Literal prefixes below are
versioned wire decoders, never natural-language command recognition.
"""
import base64
import hashlib
import json
import math
import os
import re
import uuid
import urllib.parse
from .mcp_http import MCPHttpClient, MCPTransportError, endpoint_url


ELEMENTS_PREFIX = 'Found these elements on screen: '
UPSTREAM_COMMIT = 'd4a83fa45b9ce7551dba1d1f5e7bdf7400b1b023'


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _text(result):
    blocks = result.get('content')
    if (type(blocks) is not list or len(blocks) != 1 or type(blocks[0]) is not dict
            or blocks[0].get('type') != 'text' or type(blocks[0].get('text')) is not str):
        raise MCPTransportError('Mobile MCP tool requires exactly one text block')
    return blocks[0]['text']


def _decode_json(value):
    def pairs(rows):
        result = {}
        for key, item in rows:
            if key in result:
                raise ValueError()
            result[key] = item
        return result
    try:
        return json.loads(value, object_pairs_hook=pairs,
                          parse_constant=lambda item: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise MCPTransportError('Mobile MCP returned malformed structured data') from None


def _tree(result):
    text = _text(result)
    if len(text) > 250000 or not text.startswith(ELEMENTS_PREFIX):
        raise MCPTransportError('Unsupported Mobile MCP accessibility response revision')
    elements = _decode_json(text[len(ELEMENTS_PREFIX):])
    if type(elements) is not list or len(elements) > 2000:
        raise MCPTransportError('Mobile MCP accessibility tree exceeds its element budget')
    seen = set()
    for row in elements:
        if type(row) is not dict or set(row) - {
                'ref', 'type', 'text', 'label', 'name', 'value', 'identifier',
                'coordinates', 'focused', 'selected', 'checked', 'enabled'}:
            raise MCPTransportError('Invalid Mobile MCP accessibility element')
        if type(row.get('type')) is not str or not 1 <= len(row['type']) <= 500:
            raise MCPTransportError('Invalid Mobile MCP accessibility element type')
        for key in ('text', 'label', 'name', 'value', 'identifier'):
            if key in row and (type(row[key]) is not str or len(row[key]) > 20000):
                raise MCPTransportError('Invalid Mobile MCP accessibility string')
        ref = row.get('ref')
        if ref is not None:
            if type(ref) is not str or not 1 <= len(ref) <= 128 or ref in seen:
                raise MCPTransportError('Invalid or duplicate Mobile MCP element reference')
            seen.add(ref)
        for key in ('focused', 'selected', 'checked', 'enabled'):
            if key in row and type(row[key]) is not bool:
                raise MCPTransportError('Invalid Mobile MCP element state')
        box = row.get('coordinates')
        if (type(box) is not dict or set(box) != {'x', 'y', 'width', 'height'}
                or any(not _finite(value) or abs(value) > 1000000 for value in box.values())
                or box['width'] < 0 or box['height'] < 0):
            raise MCPTransportError('Invalid Mobile MCP element geometry')
    # This is the full structured tree, including rows beyond the public target
    # budget. A changed hidden row is still a changed observation.
    fingerprint = hashlib.sha256(json.dumps(elements, ensure_ascii=False, allow_nan=False,
                                            sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    return elements, fingerprint


class MobileMCP:
    """Host-configured single-device adapter; construction performs no I/O.

    Native refs and logical screen geometry remain private. A snapshot target
    gets a local ref; a source program cannot replace the device or tool name.
    A failed sent request permanently quarantines close() unless the original
    worker later receives a matching terminal response. No mutation is retried.
    """
    ACTIONS = ('navigate', 'click', 'input', 'button', 'launch', 'swipe')

    def __init__(self, endpoint, device, *, token_env=None, timeout=15.0,
                 image_preparer=None, client_factory=MCPHttpClient):
        self.endpoint = endpoint_url(endpoint)
        if type(device) is not str or not 1 <= len(device) <= 256 or any(ord(c) < 32 for c in device):
            raise ValueError('Mobile MCP device must be a bounded host-configured identifier')
        if token_env is not None and (type(token_env) is not str
                or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', token_env) is None):
            raise ValueError('Mobile MCP token_env must name an environment variable')
        if type(timeout) not in (int, float) or not 0.1 <= timeout <= 60:
            raise ValueError('Mobile MCP timeout must be 0.1..60 seconds')
        self.device = device
        self.token_env = token_env
        self.timeout = float(timeout)
        self._client_factory = client_factory
        self._client = None
        self._image_preparer = image_preparer
        self._closed = False
        self._verified = False
        self._session_id = uuid.uuid4().hex
        self._snapshot_id = 0
        self._latest = None
        self._targets = {}
        self._cursor = {'x': 0, 'y': 0}

    def _transport(self):
        if self._closed:
            raise MCPTransportError('Mobile Computer is closed')
        if self._client is None:
            token = None
            if self.token_env is not None:
                token = os.environ.get(self.token_env)
                if not token:
                    raise MCPTransportError('Configured Mobile MCP token environment variable is unset')
            self._client = self._client_factory(self.endpoint, token=token, timeout=self.timeout)
        return self._client

    def _tool(self, name, args, checkpoint, *, mutation=False, device=True):
        checkpoint()
        arguments = dict(args)
        if device:
            # No payload from a source program can override this binding.
            if 'device' in arguments:
                raise MCPTransportError('Mobile MCP device override is not permitted')
            arguments['device'] = self.device
        result = self._transport().call_tool(name, arguments, checkpoint, mutation=mutation)
        checkpoint()
        return result

    def _verify_device(self, checkpoint):
        if self._verified:
            return
        result = _decode_json(_text(self._tool('mobile_list_available_devices', {}, checkpoint, device=False)))
        if type(result) is not dict or type(result.get('devices')) is not list or len(result['devices']) > 256:
            raise MCPTransportError('Invalid Mobile MCP device enumeration')
        matches = [row for row in result['devices'] if type(row) is dict and row.get('id') == self.device]
        if (len(matches) != 1 or matches[0].get('state') != 'online'
                or matches[0].get('platform') not in ('android', 'ios')
                or matches[0].get('type') not in ('real', 'emulator', 'simulator')):
            raise MCPTransportError('Configured Mobile MCP device is not uniquely online')
        self._verified = True

    def _read_tree(self, checkpoint):
        return _tree(self._tool('mobile_list_elements_on_screen', {'format': 'json'}, checkpoint))

    def _screen(self, checkpoint):
        text = _text(self._tool('mobile_get_screen_size', {}, checkpoint))
        match = re.fullmatch(r'Screen size is ([0-9]{1,5})x([0-9]{1,5}) pixels', text)
        if match is None:
            raise MCPTransportError('Unsupported Mobile MCP screen geometry response')
        width, height = map(int, match.groups())
        if not 1 <= width <= 16384 or not 1 <= height <= 16384:
            raise MCPTransportError('Mobile MCP screen dimensions exceed the viewport budget')
        return {'width': width, 'height': height}

    def _images(self, checkpoint):
        result = self._tool('mobile_take_screenshot', {'maxSize': 1280}, checkpoint)
        blocks = result.get('content')
        if type(blocks) is not list or not 1 <= len(blocks) <= 2:
            raise MCPTransportError('Invalid Mobile MCP screenshot content')
        images = [row for row in blocks if type(row) is dict and row.get('type') == 'image']
        if (len(images) != 1 or blocks[-1] is not images[0]
                or any(type(row) is not dict or row.get('type') not in ('text', 'image') for row in blocks)):
            raise MCPTransportError('Mobile MCP screenshot requires one final image block')
        if len(blocks) == 2 and type(blocks[0].get('text')) is not str:
            raise MCPTransportError('Invalid Mobile MCP screenshot mapping metadata')
        item = images[0]
        if item.get('mimeType') not in ('image/png', 'image/jpeg') or type(item.get('data')) is not str or len(item['data']) > 1800000:
            raise MCPTransportError('Mobile MCP screenshot exceeds the image budget')
        try:
            raw = base64.b64decode(item['data'], validate=True)
        except (ValueError, TypeError):
            raise MCPTransportError('Invalid Mobile MCP screenshot encoding') from None
        preparer = self._image_preparer
        if preparer is None:
            from .computer_adapters import prepare_computer_image
            preparer = prepare_computer_image
        # One actual capture generates both variants. Logical screen coordinates
        # are never inferred from screenshot pixels or prose ratio instructions.
        return preparer(raw, item['mimeType'], checkpoint=checkpoint)

    def _observe(self, image, checkpoint):
        if type(image) is not bool:
            raise MCPTransportError('Mobile observation image option must be Bool')
        self._verify_device(checkpoint)
        viewport = self._screen(checkpoint)
        rows, fingerprint = self._read_tree(checkpoint)
        targets = []
        mapping = {}
        snapshot_lines = []
        for row in rows:
            # This is a serialized structured accessibility description, not an
            # instruction parsed for command keywords.
            box = row['coordinates']
            visible = (box['width'] > 0 and box['height'] > 0 and box['x'] < viewport['width']
                       and box['y'] < viewport['height'] and box['x'] + box['width'] > 0
                       and box['y'] + box['height'] > 0)
            description = {key: value for key, value in row.items() if key != 'ref'}
            if not visible or row.get('enabled') is False or len(targets) >= 40:
                snapshot_lines.append(json.dumps(description, ensure_ascii=False, separators=(',', ':')))
                continue
            ref = 'e' + str(len(targets) + 1)
            text = next((row[key] for key in ('label', 'text', 'name', 'value', 'identifier') if row.get(key)), '')[:250]
            target = {'ref': ref, 'tag': row['type'][:100], 'text': text, 'bbox': dict(box)}
            targets.append(target)
            mapping[ref] = row
            description['targetRef'] = ref
            snapshot_lines.append(json.dumps(description, ensure_ascii=False, separators=(',', ':')))
        snapshot = '\n'.join(snapshot_lines)
        images = self._images(checkpoint) if image else None
        # Snapshot/image reads are serialized but not an OS-atomic capture. A
        # second accessibility read rejects a screen changed during capture.
        if image:
            _, after = self._read_tree(checkpoint)
            if after != fingerprint:
                raise MCPTransportError('Mobile screen changed during observation; observe again')
        checkpoint()
        self._snapshot_id += 1
        self._targets = mapping
        self._latest = {'sessionId': self._session_id, 'controlEpoch': 1,
                        'snapshotId': self._snapshot_id, 'fingerprint': fingerprint,
                        'viewport': viewport}
        self._cursor = {'x': min(self._cursor['x'], viewport['width'] - 1),
                        'y': min(self._cursor['y'], viewport['height'] - 1)}
        result = {'sessionId': self._session_id, 'controlEpoch': 1, 'snapshotId': self._snapshot_id,
                  'url': '', 'title': 'Mobile device',
                  'viewport': viewport, 'cursor': dict(self._cursor),
                  'truncated': {'text': len(snapshot) > 12000, 'targets': len(rows) > len(targets)},
                  'snapshot': snapshot[:12000], 'targets': targets}
        if images is not None:
            result['_images'] = images
        return result

    def _target(self, command, checkpoint):
        row = self._targets.get(command.get('ref'))
        if row is None:
            raise MCPTransportError('Mobile target is not in the current observation')
        _, current = self._read_tree(checkpoint)
        if current != self._latest['fingerprint']:
            raise MCPTransportError('Mobile accessibility tree changed; observe again')
        ref = row.get('ref')
        if ref is not None:
            return {'ref': ref}
        box = row['coordinates']
        # Old platform adapters have no native refs. Comparing the complete
        # accessibility fingerprint prevents silent use of stale geometry, but
        # the OS can still change after this check (documented TOCTOU boundary).
        viewport = self._latest['viewport']
        x = max(0, min(viewport['width'] - 1, box['x'] + box['width'] / 2))
        y = max(0, min(viewport['height'] - 1, box['y'] + box['height'] / 2))
        self._cursor = {'x': x, 'y': y}
        return {'x': x, 'y': y}

    def call(self, command, checkpoint=lambda: None):
        if type(command) is not dict or type(command.get('action')) is not str:
            raise MCPTransportError('Invalid Mobile Computer command')
        action = command['action']
        if action == 'observe':
            if set(command) - {'action', 'image'}:
                raise MCPTransportError('Unexpected Mobile observation fields')
            return self._observe(command.get('image', True), checkpoint)
        if action not in self.ACTIONS:
            raise MCPTransportError('Mobile backend does not implement this action semantic')
        fields = {'navigate': {'url'}, 'click': {'ref'}, 'input': {'ref', 'text'},
                  'button': {'button'}, 'launch': {'packageName'}, 'swipe': {'direction', 'distance'}}[action]
        internal = {'_observation', '_capture', 'image'}
        if 'ref' in fields:
            internal |= {'sessionId', 'controlEpoch', 'snapshotId'}
        if not fields <= set(command) or set(command) - fields - internal - {'action'}:
            raise MCPTransportError('Unexpected Mobile action fields or device override')
        if type(command.get('image', True)) is not bool or '_capture' in command and command['_capture'] is not True:
            raise MCPTransportError('Invalid Mobile internal capture metadata')
        identity = command.get('_observation')
        if (type(identity) is not list or len(identity) != 3 or type(identity[0]) is not str
                or type(identity[1]) is not int or type(identity[2]) is not int):
            raise MCPTransportError('Invalid Mobile observation identity')
        if 'ref' in fields and (type(command['ref']) is not str or not 1 <= len(command['ref']) <= 128):
            raise MCPTransportError('Mobile action requires a bounded target reference')
        latest = self._latest
        if latest is None or identity != [latest[key] for key in ('sessionId', 'controlEpoch', 'snapshotId')]:
            raise MCPTransportError('Mobile action requires its current observation identity')
        for key in ('sessionId', 'controlEpoch', 'snapshotId'):
            if key in command and (type(command[key]) is not type(latest[key]) or command[key] != latest[key]):
                raise MCPTransportError('Mobile action internal identity does not match its observation')
        target = self._target(command, checkpoint) if action in ('click', 'input') else None
        # Invalidating before dispatch prevents aliases from replaying this
        # admission even when its remote effect or subsequent capture fails.
        self._latest = None
        self._targets = {}
        if action == 'navigate':
            from .computer_schema import validate_url
            url = validate_url(command.get('url'))
            self._tool('mobile_open_url', {'url': url}, checkpoint, mutation=True)
        elif action == 'click':
            self._tool('mobile_click_on_screen_at_coordinates', target, checkpoint, mutation=True)
        elif action == 'input':
            text = command.get('text')
            if type(text) is not str or len(text) > 100000:
                raise MCPTransportError('Mobile input requires bounded text')
            self._tool('mobile_click_on_screen_at_coordinates', target, checkpoint, mutation=True)
            # Append is deliberate. There is no pretend fill/clear command.
            self._tool('mobile_type_keys', {'text': text, 'submit': False}, checkpoint, mutation=True)
        elif action == 'button':
            button = command.get('button')
            if button not in ('HOME', 'BACK', 'VOLUME_UP', 'VOLUME_DOWN', 'ENTER', 'BACKSPACE',
                              'APP_SWITCH', 'POWER', 'LOCK', 'DPAD_CENTER', 'DPAD_UP',
                              'DPAD_DOWN', 'DPAD_LEFT', 'DPAD_RIGHT'):
                raise MCPTransportError('Unsupported Mobile hardware button')
            self._tool('mobile_press_button', {'button': button}, checkpoint, mutation=True)
        elif action == 'launch':
            name = command.get('packageName')
            if type(name) is not str or not 1 <= len(name) <= 256 or any(ord(c) < 32 for c in name):
                raise MCPTransportError('Mobile launch requires a bounded package identifier')
            self._tool('mobile_launch_app', {'packageName': name}, checkpoint, mutation=True)
        else:
            direction, distance = command.get('direction'), command.get('distance')
            if direction not in ('up', 'down', 'left', 'right') or type(distance) is not int or not 1 <= distance <= 10000:
                raise MCPTransportError('Mobile swipe requires direction and bounded Int distance')
            # Explicit coordinates are required upstream for distance to apply.
            viewport = latest['viewport']
            self._tool('mobile_swipe_on_screen', {'direction': direction, 'distance': distance,
                       'x': viewport['width'] // 2, 'y': viewport['height'] // 2}, checkpoint, mutation=True)
        return self._observe(command.get('image', True), checkpoint)

    def close(self):
        self._closed = True
        self._latest = None
        self._targets = {}
        if self._client is not None:
            self._client.close()


def mobile_mcp_computer(name, endpoint, device, *, token_env=None, timeout=15.0, resource_id=None):
    """Cold trusted registration; language source selects only ``name``.

    Endpoint, device id and token-variable name remain host-private. Bindings
    reaching one physical device through different servers should explicitly
    share a trusted resource_id; remote aliases cannot be inferred without I/O.
    """
    from .computer_adapters import ComputerSpec
    # Option validation is intentionally cold: no socket, token lookup, device
    # enumeration, upstream installation or client process is started here.
    MobileMCP(endpoint, device, token_env=token_env, timeout=timeout)
    parsed = urllib.parse.urlsplit(endpoint)
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    canonical = [parsed.scheme, parsed.hostname.lower(), port, parsed.path or '/', device]
    identity = resource_id if resource_id is not None else 'mobile-mcp:' + hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    def factory(context):
        context.checkpoint()
        return MobileMCP(endpoint, device, token_env=token_env, timeout=timeout)
    return ComputerSpec(name, factory,
                        description='Android/iOS native app control; input appends to the focused target',
                        profile='mobile-mcp/2025-11-25', resource_id=identity, actions=MobileMCP.ACTIONS)
