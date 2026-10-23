"""UART framing and bounded report execution for the RP2040 bridge.

Importable with stub report devices for software tests. No hardware at import.
"""
import json
from hid_protocol import PROTOCOL, MAX_FRAME, validate_request


class Bridge:
    def __init__(self, keyboard, mouse):
        self.keyboard, self.mouse = keyboard, mouse
        self.pending = bytearray()
        self.discarding = False
        self.last_byte_at = None
        self.recent = []

    def release(self):
        failure = None
        for device, report in ((self.keyboard, bytes(8)), (self.mouse, bytes(4))):
            try:
                device.send_report(report)
            except Exception as error:
                failure = error
        if failure is not None:
            raise failure

    def execute(self, spec):
        action = spec['action']
        try:
            if action == 'key_tap':
                keys = spec['keys']
                self.keyboard.send_report(bytes([spec['modifiers'], 0] + keys + [0] * (6 - len(keys))))
            elif action == 'mouse_move':
                self.mouse.send_report(bytes([0, spec['x'] & 255, spec['y'] & 255, spec['wheel'] & 255]))
            elif action == 'mouse_click':
                self.mouse.send_report(bytes([spec['button'], 0, 0, 0]))
            elif action != 'release':
                raise ValueError('Unknown action')
        finally:
            # A report sequence never intentionally leaves a key/button held.
            self.release()

    def feed(self, chunk, now):
        replies = []
        if self.last_byte_at is not None and now - self.last_byte_at > 0.5 and (self.pending or self.discarding):
            self.pending = bytearray()
            self.discarding = False
            self.release()
        if not chunk:
            return replies
        self.last_byte_at = now
        for byte in chunk:
            if byte == 10:
                if self.discarding:
                    self.pending = bytearray()
                    self.discarding = False
                    continue
                line = bytes(self.pending)
                self.pending = bytearray()
                try:
                    request = validate_request(json.loads(line))
                except (ValueError, UnicodeError):
                    self.release()
                    continue
                identity = (request['session'], request['id'])
                status = 'rejected'
                if identity not in self.recent:
                    self.recent.append(identity)
                    if len(self.recent) > 64:
                        self.recent.pop(0)
                    try:
                        self.execute(request['spec'])
                        status = 'acknowledged'
                    except Exception:
                        # execute already attempted release of both reports.
                        pass
                replies.append((json.dumps({'protocol': PROTOCOL, 'session': request['session'],
                    'id': request['id'], 'status': status}) + '\n').encode('ascii'))
            elif not self.discarding:
                self.pending.append(byte)
                if len(self.pending) > MAX_FRAME:
                    self.pending = bytearray()
                    self.discarding = True
                    self.release()
        return replies
