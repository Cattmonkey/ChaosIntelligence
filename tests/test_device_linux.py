"""Software-only Linux gadget tests: no test opens a real device or configfs."""
from contextlib import ExitStack
import errno
import os
from pathlib import Path
import select
import stat
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from chaos import Framework, Registry, Settings, ProviderSpec, ToolCall, Final
from chaos import device_linux as linux
from chaos.device_serial import DeviceTransportError


class Context:
    def __init__(self, check=None, deadline=None):
        self.check = check
        self.deadline = time.monotonic() + 2 if deadline is None else deadline
    def checkpoint(self):
        if self.check:
            self.check()


class FakeKernel:
    def __init__(self):
        self.paths = {
            '/fixture/keyboard': (os.makedev(42, 0), stat.S_IFCHR),
            '/fixture/mouse': (os.makedev(42, 1), stat.S_IFCHR),
            '/fixture/othermouse': (os.makedev(42, 2), stat.S_IFCHR),
            '/fixture/alias': (os.makedev(42, 0), stat.S_IFCHR),
            '/fixture/file': (0, stat.S_IFREG),
            '/fixture/tty': (os.makedev(4, 1), stat.S_IFCHR),
        }
        self.fds = {}
        self.next_fd = 100
        self.opens, self.writes, self.closes, self.locks, self.polls = [], [], [], [], []
        self.write_hook = self.close_hook = self.lock_hook = self.poll_hook = None
    def open(self, path, flags):
        self.opens.append((path, flags))
        if path not in self.paths:
            raise FileNotFoundError(path)
        fd = self.next_fd
        self.next_fd += 1
        self.fds[fd] = self.paths[path]
        return fd
    def fstat(self, fd):
        device, mode = self.fds[fd]
        return SimpleNamespace(st_mode=mode, st_rdev=device)
    def samefile(self, left, right):
        return right == '/sys/class/hidg' and left in (
            '/sys/dev/char/42:0/subsystem', '/sys/dev/char/42:1/subsystem',
            '/sys/dev/char/42:2/subsystem')
    def flock(self, fd, flags):
        self.locks.append((fd, flags))
        if self.lock_hook:
            self.lock_hook(fd, flags)
    def write(self, fd, data):
        if fd not in self.fds:
            raise OSError(errno.EBADF, 'closed')
        self.writes.append((fd, bytes(data)))
        if self.write_hook:
            result = self.write_hook(fd, bytes(data))
            if result is not None:
                return result
        return len(data)
    def close(self, fd):
        self.closes.append(fd)
        self.fds.pop(fd)
        if self.close_hook:
            self.close_hook(fd)
    def poll(self):
        kernel = self
        class Poll:
            def register(self, fd, events):
                self.fd = fd
            def poll(self, milliseconds):
                kernel.polls.append(milliseconds)
                if kernel.poll_hook:
                    return kernel.poll_hook(self.fd, milliseconds)
                return [(self.fd, select.POLLOUT)]
        return Poll()
    def installed(self):
        import fcntl
        stack = ExitStack()
        for target, name, value in (
            (linux.sys, 'platform', 'linux'), (linux.os, 'open', self.open),
            (linux.os, 'fstat', self.fstat), (linux.os.path, 'samefile', self.samefile),
            (linux.os, 'write', self.write), (linux.os, 'close', self.close),
            (fcntl, 'flock', self.flock), (linux.select, 'poll', self.poll),
            (linux, '_LEASES', set()),
        ):
            stack.enter_context(patch.object(target, name, value))
        return stack
    def adapter(self, **kwargs):
        return linux.LinuxHID('/fixture/keyboard', '/fixture/mouse', **kwargs)


class LinuxDeviceTests(unittest.TestCase):
    def test_registration_is_cold_and_works_on_non_linux(self):
        with patch.object(linux.sys, 'platform', 'darwin'), patch.object(linux.os, 'open', side_effect=AssertionError('device opened')):
            spec = linux.linux_hid_device('desk', '/fixture/keyboard', '/fixture/mouse')
            self.assertEqual(spec.describe()['profile'], 'chaos-hid/1')
            with self.assertRaisesRegex(DeviceTransportError, 'Linux host'):
                spec.factory(Context())

    def test_options_reject_bad_paths_and_timeout(self):
        for args in ((None, '/a', 2), ('/a', '/a', 2), ('', '/b', 2), ('/a\0x', '/b', 2),
                     ('/a', '/b', True), ('/a', '/b', float('nan')), ('/a', '/b', float('inf')),
                     ('/a', '/b', 0), ('/a', '/b', 11)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                linux.linux_hid_device('desk', args[0], args[1], timeout=args[2])

    def test_symbolic_aliases_and_swapped_pair_share_registration_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            keyboard, mouse, alias = (Path(folder) / name for name in ('keyboard', 'mouse', 'alias'))
            keyboard.touch(); mouse.touch(); alias.symlink_to(keyboard)
            first = linux.linux_hid_device('a', str(keyboard), str(mouse))
            second = linux.linux_hid_device('b', str(alias), str(mouse))
            reverse = linux.linux_hid_device('c', str(mouse), str(keyboard))
            self.assertEqual(first.resource_id, second.resource_id)
            self.assertEqual(first.resource_id, reverse.resource_id)

    def test_open_flags_and_both_endpoint_locks(self):
        kernel = FakeKernel()
        with kernel.installed():
            adapter = kernel.adapter()
            for path, flags in kernel.opens:
                self.assertTrue(flags & os.O_NONBLOCK)
                self.assertTrue(flags & os.O_WRONLY)
            self.assertEqual([fd for fd, flags in kernel.locks], [100, 101])
            self.assertFalse(kernel.writes)
            adapter.close()
            self.assertFalse(kernel.fds)

    def test_rejects_regular_and_non_hid_character_devices_before_writing(self):
        for path in ('/fixture/file', '/fixture/tty'):
            kernel = FakeKernel()
            with self.subTest(path=path), kernel.installed():
                with self.assertRaisesRegex(DeviceTransportError, 'HID gadget'):
                    linux.LinuxHID('/fixture/keyboard', path)
                self.assertFalse(kernel.writes)
                self.assertFalse(kernel.fds)
                self.assertFalse(linux._LEASES)

    def test_missing_second_endpoint_closes_first_and_preserves_original_error(self):
        kernel = FakeKernel()
        def close_error(fd):
            raise OSError(errno.EIO, 'cleanup failed')
        kernel.close_hook = close_error
        with kernel.installed():
            with self.assertRaises(FileNotFoundError):
                linux.LinuxHID('/fixture/keyboard', '/fixture/missing')
            self.assertFalse(kernel.fds)
            self.assertFalse(linux._LEASES)

    def test_overlapping_pairs_and_same_rdev_alias_are_exclusive(self):
        kernel = FakeKernel()
        with kernel.installed():
            adapter = kernel.adapter()
            for keyboard, mouse in (('/fixture/keyboard', '/fixture/othermouse'),
                                    ('/fixture/alias', '/fixture/othermouse')):
                with self.assertRaisesRegex(DeviceTransportError, 'already leased'):
                    linux.LinuxHID(keyboard, mouse)
            self.assertEqual(len(kernel.fds), 2)
            adapter.close()
            replacement = linux.LinuxHID('/fixture/alias', '/fixture/othermouse')
            replacement.close()
            self.assertFalse(linux._LEASES)

    def test_duplicate_endpoint_under_different_path_is_rejected(self):
        kernel = FakeKernel()
        with kernel.installed():
            with self.assertRaisesRegex(DeviceTransportError, 'already leased'):
                linux.LinuxHID('/fixture/keyboard', '/fixture/alias')
            self.assertFalse(kernel.fds)
            self.assertFalse(linux._LEASES)

    def test_external_flock_conflict_releases_local_leases(self):
        kernel = FakeKernel()
        def conflict(fd, flags):
            if fd == 101:
                raise BlockingIOError(errno.EAGAIN, 'busy')
        kernel.lock_hook = conflict
        with kernel.installed():
            with self.assertRaisesRegex(DeviceTransportError, 'another process'):
                kernel.adapter()
            self.assertFalse(kernel.fds)
            self.assertFalse(linux._LEASES)

    def test_all_actions_submit_exact_reports_then_release_both(self):
        cases = [
            ({'action': 'key_tap', 'keys': [4, 5], 'modifiers': 2}, (100, b'\x02\x00\x04\x05\x00\x00\x00\x00')),
            ({'action': 'mouse_move', 'x': -127, 'y': 127, 'wheel': -1}, (101, b'\x00\x81\x7f\xff')),
            ({'action': 'mouse_click', 'button': 16}, (101, b'\x10\x00\x00\x00')),
            ({'action': 'release'}, None),
        ]
        for action, expected in cases:
            kernel = FakeKernel()
            with self.subTest(action=action), kernel.installed():
                adapter = kernel.adapter()
                result = adapter.handle('one', action, Context())
                self.assertEqual(result, {'protocol': 'chaos-hid/1', 'id': 'one', 'status': 'acknowledged'})
                self.assertEqual(kernel.writes, ([expected] if expected else []) + [(100, bytes(8)), (101, bytes(4))])
                adapter.close()

    def test_invalid_actions_or_ids_do_not_write(self):
        kernel = FakeKernel()
        with kernel.installed():
            adapter = kernel.adapter()
            for action in ({'action': 'type', 'text': 'hello'}, {'action': 'key_tap', 'keys': [True]},
                           {'action': 'release', 'extra': 1}, {'action': 'mouse_move', 'x': 128, 'y': 0}):
                with self.assertRaises(ValueError):
                    adapter.handle('one', action, Context())
            for request_id in ('', 'a b', 1, 'x' * 129):
                with self.assertRaises(ValueError):
                    adapter.handle(request_id, {'action': 'release'}, Context())
            self.assertFalse(kernel.writes)
            adapter.close()

    def test_duplicate_and_exhausted_requests_do_not_write_again(self):
        kernel = FakeKernel()
        with kernel.installed():
            adapter = kernel.adapter()
            adapter.handle('one', {'action': 'release'}, Context())
            before = list(kernel.writes)
            with self.assertRaisesRegex(DeviceTransportError, 'consumed'):
                adapter.handle('one', {'action': 'release'}, Context())
            adapter.used_ids = {str(i) for i in range(4096)}
            with self.assertRaisesRegex(DeviceTransportError, 'budget'):
                adapter.handle('new', {'action': 'release'}, Context())
            self.assertEqual(before, kernel.writes)
            adapter.close()

    def test_eagain_polls_bounded_then_submits_once(self):
        kernel = FakeKernel()
        attempts = []
        def write(fd, report):
            if any(report):
                attempts.append(report)
                if len(attempts) == 1:
                    raise BlockingIOError(errno.EAGAIN, 'would block')
        kernel.write_hook = write
        with kernel.installed():
            adapter = kernel.adapter()
            adapter.handle('one', {'action': 'key_tap', 'keys': [4]}, Context())
            self.assertEqual(attempts, [b'\x00\x00\x04\x00\x00\x00\x00\x00'] * 2)
            self.assertTrue(all(1 <= duration <= 25 for duration in kernel.polls))
            adapter.close()

    def test_short_or_invalid_write_is_not_completed_or_replayed(self):
        for count in (0, 3, True, 9):
            kernel = FakeKernel()
            kernel.write_hook = lambda fd, report: count if any(report) else None
            with self.subTest(count=count), kernel.installed():
                adapter = kernel.adapter()
                with self.assertRaisesRegex(DeviceTransportError, 'Incomplete'):
                    adapter.handle('one', {'action': 'key_tap', 'keys': [4]}, Context())
                self.assertEqual(len([r for _, r in kernel.writes if any(r)]), 1)
                self.assertEqual(kernel.writes[-2:], [(100, bytes(8)), (101, bytes(4))])
                self.assertTrue(adapter.closed)
                self.assertFalse(kernel.fds)

    def test_poll_disconnect_is_uncertain_and_cleanup_runs(self):
        kernel = FakeKernel()
        def write(fd, report):
            if any(report):
                raise BlockingIOError(errno.EAGAIN, 'pending')
        kernel.write_hook = write
        kernel.poll_hook = lambda fd, duration: [(fd, select.POLLHUP)]
        with kernel.installed():
            adapter = kernel.adapter()
            with self.assertRaisesRegex(DeviceTransportError, 'disconnected'):
                adapter.handle('one', {'action': 'mouse_click', 'button': 1}, Context())
            self.assertEqual(kernel.writes[-2:], [(100, bytes(8)), (101, bytes(4))])
            self.assertFalse(kernel.fds)

    def test_timeout_stops_pending_report_and_does_not_replay(self):
        kernel = FakeKernel()
        def write(fd, report):
            if any(report):
                raise BlockingIOError(errno.EAGAIN, 'pending')
        kernel.write_hook = write
        kernel.poll_hook = lambda fd, duration: (time.sleep(duration / 1000), [])[1]
        with kernel.installed():
            adapter = kernel.adapter(timeout=0.05)
            started = time.monotonic()
            with self.assertRaisesRegex(DeviceTransportError, 'timed out'):
                adapter.handle('one', {'action': 'key_tap', 'keys': [4]}, Context())
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertFalse(kernel.fds)
            self.assertEqual(kernel.writes[-2:], [(100, bytes(8)), (101, bytes(4))])

    def test_cancel_before_press_still_cleans_up_without_nonzero_input(self):
        class Cancel(BaseException):
            pass
        signal = Cancel('cancel')
        def cancel():
            raise signal
        kernel = FakeKernel()
        with kernel.installed():
            adapter = kernel.adapter()
            with self.assertRaises(Cancel) as caught:
                adapter.handle('one', {'action': 'key_tap', 'keys': [4]}, Context(cancel))
            self.assertIs(caught.exception, signal)
            self.assertTrue(all(not any(report) for _, report in kernel.writes))
            self.assertFalse(kernel.fds)
            before = list(kernel.writes)
            adapter.close()
            self.assertEqual(before, kernel.writes)

    def test_cancel_after_submission_survives_close_failure(self):
        class Cancel(BaseException):
            pass
        signal = Cancel('cancel')
        kernel = FakeKernel()
        def cancel():
            if any(any(report) for _, report in kernel.writes):
                raise signal
        def close_error(fd):
            raise OSError(errno.EIO, 'close error')
        kernel.close_hook = close_error
        with kernel.installed():
            adapter = kernel.adapter()
            with self.assertRaises(Cancel) as caught:
                adapter.handle('one', {'action': 'key_tap', 'keys': [4]}, Context(cancel))
            self.assertIs(caught.exception, signal)
            self.assertEqual(kernel.writes[-2:], [(100, bytes(8)), (101, bytes(4))])
            self.assertFalse(kernel.fds)
            self.assertFalse(linux._LEASES)

    def test_write_error_preserved_when_both_cleanup_stages_fail(self):
        kernel = FakeKernel()
        original = OSError(errno.ENODEV, 'disconnected')
        def write(fd, report):
            if any(report):
                raise original
            raise OSError(errno.EIO, 'release error')
        def close_error(fd):
            raise OSError(errno.EIO, 'close error')
        kernel.write_hook = write
        kernel.close_hook = close_error
        with kernel.installed():
            adapter = kernel.adapter()
            with self.assertRaises(OSError) as caught:
                adapter.handle('one', {'action': 'mouse_click', 'button': 1}, Context())
            self.assertIs(caught.exception, original)
            self.assertEqual(kernel.writes[-2:], [(100, bytes(8)), (101, bytes(4))])
            self.assertFalse(kernel.fds)

    def test_one_release_timeout_cannot_skip_other_endpoint_or_close(self):
        kernel = FakeKernel()
        def write(fd, report):
            if fd == 100:
                raise BlockingIOError(errno.EAGAIN, 'not ready')
        kernel.write_hook = write
        kernel.poll_hook = lambda fd, duration: (time.sleep(duration / 1000), [])[1]
        with kernel.installed():
            adapter = kernel.adapter(timeout=0.05)
            with self.assertRaisesRegex(DeviceTransportError, 'timed out'):
                adapter.close()
            self.assertIn((101, bytes(4)), kernel.writes)
            self.assertFalse(kernel.fds)

    def test_failed_close_never_retries_reused_fd_number(self):
        kernel = FakeKernel()
        def close_error(fd):
            if fd == 101:
                # Linux close has freed the number even when reporting EINTR.
                kernel.fds[fd] = (os.makedev(4, 1), stat.S_IFCHR)
                raise InterruptedError(errno.EINTR, 'close interrupted')
        kernel.close_hook = close_error
        with kernel.installed():
            adapter = kernel.adapter()
            with self.assertRaises(InterruptedError) as first:
                adapter.close()
            writes = list(kernel.writes)
            with self.assertRaises(InterruptedError) as second:
                adapter.close()
            self.assertIs(first.exception, second.exception)
            self.assertEqual(kernel.closes, [101, 100])
            self.assertEqual(writes, kernel.writes)
            self.assertIn(101, kernel.fds)
            self.assertFalse(linux._LEASES)

    def test_cancel_preserved_and_cleanup_failure_remains_sticky(self):
        class Cancel(BaseException):
            pass
        signal = Cancel('cancel during submission')
        cleanup = OSError(errno.EIO, 'release failed')
        kernel = FakeKernel()
        def write(fd, report):
            if any(report):
                raise signal
            if fd == 100:
                raise cleanup
        kernel.write_hook = write
        with kernel.installed():
            adapter = kernel.adapter()
            with self.assertRaises(Cancel) as caught:
                adapter.handle('one', {'action': 'key_tap', 'keys': [4]}, Context())
            self.assertIs(caught.exception, signal)
            self.assertIn((101, bytes(4)), kernel.writes)
            before = (list(kernel.writes), list(kernel.closes))
            for _ in range(2):
                with self.assertRaises(OSError) as repeated:
                    adapter.close()
                self.assertIs(repeated.exception, cleanup)
            self.assertEqual(before, (kernel.writes, kernel.closes))

    def test_framework_retains_quarantine_after_release_failure(self):
        kernel = FakeKernel()
        cleanup = OSError(errno.EIO, 'release failed')
        def write(fd, report):
            if fd == 100 and not any(report):
                raise cleanup
        kernel.write_hook = write
        registry = Registry()
        for name in ('desk', 'alias'):
            registry.register_device(linux.linux_hid_device(name, '/fixture/keyboard', '/fixture/mouse'))
        with Framework(registry=registry, settings=Settings(':memory:')) as framework:
            first = framework.create_project('first', source='let d=await device_open("desk");await d(device_action(d,{action:"key_tap",keys:[4]}))', capabilities=['device.control'])
            second = framework.create_project('second', source='try {await device_open("alias")} catch e {e.code}', capabilities=['device.control'])
            with kernel.installed():
                with self.assertRaises(Exception) as failed:
                    framework.start('first', first['version'], None, mode='source').result(10)
                self.assertIn('release failed', str(failed.exception))
                self.assertFalse(kernel.fds)
                before = (list(kernel.opens), list(kernel.writes), list(kernel.closes))
                self.assertEqual(framework.start('second', second['version'], None, mode='source').result(10), 'device_busy')
                self.assertEqual(before, (kernel.opens, kernel.writes, kernel.closes))

    def test_close_is_idempotent_and_future_actions_are_denied(self):
        kernel = FakeKernel()
        with kernel.installed():
            adapter = kernel.adapter()
            adapter.close(); adapter.close()
            before = list(kernel.writes)
            with self.assertRaisesRegex(DeviceTransportError, 'closed'):
                adapter.handle('one', {'action': 'release'}, Context())
            self.assertEqual(before, kernel.writes)
            self.assertEqual(kernel.closes, [101, 100])

    def test_native_device_task_permissions_and_receipt_remain_unchanged(self):
        kernel = FakeKernel()
        registry = Registry()
        registry.register_device(linux.linux_hid_device('desk', '/fixture/keyboard', '/fixture/mouse'))
        with Framework(registry=registry, settings=Settings(':memory:')) as framework:
            denied = framework.create_project('denied', source='await device_open("desk")', capabilities=[])
            with kernel.installed():
                with self.assertRaises(Exception):
                    framework.start('denied', denied['version'], None, mode='source').result()
                self.assertFalse(kernel.opens)
            source = 'async with await device_open("desk") as d { device_receipt_info(await d(device_action(d,{action:"mouse_move",x:3,y:-2}))) }'
            allowed = framework.create_project('allowed', source=source, capabilities=['device.control'])
            with kernel.installed():
                result = framework.start('allowed', allowed['version'], None, mode='source').result()
                self.assertEqual(result['status'], 'acknowledged')
                self.assertFalse(result['targetVerified'])
                self.assertEqual(result['action'], 'mouse_move')
                self.assertFalse(kernel.fds)


class LinuxFunctionalRobotTests(unittest.TestCase):
    SOURCE = r'''
let policy=function_from(code_from("fn(value){ {action:\"mouse_move\",x:value.x+1,y:-value.y} }"));
agent planner {provider:"fixture.hid",tools:[]};
let reasoning=agent_bind(planner,[agent_tool("propose",policy)]);
let dispatch=compile_function(fn(target,action){target(action)},target="auto-flow");
let roles=team_from(input.roles);
async fn worker(value) {
 let proposal=await reasoning(value);
 async with await device_open("desk") as device {
  let action=device_action(device,proposal);
  let pending=dispatch(device,action);
  let cold=task_info(pending).state;
  let receipt=device_receipt_info(await pending);
  {state:cold,status:receipt.status,targetVerified:receipt.targetVerified,action:receipt.action}
 }
};
let robot=team_bind(roles,{main:worker});
try {await robot(input.move)} catch e {{error:e.code}}
'''

    def execute(self, mode='source', capabilities=('device.control',)):
        requests = []
        def provider(request, context):
            context.checkpoint()
            requests.append(request.input)
            return Final(request.history[-1]['output']) if request.history else ToolCall('propose', request.input)
        registry = Registry()
        registry.register_provider(ProviderSpec('fixture.hid', provider))
        registry.register_device(linux.linux_hid_device('desk', '/fixture/keyboard', '/fixture/mouse'))
        value = {'move': {'x': 2, 'y': 3}, 'roles': {'kind': 'team', 'name': 'robot', 'entry': 'main', 'roles': [
            {'id': 'main', 'parent': None, 'capabilities': list(capabilities),
             'agent': {'kind': 'agent', 'name': 'main', 'config': {'provider': 'local', 'tools': []}}}]}}
        kernel = FakeKernel()
        with Framework(registry=registry, settings=Settings(':memory:')) as framework:
            project = framework.create_project('robot', source=self.SOURCE, capabilities=['device.control'])
            with kernel.installed():
                run = framework.start('robot', project['version'], value, mode=mode)
                result = run.result(10)
                events = framework.events('robot', run_id=run.id)['items']
                self.assertFalse(kernel.fds)
        return result, kernel, requests, events

    def test_agent_generated_policy_team_compiled_function_and_device_form_one_program(self):
        result, kernel, requests, events = self.execute()
        self.assertEqual(result, {'state': 'created', 'status': 'acknowledged', 'targetVerified': False, 'action': 'mouse_move'})
        self.assertEqual(requests, [{'x': 2, 'y': 3}] * 2)
        self.assertEqual([report for _, report in kernel.writes if any(report)], [bytes([0, 3, 253, 0])])
        kinds = [event['type'] for event in events]
        self.assertIn('agent.observing', kinds)
        self.assertIn('device.action.completed', kinds)
        self.assertIn('team.role.completed', kinds)

    def test_main_role_cannot_inherit_project_hardware_authority(self):
        result, kernel, requests, events = self.execute(capabilities=())
        self.assertEqual(result, {'error': 'capability_error'})
        self.assertEqual(len(requests), 2)
        self.assertFalse(kernel.opens)
        self.assertFalse(kernel.writes)

    def test_source_and_graph_share_agent_policy_function_device_execution(self):
        source, source_kernel, source_requests, _ = self.execute('source')
        graph, graph_kernel, graph_requests, _ = self.execute('graph')
        self.assertEqual(source, graph)
        self.assertEqual(source_kernel.writes, graph_kernel.writes)
        self.assertEqual(source_requests, graph_requests)


if __name__ == '__main__':
    unittest.main()
