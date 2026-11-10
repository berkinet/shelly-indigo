"""Notification grace periods with an explicit clock; no hardware or timed sleeps."""
from pathlib import Path
import unittest
import time
import types
from unittest.mock import Mock, patch
from xml.etree import ElementTree
import support
from indigo_host import IndigoHost
from test_integration import RunningPlugin
from simulator import wait_for


class OutageNotificationTests(unittest.TestCase):
    def setUp(self):
        self.clock = Mock(return_value=100.0)
        self.host = IndigoHost(prefs={'lossNotificationDelay': '30'})
        self.addCleanup(self.host.close)
        self.plugin = self.host.plugin
        self.address = self.host.address
        self.identity = self.host.info['id']
        self.clock_patch = patch('plugin.time.monotonic', self.clock)
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)

    def tick(self, now):
        self.clock.return_value = now
        self.plugin._process_events()

    def offline(self, reason='RPC timeout'):
        self.plugin._handle('offline', self.address, reason)

    def ready(self, address=None):
        self.plugin._handle('ready', address or self.address, (self.host.info, self.host.status))

    def test_brief_outage_suppresses_loss_and_recovery_but_updates_indigo_immediately(self):
        with self.assertNoLogs('test-plugin', level='DEBUG'):
            self.offline()
            for dev in self.host.devices.values():
                self.assertFalse(dev.states['online'])
                self.assertEqual(dev.error, 'Offline')
            self.tick(129)
            self.offline('Connection reset by peer')
        with self.assertNoLogs('test-plugin', level='INFO'):
            self.ready()
            self.tick(200)
        self.assertFalse(self.plugin.outages)
        self.assertTrue(all(dev.states['online'] and dev.error is None for dev in self.host.devices.values()))

    def test_deadline_logs_latest_reason_without_waiting_for_retry_and_then_deduplicates(self):
        self.offline()
        self.clock.return_value = 115
        self.offline('Connection reset by peer')
        with self.assertNoLogs('test-plugin', level='DEBUG'):
            self.tick(129.999)
        with self.assertLogs('test-plugin', level='WARNING') as logs:
            self.tick(130)
        self.assertEqual(len(logs.records), 1)
        message = logs.records[0].getMessage()
        for text in ('Connection reset by peer', self.address, self.identity, 'active Indigo devices:'):
            self.assertIn(text, message)
        for dev in self.host.devices.values():
            self.assertIn(f'{dev.name} (Indigo {dev.id})', message)
        with self.assertNoLogs('test-plugin', level='DEBUG'):
            self.tick(180)
        with self.assertLogs('test-plugin', level='DEBUG') as logs:
            self.offline('Connection reset by peer')
            self.offline('Connection refused')
            self.ready()
        relevant = [r for r in logs.records if 'unavailable' in r.getMessage() or 'recovered' in r.getMessage()]
        self.assertEqual([r.levelname for r in relevant], ['DEBUG', 'WARNING', 'INFO'])

    def test_repeated_short_outages_each_get_a_fresh_delay(self):
        with self.assertNoLogs('test-plugin', level='INFO'):
            self.offline()
            self.clock.return_value = 120
            self.ready()
            self.clock.return_value = 125
            self.offline()
            self.tick(154)
        with self.assertLogs('test-plugin', level='WARNING'):
            self.tick(155)

    def test_pending_outage_cancels_when_last_indigo_channel_stops_communication(self):
        self.offline()
        with self.assertNoLogs('test-plugin', level='INFO'):
            for dev in self.host.devices.values():
                self.plugin._handle('stop_device', self.address, (dev.id, self.identity))
            self.tick(200)
        self.assertFalse(self.plugin.outages)

    def test_quick_recovery_at_new_ip_reports_address_change_without_loss_or_recovery(self):
        self.offline()
        self.clock.return_value = 110
        with self.assertLogs('test-plugin', level='INFO') as logs:
            self.ready('192.0.2.2:80')
            self.plugin._report_outages()
        self.assertEqual(len(logs.records), 1)
        self.assertIn('address changed', logs.records[0].getMessage())
        self.assertNotIn('recovered', logs.records[0].getMessage())
        self.assertFalse(self.plugin.outages)
        self.assertTrue(all(dev.pluginProps['host'] == '192.0.2.2:80' for dev in self.host.devices.values()))

    def test_reported_outage_at_new_ip_has_one_recovery_message(self):
        self.offline()
        with self.assertLogs('test-plugin', level='WARNING'):
            self.tick(130)
        with self.assertLogs('test-plugin', level='INFO') as logs:
            self.ready('192.0.2.2:80')
            self.ready('192.0.2.2:80')
        self.assertEqual(len(logs.records), 1)
        self.assertIn('recovered after address change', logs.records[0].getMessage())

    def test_unidentified_outage_keeps_deadline_after_identity_becomes_known(self):
        address = '192.0.2.40:80'
        self.plugin._handle('offline', address, 'Connect timed out')
        self.clock.return_value = 120
        self.plugin._handle('identified', address, {'id': 'shelly-other', 'gen': 4})
        self.plugin.workers[address] = Mock(expected_id='shelly-other')
        self.plugin._handle('offline', address, 'Status timed out')
        self.assertEqual(len(self.plugin.outages), 1)
        with self.assertLogs('test-plugin', level='WARNING') as logs:
            self.tick(130)
        message = logs.records[0].getMessage()
        self.assertIn('shelly-other', message)
        self.assertIn('no Indigo devices assigned', message)

    def test_pending_outage_after_ip_reuse_names_original_unit_and_does_not_stop_replacement(self):
        self.offline()
        replacement = {**self.host.info, 'id': 'replacement-unit', 'name': 'Replacement'}
        self.plugin._handle('ready', self.address, (replacement, self.host.status))
        worker = Mock(expected_id='replacement-unit', online=True)
        self.plugin.workers[self.address] = worker
        with self.assertLogs('test-plugin', level='WARNING') as logs:
            self.tick(130)
        message = logs.records[0].getMessage()
        self.assertIn(self.identity, message)
        self.assertNotIn('Replacement', message)
        # Start a second grace period, then disable the original unit while
        # a different active unit now owns the address.
        self.plugin.outages.clear()
        self.plugin._record_outage(self.address, self.identity, 'No response')
        for dev in self.host.devices.values():
            dev.enabled = False
        with self.assertNoLogs('test-plugin', level='INFO'):
            self.tick(160)
        self.assertFalse(self.plugin.outages)
        worker.stop.assert_not_called()
        self.assertIs(self.plugin.workers[self.address], worker)

    def test_saved_preference_applies_live_cancel_keeps_delay_and_zero_logs_immediately(self):
        self.offline()
        self.clock.return_value = 105
        self.plugin.closedPrefsConfigUi({'lossNotificationDelay': '0'}, True)
        with self.assertNoLogs('test-plugin', level='INFO'):
            self.plugin._process_events()
        with self.assertLogs('test-plugin', level='WARNING') as logs:
            self.plugin.closedPrefsConfigUi({'lossNotificationDelay': '0'}, False)
            self.plugin._process_events()
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(self.plugin.loss_notification_delay, 0)
        self.ready()
        with self.assertLogs('test-plugin', level='WARNING'):
            self.offline()

    def test_increased_delay_uses_original_outage_start_and_does_not_retract_reported_loss(self):
        self.offline()
        self.clock.return_value = 110
        self.plugin.closedPrefsConfigUi({'lossNotificationDelay': '60'}, False)
        with self.assertNoLogs('test-plugin', level='INFO'):
            self.tick(130)
            self.tick(159)
        with self.assertLogs('test-plugin', level='WARNING'):
            self.tick(160)
        self.plugin.closedPrefsConfigUi({'lossNotificationDelay': '120'}, False)
        self.plugin._process_events()
        with self.assertLogs('test-plugin', level='INFO') as logs:
            self.ready()
        self.assertIn('recovered', logs.records[0].getMessage())

    def test_delay_field_validation_defaults_and_invalid_saved_value(self):
        xml = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin/PluginConfig.xml'
        field = ElementTree.parse(xml).find("Field[@id='lossNotificationDelay']")
        self.assertEqual(field.get('defaultValue'), '0')
        for value in ('0', '60', ' 120 ', '86400'):
            with self.subTest(valid=value):
                self.assertTrue(self.plugin.validatePrefsConfigUi({'lossNotificationDelay': value})[0])
        for value in ('', '-1', '1.5', 'nan', 'inf', '86401', None, True):
            with self.subTest(invalid=value):
                result = self.plugin.validatePrefsConfigUi({'lossNotificationDelay': value})
                self.assertFalse(result[0])
                self.assertIn('lossNotificationDelay', result[2])
        invalid_host = IndigoHost(prefs={'lossNotificationDelay': 'invalid'})
        try:
            self.assertEqual(invalid_host.plugin.loss_notification_delay, 0)
        finally:
            invalid_host.close()


class NotificationLoopTests(unittest.TestCase):
    def test_running_adapter_delivers_deadline_without_another_failure_event(self):
        # Exercise the actual adapter loop with queued device messages and a local
        # clock offset, without changing the clock used by networking or sleeps.
        with RunningPlugin(None, prefs={'lossNotificationDelay': '30'}) as running:
            plugin = running.plugin
            plugin.startup()
            host = running.host
            plugin._emit('ready', host.address, (host.info, host.status))
            devices = running.create(includeSwitches=True)
            offset = [0]
            adapter_clock = types.SimpleNamespace(monotonic=lambda: time.monotonic() + offset[0])
            with patch('plugin.time', adapter_clock):
                with self.assertNoLogs('test-plugin', level='WARNING'):
                    plugin._emit('offline', host.address, 'Connection reset by peer')
                    wait_for(lambda: all(not dev.states['online'] for dev in devices))
                offset[0] = 31
                wait_for(lambda: any('unavailable' in record.getMessage()
                                    for record in plugin.indigo_log_handler.buffer),
                         message='expired notification with no new worker messages')
                losses = [r for r in plugin.indigo_log_handler.buffer if 'unavailable' in r.getMessage()]
                self.assertEqual(len(losses), 1)
                plugin._emit('ready', host.address, (host.info, host.status))
                wait_for(lambda: all(dev.states['online'] for dev in devices))
                recoveries = [r for r in plugin.indigo_log_handler.buffer if 'recovered' in r.getMessage()]
                self.assertEqual(len(recoveries), 1)
