"""Inventory requirements: physical identity, persisted local evidence and cloud-only rows."""
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree
import support
from indigo_host import IndigoHost
from shelly.inventory_report import DeviceHistory, InventoryJob, cloud_rows, render_report


class InventoryReportTests(unittest.TestCase):
    def setUp(self):
        self.info = {'id': 'shelly2pmg4-020000000001', 'mac': '020000000001',
                     'name': 'Kitchen remote', 'model': 'S4SW-002P16EU'}

    def test_history_survives_restart_with_last_success_but_never_assumes_online(self):
        history = DeviceHistory()
        with patch('shelly.inventory_report.time.time', return_value=1700000000):
            history.observe('192.0.2.10:80', self.info)
        self.assertEqual(history.rows()[0]['online'], True)
        saved = history.saved()
        restored = DeviceHistory(saved)
        row = restored.rows()[0]
        self.assertFalse(row['online'])
        self.assertEqual(row['last_seen'], 1700000000)
        report = render_report(restored.rows(), 'Local LAN only')
        self.assertIn('Offline', report)
        self.assertNotIn('Never', report)
        self.assertNotIn('online"', saved)

    def test_cloud_merge_deduplicates_channels_and_mac_and_preserves_local_status_name_and_address(self):
        history = DeviceHistory()
        history.observe('192.0.2.10:80', self.info)
        remote = {'id': 'cloud:020000000001', 'cloud_id': '020000000001', 'mac': '020000000001',
                  'host': '192.0.2.90', 'name': 'Cloud name', 'model': 'Cloud model', 'online': False}
        props = {'shellyId': self.info['id'], 'host': '192.0.2.10:80'}
        rows = history.rows([props, props], [remote])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'Kitchen remote')
        self.assertEqual(rows[0]['host'], '192.0.2.10:80')
        self.assertTrue(rows[0]['online'])
        history.offline('192.0.2.10:80')
        self.assertFalse(history.rows(cloud=[{**remote, 'online': True}])[0]['online'])
        self.assertNotIn('Cloud name', history.saved())

    def test_cloud_only_and_never_contacted_bindings_are_offline_never_and_do_not_persist_cloud_data(self):
        history = DeviceHistory()
        response = {'data': {'devices_status': {'unreliable-map-key': {
            '_dev_info': {'id': '020000000002', 'code': 'S3SW-001P8EU', 'gen': 'G2', 'online': True},
            'sys': {'mac': '020000000002'}, 'wifi': {'sta_ip': '192.0.2.9', 'ssid': 'DO NOT RETAIN'},
            'secret': 'DO NOT RETAIN'}}}}
        cloud = cloud_rows(response)
        rows = history.rows([{'shellyId': 'shelly-saved', 'host': '192.0.2.20:80'}], cloud)
        report = render_report(rows, 'Local LAN + Shelly account')
        self.assertEqual(len(rows), 2)
        self.assertEqual(report.count('Never'), 2)
        self.assertIn('02:00:00:00:00:02', report)
        self.assertIn('020000000002', report)  # Assigned names are optional in the documented API.
        self.assertIn('0 online, 2 offline', report)
        self.assertLess(report.index('192.0.2.9'), report.index('192.0.2.20'))
        self.assertNotIn('DO NOT RETAIN', str(cloud))
        self.assertEqual(history.saved(), '{}')

    def test_mac_matching_uses_full_values_and_does_not_merge_by_reused_ip(self):
        history = DeviceHistory()
        history.observe('192.0.2.10:80', self.info)
        other = {'id': 'cloud:020000000002', 'cloud_id': '020000000002', 'host': '192.0.2.10', 'name': 'Other'}
        self.assertEqual(len(history.rows(cloud=[other])), 2)
        history.offline('192.0.2.10:80')
        history.observe('192.0.2.11:80', self.info)
        history.offline('192.0.2.10:80')
        self.assertTrue(history.rows()[0]['online'])

    def test_cloud_duplicates_share_one_physical_row_even_without_local_history(self):
        one = {'id': 'cloud:first', 'cloud_id': 'first', 'mac': '020000000001', 'name': 'First channel'}
        two = {**one, 'id': 'cloud:second', 'cloud_id': 'second', 'name': 'Second channel'}
        rows = DeviceHistory().rows(cloud=[one, two])
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['online'])
        self.assertIsNone(rows[0].get('last_seen'))

    def test_unknown_fields_corrupt_history_and_log_injection_are_not_retained(self):
        for saved in ('invalid', '[]', '{"unit":{"last_seen":true}}', '{"unit":{"last_seen":NaN}}'):
            self.assertFalse(DeviceHistory(saved).records)
        history = DeviceHistory(json.dumps({'unit': {'last_seen': 1700000000, 'token': 'SECRET', 'name': 'Kitchen\nFORGED\x1b'}}))
        self.assertNotIn('SECRET', history.saved())
        self.assertNotIn('\x1b', render_report(history.rows(), 'Local LAN only'))
        self.assertNotIn('\nFORGED', render_report(history.rows(), 'Local LAN only'))
        for response in ({}, {'data': []}, {'data': {'devices_status': []}}):
            with self.assertRaises(ValueError):
                cloud_rows(response)

    def test_scan_is_bounded_cloud_is_optional_and_cancellation_closes_discovery(self):
        events = []
        with patch('shelly.inventory_report.Discovery') as discovery, patch('shelly.cloud_inventory.fetch_inventory') as cloud:
            job = InventoryJob('local', lambda *args: events.append(args), scan_seconds=0)
            job.run()
            discovery.return_value.close.assert_called_once()
            cloud.assert_not_called()
            self.assertEqual(events, [('inventory_result', '', ('local', []))])
        with patch('shelly.inventory_report.Discovery') as discovery, patch('shelly.cloud_inventory.fetch_inventory') as cloud:
            job = InventoryJob('cloud', Mock(), scan_seconds=0)
            job.stop()
            job.run()
            discovery.return_value.close.assert_called_once()
            cloud.assert_not_called()
            job.emit.assert_not_called()


class InventoryAdapterTests(unittest.TestCase):
    def setUp(self):
        self.host = IndigoHost()
        self.addCleanup(self.host.close)
        self.plugin = self.host.plugin

    def test_native_menu_defaults_to_local_and_background_job_does_not_block_owner(self):
        root = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin/MenuItems.xml'
        ui = ElementTree.parse(root).find("MenuItem[@id='logDeviceInventory']/ConfigUI")
        self.assertEqual(ui.find("Field[@id='inventoryScope']").get('defaultValue'), 'local')
        with patch('plugin.InventoryJob') as factory:
            self.assertTrue(self.plugin.logDeviceInventory())
            self.plugin._process_events()
            factory.assert_called_once()
            self.assertEqual(factory.call_args.args[0], 'local')
            factory.return_value.start.assert_called_once()
            with self.assertLogs('test-plugin', level='WARNING'):
                self.plugin._handle('inventory_request', '', 'cloud')
            self.plugin.cancelDeviceInventory()
            self.plugin._process_events()
            factory.return_value.stop.assert_called_once()

    def test_cancel_fences_a_result_already_queued_behind_the_cancel_request(self):
        with patch('plugin.InventoryJob') as factory:
            self.plugin.logDeviceInventory()
            self.plugin._process_events()
            deliver = factory.call_args.args[1]
            self.plugin.cancelDeviceInventory()
            deliver('inventory_result', '', ('local', []))
            with self.assertLogs('test-plugin', level='INFO') as logs:
                self.plugin._process_events()
            self.assertTrue(any('cancelled' in message for message in logs.output))
            self.assertFalse(any('physical devices:' in message for message in logs.output))

    def test_report_updates_after_recovery_and_restart_keeps_last_seen_without_fabricating_a_date(self):
        self.plugin._handle('offline', self.host.address, 'No answer')
        with self.assertLogs('test-plugin', level='INFO') as logs:
            self.plugin._handle('inventory_result', '', ('local', []))
        self.assertIn('1 physical devices: 0 online, 1 offline', logs.output[0])
        self.assertNotIn('Never', logs.output[0])
        saved = self.plugin.pluginPrefs['deviceHistory']
        self.assertEqual(len(json.loads(saved)), 1)
        self.assertFalse(DeviceHistory(saved).rows()[0]['online'])
        self.plugin._handle('ready', self.host.address, (self.host.info, self.host.status))
        with self.assertLogs('test-plugin', level='INFO') as logs:
            self.plugin._handle('inventory_result', '', ('local', []))
        self.assertIn('1 online, 0 offline', logs.output[0])

    def test_report_includes_current_indigo_names_for_all_channels_and_leaves_unassigned_blank(self):
        self.host.info.update(mac='020000000001', name='Local Shelly name')
        self.plugin._handle('ready', self.host.address, (self.host.info, self.host.status))
        self.host.devices[1].name = 'Relay alias'
        self.host.devices[2].name = 'Input alias'
        self.host.devices[2].enabled = False
        self.plugin._handle('offline', self.host.address, 'No answer')
        cloud = [
            {'id': 'cloud:020000000001', 'cloud_id': '020000000001', 'mac': '020000000001'},
            {'id': 'cloud:020000000002', 'cloud_id': '020000000002', 'host': '192.0.2.20', 'name': 'Unassigned'},
        ]
        def report():
            with self.assertLogs('test-plugin', level='INFO') as logs:
                self.plugin._handle('inventory_result', '', ('cloud', cloud))
            return logs.records[0].getMessage()
        message = report()
        lines = message.splitlines()
        columns = [value.strip() for value in lines[1].split('|')]
        self.assertEqual(columns[columns.index('Name') + 1], 'Indigo device name')
        rows = [[value.strip() for value in line.split('|')] for line in lines[2:-1]]
        named = next(row for row in rows if row[columns.index('Name')] == 'Local Shelly name')
        unassigned = next(row for row in rows if row[columns.index('Name')] == 'Unassigned')
        self.assertEqual(named[columns.index('Indigo device name')], 'Input alias; Relay alias')
        self.assertEqual(named[columns.index('Status')], 'Offline')
        self.assertEqual(unassigned[columns.index('Indigo device name')], '—')
        self.assertIn('2 physical devices:', message)
        self.host.devices[1].name = 'Renamed relay'
        renamed = report()
        self.assertIn('Input alias; Renamed relay', renamed)
        self.assertNotIn('Relay alias', renamed)
        self.assertNotIn('Input alias', self.plugin.pluginPrefs['deviceHistory'])
        self.assertNotIn('Renamed relay', self.plugin.pluginPrefs['deviceHistory'])

    def test_existing_bindings_without_any_new_runtime_observation_are_never_seen(self):
        self.plugin.device_history = DeviceHistory()
        with self.assertLogs('test-plugin', level='INFO') as logs:
            self.plugin._handle('inventory_result', '', ('local', []))
        self.assertIn('Never', logs.output[0])
        self.assertIn('0 online, 1 offline', logs.output[0])
