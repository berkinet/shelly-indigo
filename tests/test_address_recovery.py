"""Physical recovery across IP moves and finite vacated-address discovery."""
import queue
import unittest
from unittest.mock import Mock, patch
import support
from indigo_host import IndigoHost
from shelly.worker import Worker
from simulator import LocalShelly, next_event


class AddressRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.host = IndigoHost()
        self.addCleanup(self.host.close)
        self.plugin = self.host.plugin
        self.old = self.host.address
        self.new = '192.0.2.2:80'
        self.identity = self.host.info['id']
        self.track(self.old, self.identity)

    def track(self, address, identity):
        worker = Mock(expected_id=identity, online=True)
        worker.is_alive.return_value = True
        self.plugin.workers[address] = worker
        generation = self.plugin.connections.reserve(address)
        return worker, generation

    @staticmethod
    def factory(address, emit, password, expected, **kwargs):
        worker = Mock(expected_id=expected, online=False)
        worker.is_alive.return_value = True
        worker.emit = emit
        worker.exclude_id = kwargs.get("exclude_id")
        return worker

    @staticmethod
    def transitions(logs):
        return [record.getMessage() for record in logs.records if record.levelname == 'INFO'
                and ('address change' in record.getMessage() or 'recovered' in record.getMessage())]

    def move(self):
        worker, generation = self.track(self.new, self.identity)
        self.plugin._handle('ready', self.new, (self.host.info, self.host.status), generation)
        return worker

    def test_failed_old_ip_then_new_ip_logs_one_physical_recovery_and_preserves_bindings(self):
        old_worker = self.plugin.workers[self.old]
        old_generation = self.plugin.connections.generations[self.old]
        saved = [(dev.id, dev.name) for dev in self.host.devices.values()]
        with self.assertLogs('test-plugin', level='DEBUG') as logs:
            self.plugin._handle('offline', self.old, 'RPC timed out', old_generation)
            self.move()
            self.plugin._handle('ready', self.new, (self.host.info, self.host.status))
            self.plugin._handle('offline', self.old, 'late failure', old_generation)
        transitions = self.transitions(logs)
        self.assertEqual(len(transitions), 1)
        self.assertIn('recovered after address change', transitions[0])
        for value in (self.old, self.new, self.identity, self.host.devices[1].name):
            self.assertIn(value, transitions[0])
        self.assertEqual(saved, [(dev.id, dev.name) for dev in self.host.devices.values()])
        self.assertTrue(all(dev.states['online'] and dev.error is None for dev in self.host.devices.values()))
        self.assertTrue(all(dev.pluginProps['host'] == self.new for dev in self.host.devices.values()))
        old_worker.stop.assert_called_once()
        self.assertFalse(self.plugin.outages)
        self.assertNotIn('late failure', str(logs.output))

    def test_healthy_move_logs_address_change_without_claiming_an_outage(self):
        with self.assertLogs('test-plugin', level='INFO') as logs:
            self.move()
            self.plugin._handle('ready', self.new, (self.host.info, self.host.status))
        self.assertEqual(len(self.transitions(logs)), 1)
        self.assertIn('address changed', self.transitions(logs)[0])
        self.assertNotIn('recovered', self.transitions(logs)[0])

    def test_failed_vacated_probe_is_retired_quietly_and_old_bonjour_cannot_revive_it(self):
        self.move()
        with patch('plugin.Worker', side_effect=self.factory) as factory:
            self.plugin._process_events()
            self.assertEqual(factory.call_args.kwargs['exclude_id'], self.identity)
            probe = self.plugin.workers[self.old]
            with self.assertLogs('test-plugin', level='DEBUG') as logs:
                probe.emit('probe_failed', self.old, 'Connection timed out')
                self.plugin._process_events()
                self.plugin._handle('bonjour', self.old, self.identity)
                self.plugin._handle('bonjour', self.old, self.identity)
                self.plugin._process_events()
            self.assertEqual(factory.call_count, 1)
        self.assertFalse(any(record.levelname == 'WARNING' for record in logs.records))
        self.assertNotIn(self.old, self.plugin.workers)
        self.assertFalse(self.plugin.pending_probes)
        self.assertFalse(self.plugin.outages)
        probe.stop.assert_called_once()
        self.assertTrue(self.host.devices[1].states['online'])

    def test_probe_queue_overload_does_not_restart_it_as_persistent_worker(self):
        self.move()
        with patch('plugin.Worker', side_effect=self.factory) as factory:
            self.plugin._process_events()
            probe = self.plugin.workers[self.old]
            self.plugin.events = queue.Queue(maxsize=1)
            self.plugin.events.put(('unrecognized', '', None))
            with self.assertRaises(BufferError):
                probe.emit('probe_failed', self.old, 'No answer')
            with self.assertNoLogs('test-plugin', level='WARNING'):
                self.plugin._process_events()
            self.assertEqual(factory.call_count, 1)
            self.assertNotIn(self.old, self.plugin.workers)
            self.assertFalse(self.plugin.outages)

    def test_vacated_address_can_be_reused_by_another_verified_unit(self):
        self.move()
        self.plugin.pending_probes.clear()
        info = {**self.host.info, 'id': 'replacement-unit'}
        with patch('plugin.Worker', side_effect=self.factory):
            self.plugin._handle('bonjour', self.old, info['id'])
            probe = self.plugin.workers[self.old]
            probe.emit('ready', self.old, (info, self.host.status))
            self.plugin._process_events()
        self.assertEqual(self.plugin.connections.owners[self.identity], self.new)
        self.assertEqual(self.plugin.connections.owners['replacement-unit'], self.old)
        self.assertTrue(self.host.devices[1].states['online'])
        self.assertEqual(self.host.devices[1].pluginProps['host'], self.new)
        self.assertIn('replacement-unit/switch:0', self.plugin.catalog)

    def test_deferred_old_hint_is_rechecked_if_current_owner_goes_offline(self):
        self.move()
        self.plugin.pending_probes.clear()
        with patch('plugin.Worker', side_effect=self.factory) as factory:
            self.plugin._handle('bonjour', self.old, self.identity)
            factory.assert_not_called()
            self.plugin.workers[self.new].online = False
            self.plugin._handle('offline', self.new, 'Connection lost')
            self.assertEqual(factory.call_args.args[0], self.old)
            self.assertEqual(factory.call_args.args[3], self.identity)
            probe = self.plugin.workers[self.old]
            with self.assertLogs('test-plugin', level='INFO') as logs:
                probe.emit('ready', self.old, (self.host.info, self.host.status))
                self.plugin._process_events()
            self.assertEqual(len(self.transitions(logs)), 1)
            self.assertIn('recovered after address change', self.transitions(logs)[0])
        self.assertEqual(self.host.devices[1].pluginProps['host'], self.old)
        self.assertTrue(self.host.devices[1].states['online'])

    def test_old_failure_cannot_be_misattributed_to_other_unit_reusing_that_ip(self):
        info_b = {**self.host.info, 'id': 'unit-b'}
        self.plugin._handle('ready', self.new, (info_b, self.host.status))
        self.plugin._handle('create_devices', '', {'shellyId': 'unit-b', 'includeSwitches': True})
        self.track(self.new, 'unit-b')
        self.plugin._handle('offline', self.old, 'A unavailable')
        # Address reuse starts with a separately verified worker, after old generation retirement.
        self.plugin._retire(self.old)
        self.track(self.old, 'unit-b')
        with self.assertLogs('test-plugin', level='INFO') as logs:
            self.plugin._handle('ready', self.old, (info_b, self.host.status))
        self.assertEqual(len(self.transitions(logs)), 1)
        self.assertNotIn('recovered', self.transitions(logs)[0])
        self.assertNotIn(self.identity, self.plugin.connections.owners)
        self.track(self.new, self.identity)
        with self.assertLogs('test-plugin', level='INFO') as logs:
            self.plugin._handle('ready', self.new, (self.host.info, self.host.status))
        self.assertEqual(len(self.transitions(logs)), 1)
        self.assertIn('recovered after address change', self.transitions(logs)[0])
        self.assertEqual(self.plugin.connections.owners['unit-b'], self.old)
        self.assertTrue(all(dev.states['online'] for dev in self.host.devices.values()))

    def test_initial_saved_address_and_disabled_sibling_do_not_repeat_move_logs(self):
        # A first successful connection after plugin startup may already be at a new IP.
        self.plugin.connections.last_addresses.clear()
        self.plugin._retire(self.old)
        self.host.devices[2].enabled = False
        with self.assertLogs('test-plugin', level='INFO') as logs:
            self.move()
        self.assertEqual(len(self.transitions(logs)), 1)
        self.assertEqual(self.host.devices[2].pluginProps['host'], self.old)
        self.plugin._retire(self.new)  # E.g. a saved-credential reconnect.
        with self.assertNoLogs('test-plugin', level='INFO'):
            self.plugin._handle('ready', self.new, (self.host.info, self.host.status))
        self.assertEqual(self.host.devices[1].pluginProps['host'], self.new)


class VacatedProbeWorkerTests(unittest.TestCase):
    def test_unreachable_vacated_endpoint_has_one_attempt_and_no_outage_warning(self):
        events = queue.Queue()
        connector = Mock(side_effect=TimeoutError('No answer'))
        worker = Worker('192.0.2.1:80', lambda *event: events.put(event),
                        connector=connector, exclude_id='moved-unit')
        worker.start()
        worker.join(2)
        try:
            self.assertFalse(worker.is_alive(), 'A failed vacated probe must finish without retrying')
            connector.assert_called_once()
            self.assertEqual(events.get_nowait()[0], 'probe_failed')
            self.assertTrue(events.empty())
        finally:
            worker.stop()
            worker.join(2)

    def test_different_protected_unit_gets_normal_password_onboarding(self):
        with LocalShelly(identity='replacement-unit', password='secret') as sim:
            events = queue.Queue()
            worker = Worker(sim.endpoint, lambda *event: events.put(event), exclude_id='moved-unit')
            try:
                worker.start()
                event = next_event(events, lambda e: e[0] in ('probe_failed', 'auth_required'))
                self.assertEqual(event[0], 'auth_required')
                self.assertEqual(event[2]['id'], 'replacement-unit')
                self.assertTrue(worker.is_alive())
            finally:
                worker.stop()
                worker.join(3)
