"""Passwords belong to physical units, apply on Save, and never enter device properties."""
from pathlib import Path
import types
import unittest
from unittest.mock import patch
from xml.etree import ElementTree
import support
from indigo_host import IndigoHost


class DeviceCredentialTests(unittest.TestCase):
    def setUp(self):
        self.host = IndigoHost()
        self.addCleanup(self.host.close)
        self.plugin = self.host.plugin

    def validated(self, dev_id=1, **changes):
        dev = self.host.devices[dev_id]
        values, _ = self.plugin.getDeviceConfigUiValues(dev.pluginProps, dev.deviceTypeId, dev.id)
        values.update(changes)
        result = self.plugin.validateDeviceConfigUi(values, dev.deviceTypeId, dev.id)
        self.assertTrue(result[0], result)
        self.assertEqual(result[1]['devicePassword'], '')
        self.assertNotIn('private-password', str(result[1]))
        return result[1]

    def test_all_device_dialogs_have_masked_password_and_explicit_clear(self):
        server = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin'
        for device in ElementTree.parse(server / 'Devices.xml').findall('Device'):
            ui = device.find('ConfigUI')
            self.assertEqual(ui.find("Field[@id='devicePassword']").get('secure'), 'true')
            self.assertEqual(ui.find("Field[@id='clearDevicePassword']").get('defaultValue'), 'false')
            self.assertEqual(ui.find("Field[@id='credentialEditToken']").get('hidden'), 'true')
        self.assertIsNone(ElementTree.parse(server / 'PluginConfig.xml').find("Field[@id='password']"))

    def test_save_updates_shared_physical_credential_without_exposing_secret_in_props(self):
        values = self.validated(devicePassword='private-password')
        self.assertNotIn('devicePasswords', self.plugin.pluginPrefs)
        self.assertTrue(self.plugin.events.empty())
        self.plugin.closedDeviceConfigUi(values, False, 'shellySwitch', 1)
        with patch('plugin.Worker'), self.assertLogs('test-plugin', level='INFO') as logs:
            self.plugin._process_events()
        self.assertEqual(self.plugin.credentials.password('shelly-unit'), 'private-password')
        self.assertNotIn('private-password', str(logs.output))
        # Saving an input on the same physical unit with an empty field keeps the password.
        values = self.validated(2)
        self.plugin.closedDeviceConfigUi(values, False, 'shellyInput', 2)
        self.assertTrue(self.plugin.events.empty())
        self.assertEqual(self.plugin.credentials.password('shelly-unit'), 'private-password')

    def test_cancel_discards_staged_password(self):
        self.plugin.credentials.set('shelly-unit', 'previous')
        values = self.validated(devicePassword='private-password')
        self.plugin.closedDeviceConfigUi(values, True, 'shellySwitch', 1)
        self.assertTrue(self.plugin.events.empty())
        self.assertEqual(self.plugin.credentials.password('shelly-unit'), 'previous')
        self.assertFalse(self.plugin.credential_edits.pending)

    def test_explicit_no_password_clears_override_and_blocks_legacy_fallback(self):
        self.plugin.credentials.shared = 'legacy'
        self.plugin.credentials.set('shelly-unit', 'previous')
        values = self.validated(clearDevicePassword=True)
        self.plugin.closedDeviceConfigUi(values, False, 'shellySwitch', 1)
        with patch('plugin.Worker'):
            self.plugin._process_events()
        self.assertEqual(self.plugin.credentials.password('shelly-unit'), '')

    def test_conflicting_password_choices_fail_validation_without_applying(self):
        values, _ = self.plugin.getDeviceConfigUiValues(self.host.devices[1].pluginProps, 'shellySwitch', 1)
        values.update(devicePassword='private-password', clearDevicePassword=True)
        result = self.plugin.validateDeviceConfigUi(values, 'shellySwitch', 1)
        self.assertFalse(result[0])
        self.assertIn('devicePassword', result[2])
        self.assertTrue(self.plugin.events.empty())
        self.assertFalse(self.plugin.credential_edits.pending)

    def test_ui_edit_tokens_do_not_restart_shared_transport(self):
        orig = self.host.devices[1]
        new = types.SimpleNamespace(deviceTypeId=orig.deviceTypeId,
                                    pluginProps={**orig.pluginProps, 'credentialEditToken': 'new-token'})
        self.assertFalse(self.plugin.didDeviceCommPropertyChange(orig, new))
        new.pluginProps['host'] = '192.0.2.8:80'
        self.assertTrue(self.plugin.didDeviceCommPropertyChange(orig, new))


    def test_learned_current_endpoint_does_not_restart_its_verified_worker(self):
        orig = self.host.devices[1]
        new = types.SimpleNamespace(deviceTypeId=orig.deviceTypeId,
                                    pluginProps={**orig.pluginProps, 'host': '192.0.2.8:80'})
        self.plugin.connections.owners['shelly-unit'] = '192.0.2.8:80'
        self.assertFalse(self.plugin.didDeviceCommPropertyChange(orig, new))


    def test_offline_saved_device_password_can_be_updated_before_any_discovery(self):
        for dev in self.host.devices.values():
            dev.enabled = False
        self.plugin.catalog.clear()
        self.plugin.discovered_devices.clear()
        self.plugin.connections.owners.clear()
        values = self.validated(devicePassword='private-password')
        self.plugin.closedDeviceConfigUi(values, False, 'shellySwitch', 1)
        with patch('plugin.Worker') as factory:
            self.plugin._process_events()
        factory.assert_not_called()
        self.assertEqual(self.plugin.credentials.password('shelly-unit'), 'private-password')
