"""Configfs fixtures only: these tests never configure a host or emit USB input."""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from chaos.hid_gadget import (CONFIGURATION, FUNCTIONS, GADGET_NAME, GadgetError,
                              GadgetManager, KEYBOARD_REPORT_DESCRIPTOR,
                              MOUSE_REPORT_DESCRIPTOR, main, resolve_device_nodes)


class FakeConfigFS(GadgetManager):
    """Emulate kernel-created attributes, not production Linux filesystems."""
    def __init__(self, parent):
        parent = Path(parent)
        super().__init__(parent / 'config', parent / 'udc', parent / 'dev')
        for folder in (self.root, self.udc_root, self.dev_root):
            folder.mkdir()
        (self.udc_root / 'controller.0').mkdir()
        (self.udc_root / 'controller.0/function').write_text('')
        self.writes = []
        self.fail_attribute = None
        self.kernel_entries = {}

    def _require_linux(self):
        pass

    def _mkdir(self, path):
        path = Path(path)
        path.mkdir()
        relative = path.relative_to(self.path).as_posix()
        dirs, attributes = [], {}
        if relative == '.':
            dirs = ['strings', 'configs', 'functions', 'os_desc']
            attributes = dict.fromkeys(('UDC', 'bcdUSB', 'bcdDevice', 'idVendor', 'idProduct',
                                        'bDeviceClass', 'bDeviceSubClass', 'bDeviceProtocol',
                                        'bMaxPacketSize0'), '0')
            attributes['UDC'] = ''
        elif relative == 'strings/0x409':
            attributes = dict.fromkeys(('serialnumber', 'manufacturer', 'product'), '')
        elif relative == 'configs/c.1':
            dirs = ['strings']
            attributes = {'MaxPower': '0', 'bmAttributes': '0x80'}
        elif relative == 'configs/c.1/strings/0x409':
            attributes = {'configuration': ''}
        elif relative in ('functions/hid.keyboard', 'functions/hid.mouse'):
            attributes = {'subclass': '0', 'protocol': '0', 'report_length': '0',
                          'report_desc': '', 'dev': '42:' + ('7' if relative.endswith('keyboard') else '19'),
                          'no_out_endpoint': '0', 'interval': '4'}
        for name in dirs:
            (path / name).mkdir()
        for name, value in attributes.items():
            (path / name).write_text(value)
        if relative == '.':
            (path / 'os_desc/use').write_text('0')
        self.kernel_entries[path] = (dirs, tuple(attributes))

    def _write(self, path, value):
        if str(path).endswith(str(self.fail_attribute)):
            raise OSError('injected write failure')
        super()._write(path, value)
        self.writes.append((str(path), value))

    def _rmdir(self, path):
        # configfs attributes vanish with their group; emulate that behavior.
        path = Path(path)
        dirs, attributes = self.kernel_entries[path]
        for name in attributes:
            (path / name).unlink()
        for name in dirs:
            if name == 'os_desc':
                (path / name / 'use').unlink()
            (path / name).rmdir()
        path.rmdir()


def descriptor_inputs(descriptor):
    """Small independent HID short-item reader to check declared wire layout."""
    index, page, size, count, minimum, maximum = 0, None, None, None, None, None
    usage_min, usage_max, usages, inputs = None, None, [], []
    while index < len(descriptor):
        prefix = descriptor[index]
        index += 1
        length = (0, 1, 2, 4)[prefix & 3]
        payload = descriptor[index:index + length]
        index += length
        value = int.from_bytes(payload, 'little')
        tag, kind = prefix >> 4, (prefix >> 2) & 3
        if kind == 1:
            if tag == 0: page = value
            elif tag == 1: minimum = int.from_bytes(payload, 'little', signed=True)
            elif tag == 2: maximum = int.from_bytes(payload, 'little', signed=bool(minimum and minimum < 0))
            elif tag == 7: size = value
            elif tag == 9: count = value
        elif kind == 2:
            if tag == 0: usages.append(value)
            elif tag == 1: usage_min = value
            elif tag == 2: usage_max = value
        elif kind == 0:
            if tag == 8:
                inputs.append(dict(page=page, size=size, count=count, minimum=minimum,
                                   maximum=maximum, usageMin=usage_min, usageMax=usage_max,
                                   usages=list(usages), flags=value))
            usage_min, usage_max, usages = None, None, []
    return inputs


class HIDGadgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = FakeConfigFS(self.temp.name)

    def enable(self, **kwargs):
        return self.manager.enable(0x1209, 1, 'test_serial', 100, **kwargs)

    def test_report_descriptors_match_typed_reports(self):
        keyboard = descriptor_inputs(KEYBOARD_REPORT_DESCRIPTOR)
        mouse = descriptor_inputs(MOUSE_REPORT_DESCRIPTOR)
        self.assertEqual(sum(i['size'] * i['count'] for i in keyboard), 64)
        self.assertEqual(sum(i['size'] * i['count'] for i in mouse), 32)
        keys = keyboard[-1]
        self.assertEqual((keys['size'], keys['count'], keys['minimum'], keys['maximum']), (8, 6, 0, 231))
        self.assertEqual((keys['page'], keys['usageMin'], keys['usageMax']), (7, 0, 231))
        buttons = mouse[0]
        self.assertEqual((buttons['page'], buttons['count'], buttons['usageMax']), (9, 5, 5))
        coordinates = mouse[-1]
        self.assertEqual((coordinates['usages'], coordinates['minimum'], coordinates['maximum'], coordinates['flags']),
                         ([0x30, 0x31, 0x38], -127, 127, 6))
        self.assertEqual(FUNCTIONS['hid.mouse'][:3], (0, 0, 4))

    def test_plan_and_status_are_inert(self):
        result = self.manager.plan(0x1209, 1, 'test', 100)
        self.assertTrue(result['ready'])
        self.assertFalse(result['writesPerformed'])
        self.assertEqual(result['selectedUdc'], 'controller.0')
        self.assertFalse(self.manager.status()['present'])
        self.assertEqual(self.manager.writes, [])
        self.assertFalse(self.manager.path.exists())

    def test_no_udc_needs_explicit_preparation(self):
        (self.manager.udc_root / 'controller.0/function').unlink()
        (self.manager.udc_root / 'controller.0').rmdir()
        result = self.manager.plan(1, 2, 'test', 100)
        self.assertFalse(result['ready'])
        self.assertIn('exactly one UDC', ' '.join(result['issues']))
        with self.assertRaises(GadgetError):
            self.enable()
        self.assertFalse(self.manager.path.exists())

    def test_multiple_udcs_never_guess(self):
        (self.manager.udc_root / 'controller.1').mkdir()
        (self.manager.udc_root / 'controller.1/function').write_text('')
        self.assertFalse(self.manager.plan(1, 2, 'test', 100)['ready'])
        result = self.manager.plan(1, 2, 'test', 100, udc='controller.1')
        self.assertTrue(result['ready'])
        self.assertEqual(result['selectedUdc'], 'controller.1')
        self.assertFalse(self.manager.plan(1, 2, 'test', 100, udc='../controller.0')['ready'])

    def test_identity_and_power_validation_precedes_writes(self):
        for vid, pid, serial, power in ((0, 1, 'ok', 100), (1, True, 'ok', 100),
                                      (1, 2, 'a\nb', 100), (1, 2, '../x', 100),
                                      (1, 2, 'ok', 501), (1, 2, 'ok', 99),
                                      (1, 2, 'ok', -2)):
            with self.subTest(vid=vid, pid=pid, serial=serial, power=power):
                with self.assertRaises(GadgetError):
                    self.manager.enable(vid, pid, serial, power)
        self.assertFalse(self.manager.path.exists())
        self.assertEqual(self.manager.writes, [])

    def test_legacy_network_binding_is_detected_before_writes(self):
        function = self.manager.udc_root / 'controller.0/function'
        function.write_text('Ethernet Gadget')
        result = self.manager.plan(1, 2, 'test', 100)
        self.assertFalse(result['ready'])
        self.assertIn('UDC already in use', ' '.join(result['issues']))
        self.assertEqual(self.manager.status()['controllerBindings']['controller.0'], 'Ethernet Gadget')
        with self.assertRaisesRegex(GadgetError, 'Ethernet Gadget'):
            self.enable()
        self.assertFalse(self.manager.path.exists())
        self.assertEqual(function.read_text(), 'Ethernet Gadget')
        self.assertEqual(self.manager.writes, [])

    def test_enable_bind_last_then_disable_exact_owned_tree(self):
        result = self.enable(self_powered=True)
        self.assertTrue(result['owned'])
        self.assertTrue(result['active'])
        self.assertTrue(result['selfPowered'])
        self.assertEqual(result['maxPowerMa'], 100)
        self.assertEqual(result['nodes'], {})  # No physical nodes in fixture.
        self.assertEqual(Path(self.manager.writes[-1][0]).name, 'UDC')
        result = self.manager.disable('test_serial')
        self.assertFalse(result['present'])
        self.assertEqual(list(self.manager.root.iterdir()), [])

    def test_bound_other_gadget_and_unbound_network_are_preserved(self):
        foreign = self.manager.root / 'network'
        foreign.mkdir()
        (foreign / 'UDC').write_text('')
        (foreign / 'functions').mkdir()
        (foreign / 'functions/ncm.usb0').mkdir()
        for bound in ('', 'controller.0'):
            (foreign / 'UDC').write_text(bound)
            with self.assertRaisesRegex(GadgetError, 'network gadget'):
                self.enable()
            self.assertEqual((foreign / 'UDC').read_text(), bound)
        self.assertFalse(self.manager.path.exists())

    def test_existing_own_gadget_is_never_silently_replaced(self):
        self.enable()
        before = list(self.manager.writes)
        with self.assertRaisesRegex(GadgetError, 'already exists'):
            self.enable()
        self.assertEqual(self.manager.writes, before)
        self.assertTrue(self.manager.status()['active'])

    def test_foreign_fixed_name_is_not_disabled(self):
        self.manager.path.mkdir()
        (self.manager.path / 'UDC').write_text('controller.0')
        with self.assertRaises(GadgetError):
            self.manager.disable('test_serial')
        self.assertEqual((self.manager.path / 'UDC').read_text(), 'controller.0')

    def test_serial_mismatch_does_not_unbind(self):
        self.enable()
        with self.assertRaisesRegex(GadgetError, 'Serial does not match'):
            self.manager.disable('someone_else')
        self.assertEqual((self.manager.path / 'UDC').read_text().strip(), 'controller.0')

    def test_modified_descriptor_is_not_unbound(self):
        self.enable()
        (self.manager.path / 'functions/hid.mouse/report_desc').write_bytes(b'changed')
        with self.assertRaisesRegex(GadgetError, 'descriptor mismatch'):
            self.manager.disable('test_serial')
        self.assertEqual((self.manager.path / 'UDC').read_text().strip(), 'controller.0')

    def test_extra_configuration_is_not_unbound(self):
        self.enable()
        (self.manager.path / 'configs/c.2').mkdir()
        with self.assertRaises(GadgetError):
            self.manager.disable('test_serial')
        self.assertEqual((self.manager.path / 'UDC').read_text().strip(), 'controller.0')

    def test_redirected_symlink_is_not_unbound_or_deleted(self):
        self.enable()
        link = self.manager.path / 'configs/c.1/hid.keyboard'
        link.unlink()
        link.symlink_to(self.manager.path / 'functions/hid.mouse')
        with self.assertRaises(GadgetError):
            self.manager.disable('test_serial')
        self.assertTrue(link.is_symlink())
        self.assertEqual((self.manager.path / 'UDC').read_text().strip(), 'controller.0')

    def test_attribute_symlink_is_rejected(self):
        self.enable()
        dev = self.manager.path / 'functions/hid.keyboard/dev'
        dev.unlink()
        dev.symlink_to(self.manager.path / 'idVendor')
        with self.assertRaises(GadgetError):
            self.manager.disable('test_serial')
        self.assertEqual((self.manager.path / 'UDC').read_text().strip(), 'controller.0')

    def test_enable_write_failure_rolls_back_only_created_objects(self):
        unrelated = self.manager.root / 'unused'
        unrelated.mkdir()
        (unrelated / 'UDC').write_text('')
        (unrelated / 'functions').mkdir()
        self.manager.fail_attribute = 'functions/hid.mouse/report_desc'
        with self.assertRaisesRegex(OSError, 'injected'):
            self.enable()
        self.assertFalse(self.manager.path.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual((unrelated / 'UDC').read_text(), '')

    def test_bind_failure_rolls_back_without_changing_foreign_gadget(self):
        original = self.manager._write
        def fail_once(path, value):
            if Path(path).name == 'UDC' and value:
                raise OSError('bind rejected')
            return original(path, value)
        self.manager._write = fail_once
        with self.assertRaisesRegex(OSError, 'bind rejected'):
            self.enable()
        self.assertFalse(self.manager.path.exists())

    def test_failed_unbind_retains_gadget_for_manual_inspection(self):
        self.enable()
        self.manager.fail_attribute = '/UDC'
        with self.assertRaisesRegex(OSError, 'injected'):
            self.manager.disable('test_serial')
        self.assertTrue((self.manager.path / 'configs/c.1/hid.keyboard').is_symlink())
        self.assertTrue(self.manager.path.exists())

    def test_disable_already_unbound_gadget_skips_kernel_enodev(self):
        self.enable()
        (self.manager.path / 'UDC').write_text('')
        self.manager.fail_attribute = '/UDC'
        self.assertFalse(self.manager.disable('test_serial')['present'])

    def test_resolver_uses_kernel_numbers_not_hidg_index(self):
        self.enable()
        for name in ('hidg0', 'hidg42', 'hidg88'):
            (self.manager.dev_root / name).touch()
        original = Path.lstat
        def pretend_character(path):
            if path.parent == self.manager.dev_root:
                minor = {'hidg0': 1, 'hidg42': 7, 'hidg88': 19}[path.name]
                return SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=os.makedev(42, minor))
            return original(path)
        with patch.object(Path, 'lstat', pretend_character):
            nodes = resolve_device_nodes(self.manager.path, self.manager.dev_root)
        self.assertEqual(Path(nodes['keyboard']).name, 'hidg42')
        self.assertEqual(Path(nodes['mouse']).name, 'hidg88')

    def test_resolver_rejects_regular_files_and_ambiguous_aliases(self):
        self.enable()
        (self.manager.dev_root / 'hidg7').touch()
        with self.assertRaises(GadgetError):
            resolve_device_nodes(self.manager.path, self.manager.dev_root)
        (self.manager.dev_root / 'hidg8').touch()
        original = Path.lstat
        def pretend_alias(path):
            if path.parent == self.manager.dev_root:
                return SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=os.makedev(42, 7))
            return original(path)
        with patch.object(Path, 'lstat', pretend_alias), self.assertRaises(GadgetError):
            resolve_device_nodes(self.manager.path, self.manager.dev_root)

    def test_cli_status_and_plan_have_no_system_mutation(self):
        out = io.StringIO()
        with patch('chaos.hid_gadget.GadgetManager', return_value=self.manager), contextlib.redirect_stdout(out):
            self.assertEqual(main(['status']), 0)
        self.assertFalse(json.loads(out.getvalue())['present'])
        out = io.StringIO()
        with patch('chaos.hid_gadget.GadgetManager', return_value=self.manager), contextlib.redirect_stdout(out):
            self.assertEqual(main(['plan', '--vid', '0x1209', '--pid', '1', '--serial', 'test', '--max-power-ma', '100']), 0)
        self.assertTrue(json.loads(out.getvalue())['ready'])
        self.assertEqual(self.manager.writes, [])

    def test_production_mutation_refuses_non_linux(self):
        manager = GadgetManager(self.manager.root, self.manager.udc_root, self.manager.dev_root)
        with patch('chaos.hid_gadget.sys.platform', 'darwin'):
            with self.assertRaises(GadgetError):
                manager.enable(1, 2, 'test', 100)
            with self.assertRaises(GadgetError):
                manager.disable('test')
        self.assertFalse(self.manager.path.exists())


if __name__ == '__main__':
    unittest.main()
