import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from chaos.device_serial import SerialHID, DeviceTransportError, serial_device, load_device_registry
from chaos.hid_protocol import PROTOCOL, MAX_FRAME, validate_action
from chaos import Registry


class Context:
    def __init__(self, checkpoint=None):
        self.deadline = time.monotonic() + 2
        self.check = checkpoint
    def checkpoint(self):
        if self.check:
            self.check()


class Port:
    def __init__(self, responder=None, partial=10000, close_error=False):
        self.written = bytearray()
        self.readable = bytearray()
        self.requests = []
        self.responder = responder or self.ack
        self.partial = partial
        self.closed = False
        self.close_error = close_error
    @staticmethod
    def ack(request):
        return {'protocol': PROTOCOL, 'session': request['session'], 'id': request['id'], 'status': 'acknowledged'}
    def write(self, value):
        count = min(len(value), self.partial)
        self.written.extend(value[:count])
        if b'\n' in self.written:
            line, _, rest = self.written.partition(b'\n')
            self.written = bytearray(rest)
            request = json.loads(line)
            self.requests.append(request)
            response = self.responder(request)
            if response is not None:
                self.readable.extend(response if type(response) is bytes else json.dumps(response).encode() + b'\n')
        return count
    def read(self, size):
        result = bytes(self.readable[:size])
        del self.readable[:size]
        return result
    def close(self):
        self.closed = True
        if self.close_error:
            raise RuntimeError('close failure')


class DeviceSerialTests(unittest.TestCase):
    def adapter(self, port, timeout=0.1):
        return SerialHID('fixture', timeout=timeout, serial_factory=lambda **kw: port)

    def test_partial_writes_form_exactly_one_request(self):
        port = Port(partial=7)
        bridge = self.adapter(port)
        receipt = bridge.handle('one', {'action': 'key_tap', 'keys': [4]}, Context())
        self.assertEqual(receipt, {'protocol': PROTOCOL, 'id': 'one', 'status': 'acknowledged'})
        self.assertEqual(len(port.requests), 1)
        self.assertEqual(port.requests[0]['spec']['modifiers'], 0)
        bridge.close()
        self.assertTrue(port.closed)

    def test_late_ack_cannot_acknowledge_another_connection(self):
        def response(request):
            old = Port.ack(request);old['session'] = 'previous'
            return json.dumps(old).encode() + b'\n' + json.dumps(Port.ack(request)).encode() + b'\n'
        port = Port(response)
        result = self.adapter(port).handle('one', {'action': 'release'}, Context())
        self.assertEqual(result['id'], 'one')
        self.assertEqual(len(port.requests), 1)

    def test_stale_only_ack_times_out_and_never_retries(self):
        def response(request):
            result = Port.ack(request);result['id'] = 'old';return result
        port = Port(response)
        bridge = self.adapter(port, 0.05)
        with self.assertRaises(DeviceTransportError):
            bridge.handle('one', {'action': 'release'}, Context())
        self.assertTrue(port.closed)
        self.assertEqual(len(port.requests), 1)
        with self.assertRaises(DeviceTransportError):
            bridge.handle('two', {'action': 'release'}, Context())
        self.assertEqual(len(port.requests), 1)

    def test_duplicate_request_identity_rejected_before_write(self):
        port = Port();bridge = self.adapter(port)
        bridge.handle('same', {'action': 'release'}, Context())
        with self.assertRaises(DeviceTransportError):
            bridge.handle('same', {'action': 'release'}, Context())
        self.assertEqual(len(port.requests), 1)

    def test_malformed_oversized_and_rejected_ack_close_transport(self):
        for response in (b'not-json\n', b'x' * (MAX_FRAME + 1), {'protocol': PROTOCOL}, None):
            with self.subTest(response_type=type(response).__name__):
                port = Port(lambda request: response);bridge = self.adapter(port, 0.05)
                with self.assertRaises(DeviceTransportError):
                    bridge.handle('one', {'action': 'release'}, Context())
                self.assertTrue(port.closed)
                self.assertEqual(len(port.requests), 1)

    def test_cancellation_before_write_does_not_dispatch(self):
        class Cancel(BaseException):pass
        signal = Cancel('cancel')
        def cancel():raise signal
        port = Port(close_error=True);bridge = self.adapter(port)
        with self.assertRaises(Cancel) as caught:
            bridge.handle('one', {'action': 'release'}, Context(cancel))
        self.assertIs(caught.exception, signal)
        self.assertFalse(port.requests)
        self.assertTrue(port.closed)

    def test_cancellation_after_write_is_preserved_even_if_close_fails(self):
        class Cancel(BaseException):pass
        signal = Cancel('cancel');port = Port(close_error=True)
        def checkpoint():
            if port.requests:raise signal
        with self.assertRaises(Cancel) as caught:
            self.adapter(port).handle('one', {'action': 'release'}, Context(checkpoint))
        self.assertIs(caught.exception, signal)
        self.assertEqual(len(port.requests), 1)
        self.assertTrue(port.closed)

    def test_invalid_actions_do_not_write(self):
        invalid = [True, {'action':'key_tap','keys':[True]}, {'action':'key_tap','keys':[4,4]},
                   {'action':'mouse_move','x':128,'y':0}, {'action':'mouse_click','button':3},
                   {'action':'release','script':'anything'}, {'action':'type','text':'hello'}]
        port = Port();bridge = self.adapter(port)
        for value in invalid:
            with self.assertRaises(ValueError):bridge.handle('one',value,Context())
        self.assertFalse(port.requests)

    def test_serial_aliases_have_same_resource_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'port';path.touch()
            alias = Path(folder)/'alias';alias.symlink_to(path)
            self.assertEqual(serial_device('first',str(path)).resource_id, serial_device('second',str(alias)).resource_id)

    def test_config_is_atomic_and_does_not_open_ports(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'devices.json';registry=Registry()
            path.write_text(json.dumps({'devices':[{'name':'a','port':'missing'},{'name':'a','port':'also-missing'}]}))
            with self.assertRaises(ValueError):load_device_registry(path,registry)
            self.assertEqual(dict(registry.device_snapshot()),{})
            path.write_text(json.dumps({'devices':[{'name':'a','port':'missing'}]}))
            self.assertIs(load_device_registry(path,registry),registry)
            self.assertIn('a',registry.device_snapshot())
            self.assertEqual(len(registry.snapshot()),2)

    def test_config_rejects_oversize_and_executable_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'devices.json'
            for content in (' ' * 64001, json.dumps({'devices':[{'name':'a','port':'missing','module':'os'}]})):
                path.write_text(content)
                with self.assertRaises(ValueError):load_device_registry(path)

    @unittest.skipUnless(os.name == 'posix' and importlib.util.find_spec('serial'), 'Optional pyserial and POSIX PTY required')
    def test_real_pyserial_pty_roundtrip(self):
        import pty
        master,slave=pty.openpty();path=os.ttyname(slave)
        errors=[];received=[]
        bridge=SerialHID(path,timeout=1)
        def firmware():
            try:
                buffer=b''
                while b'\n' not in buffer:buffer+=os.read(master,128)
                request=json.loads(buffer);received.append(request)
                os.write(master,json.dumps(Port.ack(request)).encode()+b'\n')
            except BaseException as error:errors.append(error)
        worker=threading.Thread(target=firmware,daemon=True);worker.start()
        try:
            result=bridge.handle('pty',{'action':'mouse_move','x':3,'y':-4},Context())
            self.assertEqual(result['status'],'acknowledged')
            worker.join(2)
            self.assertFalse(worker.is_alive());self.assertFalse(errors)
            self.assertEqual(received[0]['spec'],{'action':'mouse_move','x':3,'y':-4,'wheel':0})
        finally:
            bridge.close();os.close(master);os.close(slave)


class ReportDevice:
    def __init__(self, fail=False):self.reports=[];self.fail=fail
    def send_report(self, value):
        self.reports.append(value)
        if self.fail and any(value):raise OSError('USB disconnected during report')


class FirmwareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from chaos import hid_protocol
        file=Path(__file__).resolve().parents[1]/'examples/hid_bridge/hid_bridge.py'
        spec=importlib.util.spec_from_file_location('chaos_hid_firmware_test',file)
        module=importlib.util.module_from_spec(spec)
        previous=sys.modules.get('hid_protocol');sys.modules['hid_protocol']=hid_protocol
        try:spec.loader.exec_module(module)
        finally:
            if previous is None:sys.modules.pop('hid_protocol',None)
            else:sys.modules['hid_protocol']=previous
        cls.Bridge=module.Bridge

    def request(self,spec,ident='one'):
        return json.dumps({'protocol':PROTOCOL,'session':'test','id':ident,'spec':spec}).encode()+b'\n'

    def test_keyboard_and_mouse_always_release_after_reports(self):
        for spec in ({'action':'key_tap','keys':[4],'modifiers':2}, {'action':'mouse_click','button':1},
                     {'action':'mouse_move','x':-2,'y':3}, {'action':'release'}):
            keyboard=ReportDevice();mouse=ReportDevice();bridge=self.Bridge(keyboard,mouse)
            responses=bridge.feed(self.request(spec),0)
            self.assertEqual(json.loads(responses[0])['status'],'acknowledged')
            self.assertEqual(keyboard.reports[-1],bytes(8));self.assertEqual(mouse.reports[-1],bytes(4))

    def test_failed_key_down_still_attempts_release_both_devices(self):
        keyboard=ReportDevice(True);mouse=ReportDevice();bridge=self.Bridge(keyboard,mouse)
        reply=bridge.feed(self.request({'action':'key_tap','keys':[4]}),0)
        self.assertEqual(json.loads(reply[0])['status'],'rejected')
        self.assertEqual(keyboard.reports[-1],bytes(8));self.assertEqual(mouse.reports[-1],bytes(4))

    def test_release_failure_on_one_device_still_releases_the_other(self):
        class Disconnected(ReportDevice):
            def send_report(self, value):
                self.reports.append(value)
                raise OSError('disconnected')
        keyboard=Disconnected();mouse=ReportDevice();bridge=self.Bridge(keyboard,mouse)
        with self.assertRaises(OSError):bridge.release()
        self.assertEqual(mouse.reports,[bytes(4)])

    def test_duplicate_frame_is_rejected_without_replaying_report(self):
        keyboard=ReportDevice();mouse=ReportDevice();bridge=self.Bridge(keyboard,mouse)
        wire=self.request({'action':'key_tap','keys':[4]})
        bridge.feed(wire,0);count=len(keyboard.reports)
        self.assertEqual(json.loads(bridge.feed(wire,0.1)[0])['status'],'rejected')
        self.assertEqual(len(keyboard.reports),count)

    def test_partial_timeout_and_oversize_release_and_recover_framing(self):
        keyboard=ReportDevice();mouse=ReportDevice();bridge=self.Bridge(keyboard,mouse)
        bridge.feed(b'{',0);bridge.feed(b'',0.6)
        self.assertEqual(bridge.pending,bytearray());self.assertEqual(keyboard.reports[-1],bytes(8))
        bridge.feed(b'x'*(MAX_FRAME+4),1);self.assertTrue(bridge.discarding)
        bridge.feed(b'\n',1.1);self.assertFalse(bridge.discarding)
        self.assertEqual(json.loads(bridge.feed(self.request({'action':'release'}),1.2)[0])['status'],'acknowledged')

    def test_malformed_and_extra_fields_do_not_send_nonzero_reports(self):
        keyboard=ReportDevice();mouse=ReportDevice();bridge=self.Bridge(keyboard,mouse)
        self.assertEqual(bridge.feed(b'bad\n',0),[])
        self.assertEqual(bridge.feed(self.request({'action':'release','extra':1}),0.1),[])
        self.assertTrue(all(not any(report) for report in keyboard.reports+mouse.reports))
