"""Indigo's communication checkbox must govern shared physical worker lifetime."""
import unittest
from unittest.mock import Mock, patch
import support
from indigo_host import IndigoHost


class CommunicationTests(unittest.TestCase):
    def setUp(self):
        self.host = IndigoHost()
        self.addCleanup(self.host.close)
        self.plugin = self.host.plugin

    def disable_all(self):
        for dev in self.host.devices.values():
            dev.enabled = False

    def test_stop_last_channel_retires_worker_and_late_failures_are_ignored(self):
        self.disable_all()
        worker = Mock(expected_id='shelly-unit')
        self.plugin.workers[self.host.address] = worker
        generation = self.plugin.connections.reserve(self.host.address)
        self.plugin.deviceStopComm(self.host.devices[1])
        self.plugin._handle(*self.plugin.events.get_nowait())
        worker.stop.assert_called_once()
        self.assertNotIn(self.host.address, self.plugin.workers)
        with self.assertNoLogs('test-plugin', level='WARNING'):
            self.plugin._handle('offline', self.host.address, 'TimeoutError', generation)

    def test_discovery_and_explicit_host_cannot_restart_disabled_only_unit(self):
        self.disable_all()
        self.plugin.pluginPrefs['hosts'] = self.host.address
        with patch('plugin.Worker') as factory:
            self.plugin._handle('discover', self.host.address, None)
        factory.assert_not_called()

    def test_one_stopped_channel_does_not_interrupt_enabled_sibling(self):
        relay, button = self.host.devices[1], self.host.devices[2]
        relay.name, button.name = 'Disabled relay', 'Enabled input'
        worker = Mock(expected_id='shelly-unit')
        self.plugin.workers[self.host.address] = worker
        # Stop callback must win even if the IOM collection still has an old enabled=True copy.
        self.plugin.deviceStopComm(relay)
        self.plugin._handle(*self.plugin.events.get_nowait())
        worker.stop.assert_not_called()
        self.plugin._handle('status', self.host.address, {'switch:0': {'output': False}, 'input:0': {'state': True}})
        self.assertTrue(relay.states['onOffState'])
        self.assertTrue(button.states['onOffState'])
        with self.assertLogs('test-plugin', level='WARNING') as logs:
            self.plugin._handle('offline', self.host.address, 'TimeoutError')
        self.assertIn('Enabled input', str(logs.output))
        self.assertNotIn('Disabled relay', str(logs.output))

    def test_disabling_without_callback_still_suppresses_queued_offline_warning(self):
        self.disable_all()
        with self.assertNoLogs('test-plugin', level='WARNING'):
            self.plugin._handle('offline', self.host.address, 'TimeoutError')
        self.assertNotIn(self.host.address, self.plugin.info)
        self.assertFalse(self.plugin.catalog['shelly-unit/switch:0']['online'])

    def test_paused_bonjour_hint_remembers_new_endpoint_without_querying_and_resume_uses_it(self):
        self.disable_all()
        self.plugin._sync_communication()
        with patch('plugin.Worker') as factory:
            self.plugin._handle('bonjour', '192.0.2.7:80', 'shelly-unit')
            factory.assert_not_called()
            relay = self.host.devices[1]
            self.assertEqual(relay.pluginProps['host'], self.host.address)
            relay.enabled = True
            self.plugin.deviceStartComm(relay)
            self.plugin._handle(*self.plugin.events.get_nowait())
            self.assertEqual(factory.call_args.args[0], '192.0.2.7:80')
            self.assertTrue(self.plugin.communication.permits('shelly-unit'))
            self.assertNotIn(relay.id, self.plugin.stopped_devices)

    def test_paused_hint_cannot_stop_different_active_unit_at_same_address(self):
        self.disable_all()
        info = {**self.host.info, 'id': 'unit-b'}
        self.plugin._handle('ready', '192.0.2.7:80', (info, self.host.status))
        self.plugin._handle('create_devices', '', {'shellyId': 'unit-b', 'includeSwitches': True})
        worker = Mock(expected_id='unit-b')
        self.plugin.workers['192.0.2.7:80'] = worker
        self.plugin._handle('bonjour', '192.0.2.7:80', 'shelly-unit')
        worker.stop.assert_not_called()
        self.assertIs(self.plugin.workers['192.0.2.7:80'], worker)

    def test_password_update_does_not_resume_disabled_communications(self):
        self.disable_all()
        self.plugin.setDevicePassword({'shellyId': 'shelly-unit', 'devicePassword': 'saved'})
        with patch('plugin.Worker') as factory:
            self.plugin._process_events()
        factory.assert_not_called()
        self.assertEqual(self.plugin.credentials.password('shelly-unit'), 'saved')

    def test_delete_last_device_pauses_worker_but_next_discovery_can_offer_it_again(self):
        worker = Mock(expected_id='shelly-unit')
        self.plugin.workers[self.host.address] = worker
        deleted = self.host.devices[1]
        self.host.devices.clear()
        self.plugin.deviceStopComm(deleted)
        self.plugin._handle(*self.plugin.events.get_nowait())
        worker.stop.assert_called_once()
        with patch('plugin.Worker') as factory:
            self.plugin._handle('bonjour', self.host.address, 'shelly-unit')
        factory.assert_called_once()


    def test_already_queued_control_is_cancelled_when_only_its_channel_stops(self):
        from shelly.worker import Worker
        relay = self.host.devices[1]
        self.plugin.deviceStopComm(relay)
        self.plugin._handle(*self.plugin.events.get_nowait())
        self.assertTrue(self.plugin.communication.permits('shelly-unit'))  # Input sibling is enabled.
        events = []
        worker = Worker(self.host.address, lambda *args: events.append(args), expected_id='shelly-unit',
                        permitted_command=self.plugin.communication.permits_command)
        session = Mock()
        worker._execute(session, 'Switch.Set', {'id': 0, 'on': True})
        session.call.assert_not_called()
        self.assertEqual(events[0][0], 'command_cancelled')
        session.call.return_value = {'state': False}
        worker._execute(session, 'Input.GetStatus', {'id': 0})
        session.call.assert_called_once_with('Input.GetStatus', {'id': 0})
