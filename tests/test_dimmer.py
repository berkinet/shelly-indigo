"""Dimmer behavior through Indigo's real adapter and a strict host double."""
import types
import unittest
from unittest.mock import Mock
import support
import test_indigo as host


class DimmerTests(unittest.TestCase):
    def setUp(self):
        fixture = host.IndigoTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.plugin, self.devices = fixture.plugin, fixture.devices
        self.info, self.address = {**fixture.info, 'name': 'Séjour chandelier'}, fixture.address
        self.status = {**fixture.status,
            'light:0': {'id': 0, 'output': False, 'brightness': 70, 'apower': 0, 'voltage': 230,
                        'current': 0, 'aenergy': {'total': 1854}},
            'light:1': {'id': 1, 'output': True, 'brightness': 35}}
        self.plugin._handle('ready', self.address, (self.info, self.status))

    def create_dimmers(self):
        self.plugin.createDiscoveredDevices({'includeSwitches': False, 'includeInputs': False,
                                            'includeDimmers': True})
        self.plugin._handle(*self.plugin.events.get_nowait())
        return sorted((dev for dev in self.devices.values() if dev.deviceTypeId == 'shellyDimmer'),
                      key=lambda dev: dev.pluginProps['component'])

    def test_explicit_menu_creation_uses_native_dimmer_type_and_keeps_names(self):
        from pathlib import Path
        from xml.etree import ElementTree
        server = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin'
        definition = ElementTree.parse(server / 'Devices.xml').find("Device[@id='shellyDimmer']")
        self.assertEqual(definition.get('type'), 'dimmer')
        self.assertEqual(definition.findtext('Name'), 'Shelly Dimmer')
        self.assertEqual(ElementTree.parse(server / 'MenuItems.xml').find(
            "MenuItem/ConfigUI/Field[@id='includeDimmers']").get('defaultValue'), 'true')
        self.assertEqual(len(self.devices), 2)  # Continuous discovery never creates.
        self.assertEqual(len(self.plugin.getShellyChannels(typeId='shellyDimmer')), 3)
        first, second = self.create_dimmers()
        self.assertIn('Séjour chandelier', first.name)
        self.assertEqual(first.states['brightnessLevel'], 0)
        self.assertFalse(first.states['onOffState'])
        self.assertEqual(first.states['energyWh'], 1854)
        self.assertEqual(second.states['brightnessLevel'], 35)
        first.name = 'My chandelier'
        first_id = first.id
        self.create_dimmers()
        self.assertEqual(len(self.devices), 4)
        self.assertEqual(self.devices[first_id].name, 'My chandelier')
        self.assertEqual(len(self.plugin.getShellyChannels(typeId='shellyDimmer')), 1)
        self.assertIn('shelly-unit/light:0', dict(self.plugin.getShellyChannels(
            typeId='shellyDimmer', targetId=first.id)))

    def test_claimed_channels_hidden_until_deleted_and_other_channels_remain_available(self):
        first = self.create_dimmers()[0]
        first.enabled = False
        self.assertNotIn('shelly-unit/light:0', dict(self.plugin.getShellyChannels(typeId='shellyDimmer')))
        result = self.plugin.validateDeviceConfigUi({'selectedChannel': 'shelly-unit/light:0'}, 'shellyDimmer', 0)
        self.assertFalse(result[0])  # Protect a dialog opened before another device claimed it.
        self.assertIn('already configured', result[2]['selectedChannel'])
        del self.devices[first.id]
        self.assertIn('shelly-unit/light:0', dict(self.plugin.getShellyChannels(typeId='shellyDimmer')))
        self.plugin._handle('status', self.address, {'input:2': {'state': False}})
        self.assertIn('shelly-unit/input:2', dict(self.plugin.getShellyChannels(typeId='shellyInput')))
        self.assertNotIn('shelly-unit/input:0', dict(self.plugin.getShellyChannels(typeId='shellyInput')))

    def test_manual_dimmer_binding_and_offline_edit_remain_valid(self):
        ok, props = self.plugin.validateDeviceConfigUi({'selectedChannel': 'shelly-unit/light:1'}, 'shellyDimmer', 0)
        self.assertTrue(ok)
        dev = self.devices.create(name='Dimmer', deviceTypeId='shellyDimmer', props=props)
        self.plugin._handle('status', self.address, {'light:1': {'brightness': 40}})
        self.assertEqual(dev.states['brightnessLevel'], 40)
        self.plugin.catalog.clear()
        options = dict(self.plugin.getShellyChannels(typeId='shellyDimmer', targetId=dev.id))
        self.assertIn('offline', options['shelly-unit/light:1'])
        self.assertTrue(self.plugin.validateDeviceConfigUi(props, 'shellyDimmer', dev.id)[0])
        self.assertFalse(self.plugin.validateDeviceConfigUi(props, 'shellySwitch', dev.id)[0])
        self.assertFalse(self.plugin.validateDeviceConfigUi(props, 'shellyInput', dev.id)[0])

    def test_on_off_toggle_and_brightness_actions_use_light_rpc_without_optimistic_state(self):
        first, dev = self.create_dimmers()
        worker = Mock()
        self.plugin.workers[self.address] = worker
        before = dict(dev.states)
        expected = [('TurnOn', None, 'Light.Set', {'id': 1, 'on': True}),
                    ('TurnOff', None, 'Light.Set', {'id': 1, 'on': False}),
                    ('Toggle', None, 'Light.Toggle', {'id': 1}),
                    ('SetBrightness', 65, 'Light.Set', {'id': 1, 'on': True, 'brightness': 65}),
                    ('SetBrightness', 0, 'Light.Set', {'id': 1, 'on': False}),
                    ('BrightenBy', 10, 'Light.Set', {'id': 1, 'on': True, 'brightness': 45}),
                    ('DimBy', 10, 'Light.Set', {'id': 1, 'on': True, 'brightness': 25}),
                    ('BrightenBy', 80, 'Light.Set', {'id': 1, 'on': True, 'brightness': 100}),
                    ('DimBy', 50, 'Light.Set', {'id': 1, 'on': False})]
        for action, value, method, params in expected:
            self.plugin.actionControlDevice(types.SimpleNamespace(deviceAction=action, actionValue=value), dev)
            self.plugin._handle(*self.plugin.events.get_nowait())
            worker.command.assert_called_with(method, params)
            self.assertEqual(dev.states, before)
        for action in ('RequestStatus', 'EnergyUpdate'):
            self.plugin.actionControlUniversal(types.SimpleNamespace(deviceAction=action), dev)
            self.plugin._handle(*self.plugin.events.get_nowait())
            worker.command.assert_called_with('Shelly.GetStatus', {})
        self.plugin._handle('status', self.address, {'light:1': {'output': True, 'brightness': 65}})
        self.assertEqual(dev.states['brightnessLevel'], 65)

    def test_invalid_levels_disabled_devices_and_identity_mismatch_cannot_send(self):
        dev = self.create_dimmers()[0]
        worker = Mock()
        self.plugin.workers[self.address] = worker
        for value in (-1, 101, True, None, '50', float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.plugin._handle('command', '', (dev.id, 'brightness', value))
        worker.command.assert_not_called()
        dev.enabled = False
        self.plugin._handle('command', '', (dev.id, 'brightness', 50))
        worker.command.assert_not_called()
        dev.enabled = True
        self.plugin.info[self.address] = {'id': 'wrong-unit', 'gen': 3}
        with self.assertRaises(ConnectionError):
            self.plugin._handle('command', '', (dev.id, 'brightness', 50))
        worker.command.assert_not_called()

    def test_dimmer_partial_metering_and_input_events_preserve_output_state(self):
        first, second = self.create_dimmers()
        self.plugin._handle('status', self.address, {'light:0': {'output': True}, 'light:1': {'apower': 12}})
        self.assertEqual(first.states['brightnessLevel'], 70)
        self.assertEqual(second.states['brightnessLevel'], 35)
        self.assertEqual(second.states['powerWatts'], 12)
        self.plugin._handle('event', self.address, {'events': [
            {'component': 'input:0', 'event': 'btn_down', 'ts': 1}]})
        self.assertEqual(first.states['brightnessLevel'], 70)
        first.states.pop('brightnessLevel')
        self.plugin._handle('ready', self.address, ({**self.info, 'gen': 2}, self.status))
        self.assertEqual(first.states['brightnessLevel'], 0)
        self.assertEqual(first.states['energyWh'], 1854)
