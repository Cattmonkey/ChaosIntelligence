"""Linux USB gadget HID adapter; importing or registering never opens hardware.

Reports are submitted to f_hid's kernel queue. This is not evidence that the
connected computer received a report or that an application acted on it.
"""
import hashlib
import json
import os
import select
import stat
import sys
import threading
import time

from .device_serial import DeviceTransportError
from .hid_protocol import PROTOCOL, identity, validate_action


_LEASE_LOCK = threading.RLock()
_LEASES = set()


def _options(keyboard, mouse, timeout):
    for path in (keyboard, mouse):
        if type(path) is not str or not path or len(path) > 1024 or '\0' in path:
            raise ValueError('HID endpoint must be a nonempty host-configured path')
    if type(timeout) not in (int, float) or not 0.05 <= timeout <= 10:
        raise ValueError('HID timeout must be 0.05..10 seconds')
    paths = tuple(os.path.realpath(path) for path in (keyboard, mouse))
    if paths[0] == paths[1]:
        raise ValueError('Keyboard and mouse require distinct HID endpoints')
    return paths


def _is_hid_gadget(device):
    subsystem = '/sys/dev/char/%s:%s/subsystem' % (os.major(device), os.minor(device))
    try:
        return os.path.samefile(subsystem, '/sys/class/hidg')
    except OSError:
        return False


class LinuxHID:
    """Own two verified Linux HID gadget endpoints using standard-library I/O.

    Per-device process leases also cover overlapping keyboard/mouse pairs.
    flock adds cooperative interprocess exclusion for the same device inode;
    it is not a security boundary against other privileged input writers.
    """

    def __init__(self, keyboard='/dev/hidg0', mouse='/dev/hidg1', *, timeout=2.0):
        paths = _options(keyboard, mouse, timeout)
        if sys.platform != 'linux':
            raise DeviceTransportError('Linux USB HID gadgets require a Linux host')
        import fcntl
        self.timeout = float(timeout)
        self.closed = False
        self._cleanup_failure = None
        self.used_ids = set()
        self._lock = threading.RLock()
        self._endpoints = []
        flags = os.O_WRONLY | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
        try:
            for path in paths:
                endpoint = {'fd': os.open(path, flags), 'device': None, 'leased': False, 'ready': False}
                self._endpoints.append(endpoint)
                info = os.fstat(endpoint['fd'])
                if not stat.S_ISCHR(info.st_mode) or not _is_hid_gadget(info.st_rdev):
                    raise DeviceTransportError('Endpoint is not a Linux HID gadget character device')
                endpoint['device'] = info.st_rdev
                with _LEASE_LOCK:
                    if info.st_rdev in _LEASES:
                        raise DeviceTransportError('Physical HID endpoint is already leased')
                    _LEASES.add(info.st_rdev)
                    endpoint['leased'] = True
                try:
                    fcntl.flock(endpoint['fd'], fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise DeviceTransportError('HID endpoint is locked by another process') from None
                endpoint['ready'] = True
        except BaseException:
            self.closed = True
            try:
                self._close_descriptors()
            except BaseException:
                pass
            raise

    @staticmethod
    def _checkpoint(context, deadline):
        if context is not None:
            context.checkpoint()
        if time.monotonic() >= deadline:
            raise DeviceTransportError('HID report submission timed out; outcome is uncertain')

    def _write_report(self, endpoint, report, deadline, context=None):
        fd = endpoint['fd']
        if fd is None or not endpoint['ready']:
            raise DeviceTransportError('HID endpoint is closed')
        poller = select.poll()
        poller.register(fd, select.POLLOUT)
        first = True
        while True:
            # Cleanup is allowed one immediate nonblocking attempt on EACH
            # device, even when a preceding release used the grace period.
            if context is not None or not first:
                self._checkpoint(context, deadline)
            first = False
            try:
                count = os.write(fd, report)
            except (BlockingIOError, InterruptedError):
                self._checkpoint(context, deadline)
                milliseconds = max(1, int(min(0.025, deadline - time.monotonic()) * 1000))
                try:
                    events = poller.poll(milliseconds)
                except InterruptedError:
                    continue
                for event_fd, events_mask in events:
                    if event_fd == fd and events_mask & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                        raise DeviceTransportError('HID endpoint disconnected; outcome is uncertain')
                continue
            # A kernel HID write is one report. Appending a short-write tail
            # would submit a different report, so NEVER retry or append it.
            if type(count) is not int or count != len(report):
                raise DeviceTransportError('Incomplete HID report submission; outcome is uncertain')
            return

    def _release(self):
        deadline = time.monotonic() + min(self.timeout, 0.25)
        failure = None
        for endpoint, size in zip(self._endpoints, (8, 4)):
            if endpoint['fd'] is None or not endpoint['ready']:
                continue
            try:
                self._write_report(endpoint, bytes(size), deadline)
            except BaseException as error:
                if failure is None:
                    failure = error
        if failure is not None:
            if self._cleanup_failure is None:
                self._cleanup_failure = failure
            raise failure

    def _close_descriptors(self):
        failure = None
        for endpoint in reversed(self._endpoints):
            fd = endpoint['fd']
            if fd is None:
                continue
            # Linux releases the fd number before reporting close errors.
            # Forget it before close; retrying could close an unrelated fd
            # subsequently allocated by another thread.
            endpoint['fd'] = None
            try:
                os.close(fd)
            except BaseException as error:
                if failure is None:
                    failure = error
            finally:
                if endpoint['leased']:
                    with _LEASE_LOCK:
                        _LEASES.discard(endpoint['device'])
                    endpoint['leased'] = False
        if failure is not None:
            if self._cleanup_failure is None:
                self._cleanup_failure = failure
            raise failure

    def handle(self, request_id, spec, context):
        with self._lock:
            if self.closed:
                raise DeviceTransportError('Linux HID connection is closed')
            if not identity(request_id):
                raise ValueError('Invalid HID request identity')
            spec = validate_action(spec)
            if request_id in self.used_ids or len(self.used_ids) >= 4096:
                raise DeviceTransportError('HID request identity consumed or connection budget exhausted')
            self.used_ids.add(request_id)
            deadline = min(context.deadline, time.monotonic() + self.timeout)
            try:
                self._checkpoint(context, deadline)
                action = spec['action']
                if action == 'key_tap':
                    keys = spec['keys']
                    report = bytes([spec['modifiers'], 0] + keys + [0] * (6 - len(keys)))
                    self._write_report(self._endpoints[0], report, deadline, context)
                elif action == 'mouse_move':
                    report = bytes([0, spec['x'] & 255, spec['y'] & 255, spec['wheel'] & 255])
                    self._write_report(self._endpoints[1], report, deadline, context)
                elif action == 'mouse_click':
                    self._write_report(self._endpoints[1], bytes([spec['button'], 0, 0, 0]), deadline, context)
            except BaseException:
                self.closed = True
                try:
                    self._release()
                except BaseException:
                    pass
                try:
                    self._close_descriptors()
                except BaseException:
                    pass
                raise
            try:
                self._release()
                self._checkpoint(context, deadline)
            except BaseException:
                self.closed = True
                try:
                    self._close_descriptors()
                except BaseException:
                    pass
                raise
            return {'protocol': PROTOCOL, 'id': request_id, 'status': 'acknowledged'}

    def close(self):
        with self._lock:
            self.closed = True
            if self._cleanup_failure is not None and all(endpoint['fd'] is None for endpoint in self._endpoints):
                # The host must retain its quarantine after an uncertain
                # cleanup. Never pretend a failed release succeeded merely
                # because all fd numbers have already been forgotten.
                raise self._cleanup_failure
            failure = None
            try:
                self._release()
            except BaseException as error:
                failure = error
            try:
                self._close_descriptors()
            except BaseException as error:
                if failure is None:
                    failure = error
            if failure is not None:
                raise failure


def linux_hid_device(name, keyboard='/dev/hidg0', mouse='/dev/hidg1', *, timeout=2.0):
    """Register a trusted Linux gadget pair without importing fcntl or opening it."""
    from .devices import DeviceSpec
    paths = _options(keyboard, mouse, timeout)
    resource_id = 'linux-hid:' + hashlib.sha256(json.dumps(sorted(paths)).encode('utf-8')).hexdigest()
    def factory(context):
        context.checkpoint()
        return LinuxHID(paths[0], paths[1], timeout=timeout)
    return DeviceSpec(name, factory, description='Linux USB gadget keyboard and mouse',
                      version='1', resource_id=resource_id)
