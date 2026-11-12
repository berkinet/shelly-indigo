"""Firmware-prefix changes preserve physical bindings without relaxing MAC/realm checks."""
import json
import queue
import socket
import sys
import types
import threading
import unittest
from unittest.mock import Mock, patch
import support
from indigo_host import IndigoHost
from shelly.bindings import matches_physical_identity
from shelly.connections import CommunicationPolicy
from shelly.inventory_report import DeviceHistory
from shelly.worker import Worker
from simulator import LocalShelly, next_event, wait_for
from test_integration import RunningPlugin

OLD = 'shellyoldmodel-020000000001'
NEW = 'shellynewmodel-020000000001'
MAC = '020000000001'


class IdentityAliasWorkerTests(unittest.TestCase):
    def test_protected_alias_keeps_password_key_but_uses_new_rpc_sources_and_digest_realm(self):
        lookup = Mock(return_value='secret')
        with LocalShelly(identity=NEW, gen=4, mac=MAC, password='secret') as sim:
            events = queue.Queue()
            worker = Worker(sim.endpoint, lambda *event: events.put(event), expected_id=OLD, password=lookup)
            try:
                worker.start()
                ready = next_event(events, lambda e: e[0] in ('ready', 'offline', 'auth_required', 'mismatch'))
                self.assertEqual(ready[0], 'ready')
                self.assertEqual(ready[2][0]['id'], OLD)
                self.assertEqual(ready[2][0]['rpc_id'], NEW)
                self.assertTrue(worker.online)
                lookup.assert_called_once_with(OLD)
                # GetStatus responses use NEW as src. Successful authenticated
                # reconciliation proves source and digest realm pinning use NEW.
                requests = [request for _, request in list(sim.server.requests.queue)]
                self.assertTrue(any(request.get('auth', {}).get('realm') == NEW for request in requests))
            finally:
                worker.stop()
                worker.join(3)

    def test_missing_inconsistent_or_different_mac_never_accesses_saved_password(self):
        for mac, identity in ((None, NEW), ('020000000002', NEW), (MAC, 'shellynewmodel-020000000002')):
            with self.subTest(mac=mac, identity=identity), LocalShelly(identity=identity, gen=4, mac=mac) as sim:
                lookup = Mock(return_value='secret')
                events = queue.Queue()
                worker = Worker(sim.endpoint, lambda *event: events.put(event), expected_id=OLD, password=lookup)
                try:
                    worker.start()
                    event = next_event(events, lambda e: e[0] in ('mismatch', 'offline', 'ready'))
                    self.assertIn(event[0], ('mismatch', 'offline'))
                    lookup.assert_not_called()
                    self.assertEqual(list(sim.server.methods.queue), ['Shelly.GetDeviceInfo'])
                finally:
                    worker.stop()
                    worker.join(3)
        self.assertFalse(matches_physical_identity('shellyoldmodel-000001', {'id': NEW, 'mac': MAC}))

    def test_vacated_alias_stops_before_password_lookup_even_after_prefix_change(self):
        with LocalShelly(identity=NEW, gen=4, mac=MAC) as sim:
            events = queue.Queue()
            lookup = Mock()
            worker = Worker(sim.endpoint, lambda *event: events.put(event), password=lookup, exclude_id=OLD)
            try:
                worker.start()
                event = next_event(events, lambda e: e[0] in ('alias', 'ready'))
                self.assertEqual(event[0], 'alias')
                worker.join(2)
                self.assertFalse(worker.is_alive())
                lookup.assert_not_called()
            finally:
                worker.stop()
                worker.join(3)


class IdentityAliasAdapterTests(unittest.TestCase):
    def setUp(self):
        self.host = IndigoHost()
        self.addCleanup(self.host.close)
        self.plugin = self.host.plugin
        for dev in self.host.devices.values():
            dev.pluginProps['shellyId'] = OLD
            dev.pluginProps['address'] = OLD + '/' + dev.pluginProps['component']
        self.plugin.device_history = DeviceHistory()

    def test_bonjour_new_prefix_selects_existing_id_and_disabled_unit_stays_disabled(self):
        with patch('plugin.Worker') as factory:
            self.plugin._handle('bonjour', '192.0.2.20:80', NEW)
            self.assertEqual(factory.call_args.args[3], OLD)
        self.plugin._retire('192.0.2.20:80')
        for dev in self.host.devices.values():
            dev.enabled = False
        with patch('plugin.Worker') as factory:
            self.plugin._handle('bonjour', '192.0.2.21:80', NEW)
            factory.assert_not_called()
        self.assertEqual(self.plugin.catalog_service.get_device(OLD)['host'], '192.0.2.21:80')
        policy = CommunicationPolicy()
        policy.replace({OLD: False})
        self.assertFalse(policy.permits(NEW))
        self.assertFalse(policy.permits_command(NEW, 'Switch.Set', {'id': 0}))
        policy.replace({OLD: True})
        self.assertTrue(policy.permits(NEW))

    def test_explicit_unhinted_alias_is_fenced_and_restarted_with_saved_identity(self):
        info = {'id': NEW, 'mac': MAC, 'gen': 4, 'model': 'test'}
        old_worker = Mock(expected_id=NEW)
        self.plugin.workers['192.0.2.20:80'] = old_worker
        with patch('plugin.Worker') as factory:
            self.plugin._handle('identified', '192.0.2.20:80', info)
            old_worker.stop.assert_called_once()
            self.assertEqual(factory.call_args.args[3], OLD)

    def test_inventory_merges_old_and_new_prefixes_preserving_names_online_data_and_date(self):
        history = self.plugin.device_history
        history.observe('192.0.2.10:80', {'id': OLD, 'mac': MAC, 'name': 'Old name'})
        history.offline('192.0.2.10:80')
        # Reproduce a history saved by the prior version: both IDs exist.
        history.records[NEW] = {'id': NEW, 'mac': MAC, 'host': '192.0.2.20:80',
                                'name': 'Current local name', 'last_seen': 2000000000}
        history.online.add(NEW)
        bindings = [{**dev.pluginProps, 'indigoName': dev.name} for dev in self.host.devices.values()]
        rows = history.rows(bindings, [{'id': 'cloud:' + MAC, 'cloud_id': MAC, 'mac': MAC}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], OLD)
        self.assertTrue(rows[0]['online'])
        self.assertEqual(rows[0]['host'], '192.0.2.20:80')
        self.assertEqual(rows[0]['name'], 'Current local name')
        self.assertEqual(set(rows[0]['indigo_names']), {dev.name for dev in self.host.devices.values()})
        # An accepted live binding clears obsolete persisted history aliases.
        history.observe('192.0.2.20:80', {'id': OLD, 'rpc_id': NEW, 'mac': MAC})
        self.assertEqual(set(json.loads(history.saved())), {OLD})
        self.assertNotIn('rpc_id', history.saved())

    def test_local_id_alias_notice_is_logged_once_and_preserves_existing_device_ids(self):
        info = {'id': OLD, 'rpc_id': NEW, 'mac': MAC, 'gen': 4}
        with self.assertLogs('test-plugin', level='INFO') as logs:
            self.plugin._handle('identified', self.host.address, info)
            self.plugin._handle('identified', self.host.address, info)
        self.assertEqual(len(logs.records), 1)
        self.assertIn('retaining Indigo bindings', logs.records[0].getMessage())
        self.assertEqual(set(self.host.devices), {1, 2})


class IdentityAliasIntegrationTests(unittest.TestCase):
    def test_real_worker_restores_original_input_binding_without_creating_an_indigo_device(self):
        unavailable = threading.Event()
        pressed = threading.Event()
        def press_event(handler, request):
            if request['method'] == 'Shelly.GetStatus' and pressed.is_set():
                pressed.clear()
                handler.notify('NotifyEvent', {'events': [{'component': 'input:0', 'event': 'btn_down', 'ts': 100}]})
        def disconnect(handler, request):
            if unavailable.is_set():
                handler.connection.shutdown(socket.SHUT_RDWR)
                return True
        with LocalShelly(identity=OLD, gen=4, mac=MAC, button=True, on_request=disconnect) as old, \
                LocalShelly(identity=NEW, gen=4, mac=MAC, button=True, on_request=press_event) as new, \
                RunningPlugin(old.endpoint, poll_interval=.08, rpc_timeout=.3) as running:
            plugin, host = running.plugin, running.host
            devices = running.create(includeInputs=True, includeSwitches=False)
            for dev in devices:
                dev.name = 'Existing input alias'
            original = {dev.id: dev.name for dev in devices}
            trigger = types.SimpleNamespace(id=5, enabled=True, pluginTypeId='buttonPressed',
                                            pluginProps={'inputDeviceId': str(devices[0].id)})
            sys.modules['indigo'].triggers[trigger.id] = trigger
            plugin.triggerStartProcessing(trigger)
            unavailable.set()
            wait_for(lambda: all(not dev.states['online'] for dev in devices))
            plugin._emit('bonjour', new.endpoint, NEW)
            wait_for(lambda: all(dev.states['online'] and dev.error is None and dev.pluginProps['host'] == new.endpoint for dev in devices))
            self.assertEqual({dev.id: dev.name for dev in host.devices.values()}, original)
            self.assertTrue(all(dev.pluginProps['shellyId'] == OLD for dev in devices))
            wait_for(lambda: OLD in plugin.device_history.online)
            self.assertEqual(len(plugin.device_history.rows([dev.pluginProps for dev in devices])), 1)
            execute = sys.modules['indigo'].trigger.execute
            execute.assert_not_called()
            pressed.set()
            wait_for(lambda: execute.call_count == 1, message='existing Button Pressed trigger receives new firmware notifications')
