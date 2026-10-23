"""OpenBot serial transport with one writer and a lease-local motion watchdog.

Transport completion never acknowledges motion. No desktop input APIs, model
requests, user policy callbacks, frame replay or automatic controller recovery.
"""
from collections import deque
from dataclasses import dataclass, field
import os
import threading
import time
from .robot_protocol import ACTUATORS, PROFILE, MAX_RX_FRAME, control_frame, interval_frame, telemetry_line
from .robots import RobotSpec, validate_channels


class RobotTransportError(RuntimeError):
    pass


def _options(port, baudrate, timeout, telemetry_ms, max_observation_age_ms):
    if type(port) is not str or not 1 <= len(port) <= 1024:
        raise ValueError('Robot serial port must be a host-configured path')
    if type(baudrate) is not int or baudrate not in (9600, 19200, 38400, 57600, 115200, 230400):
        raise ValueError('Unsupported robot serial baudrate')
    if type(timeout) not in (int, float) or not 0.05 <= timeout <= 10:
        raise ValueError('Robot serial timeout must be 0.05..10 seconds')
    for value in (telemetry_ms, max_observation_age_ms):
        if type(value) is not int or not 1 <= value <= 10000:
            raise ValueError('Robot intervals must be 1..10000 ms')


@dataclass
class _Job:
    operation: str
    payload: dict
    context: object
    deadline_ns: int
    done: object = field(default_factory=threading.Event)
    result: object = None
    error: object = None


class SerialOpenBot:
    def __init__(self, port, *, baudrate=115200, timeout=2.0, telemetry_ms=100,
                 max_observation_age_ms=1000, serial_factory=None, context=None):
        _options(port, baudrate, timeout, telemetry_ms, max_observation_age_ms)
        if serial_factory is None:
            try:
                import serial
            except ImportError:
                raise RobotTransportError('Install chaos-intelligence[devices] for OpenBot serial transport') from None
            serial_factory = serial.Serial
        self.timeout = float(timeout)
        self.telemetry_ms = telemetry_ms
        self.max_age_ns = max_observation_age_ms * 1000000
        self._lock = threading.RLock()
        self._jobs = deque()
        self._samples = deque(maxlen=128)
        self._pending = bytearray()
        self._received = 0
        self._sequence = 0
        self._issued = None
        self._ids = set()
        self._closing = False
        self._closed = False
        self._error = None
        self._close_error = None
        self._boundary_known = True
        self._configured = False
        self._motion_deadline = None
        self._heartbeat_next = None
        self._motion_context = None
        self._ever_motion = False
        self._ready = threading.Event()
        self._retired = threading.Event()
        if context is not None:
            context.checkpoint()
        options = {'port': port, 'baudrate': baudrate, 'timeout': 0.01, 'write_timeout': 0.01}
        if os.name == 'posix':
            options['exclusive'] = True
        self.port = serial_factory(**options)
        started = False
        try:
            setup_ns = time.monotonic_ns() + int(self.timeout * 1000000000)
            if context is not None:
                setup_ns = min(setup_ns, int(context.deadline * 1000000000))
            self._thread = threading.Thread(target=self._worker, args=(setup_ns,), name='chaos-openbot-io', daemon=True)
            if context is not None:
                context.checkpoint()
            self._thread.start()
            started = True
            while not self._ready.wait(0.005):
                if context is not None:
                    context.checkpoint()
                if time.monotonic_ns() >= setup_ns:
                    raise RobotTransportError('OpenBot initialization timed out')
            if self._error is not None:
                raise RobotTransportError('OpenBot initialization failed') from self._error
            if context is not None:
                context.checkpoint()
        except BaseException as error:
            try:
                if started:
                    self.close()
                else:
                    self.port.close()
            except BaseException:
                # Trusted factory failure provenance: the host must keep the
                # physical lease when construction acquired an unretired port.
                # Preserve the original cancellation/error object and priority.
                error._chaos_robot_retirement_failed = True
            raise

    def _write(self, frame, deadline_ns, context=None, *, cleanup=False):
        # This method is called only by the IO worker. A failed partial line
        # cannot safely be followed by another line on the legacy firmware.
        if context is not None:
            context.checkpoint()
        if time.monotonic_ns() >= deadline_ns:
            raise RobotTransportError('OpenBot write deadline expired before transmission')
        self._boundary_known = False
        offset = 0
        while offset < len(frame):
            if context is not None:
                context.checkpoint()
            with self._lock:
                stopping = self._closing
            if not cleanup and stopping:
                raise RobotTransportError('OpenBot connection is retiring')
            if time.monotonic_ns() >= deadline_ns:
                raise RobotTransportError('OpenBot write deadline expired; outcome is uncertain')
            count = self.port.write(frame[offset:])
            if type(count) is not int or not 0 <= count <= len(frame) - offset:
                raise RobotTransportError('Invalid robot serial write count')
            offset += count
            if not count:
                time.sleep(0.001)
        self._boundary_known = True

    def _configure(self, deadline_ns):
        self._write(interval_frame('h', 750), deadline_ns)
        for kind in ('v', 's', 'w'):
            self._write(interval_frame(kind, self.telemetry_ms), deadline_ns)
        self._write(control_frame({'left': 0, 'right': 0}), deadline_ns)
        self._configured = True

    def _receive(self):
        chunk = self.port.read(256)
        if type(chunk) not in (bytes, bytearray):
            raise RobotTransportError('Invalid robot serial read result')
        self._received += len(chunk)
        if self._received > 16000000:
            raise RobotTransportError('Robot connection receive budget exhausted')
        self._pending.extend(chunk)
        while b'\n' in self._pending:
            line, _, rest = self._pending.partition(b'\n')
            self._pending = bytearray(rest)
            packet = telemetry_line(line)
            if packet is None:
                continue
            kind, readings = packet
            if kind == 'reset':
                if self._sequence or self._ever_motion:
                    raise RobotTransportError('OpenBot reset invalidated the active observation lease')
                self._configure(time.monotonic_ns() + int(self.timeout * 1000000000))
                continue
            self._sequence += 1
            self._samples.append({'sequence': self._sequence, 'receivedAtNs': time.monotonic_ns(), 'readings': readings})
        if len(self._pending) > MAX_RX_FRAME:
            raise RobotTransportError('Unterminated robot serial receive frame')

    def _stop(self, deadline_ns, context=None, *, cleanup=False):
        # Disable the heartbeat before attempting a stop. An uncertain write
        # cannot accidentally keep earlier motion alive.
        self._motion_deadline = self._heartbeat_next = None
        self._motion_context = None
        self._issued = None
        if not self._boundary_known:
            raise RobotTransportError('Unknown serial frame boundary; controller reset is required')
        self._write(control_frame({'left': 0, 'right': 0}), deadline_ns, context, cleanup=cleanup)

    def _execute(self, job):
        job.context.checkpoint()
        now = time.monotonic_ns()
        if now >= job.deadline_ns:
            raise RobotTransportError('Robot operation deadline expired')
        if job.operation == 'observe':
            while self._samples:
                sample = self._samples.popleft()
                if now - sample['receivedAtNs'] > self.max_age_ns:
                    continue
                self._issued = (sample['sequence'], sample['receivedAtNs'])
                return sample
            return None
        if job.operation == 'stop':
            self._stop(job.deadline_ns, job.context)
            return {'status': 'stopSent'}
        payload = job.payload
        if self._issued != (payload['sequence'], payload['receivedAtNs']):
            raise RobotTransportError('Robot command does not name the latest issued observation')
        if now >= payload['expiresAtNs'] or now - payload['receivedAtNs'] > self.max_age_ns:
            raise RobotTransportError('Robot command expired before its serial byte edge')
        channels = validate_channels(payload['channels'], ACTUATORS)
        # Consume the observation and command identity at the actual writer.
        self._issued = None
        if payload['requestId'] in self._ids or len(self._ids) >= 4096:
            raise RobotTransportError('Robot command identity consumed or connection budget exhausted')
        self._ids.add(payload['requestId'])
        expiry = min(payload['expiresAtNs'], job.deadline_ns)
        self._write(control_frame(channels), expiry, job.context)
        self._ever_motion = True
        self._motion_deadline = expiry
        self._heartbeat_next = now
        self._motion_context = job.context
        return {'status': 'sent'}

    def _worker(self, setup_ns):
        active = None
        try:
            self._configure(setup_ns)
            self._ready.set()
            while True:
                with self._lock:
                    if self._closing:
                        break
                # Read pending reset/telemetry before any admitted drive frame.
                self._receive()
                now = time.monotonic_ns()
                if self._motion_deadline is not None:
                    if now >= self._motion_deadline:
                        self._stop(now + 250000000)
                    elif self._heartbeat_next is not None and now >= self._heartbeat_next:
                        # A trusted operation context rechecks the live project
                        # grant/cancellation at this byte edge, even after the
                        # sending Task completed. Never retain the open context.
                        self._write(interval_frame('h', 750), self._motion_deadline, self._motion_context)
                        self._heartbeat_next = now + 250000000
                if active is None:
                    with self._lock:
                        if self._jobs:
                            active = self._jobs.popleft()
                if active is not None:
                    result = self._execute(active)
                    if result is not None:
                        active.result = result
                        active.done.set()
                        active = None
        except BaseException as error:
            with self._lock:
                self._error = error
                self._closing = True
            if active is not None:
                active.error = error
                active.done.set()
        finally:
            self._motion_deadline = self._heartbeat_next = None
            self._motion_context = None
            self._issued = None
            self._ready.set()
            # The legacy parser is not resynchronizable after a partial line.
            # Never append a stop or heartbeat to an unknown frame boundary.
            try:
                if not self._boundary_known:
                    raise RobotTransportError('Unknown OpenBot frame boundary; stop is unverified and reset is required')
                if self._configured:
                    self._stop(time.monotonic_ns() + 250000000, cleanup=True)
            except BaseException as error:
                self._close_error = error
            try:
                self.port.close()
            except BaseException as error:
                if self._close_error is None:
                    self._close_error = error
            with self._lock:
                self._closed = True
                for job in self._jobs:
                    job.error = self._error or RobotTransportError('Robot connection closed')
                    job.done.set()
                self._jobs.clear()
            if active is not None and not active.done.is_set():
                active.error = self._error or RobotTransportError('Robot connection closed')
                active.done.set()
            self._retired.set()

    def handle(self, operation, payload, context):
        shapes = {'observe': {'timeoutMs'}, 'act': {'requestId', 'observationId', 'sequence', 'receivedAtNs', 'expiresAtNs', 'channels'}, 'stop': {'requestId'}}
        if operation not in shapes or type(payload) is not dict or set(payload) != shapes[operation]:
            raise RobotTransportError('Invalid OpenBot adapter request')
        context.checkpoint()
        if operation == 'observe' and (type(payload['timeoutMs']) is not int or not 1 <= payload['timeoutMs'] <= 10000):
            raise RobotTransportError('Invalid robot observation timeout')
        if operation == 'act':
            channels = validate_channels(payload['channels'], ACTUATORS)
            if (any(type(payload[key]) is not int or payload[key] <= 0 for key in ('sequence', 'receivedAtNs', 'expiresAtNs'))
                    or any(type(payload[key]) is not str or not 1 <= len(payload[key]) <= 128 for key in ('requestId', 'observationId'))):
                raise RobotTransportError('Invalid robot command provenance')
            payload = dict(payload, channels=channels)
        if operation == 'stop' and (type(payload['requestId']) is not str or not 1 <= len(payload['requestId']) <= 128):
            raise RobotTransportError('Invalid robot stop identity')
        seconds = payload['timeoutMs'] / 1000 if operation == 'observe' else self.timeout
        deadline = min(int(context.deadline * 1000000000), time.monotonic_ns() + int(seconds * 1000000000))
        job = _Job(operation, dict(payload), context, deadline)
        with self._lock:
            if self._closing or self._closed or self._error is not None:
                raise RobotTransportError('OpenBot connection is retired') from self._error
            if len(self._jobs) >= 32:
                raise RobotTransportError('Robot operation queue is full')
            self._jobs.append(job)
        try:
            while not job.done.wait(0.005):
                context.checkpoint()
                if time.monotonic_ns() >= deadline:
                    raise RobotTransportError('Robot operation timed out; connection retired')
            context.checkpoint()
            if job.error is not None:
                raise job.error
            return job.result
        except BaseException:
            try:
                self.close()
            except BaseException:
                pass
            raise

    def close(self):
        with self._lock:
            self._closing = True
            self._motion_deadline = self._heartbeat_next = None
            self._motion_context = None
        self._thread.join(self.timeout + 0.5)
        if self._thread.is_alive() or not self._retired.is_set():
            raise RobotTransportError('Robot IO worker did not retire before its close deadline')
        if self._close_error is not None:
            raise RobotTransportError('Robot retirement has an uncertain stop outcome') from self._close_error


def openbot_robot(name, port, *, baudrate=115200, timeout=2.0, telemetry_ms=100, max_observation_age_ms=1000):
    """Trusted registration only. Opening and configuring are cold Robot effects."""
    _options(port, baudrate, timeout, telemetry_ms, max_observation_age_ms)
    resource_id = 'serial:' + os.path.normcase(os.path.realpath(port))
    def factory(context):
        return SerialOpenBot(port, baudrate=baudrate, timeout=timeout, telemetry_ms=telemetry_ms,
                             max_observation_age_ms=max_observation_age_ms, context=context)
    return RobotSpec(name, factory, ACTUATORS, profile=PROFILE, description='OpenBot serial telemetry and normalized actuators',
                     resource_id=resource_id, max_observation_age_ms=max_observation_age_ms)
