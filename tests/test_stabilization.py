"""Cross-boundary regressions from the November review; no home network access."""
import json
import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch
import support
from indigo_host import IndigoHost
from shelly.credentials import Credentials
from shelly.model import Inventory
from shelly.rpc import AuthenticationError, ProtocolError, Session
from shelly.worker import Worker
from test_protocol import Socket
from test_worker import ReadSession


class StabilizationTests(unittest.TestCase):
    def setUp(self):
        self.host = IndigoHost()
        self.addCleanup(self.host.close)
        self.plugin = self.host.plugin

    def apply_worker(self, frames, initial=False):
        worker = Worker(self.host.address, self.plugin._handle)
        worker.device_info = dict(self.host.info)
        worker.online = not initial
        session = Session(Socket(frames), worker._notify)
        worker._reconcile(session, initial=initial, refresh_config=True)
        return worker

    def test_live_notification_during_config_read_wins_over_earlier_snapshot(self):
        self.apply_worker([
            {'id': 1, 'result': {'switch:0': {'output': False}, 'input:0': {'state': None}}},
            {'method': 'NotifyStatus', 'params': {'switch:0': {'output': True}}},
            {'id': 2, 'result': {'device': {}}},
            {'id': 3, 'result': {'type': 'button', 'enable': True}},
        ])
        self.assertTrue(self.host.devices[1].states['onOffState'])
        values = [change['onOffState'] for change in self.host.devices[1].history if 'onOffState' in change]
        self.assertEqual(values[-2:], [False, True])

    def test_startup_notification_before_snapshot_is_superseded(self):
        self.apply_worker([
            {'method': 'NotifyStatus', 'params': {'switch:0': {'output': True}}},
            {'id': 1, 'result': {'switch:0': {'output': False}}},
            {'id': 2, 'result': {'device': {}}},
        ], initial=True)
        self.assertFalse(self.host.devices[1].states['onOffState'])

    def test_pre_snapshot_press_is_delivered_but_later_sample_controls_final_position(self):
        worker = Worker(self.host.address, self.plugin._handle)
        worker.device_info = {**self.host.info, 'gen': 4, 'mac': 'E4B06365AF10'}
        worker.online = True
        self.plugin._handle('status', self.host.address, {'input:0': {'state': None, '_inputType': 'button'}})
        self.host.devices[2].history.clear()
        session = Session(Socket([
            {'method': 'NotifyEvent', 'params': {'events': [
                {'component': 'input:0', 'event': 'btn_down', 'ts': 10}]}},
            {'id': 1, 'result': {'switch:0': {'output': False}, 'input:0': {'state': None}}},
            {'id': 2, 'result': {'device': {}}},
            {'id': 3, 'result': {'type': 'button', 'enable': True}},
            {'id': 4, 'result': {'enable': True}},
        ]), worker._notify)
        with patch('shelly.worker.read_button_inputs', return_value={0: False}):
            worker._reconcile(session, refresh_config=True)
        self.assertTrue(any(change.get('onOffState') is True for change in self.host.devices[2].history))
        self.assertFalse(self.host.devices[2].states['onOffState'])

    def test_reenabled_input_and_configuration_type_changes_reconcile(self):
        inventory = Inventory()
        worker = Worker(self.host.address, lambda *args: None)
        worker.device_info = self.host.info
        session = ReadSession({'input:0': {'state': None}}, input_type='switch', enabled=False)
        inventory.merge(worker._read_status(session).status)
        self.assertFalse(inventory.channels['input:0'].supported)
        session.status = {'input:0': {'state': True}}
        session.enabled = True
        inventory.merge(worker._read_status(session).status)
        channel = inventory.channels['input:0']
        self.assertTrue(channel.supported)
        self.assertTrue(channel.states()['onOffState'])
        session.status, session.input_type = {'input:0': {'state': None}}, 'button'
        inventory.merge(worker._read_status(session).status)
        self.assertEqual(channel.states()['inputMode'], 'button')
        self.assertFalse(channel.states()['stateKnown'])

    def test_complete_snapshot_removes_channels_while_deltas_preserve_them(self):
        self.plugin._handle('status', self.host.address, {'sys': {'cfg_rev': 2}})
        self.assertTrue(self.host.devices[1].states['online'])
        self.plugin._handle('snapshot', self.host.address, {'input:0': {'state': False}})
        self.assertNotIn('shelly-unit/switch:0', self.plugin.catalog)
        self.assertFalse(self.host.devices[1].states['online'])
        self.assertEqual(self.host.devices[1].error, 'Channel unavailable')

    def test_address_reuse_starts_new_probe_and_fences_old_events(self):
        generation = self.plugin.connections.reserve(self.host.address)
        old = Mock(expected_id='shelly-unit')
        self.plugin.workers[self.host.address] = old
        with patch('plugin.Worker') as factory:
            self.plugin._handle('mismatch', self.host.address, {'id': 'replacement'}, generation)
        old.stop.assert_called_once()
        factory.assert_called_once()
        replacement_generation = self.plugin.connections.generations[self.host.address]
        self.assertNotEqual(generation, replacement_generation)
        self.plugin._handle('ready', self.host.address, ({**self.host.info, 'id': 'replacement'},
                            {'switch:0': {'output': True}}), replacement_generation)
        self.plugin._handle('ready', self.host.address, (self.host.info, self.host.status), generation)
        self.plugin._handle('offline', self.host.address, 'late old failure', generation)
        self.assertEqual(self.plugin.info[self.host.address]['id'], 'replacement')

    def test_dhcp_move_cleans_never_connected_worker_and_old_inventory(self):
        old = Mock(expected_id='shelly-unit')
        never = Mock(expected_id='shelly-unit')
        self.plugin.workers[self.host.address] = old
        self.plugin.workers['192.0.2.9:80'] = never
        self.plugin._handle('ready', '192.0.2.2:80', (self.host.info, self.host.status))
        old.stop.assert_called_once()
        never.stop.assert_called_once()
        self.assertNotIn(self.host.address, self.plugin.inventory)
        self.assertNotIn(self.host.address, self.plugin.component_reports)
        self.assertFalse(self.plugin.workers)
        self.assertEqual(self.host.devices[1].pluginProps['host'], '192.0.2.2:80')
        self.assertEqual(self.host.devices[1].id, 1)

    def test_same_object_concurrent_thread_restart_rebuilds_workers_and_accepts_events(self):
        self.plugin.pluginPrefs.update(discovery=False)
        with patch('plugin.Worker') as factory:
            self.plugin.sleep = Mock(side_effect=RuntimeError('injected loop failure'))
            with self.assertRaises(RuntimeError):
                self.plugin.runConcurrentThread()
            self.assertFalse(self.plugin.workers)
            def resumed_sleep(_):
                self.assertFalse(self.plugin.closing.is_set())
                self.assertTrue(self.plugin._emit('status', self.host.address, {}))
                raise self.plugin.StopThread()
            self.plugin.sleep = resumed_sleep
            self.plugin.runConcurrentThread()
            self.assertEqual(factory.call_count, 2)

    def test_offline_logs_are_deduplicated_and_recovery_is_visible(self):
        with self.assertLogs('test-plugin', level='DEBUG') as logs:
            self.plugin._handle('offline', self.host.address, 'RPC timeout')
            self.plugin._handle('offline', self.host.address, 'RPC timeout')
            self.plugin._handle('ready', self.host.address, (self.host.info, self.host.status))
        warnings = [record for record in logs.records if record.levelname == 'WARNING']
        self.assertEqual(len(warnings), 1)
        self.assertTrue(any('recovered' in line for line in logs.output))

    def test_bounded_queue_rejects_action_without_blocking(self):
        self.plugin.events = queue.Queue(maxsize=1)
        self.plugin.events.put(('refresh', self.host.address, None))
        started = time.monotonic()
        with self.assertLogs('test-plugin', level='ERROR'):
            self.assertFalse(self.plugin._emit('command', '', (1, 'on')))
        self.assertLess(time.monotonic() - started, .5)

    def test_device_xml_uses_documented_readonly_attribute(self):
        from pathlib import Path
        from xml.etree import ElementTree
        path = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin/Devices.xml'
        for ui in ElementTree.parse(path).findall('Device/ConfigUI'):
            for key in ('shellyId', 'component', 'host', 'model', 'generation'):
                field = ui.find(f"Field[@id='{key}']")
                self.assertEqual(field.get('readonly'), 'true')
                self.assertNotIn('readOnly', field.attrib)


class CredentialTests(unittest.TestCase):
    def test_unknown_discovery_does_not_receive_shared_password(self):
        store = Credentials('shared')
        store.authorize({'known'})
        self.assertEqual(store.password('unknown'), '')
        self.assertEqual(store.password('known'), 'shared')
        self.assertEqual(store.password('explicit', explicit_host=True), 'shared')
        saved = store.set('unknown', 'individual')
        self.assertEqual(store.password('unknown'), 'individual')
        self.assertEqual(Credentials('shared', saved).password('unknown'), 'individual')
        store.set('known', '')
        self.assertEqual(store.password('known'), '')

    def test_reject_unpinned_or_mismatched_realm_without_sending_digest(self):
        for identity in (None, 'expected'):
            challenge = json.dumps({'realm': 'rogue', 'nonce': 'nonce', 'algorithm': 'SHA-256'})
            sock = Socket([{'id': 1, 'error': {'code': 401, 'message': challenge}}])
            with self.assertRaises(AuthenticationError):
                Session(sock, lambda *args: None, 'secret', identity=identity).call('Shelly.GetStatus')
            self.assertEqual(len(sock.sent), 1)
            self.assertNotIn('auth', sock.sent[0])

    def test_invalid_notification_is_rejected_without_publishing(self):
        for frame in ([1, 2], {'method': 'NotifyStatus', 'params': []},
                      {'method': 'NotifyEvent', 'params': {'events': 'bad'}}):
            notify = Mock()
            with self.assertRaises(ProtocolError):
                Session(Socket([frame]), notify).call('Shelly.GetStatus')
            notify.assert_not_called()


class LifecycleEdgeTests(unittest.TestCase):
    def setUp(self):
        self.host = IndigoHost()
        self.addCleanup(self.host.close)
        self.plugin = self.host.plugin

    def test_dhcp_swap_reprobes_vacated_address_even_when_bonjour_update_was_earlier(self):
        address_a, address_b = self.host.address, '192.0.2.2:80'
        info_b = {**self.host.info, 'id': 'unit-b'}
        self.plugin._handle('ready', address_b, (info_b, self.host.status))
        self.plugin._handle('create_devices', '', {'shellyId': 'unit-b', 'includeSwitches': True})
        self.plugin.workers[address_a] = Mock(expected_id='shelly-unit')
        self.plugin.workers[address_b] = Mock(expected_id='unit-b')
        generation_a = self.plugin.connections.reserve(address_a)
        self.plugin.connections.reserve(address_b)
        with patch('plugin.Worker') as factory:
            factory.side_effect = lambda address, emit, password, expected, **kw: Mock(expected_id=expected)
            # Both new Bonjour updates arrived while old workers still existed.
            self.plugin._handle('discover', address_a, None)
            self.plugin._handle('discover', address_b, None)
            self.assertEqual(factory.call_count, 0)
            self.plugin._handle('mismatch', address_a, info_b, generation_a)
            generation_new = self.plugin.connections.generations[address_a]
            self.plugin._handle('ready', address_a, (info_b, self.host.status), generation_new)
            # B's ready event retired its old worker, so its mismatch may never arrive.
            self.plugin._process_events()
            self.assertIn(address_b, self.plugin.workers)
            generation_b = self.plugin.connections.generations[address_b]
            self.plugin._handle('ready', address_b, (self.host.info, self.host.status), generation_b)
        self.assertEqual(self.host.devices[1].pluginProps['host'], address_b)
        device_b = next(d for d in self.host.devices.values() if d.pluginProps['shellyId'] == 'unit-b')
        self.assertEqual(device_b.pluginProps['host'], address_a)
        self.assertTrue(device_b.states['online'])
        self.assertTrue(self.host.devices[1].states['online'])

    def test_stale_unassigned_endpoint_expires_but_configured_endpoint_remains(self):
        stale, configured = Mock(online=False), Mock(online=False)
        self.plugin.workers['192.0.2.8:80'] = stale
        self.plugin.workers[self.host.address] = configured
        self.plugin.connections.seen.update({'192.0.2.8:80': -1000, self.host.address: -1000})
        self.plugin._prune_connections()
        stale.stop.assert_called_once()
        configured.stop.assert_not_called()
        self.assertNotIn('192.0.2.8:80', self.plugin.workers)

    def test_full_worker_event_queue_is_visible_and_connection_is_rebuilt(self):
        with patch('plugin.Worker') as factory:
            self.plugin._connect('192.0.2.7:80')
            emit = factory.call_args.args[1]
            generation = self.plugin.connections.generations['192.0.2.7:80']
            self.plugin.events = queue.Queue(maxsize=1)
            self.plugin.events.put(('unrecognized', '', None))
            with self.assertRaises(BufferError):
                emit('status', '192.0.2.7:80', {'switch:0': {'output': True}})
            with self.assertLogs('test-plugin', level='WARNING') as logs:
                self.plugin._process_events()
            self.assertTrue(any('overloaded' in line for line in logs.output))
            self.assertNotEqual(self.plugin.connections.generations['192.0.2.7:80'], generation)
            self.assertEqual(factory.call_count, 2)

    def test_per_device_password_menu_persists_override_and_reconnects(self):
        self.assertTrue(self.plugin.setDevicePassword({'shellyId': 'shelly-unit', 'devicePassword': 'individual'}))
        with patch('plugin.Worker') as factory, self.assertLogs('test-plugin', level='DEBUG') as logs:
            self.plugin._process_events()
        self.assertEqual(json.loads(self.plugin.pluginPrefs['devicePasswords']), {'shelly-unit': 'individual'})
        self.assertEqual(self.plugin.credentials.password('shelly-unit'), 'individual')
        self.assertEqual(factory.call_count, 1)
        self.assertNotIn('individual', str(logs.output))
        self.assertEqual(len(self.host.devices), 2)
