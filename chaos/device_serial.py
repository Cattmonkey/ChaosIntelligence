"""Optional serial-to-HID bridge. No retries and no desktop input API.

A completed ACK proves bridge report processing, not application success.
"""
import json
import os
import time
import uuid
from .hid_protocol import PROTOCOL, MAX_FRAME, validate_request


class DeviceTransportError(RuntimeError):
    pass


def _validate_options(port, baudrate, timeout):
    if type(port) is not str or not port or len(port) > 1024:
        raise ValueError('Serial port must be a nonempty host-configured path')
    if type(baudrate) is not int or baudrate not in (9600, 19200, 38400, 57600, 115200, 230400):
        raise ValueError('Unsupported serial baudrate')
    if type(timeout) not in (int, float) or not 0.05 <= timeout <= 10:
        raise ValueError('Serial timeout must be 0.05..10 seconds')


class SerialHID:
    def __init__(self, port, *, baudrate=115200, timeout=2.0, serial_factory=None):
        _validate_options(port, baudrate, timeout)
        if serial_factory is None:
            try:
                import serial
            except ImportError:
                raise DeviceTransportError('Install chaos-intelligence[devices] for serial HID') from None
            serial_factory = serial.Serial
        self.session = uuid.uuid4().hex
        self.timeout = float(timeout)
        self.closed = False
        self._port_closed = False
        self.used_ids = set()
        self.port = serial_factory(port=port, baudrate=baudrate, timeout=0.025, write_timeout=0.025)

    def handle(self, request_id, spec, context):
        if self.closed:
            raise DeviceTransportError('Serial HID connection is closed')
        request = validate_request({'protocol': PROTOCOL, 'session': self.session, 'id': request_id, 'spec': spec})
        if request_id in self.used_ids or len(self.used_ids) >= 4096:
            raise DeviceTransportError('Serial HID request identity consumed or connection budget exhausted')
        self.used_ids.add(request_id)
        wire = json.dumps(request, separators=(',', ':'), allow_nan=False).encode('ascii') + b'\n'
        if len(wire) > MAX_FRAME:
            raise DeviceTransportError('HID frame is too large')
        deadline = min(context.deadline, time.monotonic() + self.timeout)
        def checkpoint():
            context.checkpoint()
            if time.monotonic() >= deadline:
                raise DeviceTransportError('Serial HID acknowledgment timed out; outcome is uncertain')
        try:
            # Partial writes advance only over unwritten bytes. Never resend a frame.
            offset = 0
            while offset < len(wire):
                checkpoint()
                count = self.port.write(wire[offset:])
                if type(count) is not int or not 0 <= count <= len(wire) - offset:
                    raise DeviceTransportError('Invalid serial write result')
                offset += count
                if count == 0:
                    time.sleep(0.001)
            pending = bytearray()
            received = 0
            while True:
                checkpoint()
                chunk = self.port.read(128)
                if type(chunk) not in (bytes, bytearray):
                    raise DeviceTransportError('Invalid serial read result')
                received += len(chunk)
                if received > MAX_FRAME * 16:
                    raise DeviceTransportError('Serial HID receive budget exhausted')
                pending.extend(chunk)
                while b'\n' in pending:
                    line, _, rest = pending.partition(b'\n')
                    pending = bytearray(rest)
                    if len(line) > MAX_FRAME:
                        raise DeviceTransportError('Oversized serial HID acknowledgment')
                    try:
                        response = json.loads(line)
                    except (ValueError, UnicodeError):
                        raise DeviceTransportError('Malformed serial HID acknowledgment') from None
                    if (type(response) is not dict or set(response) != {'protocol', 'session', 'id', 'status'}
                            or response['protocol'] != PROTOCOL):
                        raise DeviceTransportError('Invalid serial HID acknowledgment envelope')
                    if response['session'] != self.session or response['id'] != request_id:
                        continue
                    checkpoint()
                    if response['status'] != 'acknowledged':
                        raise DeviceTransportError('Bridge rejected the HID request; outcome is uncertain')
                    return {'protocol': PROTOCOL, 'id': request_id, 'status': 'acknowledged'}
                if len(pending) > MAX_FRAME:
                    raise DeviceTransportError('Unterminated serial HID acknowledgment')
                if not chunk:
                    time.sleep(0.001)
        except BaseException:
            try:
                self.close()
            except BaseException:
                pass  # Cleanup must not replace the original cancellation/control signal.
            raise

    def close(self):
        self.closed = True
        if not self._port_closed:
            self.port.close()
            self._port_closed = True


def serial_device(name, port, *, baudrate=115200, timeout=2.0):
    """Trusted host factory; source programs select only the registered name."""
    from .devices import DeviceSpec
    _validate_options(port, baudrate, timeout)
    # Canonicalize symlinks so aliases cannot obtain simultaneous Engine leases.
    resource_id = 'serial:' + os.path.normcase(os.path.realpath(port))
    def factory(context):
        context.checkpoint()
        return SerialHID(port, baudrate=baudrate, timeout=timeout)
    return DeviceSpec(name, factory, description='UART bridge to target USB HID',
                      version='1', resource_id=resource_id)


def load_device_registry(path, registry=None):
    """Read trusted serial/Linux HID bindings; registration grants no capability."""
    from pathlib import Path
    from .extensions import Registry
    with Path(path).open('rb') as stream:
        raw = stream.read(64001)
    if len(raw) > 64000:
        raise ValueError('Device configuration exceeds 64KB')
    document = json.loads(raw)
    if type(document) is not dict or set(document) != {'devices'} or type(document['devices']) is not list or len(document['devices']) > 32:
        raise ValueError('Expected {devices: [...]} with at most 32 devices')
    registry = registry or Registry()
    specs = []
    for row in document['devices']:
        if type(row) is not dict:
            raise ValueError('Device configuration entry must be a record')
        transport = row.get('transport', 'serial')
        options = {key: value for key, value in row.items() if key != 'transport'}
        if transport == 'serial':
            if not {'name', 'port'} <= set(options) or set(options) - {'name', 'port', 'baudrate', 'timeout'}:
                raise ValueError('Invalid serial device configuration')
            specs.append(serial_device(**options))
        elif transport == 'linux-hid':
            from .device_linux import linux_hid_device
            if not {'name', 'keyboard', 'mouse'} <= set(options) or set(options) - {'name', 'keyboard', 'mouse', 'timeout'}:
                raise ValueError('Invalid Linux HID device configuration')
            specs.append(linux_hid_device(**options))
        else:
            raise ValueError('Unknown device transport')
    names = [spec.name for spec in specs]
    if len(set(names)) != len(names) or set(names) & set(registry.device_snapshot()):
        raise ValueError('Duplicate or already registered device name')
    for spec in specs:
        registry.register_device(spec)
    return registry
