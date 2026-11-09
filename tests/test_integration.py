"""Real worker + plugin event loop + strict Indigo contract double, using loopback only."""
import queue
import socket
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch
import support
from indigo_host import IndigoHost
from shelly.rpc import Session
from shelly.worker import Worker
from simulator import LocalShelly, next_event, wait_for


class RunningPlugin:
    def __init__(self, endpoint, prefs=None, **worker_options):
        self.host = IndigoHost(seed=False, prefs=prefs)
        self.plugin = self.host.plugin
        self.plugin.pluginPrefs.update(discovery=False, hosts=endpoint or "")
        self.worker_options = worker_options
        self.errors = queue.Queue()

    def __enter__(self):
        def factory(*args, **kwargs):
            return Worker(*args, **kwargs, **self.worker_options)
        self.patcher = patch('plugin.Worker', side_effect=factory)
        self.patcher.start()
        def run():
            try:
                self.plugin.runConcurrentThread()
            except BaseException as exc:
                self.errors.put(exc)
        self.thread = threading.Thread(target=run, name='Indigo-test-event-loop', daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *unused):
        try:
            self.plugin.stopConcurrentThread()
            self.thread.join(4)
            if self.thread.is_alive():
                raise AssertionError('Plugin loop failed to stop')
            if not self.errors.empty():
                raise self.errors.get_nowait()
        finally:
            self.patcher.stop()
            self.host.close()

    def create(self, **kinds):
        wait_for(lambda: bool(self.plugin.catalog), message='discovery inventory')
        self.plugin.createDiscoveredDevices(kinds)
        return wait_for(lambda: list(self.host.devices.values()), message='explicit Indigo device creation')

    def action(self, dev, action, value=None):
        self.plugin.actionControlDevice(types.SimpleNamespace(deviceAction=action, actionValue=value), dev)


class IntegrationTests(unittest.TestCase):
    def test_actual_loop_discovers_creates_and_orders_rapid_relative_commands(self):
        with LocalShelly(dimmer=True) as sim, RunningPlugin(sim.endpoint) as host:
            devices = host.create(includeSwitches=False, includeDimmers=True)
            dev = next(d for d in devices if d.deviceTypeId == 'shellyDimmer')
            host.action(dev, 'SetBrightness', 35)
            host.action(dev, 'BrightenBy', 10)
            host.action(dev, 'BrightenBy', 10)
            host.action(dev, 'DimBy', 5)
            wait_for(lambda: dev.states['brightnessLevel'] == 50, message='35 + 10 + 10 - 5')
            targets = [item[1]['params']['brightness'] for item in list(sim.server.controls.queue)]
            self.assertEqual(targets, [35, 45, 55, 50])
            host.action(dev, 'BrightenBy', 90)
            wait_for(lambda: dev.states['brightnessLevel'] == 100)
            host.action(dev, 'DimBy', 100)
            wait_for(lambda: not dev.states['onOffState'])
            self.assertEqual(dev.states['brightnessLevel'], 0)
            self.assertEqual(len(host.host.devices), 1)
            self.assertFalse(any('Config' in method for method in list(sim.server.methods.queue)[-3:]))

    def test_real_loop_recovers_after_endpoint_move_and_retires_failed_old_probe(self):
        unavailable = threading.Event()
        def old_endpoint(handler, request):
            if unavailable.is_set():
                handler.connection.shutdown(socket.SHUT_RDWR)
                return True
        with LocalShelly(on_request=old_endpoint) as old, LocalShelly() as new, RunningPlugin(
                old.endpoint, poll_interval=.08, rpc_timeout=.3) as host:
            dev = host.create(includeSwitches=True)[0]
            saved_id, saved_name = dev.id, dev.name
            with self.assertLogs('test-plugin', level='DEBUG') as logs:
                unavailable.set()
                wait_for(lambda: not dev.states['online'], message='old endpoint unavailable')
                host.plugin._emit('bonjour', new.endpoint, 'shelly-simulator')
                wait_for(lambda: dev.pluginProps['host'] == new.endpoint and dev.states['online'],
                         message='same physical unit recovered at its new endpoint')
                wait_for(lambda: old.endpoint not in host.plugin.workers, message='finite vacated probe retired')
                host.plugin._emit('bonjour', old.endpoint, 'shelly-simulator')
                host.action(dev, 'TurnOn')
                wait_for(lambda: dev.states['onOffState'], message='control routed to the new endpoint')
                self.assertNotIn(old.endpoint, host.plugin.workers)
            transitions = [record.getMessage() for record in logs.records if record.levelname == 'INFO'
                           and 'address change' in record.getMessage()]
            self.assertEqual(len(transitions), 1)
            self.assertIn('recovered after address change', transitions[0])
            self.assertIn(old.endpoint, transitions[0])
            self.assertIn(new.endpoint, transitions[0])
            self.assertFalse(any(record.levelname == 'WARNING' and 'no Indigo devices assigned' in record.getMessage()
                                 for record in logs.records))
            self.assertEqual((dev.id, dev.name), (saved_id, saved_name))
            self.assertEqual(old.server.controls.qsize(), 0)
            self.assertEqual(new.server.controls.qsize(), 1)

    def test_live_press_release_after_clock_rollback_updates_state_and_native_trigger(self):
        fired = threading.Event()
        def on_request(handler, request):
            if request['method'] == 'Switch.Set' and not fired.is_set():
                fired.set()
                handler.notify('NotifyEvent', {'events': [
                    {'component': 'input:0', 'event': 'btn_down', 'ts': 100},
                    {'component': 'input:0', 'event': 'btn_up', 'ts': 90}]})
        with LocalShelly(button=True, on_request=on_request) as sim, RunningPlugin(sim.endpoint) as host:
            host.create(includeSwitches=True, includeInputs=True)
            wait_for(lambda: len(host.host.devices) == 2)
            button = next(d for d in host.host.devices.values() if d.deviceTypeId == 'shellyInput')
            relay = next(d for d in host.host.devices.values() if d.deviceTypeId == 'shellySwitch')
            trigger = types.SimpleNamespace(id=101, pluginTypeId='buttonPressed', enabled=True,
                                            pluginProps={'inputDeviceId': str(button.id)})
            module = sys.modules['indigo']
            module.triggers[101] = trigger
            host.plugin.triggerStartProcessing(trigger)
            host.action(relay, 'TurnOn')
            wait_for(lambda: relay.states['onOffState'] and button.states['stateKnown'])
            self.assertFalse(button.states['onOffState'])
            self.assertTrue(any(change.get('onOffState') is True for change in button.history))
            module.trigger.execute.assert_called_once()

    def test_configuration_change_notification_reenables_input_through_actual_loop(self):
        with LocalShelly(button=True, send_press_during_config=False) as sim, RunningPlugin(
                sim.endpoint, poll_interval=.08, config_interval=30) as host:
            host.create(includeSwitches=True, includeInputs=True)
            wait_for(lambda: len(host.host.devices) == 2)
            button = next(d for d in host.host.devices.values() if d.deviceTypeId == 'shellyInput')
            with sim.server.lock:
                sim.server.input_configs['input:0'].update(type='switch', enable=False)
                sim.server.state['input:0']['state'] = None
                sim.server.state['sys']['cfg_rev'] += 1
            wait_for(lambda: 'shelly-simulator/input:0' not in host.plugin.catalog)
            with sim.server.lock:
                sim.server.input_configs['input:0']['enable'] = True
                sim.server.state['input:0']['state'] = True
                sim.server.state['sys']['cfg_rev'] += 1
            wait_for(lambda: button.states['stateKnown'] and button.states['onOffState'])
            self.assertIn('shelly-simulator/input:0', host.plugin.catalog)

    def test_new_protected_discovery_gets_no_shared_digest_until_individual_password_is_set(self):
        with LocalShelly(password='shared') as sim, RunningPlugin(None, prefs={'password': 'shared'}) as host:
            host.plugin._emit('discover', sim.endpoint)
            wait_for(lambda: host.plugin.discovered_devices.get('shelly-simulator', {}).get('requiresPassword'))
            wait_for(lambda: 'Set Device Password' in str(host.plugin.outages))
            requests = [item[1] for item in list(sim.server.requests.queue)]
            self.assertFalse(any('auth' in request for request in requests))
            self.assertEqual(len(host.host.devices), 0)
            host.plugin.setDevicePassword({'shellyId': 'shelly-simulator', 'devicePassword': 'shared'})
            wait_for(lambda: bool(host.plugin.catalog))
            self.assertIn('shelly-simulator', host.plugin.pluginPrefs['devicePasswords'])
            self.assertFalse(host.plugin.discovered_devices['shelly-simulator']['requiresPassword'])
            self.assertTrue(any('auth' in item[1] for item in list(sim.server.requests.queue)))

    def test_disabling_all_channels_stops_real_worker_and_rediscovery_then_reenable_resumes(self):
        with LocalShelly(button=True) as sim, RunningPlugin(sim.endpoint) as host:
            host.create(includeSwitches=True, includeInputs=True)
            wait_for(lambda: len(host.host.devices) == 2)
            for dev in list(host.host.devices.values()):
                dev.enabled = False
                host.plugin.deviceStopComm(dev)
            wait_for(lambda: sim.endpoint not in host.plugin.workers)
            count = sim.server.methods.qsize()
            host.plugin._emit('bonjour', sim.endpoint, 'shelly-simulator')
            host.plugin._emit('discover', sim.endpoint)
            time.sleep(.08)
            self.assertEqual(sim.server.methods.qsize(), count)
            relay = next(dev for dev in host.host.devices.values() if dev.deviceTypeId == 'shellySwitch')
            relay.enabled = True
            host.plugin.deviceStartComm(relay)
            wait_for(lambda: sim.endpoint in host.plugin.workers and host.plugin.workers[sim.endpoint].online)
            host.action(relay, 'TurnOn')
            wait_for(lambda: relay.states['onOffState'])


class TransportIntegrationTests(unittest.TestCase):
    def worker(self, sim, **options):
        events = queue.Queue()
        worker = Worker(sim.endpoint, lambda *args: events.put(args), **options)
        worker.start()
        self.addCleanup(lambda: (worker.stop(), worker.join(3)))
        return worker, events

    def test_command_wakes_idle_worker_and_only_reads_affected_output(self):
        entered = threading.Event()
        original_wait = Session.wait
        def wait(session, wake, timeout):
            if timeout > .3:
                entered.set()
            return original_wait(session, wake, timeout)
        with LocalShelly(button=True) as sim, patch.object(Session, 'wait', wait):
            worker, events = self.worker(sim)
            try:
                next_event(events, lambda e: e[0] == 'ready')
                self.assertTrue(entered.wait(2))
                baseline = len(list(sim.server.methods.queue))
                started = time.monotonic()
                worker.command('Switch.Set', {'id': 0, 'on': True})
                arrived, request = sim.server.controls.get(timeout=2)
                self.assertLess(arrived - started, .3)
                next_event(events, lambda e: e[0] == 'status' and e[2].get('switch:0', {}).get('output') is True)
                wait_for(lambda: list(sim.server.methods.queue)[-1] == 'Switch.GetStatus')
                self.assertEqual(list(sim.server.methods.queue)[baseline:], ['Switch.Set', 'Switch.GetStatus'])
            finally:
                worker.stop()
                worker.join(3)

    def test_refreshes_for_multiple_channels_are_coalesced(self):
        with LocalShelly(dimmer=True) as sim:
            worker, events = self.worker(sim)
            try:
                next_event(events, lambda e: e[0] == 'ready')
                before = list(sim.server.methods.queue).count('Shelly.GetStatus')
                for _ in range(20):
                    worker.command('Shelly.GetStatus', {})
                next_event(events, lambda e: e[0] == 'snapshot')
                self.assertEqual(list(sim.server.methods.queue).count('Shelly.GetStatus') - before, 1)
            finally:
                worker.stop()
                worker.join(3)

    def test_half_open_connection_is_detected_by_lightweight_heartbeat(self):
        with LocalShelly() as sim:
            worker, events = self.worker(sim, poll_interval=.08, rpc_timeout=.1)
            try:
                next_event(events, lambda e: e[0] == 'ready')
                started = time.monotonic()
                sim.server.drop_replies = True
                event = next_event(events, lambda e: e[0] == 'offline', timeout=1)
                self.assertLess(time.monotonic() - started, .6)
                self.assertIn('Shelly.GetStatus', event[2])
                self.assertFalse(worker.online)
            finally:
                worker.stop()
                worker.join(3)

    def test_successful_digest_authentication_and_nonce_reuse_over_real_websocket(self):
        with LocalShelly(password='private-local-password') as sim:
            worker, events = self.worker(sim, password='private-local-password', expected_id='shelly-simulator')
            try:
                next_event(events, lambda e: e[0] == 'ready')
                worker.command('Switch.Set', {'id': 0, 'on': True})
                next_event(events, lambda e: e[0] == 'status' and e[2].get('switch:0', {}).get('output') is True)
                requests = [item[1] for item in list(sim.server.requests.queue)]
                authenticated = [request['auth'] for request in requests if 'auth' in request]
                counts = [int(auth['nc'], 16) for auth in authenticated]
                self.assertGreaterEqual(len(counts), 3)
                self.assertEqual(counts, sorted(set(counts)))
                self.assertNotIn('private-local-password', str(requests))
            finally:
                worker.stop()
                worker.join(3)

    def test_rogue_realm_gets_no_password_response(self):
        with LocalShelly(password='secret', challenge_realm='unrelated-device') as sim:
            worker, events = self.worker(sim, password='secret', expected_id='shelly-simulator')
            try:
                event = next_event(events, lambda e: e[0] == 'auth_required')
                self.assertIn('identity', event[2]['reason'])
                requests = [item[1] for item in list(sim.server.requests.queue)]
                self.assertFalse(any('auth' in request for request in requests))
            finally:
                worker.stop()
                worker.join(3)

    def test_vacated_address_probe_rejects_same_device_alias_before_authentication(self):
        with LocalShelly(password='secret') as sim:
            worker, events = self.worker(sim, password='secret', exclude_id='shelly-simulator')
            try:
                next_event(events, lambda e: e[0] == 'alias')
                self.assertEqual(list(sim.server.methods.queue), ['Shelly.GetDeviceInfo'])
            finally:
                worker.stop()
                worker.join(3)

    def test_disabled_identity_at_new_endpoint_is_stopped_before_protected_rpc(self):
        from shelly.connections import CommunicationPolicy
        policy = CommunicationPolicy()
        policy.replace({'shelly-simulator': False})
        with LocalShelly(password='secret') as sim:
            worker, events = self.worker(sim, password='secret', permitted=policy.permits)
            try:
                next_event(events, lambda event: event[0] == 'suspended')
                self.assertEqual(list(sim.server.methods.queue), ['Shelly.GetDeviceInfo'])
                self.assertFalse(worker.online)
            finally:
                worker.stop()
                worker.join(3)
