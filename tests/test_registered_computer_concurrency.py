"""Real scheduler/host concurrency boundaries; deterministic driver fixtures only."""
import threading
import time
import unittest

from chaos import DeviceSpec, Framework, Registry, Settings
from chaos.computer_adapters import ComputerSpec
from test_registered_computers import Driver


class RegisteredComputerConcurrencyTests(unittest.TestCase):
    def app(self, registry):
        settings = Settings(':memory:')
        settings.update({'runtime': {'browserEnabled': False}}, 1)
        app = Framework(registry=registry, settings=settings)
        self.addCleanup(app.close)
        return app
    def start(self, app, source, capabilities=('computer.control',)):
        project = app.create_project('p', source=source, capabilities=list(capabilities))
        return app.start('p', project['version'], None, mode='source')

    def test_independent_physical_devices_observe_concurrently_at_real_barrier(self):
        registry = Registry()
        barrier = threading.Barrier(2)
        calls, drivers = [], []
        class BarrierDriver(Driver):
            def call(self, command, checkpoint):
                if command['action'] == 'observe':
                    checkpoint()
                    barrier.wait(3)
                    checkpoint()
                return super().call(command, checkpoint)
        def factory(context):
            context.checkpoint()
            driver = BarrierDriver(calls)
            drivers.append(driver)
            return driver
        for name in ('first', 'second'):
            registry.register_computer(ComputerSpec(name, factory, resource_id='physical:' + name))
        app = self.app(registry)
        source = '''let a=await computer_attach("first");let b=await computer_attach("second");
let observations=await gather([computer_observe(a,false),computer_observe(b,false)]);
map(fn(o){observation_info(o).snapshotId},observations)'''
        self.assertEqual(self.start(app, source).result(6), [1, 1])
        self.assertFalse(barrier.broken)
        self.assertEqual(len(drivers), 2)
        self.assertTrue(all(driver.close_count == 1 for driver in drivers))

    def test_concurrent_factory_reservations_admit_four_and_reject_fifth(self):
        registry = Registry()
        lock = threading.Lock()
        four_entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        entered, active, peak, drivers = [], [0], [0], []
        def factory(context):
            context.checkpoint()
            with lock:
                entered.append(1)
                active[0] += 1
                peak[0] = max(peak[0], active[0])
                if len(entered) == 4:
                    four_entered.set()
            try:
                if not release.wait(4):
                    raise AssertionError('factory capacity fixture was never released')
                context.checkpoint()
                driver = Driver([])
                drivers.append(driver)
                return driver
            finally:
                with lock:
                    active[0] -= 1
        for number in range(5):
            registry.register_computer(ComputerSpec('device' + str(number), factory,
                resource_id='physical:' + str(number)))
        app = self.app(registry)
        source = '''async fn open(name){try{type_of(await computer_attach(name))}catch e{e.code}};
await gather([open("device0"),open("device1"),open("device2"),open("device3"),open("device4")])'''
        run = self.start(app, source)
        self.assertTrue(four_entered.wait(3), 'Four distinct factories must be able to run concurrently')
        release.set()
        result = run.result(6)
        self.assertEqual(result.count('Computer'), 4)
        self.assertEqual(result.count('computer_error'), 1)
        self.assertEqual(len(entered), 4)
        self.assertEqual(peak[0], 4)
        self.assertTrue(all(driver.close_count == 1 for driver in drivers))

    def test_project_grant_revocation_interrupts_suspended_driver_read_checkpoint(self):
        registry = Registry()
        reading, retired = threading.Event(), threading.Event()
        drivers = []
        class WaitingDriver(Driver):
            def call(self, command, checkpoint):
                reading.set()
                until = time.monotonic() + 4
                while time.monotonic() < until:
                    checkpoint()
                    time.sleep(0.005)
                raise AssertionError('Revoked grant was not observed at driver checkpoint')
            def close(self):
                super().close()
                retired.set()
        def factory(context):
            context.checkpoint()
            driver = WaitingDriver([])
            drivers.append(driver)
            return driver
        registry.register_computer(ComputerSpec('phone', factory))
        app = self.app(registry)
        run = self.start(app, 'let c=await computer_attach("phone");await computer_observe(c,false)')
        self.assertTrue(reading.wait(3))
        project = app.get_project('p')
        app.edit_program('p', project['version'], source=project['source'], capabilities=[])
        result = run.wait(5)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('computer.control', result['error'])
        self.assertTrue(retired.is_set())
        self.assertEqual(drivers[0].close_count, 1)
        self.assertFalse(any(row['status'] == 'completed' and row['kind'] == 'observation'
                             for row in app.computer_history('p')['items']))

    def test_registered_computer_and_hid_share_one_physical_resource_lease(self):
        registry = Registry()
        calls = []
        class HID:
            def handle(self, request_id, spec, context):
                raise AssertionError('No HID input is authorized by this fixture')
            def close(self):
                calls.append(('hid-close', {}))
        registry.register_computer(ComputerSpec('phone', lambda ctx: Driver(calls), resource_id='physical:shared'))
        registry.register_device(DeviceSpec('keys', lambda ctx: HID(), resource_id='physical:shared'))
        app = self.app(registry)
        source = '''let c=await computer_attach("phone");
let blocked=try{await device_open("keys")}catch e{e.code};
await computer_close(c);let device=await device_open("keys");
[blocked,type_of(device)]'''
        self.assertEqual(self.start(app, source, ('computer.control', 'device.control')).result(6),
                         ['device_busy', 'Device'])
        self.assertEqual([row[0] for row in calls], ['close', 'hid-close'])
