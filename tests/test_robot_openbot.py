"""Actual adapter IO/watchdog tests use owned fake serial ports and one PTY."""
from collections import deque
import os
import threading
import time
import unittest
from unittest.mock import patch
from chaos.robot_openbot import SerialOpenBot, RobotTransportError, openbot_robot


class Context:
    def __init__(self):
        self.deadline = time.monotonic() + 5
        self.cancelled = False
        self.revoked = False
    def checkpoint(self):
        if self.cancelled or self.revoked and threading.current_thread().name == 'chaos-openbot-io':
            raise RuntimeError('operation revoked or cancelled')


class Port:
    def __init__(self, **options):
        self.options = options; self.lock = threading.Condition(); self.input = deque(); self.wire = bytearray()
        self.closed = False; self.close_count = 0; self.fail_partial = False; self.partial_started = False
        self.read_gate = None; self.read_entered = threading.Event()
    def inject(self, data):
        with self.lock:
            self.input.append(data); self.lock.notify_all()
    def read(self, count):
        gate = self.read_gate
        if gate is not None:
            self.read_entered.set(); gate.wait(0.5)
        with self.lock:
            if not self.input:
                self.lock.wait(0.005)
            if not self.input:
                return b''
            data = self.input.popleft(); result = data[:count]
            if data[count:]: self.input.appendleft(data[count:])
            return result
    def write(self, data):
        with self.lock:
            if self.closed: raise RuntimeError('port closed')
            if self.fail_partial and data.startswith(b'c') and data != b'c0,0\n':
                self.wire.extend(data[:2]); self.partial_started = True; return 2
            if self.partial_started:
                raise RuntimeError('unknown partial write failure')
            self.wire.extend(data); return len(data)
    def close(self):
        with self.lock:
            self.close_count += 1; self.closed = True; self.lock.notify_all()


class OpenBotAdapterTests(unittest.TestCase):
    def make(self, **options):
        ports = []
        def factory(**kw):
            port = Port(**kw); ports.append(port); return port
        context = Context()
        adapter = SerialOpenBot('/test/openbot', serial_factory=factory, context=context, timeout=0.2, **options)
        self.addCleanup(lambda: self.clean(adapter))
        return adapter, ports[0], context
    @staticmethod
    def clean(adapter):
        try: adapter.close()
        except RobotTransportError: pass
    def observe(self, adapter, port, context, data=b'w0.00,0.00\n'):
        port.inject(data)
        return adapter.handle('observe', {'timeoutMs': 200}, context)
    @staticmethod
    def command(sample, request='drive', ttl=100):
        return {'requestId': request, 'observationId': 'sample', 'sequence': sample['sequence'],
                'receivedAtNs': sample['receivedAtNs'], 'expiresAtNs': sample['receivedAtNs'] + ttl * 1000000,
                'channels': {'left': 0.5, 'right': -0.5}}

    def test_initialization_is_finite_and_context_is_not_retained(self):
        adapter, port, context = self.make()
        self.assertTrue(bytes(port.wire).startswith(b'h750\nv100\ns100\nw100\nc0,0\n'))
        context.cancelled = True
        time.sleep(0.02)
        sample = self.observe(adapter, port, Context())
        self.assertEqual(sample['sequence'], 1)
        self.assertEqual(sample['readings']['leftRpm']['unit'], 'rpm')

    def test_actual_drive_bytes_and_ttl_stop_survive_completed_call(self):
        adapter, port, context = self.make()
        sample = self.observe(adapter, port, context)
        self.assertEqual(adapter.handle('act', self.command(sample, ttl=80), context), {'status': 'sent'})
        start = bytes(port.wire).count(b'c0,0\n')
        limit = time.monotonic() + 0.5
        while bytes(port.wire).count(b'c0,0\n') == start and time.monotonic() < limit:
            time.sleep(0.005)
        self.assertIn(b'c127,-127\n', bytes(port.wire))
        self.assertGreater(bytes(port.wire).count(b'c0,0\n'), start)
        adapter.close(); self.assertEqual(port.close_count, 1)

    def test_partial_frame_never_gets_stop_resync_or_replay(self):
        adapter, port, context = self.make()
        sample = self.observe(adapter, port, context); before = bytes(port.wire); port.fail_partial = True
        with self.assertRaises(RuntimeError): adapter.handle('act', self.command(sample), context)
        self.assertTrue(adapter._retired.wait(0.5))
        self.assertEqual(bytes(port.wire), before + b'c1')
        with self.assertRaises(RobotTransportError): adapter.close()
        self.assertEqual(port.close_count, 1)

    def test_new_observation_invalidates_earlier_command_and_no_cache_refresh(self):
        adapter, port, context = self.make()
        first = self.observe(adapter, port, context, b'v12.0\n')
        second = self.observe(adapter, port, context, b's30\n')
        self.assertNotIn('voltage', second['readings'])
        self.assertGreater(second['sequence'], first['sequence'])
        with self.assertRaises(RobotTransportError): adapter.handle('act', self.command(first), context)
        self.assertNotIn(b'c127,-127\n', bytes(port.wire))

    def test_banner_only_and_cached_telemetry_cannot_refresh_freshness(self):
        adapter, port, context = self.make()
        sample = self.observe(adapter, port, context, b'v12.0\n')
        port.inject(b'vmin\nvlow\nf1,0\n')
        with self.assertRaises(RobotTransportError): adapter.handle('observe', {'timeoutMs': 30}, context)
        self.assertEqual(sample['readings'], {'voltage': {'value': 12.0, 'unit': 'V'}})

    def test_reset_after_observation_retires_authority(self):
        adapter, port, context = self.make()
        sample = self.observe(adapter, port, context)
        port.inject(b'r\n')
        self.assertTrue(adapter._retired.wait(0.5))
        with self.assertRaises(RobotTransportError): adapter.handle('act', self.command(sample), context)
        self.assertNotIn(b'c127,-127\n', bytes(port.wire))

    def test_revocation_at_queued_byte_edge_prevents_motion(self):
        adapter, port, context = self.make()
        sample = self.observe(adapter, port, context)
        gate = threading.Event(); port.read_gate = gate
        self.assertTrue(port.read_entered.wait(0.5))
        errors = []
        def drive():
            try: adapter.handle('act', self.command(sample, ttl=500), context)
            except BaseException as error: errors.append(error)
        thread = threading.Thread(target=drive); thread.start()
        context.revoked = True; gate.set(); thread.join(1)
        self.assertFalse(thread.is_alive()); self.assertTrue(errors)
        self.assertNotIn(b'c127,-127\n', bytes(port.wire))

    def test_cancel_between_port_open_and_worker_start_closes_port(self):
        context = Context(); ports = []
        def factory(**options):
            port = Port(**options); ports.append(port); context.cancelled = True; return port
        with self.assertRaises(RuntimeError):
            SerialOpenBot('/test/openbot', serial_factory=factory, context=context)
        self.assertEqual(ports[0].close_count, 1)
        self.assertEqual(bytes(ports[0].wire), b'')

    def test_thread_start_failure_closes_port(self):
        ports = []
        def factory(**options):
            port = Port(**options); ports.append(port); return port
        with patch('chaos.robot_openbot.threading.Thread.start', side_effect=RuntimeError('start failed')):
            with self.assertRaises(RuntimeError): SerialOpenBot('/test/openbot', serial_factory=factory)
        self.assertEqual(ports[0].close_count, 1)

    def test_failed_constructor_retirement_preserves_original_error_and_marker(self):
        original = RuntimeError('start failed'); ports = []
        class Unretired(Port):
            def close(self):
                self.close_count += 1
                raise RuntimeError('physical retirement failed')
        def factory(**options):
            port = Unretired(**options); ports.append(port); return port
        with patch('chaos.robot_openbot.threading.Thread.start', side_effect=original):
            with self.assertRaises(RuntimeError) as caught:
                SerialOpenBot('/test/openbot', serial_factory=factory)
        self.assertIs(caught.exception, original)
        self.assertIs(caught.exception._chaos_robot_retirement_failed, True)
        self.assertEqual(ports[0].close_count, 1)
        self.assertFalse(ports[0].closed)
        self.assertEqual(bytes(ports[0].wire), b'')

    def test_registration_is_cold_and_matches_hid_physical_identity(self):
        from chaos.device_serial import serial_device
        spec = openbot_robot('bot', '/tmp/../tmp/robot-port')
        self.assertEqual(spec.resource_id, serial_device('keys', '/tmp/robot-port').resource_id)
        self.assertEqual(spec.describe()['profile'], 'openbot-serial/1')
        self.assertNotIn('port', spec.describe())

    @unittest.skipUnless(os.name == 'posix', 'PTY is a POSIX fixture')
    def test_real_serial_bytes_over_owned_pseudoterminal(self):
        try: import serial
        except ImportError: self.skipTest('optional serial transport extra unavailable')
        master, slave = os.openpty(); path = os.ttyname(slave); os.set_blocking(master, False)
        stop = threading.Event(); wire = bytearray(); pending = bytearray()
        def emulate():
            while not stop.is_set():
                try: data = os.read(master, 256)
                except BlockingIOError: time.sleep(0.002); continue
                except OSError: break
                wire.extend(data); pending.extend(data)
                while b'\n' in pending:
                    line, _, rest = pending.partition(b'\n'); pending[:] = rest
                    if line == b'w100': os.write(master, b'w0.00,0.00\n')
        thread = threading.Thread(target=emulate); thread.start()
        adapter = None
        try:
            adapter = SerialOpenBot(path, context=Context(), timeout=0.3)
            sample = adapter.handle('observe', {'timeoutMs': 300}, Context())
            self.assertEqual(adapter.handle('act', self.command(sample, ttl=100), Context()), {'status': 'sent'})
            adapter.close(); adapter = None
            limit = time.monotonic() + 0.2
            while b'c127,-127\n' not in wire and time.monotonic() < limit: time.sleep(0.002)
            self.assertIn(b'h750\n', bytes(wire)); self.assertIn(b'c127,-127\n', bytes(wire))
        finally:
            if adapter is not None: self.clean(adapter)
            stop.set(); thread.join(1); os.close(master); os.close(slave)
