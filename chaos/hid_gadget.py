"""Explicit Linux configfs setup for Chaos keyboard/mouse USB peripherals.

No import side effects, boot edits, modprobe invocation, mounts, input reports or
network listeners. The operator prepares a Linux USB device controller first.
"""
import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path

GADGET_NAME = 'chaos_hid'
KEYBOARD_FUNCTION = 'hid.keyboard'
MOUSE_FUNCTION = 'hid.mouse'
MANUFACTURER = 'Chaos Intelligence'
PRODUCT = 'Chaos HID/1'
CONFIGURATION = 'Chaos keyboard and mouse/1'

# Six-key keyboard array covers the same 0..231 usages as hid_protocol.py.
# The two-byte logical maximum avoids interpreting 0xe7 as a negative value.
KEYBOARD_REPORT_DESCRIPTOR = bytes.fromhex(
    '05 01 09 06 a1 01 05 07 19 e0 29 e7 15 00 25 01 '
    '75 01 95 08 81 02 95 01 75 08 81 03 '
    '95 05 75 01 05 08 19 01 29 05 91 02 '
    '95 01 75 03 91 03 95 06 75 08 15 00 26 e7 00 '
    '05 07 19 00 29 e7 81 00 c0')
# Five buttons, padding, signed relative X/Y/wheel. This is report protocol,
# not a three-byte boot mouse; no BIOS compatibility promise is implied.
MOUSE_REPORT_DESCRIPTOR = bytes.fromhex(
    '05 01 09 02 a1 01 09 01 a1 00 05 09 19 01 29 05 '
    '15 00 25 01 95 05 75 01 81 02 95 01 75 03 81 03 '
    '05 01 09 30 09 31 09 38 15 81 25 7f 75 08 95 03 '
    '81 06 c0 c0')
FUNCTIONS = {
    KEYBOARD_FUNCTION: (1, 1, 8, KEYBOARD_REPORT_DESCRIPTOR),
    MOUSE_FUNCTION: (0, 0, 4, MOUSE_REPORT_DESCRIPTOR),
}


class GadgetError(RuntimeError):
    """A prerequisite, conflict or ownership check prevented an operation."""


def _read(path, binary=False):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise GadgetError('Expected a non-symlink attribute: ' + str(path))
    with path.open('rb') as stream:
        value = stream.read(4097)
    if len(value) > 4096:
        raise GadgetError('Oversized configfs attribute: ' + str(path))
    return value if binary else value.decode('utf-8').strip()


def _number(path):
    text = _read(path)
    try:
        return int(text, 16 if text.lower().startswith('0x') else 10)
    except ValueError as exc:
        raise GadgetError('Invalid numeric configfs attribute: ' + str(path)) from exc


def _directory(path):
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise GadgetError('Expected a non-symlink directory: ' + str(path))
    return path


def _entries(path, allowed, required=None):
    found = {item.name for item in _directory(path).iterdir()}
    if found - set(allowed) or set(allowed if required is None else required) - found:
        raise GadgetError('Unexpected gadget shape: ' + str(path))


def _serial(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
        raise GadgetError('Serial must be 1..64 ASCII letters, digits, _ or -')
    return value


def _ids(vid, pid, serial, max_power_ma, self_powered):
    if any(type(n) is not int or not 1 <= n <= 65535 for n in (vid, pid)):
        raise GadgetError('Explicit VID and PID must each be 1..65535')
    _serial(serial)
    if type(max_power_ma) is not int or not 0 <= max_power_ma <= 500 or max_power_ma % 2:
        raise GadgetError('USB 2.0 MaxPower must be an even number from 0 to 500 mA')
    if type(self_powered) is not bool:
        raise GadgetError('self_powered must be a boolean')


def resolve_device_nodes(gadget_path, dev_root='/dev'):
    """Map kernel function dev major:minor to actual nodes; never guess indexes.

    Nodes may not yet exist immediately after binding. That is an explicit
    error, not permission to substitute another hidg device.
    """
    result = {}
    for role, function in (('keyboard', KEYBOARD_FUNCTION), ('mouse', MOUSE_FUNCTION)):
        raw = _read(Path(gadget_path) / 'functions' / function / 'dev')
        if not re.fullmatch(r'[0-9]+:[0-9]+', raw):
            raise GadgetError('Kernel HID function has no valid dev major:minor')
        major, minor = (int(part) for part in raw.split(':'))
        if major == 0:
            raise GadgetError('Kernel HID function has an unallocated device number')
        matches = []
        for node in Path(dev_root).glob('hidg*'):
            try:
                info = node.lstat()
            except FileNotFoundError:
                continue
            if (stat.S_ISCHR(info.st_mode) and os.major(info.st_rdev) == major
                    and os.minor(info.st_rdev) == minor):
                matches.append(str(node))
        if len(matches) != 1:
            raise GadgetError('Expected one HID character node for ' + role + ' (' + raw + ')')
        result[role] = matches[0]
    if result['keyboard'] == result['mouse']:
        raise GadgetError('Keyboard and mouse cannot share a HID endpoint')
    return result


class GadgetManager:
    """Own exactly one fixed-shape gadget; all other gadgets remain untouched."""

    def __init__(self, configfs_root='/sys/kernel/config/usb_gadget',
                 udc_root='/sys/class/udc', dev_root='/dev'):
        self.root = Path(configfs_root)
        self.udc_root = Path(udc_root)
        self.dev_root = Path(dev_root)
        self.path = self.root / GADGET_NAME

    def _write(self, path, value):
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise GadgetError('Refusing to create/follow configfs attribute: ' + str(path))
        data = value if isinstance(value, bytes) else (str(value) + '\n').encode('ascii')
        with path.open('wb') as stream:
            if stream.write(data) != len(data):
                raise GadgetError('Incomplete configfs write: ' + str(path))

    def _mkdir(self, path):
        Path(path).mkdir()

    def _rmdir(self, path):
        Path(path).rmdir()

    def _require_linux(self):
        if sys.platform != 'linux':
            raise GadgetError('enable/disable require Linux with prepared configfs and a UDC')

    def _udcs(self):
        return sorted(path.name for path in self.udc_root.iterdir()) if self.udc_root.is_dir() else []

    def _choose_udc(self, requested):
        available = self._udcs()
        if requested is not None:
            if type(requested) is not str or requested not in available or '/' in requested:
                raise GadgetError('Requested UDC is not present in /sys/class/udc')
            selected = requested
        else:
            if len(available) != 1:
                raise GadgetError('Expected exactly one UDC; prepare device mode or explicitly choose --udc')
            selected = available[0]
        # Legacy drivers such as g_ether have no configfs directory. The UDC
        # sysfs function attribute also exposes these existing bindings.
        function = _read(self.udc_root / selected / 'function')
        if function:
            raise GadgetError('UDC already in use by ' + function + '; leave that gadget/network untouched')
        return selected

    def _conflicts(self):
        conflicts = []
        if not self.root.is_dir():
            return conflicts
        for gadget in self.root.iterdir():
            if gadget.name == GADGET_NAME:
                continue
            if gadget.is_symlink() or not gadget.is_dir():
                conflicts.append(gadget.name + ': unexpected configfs entry')
                continue
            try:
                bound = _read(gadget / 'UDC')
                functions = [item.name for item in _directory(gadget / 'functions').iterdir()]
            except (OSError, UnicodeError, GadgetError):
                conflicts.append(gadget.name + ': cannot safely inspect')
                continue
            network = any(name.split('.')[0] in ('ecm', 'eem', 'ncm', 'rndis', 'geth') for name in functions)
            if bound or network:
                conflicts.append(gadget.name + (': USB network gadget' if network else ': UDC already bound'))
        return conflicts

    def _owned(self, serial=None):
        p = _directory(self.path)
        top = {'UDC', 'bcdUSB', 'bcdDevice', 'idVendor', 'idProduct', 'bDeviceClass',
               'bDeviceSubClass', 'bDeviceProtocol', 'bMaxPacketSize0', 'strings', 'configs',
               'functions', 'os_desc', 'webusb', 'max_speed'}
        _entries(p, top, top - {'webusb', 'max_speed'})
        for name in top - {'strings', 'configs', 'functions', 'os_desc', 'webusb'}:
            if (p / name).exists():
                _read(p / name)
        for name in ('os_desc', 'webusb'):
            if (p / name).exists():
                for item in _directory(p / name).iterdir():
                    if item.is_symlink() or not item.is_file():
                        raise GadgetError('Gadget has extra descriptor links: ' + str(item))
                if (p / name / 'use').exists() and _number(p / name / 'use') != 0:
                    raise GadgetError('Gadget has enabled extra descriptors')
        expected = {'bcdUSB': 0x0200, 'bcdDevice': 0x0100, 'bDeviceClass': 0,
                    'bDeviceSubClass': 0, 'bDeviceProtocol': 0, 'bMaxPacketSize0': 64}
        for name, value in expected.items():
            if _number(p / name) != value:
                raise GadgetError('Gadget identity/descriptor mismatch: ' + name)
        _entries(p / 'strings', {'0x409'})
        _entries(p / 'strings/0x409', {'manufacturer', 'product', 'serialnumber'})
        if (_read(p / 'strings/0x409/manufacturer') != MANUFACTURER
                or _read(p / 'strings/0x409/product') != PRODUCT):
            raise GadgetError('Gadget is not owned by Chaos HID/1')
        actual_serial = _serial(_read(p / 'strings/0x409/serialnumber'))
        if serial is not None and _serial(serial) != actual_serial:
            raise GadgetError('Serial does not match the existing Chaos gadget')
        _entries(p / 'functions', set(FUNCTIONS))
        for name, (subclass, protocol, length, descriptor) in FUNCTIONS.items():
            f = p / 'functions' / name
            _entries(f, {'subclass', 'protocol', 'report_length', 'report_desc', 'dev',
                         'no_out_endpoint', 'interval'},
                     {'subclass', 'protocol', 'report_length', 'report_desc', 'dev'})
            for attribute in f.iterdir():
                _read(attribute, binary=True)
            for key, value in (('subclass', subclass), ('protocol', protocol), ('report_length', length)):
                if _number(f / key) != value:
                    raise GadgetError('HID function attribute mismatch: ' + name + '/' + key)
            if _read(f / 'report_desc', binary=True) != descriptor:
                raise GadgetError('HID report descriptor mismatch: ' + name)
            if (f / 'no_out_endpoint').exists() and _number(f / 'no_out_endpoint') != 0:
                raise GadgetError('Unexpected HID endpoint configuration')
        _entries(p / 'configs', {'c.1'})
        c = p / 'configs/c.1'
        _entries(c, {'strings', 'MaxPower', 'bmAttributes'} | set(FUNCTIONS))
        _entries(c / 'strings', {'0x409'})
        _entries(c / 'strings/0x409', {'configuration'})
        if _read(c / 'strings/0x409/configuration') != CONFIGURATION:
            raise GadgetError('Configuration is not owned by Chaos HID/1')
        for function in FUNCTIONS:
            link = c / function
            if not link.is_symlink() or link.resolve() != (p / 'functions' / function).resolve():
                raise GadgetError('Unexpected HID configuration link: ' + str(link))
        power, attributes = _number(c / 'MaxPower'), _number(c / 'bmAttributes')
        _ids(_number(p / 'idVendor'), _number(p / 'idProduct'), actual_serial,
             power, attributes == 0xc0)
        if attributes not in (0x80, 0xc0):
            raise GadgetError('Unexpected configuration attributes')
        return {'serial': actual_serial, 'vid': _number(p / 'idVendor'), 'pid': _number(p / 'idProduct'),
                'maxPowerMa': power, 'selfPowered': attributes == 0xc0}

    def status(self):
        result = {'gadget': GADGET_NAME, 'present': self.path.exists() or self.path.is_symlink(),
                  'owned': False, 'active': False, 'udcs': self._udcs(), 'nodes': {}, 'issues': []}
        result['controllerBindings'] = {}
        for name in result['udcs']:
            try:
                result['controllerBindings'][name] = _read(self.udc_root / name / 'function')
            except (GadgetError, OSError, UnicodeError) as exc:
                result['issues'].append(str(exc))
        if not self.root.is_dir():
            result['issues'].append('configfs USB gadget directory is unavailable; prepare Linux first')
        result['conflicts'] = self._conflicts()
        if result['present']:
            try:
                result.update(self._owned())
                result['owned'] = True
                result['udc'] = _read(self.path / 'UDC')
                result['active'] = bool(result['udc'])
                if result['active']:
                    try:
                        result['nodes'] = resolve_device_nodes(self.path, self.dev_root)
                    except (GadgetError, OSError, UnicodeError) as exc:
                        result['issues'].append(str(exc))
            except (GadgetError, OSError, UnicodeError) as exc:
                result['issues'].append(str(exc))
        return result

    def plan(self, vid, pid, serial, max_power_ma, self_powered=False, udc=None):
        _ids(vid, pid, serial, max_power_ma, self_powered)
        result = self.status()
        result['requested'] = {'vid': vid, 'pid': pid, 'serial': serial,
                               'maxPowerMa': max_power_ma, 'selfPowered': self_powered}
        try:
            result['selectedUdc'] = self._choose_udc(udc)
        except (GadgetError, OSError, UnicodeError) as exc:
            result['issues'].append(str(exc))
        if result['present']:
            result['issues'].append('Gadget name already exists; inspect and explicitly disable it first')
        result['issues'].extend(result['conflicts'])
        result['ready'] = not result['issues']
        result['writesPerformed'] = False
        return result

    def enable(self, vid, pid, serial, max_power_ma, self_powered=False, udc=None):
        self._require_linux()
        plan = self.plan(vid, pid, serial, max_power_ma, self_powered, udc)
        if not plan['ready']:
            raise GadgetError('; '.join(plan['issues']))
        _directory(self.root)
        created, linked = [], []
        binding_attempted = False

        def mkdir(path):
            self._mkdir(path)
            info = Path(path).lstat()
            created.append((Path(path), info.st_dev, info.st_ino))

        try:
            p = self.path
            mkdir(p)
            for name, value in (('idVendor', hex(vid)), ('idProduct', hex(pid)),
                                ('bcdUSB', '0x0200'), ('bcdDevice', '0x0100'),
                                ('bDeviceClass', 0), ('bDeviceSubClass', 0),
                                ('bDeviceProtocol', 0), ('bMaxPacketSize0', 64)):
                self._write(p / name, value)
            mkdir(p / 'strings/0x409')
            for name, value in (('manufacturer', MANUFACTURER), ('product', PRODUCT), ('serialnumber', serial)):
                self._write(p / 'strings/0x409' / name, value)
            mkdir(p / 'configs/c.1')
            self._write(p / 'configs/c.1/MaxPower', max_power_ma)
            self._write(p / 'configs/c.1/bmAttributes', '0xc0' if self_powered else '0x80')
            mkdir(p / 'configs/c.1/strings/0x409')
            self._write(p / 'configs/c.1/strings/0x409/configuration', CONFIGURATION)
            for name, (subclass, protocol, length, descriptor) in FUNCTIONS.items():
                f = p / 'functions' / name
                mkdir(f)
                for attr, value in (('subclass', subclass), ('protocol', protocol),
                                    ('report_length', length), ('report_desc', descriptor)):
                    self._write(f / attr, value)
            for name in FUNCTIONS:
                link = self.path / 'configs/c.1' / name
                target = self.path / 'functions' / name
                link.symlink_to(target)
                linked.append((link, target))
            self._owned(serial)
            if self._conflicts():
                raise GadgetError('Another gadget appeared before binding; refusing UDC takeover')
            self._choose_udc(plan['selectedUdc'])
            binding_attempted = True
            self._write(self.path / 'UDC', plan['selectedUdc'])
            return self.status()
        except BaseException as exc:
            return self._rollback(exc, created, linked, binding_attempted)

    def _rollback(self, original, created, linked, binding_attempted):
        errors = []
        if binding_attempted:
            try:
                # Linux returns ENODEV when asked to unbind an already
                # unbound gadget, including most rejected bind attempts.
                if _read(self.path / 'UDC'):
                    self._write(self.path / 'UDC', '')
            except BaseException as exc:
                errors.append(str(exc))
                raise GadgetError('Enable failed; unbind failed, created gadget retained: ' + str(original)) from original
        for link, target in reversed(linked):
            try:
                if not link.is_symlink() or link.resolve() != target.resolve():
                    raise GadgetError('Created link changed during rollback: ' + str(link))
                link.unlink()
            except BaseException as exc:
                errors.append(str(exc))
        for path, device, inode in reversed(created):
            try:
                info = path.lstat()
                if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (device, inode):
                    raise GadgetError('Created directory changed during rollback: ' + str(path))
                self._rmdir(path)
            except BaseException as exc:
                errors.append(str(exc))
        if errors:
            raise GadgetError('Enable failed; partial owned cleanup requires inspection: ' + str(original)
                              + '; ' + '; '.join(errors)) from original
        raise original

    def disable(self, serial):
        self._require_linux()
        self._owned(_serial(serial))
        if _read(self.path / 'UDC'):
            self._write(self.path / 'UDC', '')
        for name in FUNCTIONS:
            (self.path / 'configs/c.1' / name).unlink()
        for name in reversed(tuple(FUNCTIONS)):
            self._rmdir(self.path / 'functions' / name)
        self._rmdir(self.path / 'configs/c.1/strings/0x409')
        self._rmdir(self.path / 'configs/c.1')
        self._rmdir(self.path / 'strings/0x409')
        self._rmdir(self.path)
        return self.status()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status', help='Read-only UDC, ownership and node inspection')
    for command in ('plan', 'enable'):
        item = commands.add_parser(command, help='Read-only plan' if command == 'plan' else 'Create and bind HID gadget')
        item.add_argument('--vid', type=lambda text: int(text, 0), required=True)
        item.add_argument('--pid', type=lambda text: int(text, 0), required=True)
        item.add_argument('--serial', required=True)
        item.add_argument('--max-power-ma', type=int, required=True)
        item.add_argument('--self-powered', action='store_true')
        item.add_argument('--udc')
    item = commands.add_parser('disable', help='Unbind and remove only the verified owned gadget')
    item.add_argument('--serial', required=True)
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    try:
        result = getattr(GadgetManager(), command)(**args)
    except (GadgetError, OSError, UnicodeError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
