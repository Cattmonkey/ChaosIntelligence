"""Host device registration remains inert and uses the original source runtime."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chaos import DeviceSpec, Framework, LinuxHID, Registry, Settings, linux_hid_device, load_device_registry


class DeviceConfigTests(unittest.TestCase):
    def load(self, rows, registry=None):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'devices.json'
            path.write_text(json.dumps({'devices': rows}))
            return load_device_registry(path, registry)

    def test_old_and_explicit_serial_and_linux_bindings_are_inert(self):
        rows = [dict(name='old', port='/dev/serial-old'),
                dict(name='serial', transport='serial', port='/dev/serial-new'),
                dict(name='pi', transport='linux-hid', keyboard='/dev/hidg7', mouse='/dev/hidg8')]
        with patch.object(LinuxHID, '__init__', side_effect=AssertionError('registration opens hardware')):
            registry = self.load(rows)
            specs = registry.device_snapshot()
            self.assertEqual(set(specs), {'old', 'serial', 'pi'})
            self.assertTrue(all(callable(spec.factory) for spec in specs.values()))
            self.assertEqual(len(registry.snapshot()), 2)

    def test_mixed_invalid_configuration_never_partially_registers(self):
        invalids = [dict(name='bad', transport='unknown', port='/dev/tty0'),
                    dict(name='bad', transport='linux-hid', keyboard='/dev/hidg0'),
                    dict(name='bad', transport='linux-hid', keyboard='/dev/hidg0', mouse='/dev/hidg1', module='evil'),
                    dict(name='bad', transport='linux-hid', keyboard='/dev/hidg0', mouse='/dev/hidg1', baudrate=115200),
                    dict(name='bad', transport='serial', port='/dev/tty0', keyboard='/dev/hidg0'),
                    dict(name='bad', transport='linux-hid', keyboard='/dev/hidg0', mouse='/dev/hidg1', timeout=True),
                    dict(name='bad', transport=['linux-hid']), None]
        for row in invalids:
            with self.subTest(row=row):
                registry = Registry()
                registry.register_device(DeviceSpec('kept', lambda context: None))
                with self.assertRaises((ValueError, TypeError)):
                    self.load([dict(name='good', port='/dev/tty0'), row], registry)
                self.assertEqual(set(registry.device_snapshot()), {'kept'})

    def test_cross_transport_names_remain_unique_and_existing_registry_preserved(self):
        linux = dict(name='same', transport='linux-hid', keyboard='/dev/hidg0', mouse='/dev/hidg1')
        registry = Registry()
        with self.assertRaises(ValueError): self.load([linux, dict(name='same', port='/dev/tty0')], registry)
        self.assertFalse(registry.device_snapshot())
        registry.register_device(linux_hid_device('same', keyboard='/dev/hidg0', mouse='/dev/hidg1'))
        with self.assertRaises(ValueError): self.load([linux], registry)
        self.assertEqual(list(registry.device_snapshot()), ['same'])

    def test_linux_binding_never_implicitly_grants_capability(self):
        registry = self.load([dict(name='pi', transport='linux-hid', keyboard='/dev/hidg0', mouse='/dev/hidg1')])
        with patch.object(LinuxHID, '__init__', side_effect=AssertionError('denied effect opened hardware')):
            with Framework(settings=Settings(':memory:'), registry=registry) as app:
                project = app.create_project('unprivileged', source='await device_open("pi")', capabilities=[])
                with self.assertRaises(Exception) as caught:
                    app.start('unprivileged', project['version'], None, mode='source').result(5)
                self.assertIn('device.control', str(caught.exception))

    def test_cli_can_load_linux_config_and_run_cold_program_without_hardware(self):
        from chaos.cli import main
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); config = root / 'devices.json'; program = root / 'main.chaos'
            config.write_text(json.dumps({'devices': [dict(name='pi', transport='linux-hid', keyboard='/dev/hidg0', mouse='/dev/hidg1')]}))
            program.write_text('let cold = device_open("pi"); 42')
            output = io.StringIO()
            with patch.object(LinuxHID, '__init__', side_effect=AssertionError('cold task opened hardware')), contextlib.redirect_stdout(output):
                with patch('sys.argv', ['chaos', 'run', str(program), '--devices', str(config)]), patch('chaos.settings.Settings', return_value=Settings(':memory:')):
                    self.assertEqual(main(), 0)
            self.assertEqual(json.loads(output.getvalue()), 42)


if __name__ == '__main__': unittest.main()
