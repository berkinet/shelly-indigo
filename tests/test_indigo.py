import importlib
import logging
import sys
import types
import unittest
from unittest.mock import Mock, patch
import support


class Device:
    def __init__(self, identifier, **kwargs):
        self.id = identifier
        self.pluginProps = kwargs['props']
        self.deviceTypeId = kwargs['deviceTypeId']
        self.name = kwargs['name']
        self.enabled = True
        self.states = {}
        self.stateListOrDisplayStateIdChanged()
        self.history = []
        self.error = None
    def updateStatesOnServer(self, states):
        assert all(state['key'] in self.states for state in states), 'Undefined Indigo state update'
        self.history.append({state['key']: state['value'] for state in states})
        self.states.update({state['key']: state['value'] for state in states})
    def updateStateOnServer(self, key, value):
        assert key in self.states, f'Undefined Indigo state: {key}'
        self.states[key] = value
    def stateListOrDisplayStateIdChanged(self):
        from pathlib import Path
        from xml.etree import ElementTree
        xml = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin/Devices.xml'
        definition = ElementTree.parse(xml).find(f"Device[@id='{self.deviceTypeId}']/States")
        defaults = {'Boolean': False, 'Number': 0.0, 'String': ''}
        registered = {state.get('id'): defaults[state.findtext('ValueType')] for state in definition}
        registered['onOffState'] = False
        self.states = {key: self.states.get(key, default) for key, default in registered.items()}
    def setErrorStateOnServer(self, error):
        self.error = error
    def replacePluginPropsOnServer(self, props):
        self.pluginProps = props


class Devices(dict):
    def iter(self, unused):
        return iter(list(self.values()))
    def create(self, **kwargs):
        device = Device(len(self) + 1, **kwargs)
        self[device.id] = device
        return device


class Base:
    def __init__(self, unused_id, unused_name, unused_version, prefs):
        self.pluginPrefs = prefs
        self.logger = logging.getLogger('test-plugin')


class IndigoTests(unittest.TestCase):
    def setUp(self):
        self.devices = Devices()
        module = types.SimpleNamespace(PluginBase=Base, devices=self.devices,
                 device=types.SimpleNamespace(create=self.devices.create),
                 kProtocol=types.SimpleNamespace(Plugin='plugin'), Dict=dict)
        self.patcher = patch.dict(sys.modules, indigo=module)
        self.patcher.start()
        sys.modules.pop('plugin', None)
        self.plugin = importlib.import_module('plugin').Plugin('id', 'name', 'version', {})
        self.info = {'id': 'shelly-unit', 'model': 'test', 'gen': 3}
        self.address = '192.0.2.1:80'
        self.status = {'switch:0': {'output': True, 'apower': 10},
                       'input:0': {'state': False}, 'input:1': {'state': None}}
        self.plugin._handle('ready', self.address, (self.info, self.status))
        self.plugin._handle('create_devices', '', {'includeSwitches': True, 'includeInputs': True})
    def tearDown(self):
        self.patcher.stop()
        sys.modules.pop('plugin', None)

    def test_explicit_creation_only_adds_supported_channels_and_avoids_duplicates(self):
        self.assertEqual(len(self.devices), 2)
        self.plugin._handle('ready', self.address, (self.info, self.status))
        self.plugin._handle('create_devices', '', {'includeSwitches': True, 'includeInputs': True})
        self.assertEqual(len(self.devices), 2)
        self.assertTrue(self.devices[1].states['onOffState'])
        self.assertEqual(self.devices[1].states['powerWatts'], 10)

    def test_dhcp_move_preserves_device_id_and_user_name(self):
        self.devices[1].name = 'Kitchen'
        self.plugin.workers[self.address] = Mock()
        self.plugin._handle('ready', '192.0.2.2:80', (self.info, self.status))
        self.assertEqual(len(self.devices), 2)
        self.assertEqual(self.devices[1].name, 'Kitchen')
        self.assertEqual(self.devices[1].pluginProps['host'], '192.0.2.2:80')
        self.plugin._handle('offline', self.address, 'old connection')
        self.assertTrue(self.devices[1].states['online'])

    def test_offline_reconnect_and_disabled_device(self):
        self.devices[2].enabled = False
        with self.assertLogs('test-plugin', level='WARNING'):
            self.plugin._handle('offline', self.address, 'timeout')
        self.assertFalse(self.devices[1].states['online'])
        self.assertEqual(self.devices[1].error, 'Offline')
        self.assertTrue(self.devices[2].states['online'])
        self.plugin._handle('ready', self.address, (self.info, self.status))
        self.assertTrue(self.devices[1].states['online'])
        self.assertIsNone(self.devices[1].error)

    def test_toggle_uses_device_rpc_and_never_updates_optimistically(self):
        worker = Mock()
        self.plugin.workers[self.address] = worker
        self.plugin._handle('command', '', (1, 'toggle'))
        worker.command.assert_called_once_with('Switch.Toggle', {'id': 0})
        self.assertTrue(self.devices[1].states['onOffState'])
        self.devices[1].enabled = False
        self.plugin._handle('command', '', (1, 'off'))
        worker.command.assert_called_once()

    def test_request_status_reads_device(self):
        worker = Mock()
        self.plugin.workers[self.address] = worker
        self.plugin._handle('refresh', self.address, None)
        worker.command.assert_called_once_with('Shelly.GetStatus', {})

    def test_reused_ip_never_controls_the_wrong_physical_device(self):
        worker = Mock()
        self.plugin.workers[self.address] = worker
        self.plugin.info[self.address] = {'id': 'different-unit', 'gen': 3}
        with self.assertRaises(ConnectionError):
            self.plugin._handle('command', '', (1, 'on'))
        worker.command.assert_not_called()

    def test_indigo_exec_loader_without_file_global(self):
        from pathlib import Path
        source = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin/plugin.py'
        namespace = {'__name__': 'indigo_plugin_exec'}
        with patch.object(sys, 'path', list(sys.path)):
            exec(compile(source.read_text(), 'plugin.py', 'exec'), namespace)
            plugin = namespace['Plugin']('id', 'name', 'version', {})
        self.assertNotIn('__file__', namespace)
        self.assertEqual(plugin.pluginPrefs, {})
        self.assertEqual(plugin.workers, {})

    def test_startup_reports_the_missing_dependency_and_stops(self):
        plugin_module = sys.modules['plugin']
        with patch.object(plugin_module.importlib, 'import_module',
                          side_effect=ModuleNotFoundError("No module named 'zeroconf'", name='zeroconf')):
            with self.assertLogs('test-plugin', level='ERROR') as logs:
                with self.assertRaisesRegex(RuntimeError, "Missing Shelly dependency 'zeroconf'"):
                    self.plugin.startup()
        self.assertIn('Contents/Packages', logs.output[0])

    def test_startup_loads_all_required_libraries(self):
        self.plugin.startup()

    def test_device_dialog_xml_populates_type_filtered_channel_choices(self):
        from pathlib import Path
        from xml.etree import ElementTree
        xml = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin/Devices.xml'
        root = ElementTree.parse(xml)
        for type_id, component in [('shellySwitch', 'switch:0'), ('shellyInput', 'input:0')]:
            with self.subTest(type_id=type_id):
                ui = root.find(f"Device[@id='{type_id}']/ConfigUI")
                field = ui.find("Field[@id='selectedChannel']")
                self.assertEqual(field.get('type'), 'menu')
                generate = getattr(self.plugin, field.find('List').get('method'))
                options = generate(typeId=type_id)
                self.assertEqual([key for key, label in options if key], [f'shelly-unit/{component}'])
                self.assertIn('already configured', options[1][1])
                changed = getattr(self.plugin, field.findtext('CallbackMethod'))
                values = changed({'selectedChannel': options[1][0]}, type_id, 0)
                self.assertEqual(values['host'], self.address)
                self.assertEqual(values['component'], component)
                self.assertEqual(values['model'], 'test')
                button = ui.find("Field[@id='refreshChannels']")
                refreshed = getattr(self.plugin, button.findtext('CallbackMethod'))(values, type_id, 0)
                self.assertEqual(refreshed['selectedChannel'], options[1][0])

    def test_individual_manual_creation_discovers_without_creating_until_selected(self):
        self.devices.clear()
        self.plugin = sys.modules['plugin'].Plugin('id', 'name', 'version', {})
        self.plugin._handle('ready', self.address, (self.info, self.status))
        self.assertEqual(len(self.devices), 0)
        options = self.plugin.getShellyChannels(typeId='shellySwitch')
        self.assertEqual(options[1][0], 'shelly-unit/switch:0')
        self.assertNotIn('already configured', options[1][1])
        ok, values = self.plugin.validateDeviceConfigUi({'selectedChannel': options[1][0]}, 'shellySwitch', 0)
        self.assertTrue(ok)
        self.assertEqual(values['address'], 'shelly-unit/switch:0')
        self.assertTrue(values['SupportsStatusRequest'])
        dev = self.devices.create(name='Kitchen', deviceTypeId='shellySwitch', props=values)
        self.plugin._handle('status', self.address, {'switch:0': {'output': False}})
        self.assertFalse(dev.states['onOffState'])
        self.assertTrue(dev.states['online'])
        self.assertEqual(len(self.devices), 1)
        self.assertEqual(dev.states['powerWatts'], 10)
        ok, _, errors = self.plugin.validateDeviceConfigUi(values, 'shellySwitch', 0)
        self.assertFalse(ok)
        self.assertIn('Kitchen', errors['selectedChannel'])
        self.assertTrue(self.plugin.validateDeviceConfigUi(values, 'shellySwitch', dev.id)[0])

    def test_existing_device_preselected_and_editable_before_rediscovery(self):
        dev = self.devices[1]
        self.plugin = sys.modules['plugin'].Plugin('id', 'name', 'version', {})
        values, errors = self.plugin.getDeviceConfigUiValues(dev.pluginProps, dev.deviceTypeId, dev.id)
        self.assertEqual(errors, {})
        self.assertEqual(values['selectedChannel'], 'shelly-unit/switch:0')
        choices = self.plugin.getShellyChannels(typeId='shellySwitch', targetId=dev.id)
        self.assertIn('offline', choices[1][1])
        self.assertNotIn('already configured', choices[1][1])
        ok, saved = self.plugin.validateDeviceConfigUi(values, dev.deviceTypeId, dev.id)
        self.assertTrue(ok)
        self.assertEqual(saved['host'], self.address)

    def test_invalid_or_wrong_type_selection_cannot_save_unbound_device(self):
        for selection in ('', 'unknown/switch:0', 'shelly-unit/input:0'):
            with self.subTest(selection=selection):
                result = self.plugin.validateDeviceConfigUi({'selectedChannel': selection}, 'shellySwitch', 0)
                self.assertFalse(result[0])
                self.assertIn('selectedChannel', result[2])
        values = {'selectedChannel': '', 'shellyId': 'old', 'host': self.address}
        cleared = self.plugin.shellyChannelSelected(values, 'shellySwitch', 0)
        self.assertEqual(cleared['host'], '')
        self.assertEqual(cleared['shellyId'], '')

    def test_selector_uses_new_address_and_marks_disconnected_channels(self):
        self.plugin.workers[self.address] = Mock()
        address = '192.0.2.2:80'
        self.plugin._handle('ready', address, (self.info, self.status))
        key = 'shelly-unit/switch:0'
        options = dict(self.plugin.getShellyChannels(typeId='shellySwitch', targetId=1))
        self.assertIn(address, options[key])
        values = self.plugin.shellyChannelSelected({'selectedChannel': key}, 'shellySwitch', 1)
        self.assertEqual(values['host'], address)
        with self.assertLogs('test-plugin', level='WARNING'):
            self.plugin._handle('offline', address, 'test disconnect')
        self.assertIn('offline', dict(self.plugin.getShellyChannels(typeId='shellySwitch'))[key])
        # A new full snapshot must remove channels no longer exposed by the device.
        self.plugin._handle('ready', address, (self.info, {'input:0': {'state': True}}))
        self.assertNotIn(key, dict(self.plugin.getShellyChannels(typeId='shellySwitch')))

    def test_button_press_and_release_update_input_without_touching_relay(self):
        status = {'switch:0': {'output': True},
                  'input:0': {'state': None, '_inputType': 'button', '_enabled': True}}
        worker = Mock()
        self.plugin.workers[self.address] = worker
        self.plugin._handle('ready', self.address, (self.info, status))
        button = self.devices[2]
        self.assertFalse(button.states['stateKnown'])
        self.assertIn('Waiting', button.error)
        self.assertIn('shelly-unit/input:0', dict(self.plugin.getShellyChannels(typeId='shellyInput')))
        self.plugin._handle('event', self.address, {'events': [
            {'component': 'input:0', 'event': 'btn_down', 'ts': 1}]})
        self.assertTrue(button.states['onOffState'])
        self.assertTrue(button.states['stateKnown'])
        self.assertIsNone(button.error)
        self.plugin._handle('status', self.address, {'input:0': {'state': None}})
        self.assertTrue(button.states['onOffState'])
        self.plugin._handle('event', self.address, {'events': [
            {'component': 'input:0', 'event': 'btn_up', 'ts': 2}]})
        self.assertFalse(button.states['onOffState'])
        self.assertTrue(self.devices[1].states['onOffState'])
        worker.command.assert_not_called()
        button.history.clear()
        self.plugin._handle('event', self.address, {'events': [
            {'component': 'input:0', 'event': 'btn_down', 'ts': 3},
            {'component': 'input:0', 'event': 'btn_up', 'ts': 4}]})
        self.assertEqual([states['onOffState'] for states in button.history], [True, False])
        with self.assertLogs('test-plugin', level='WARNING'):
            self.plugin._handle('offline', self.address, 'disconnect')
        self.assertFalse(button.states['stateKnown'])
        self.plugin._handle('ready', self.address, (self.info, status))
        self.assertFalse(button.states['stateKnown'])
        self.assertIn('Waiting', button.error)

    def test_switch_bound_to_input_device_cannot_receive_relay_or_meter_states(self):
        dev = self.devices[1]
        dev.deviceTypeId = 'shellyInput'
        dev.stateListOrDisplayStateIdChanged()
        before = dev.states['onOffState']
        with self.assertLogs('test-plugin', level='WARNING'):
            self.plugin._handle('status', self.address, {'switch:0': {
                'output': not before, 'apower': 20, 'voltage': 230, 'current': 0.2, 'aenergy': {'total': 50}}})
        self.assertEqual(dev.states['onOffState'], before)
        self.assertNotIn('powerWatts', dev.states)
        self.assertIn('Shelly Switch', dev.error)
        self.assertEqual(dev.pluginProps['component'], 'switch:0')
        self.assertEqual(len(self.devices), 2)

    def test_type_change_clears_old_binding_and_cannot_reuse_wrong_saved_channel(self):
        dev = self.devices[1]
        dev.deviceTypeId = 'shellyInput'
        dev.stateListOrDisplayStateIdChanged()
        values, _ = self.plugin.getDeviceConfigUiValues(dev.pluginProps, 'shellyInput', dev.id)
        self.assertEqual(values['selectedChannel'], '')
        self.assertEqual(values['host'], '')
        self.assertNotIn('shelly-unit/switch:0', dict(self.plugin.getShellyChannels(typeId='shellyInput', targetId=dev.id)))
        result = self.plugin.validateDeviceConfigUi({'selectedChannel': 'shelly-unit/switch:0'}, 'shellyInput', dev.id)
        self.assertFalse(result[0])
        # Correct input already exists, so conversion cannot silently duplicate it.
        result = self.plugin.validateDeviceConfigUi({'selectedChannel': 'shelly-unit/input:0'}, 'shellyInput', dev.id)
        self.assertFalse(result[0])
        self.assertIn('already configured', result[2]['selectedChannel'])

    def test_startup_refreshes_switch_meter_state_definitions(self):
        dev = self.devices[1]
        for key in ('powerWatts', 'voltage', 'currentAmps', 'energyWh'):
            dev.states.pop(key)
        self.plugin.deviceStartComm(dev)
        self.assertTrue({'powerWatts', 'voltage', 'currentAmps', 'energyWh'} <= dev.states.keys())
        self.plugin._handle('status', self.address, {'switch:0': {
            'apower': 20, 'voltage': 230, 'current': 0.2, 'aenergy': {'total': 50}}})
        self.assertEqual(dev.states['powerWatts'], 20)
        self.assertEqual(dev.states['energyWh'], 50)

    def test_wrong_component_binding_cannot_send_relay_control(self):
        dev = self.devices[1]
        dev.pluginProps['component'] = 'input:0'
        worker = Mock()
        self.plugin.workers[self.address] = worker
        with self.assertLogs('test-plugin', level='WARNING'):
            self.plugin._handle('command', '', (dev.id, 'on'))
        worker.command.assert_not_called()
        self.assertIn('Shelly Input', dev.error)

    def test_stale_meter_states_refresh_before_next_update_without_recreating_device(self):
        dev = self.devices[1]
        identity, name = dev.id, dev.name
        for key in ('powerWatts', 'voltage', 'currentAmps', 'energyWh'):
            dev.states.pop(key)
        self.plugin._handle('status', self.address, {'switch:0': {
            'apower': 12.5, 'voltage': 230, 'current': 0.1, 'aenergy': {'total': 90}}})
        self.assertEqual((dev.id, dev.name), (identity, name))
        self.assertEqual(len(self.devices), 2)
        self.assertEqual(dev.states['powerWatts'], 12.5)
        self.assertEqual(dev.states['energyWh'], 90)
        self.assertIsNone(dev.error)

    def test_discovery_and_events_never_create_even_with_legacy_auto_preference(self):
        self.devices.clear()
        self.plugin = sys.modules['plugin'].Plugin('id', 'name', 'version', {'autoCreate': True})
        self.plugin._handle('ready', self.address, (self.info, self.status))
        self.plugin._handle('ready', self.address, (self.info, self.status))
        self.plugin._handle('status', self.address, {'switch:1': {'output': False}})
        self.plugin._handle('event', self.address, {'events': [
            {'component': 'input:1', 'event': 'btn_down', 'ts': 1}]})
        self.assertEqual(len(self.devices), 0)
        self.assertIn('shelly-unit/switch:1', dict(self.plugin.getShellyChannels(typeId='shellySwitch')))
        self.assertIn('shelly-unit/input:1', dict(self.plugin.getShellyChannels(typeId='shellyInput')))

    def test_menu_xml_creates_selected_types_with_local_names_and_preserves_renames(self):
        from pathlib import Path
        from xml.etree import ElementTree
        xml = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin/MenuItems.xml'
        item = ElementTree.parse(xml).find('MenuItem')
        ui = item.find('ConfigUI')
        self.assertEqual(ui.find("Field[@id='includeInputs']").get('defaultValue'), 'false')
        callback = getattr(self.plugin, item.findtext('CallbackMethod'))
        menu = getattr(self.plugin, ui.find("Field[@id='shellyId']/List").get('method'))
        self.devices.clear()
        info = {**self.info, 'name': 'Garden Gate'}
        self.plugin._handle('ready', self.address, (info, self.status))
        self.assertEqual(len(self.devices), 0)
        self.assertIn('Garden Gate', dict(menu())['shelly-unit'])
        self.assertTrue(callback({'includeSwitches': True, 'includeInputs': False, 'shellyId': ''}, 'createDiscoveredDevices'))
        self.plugin._handle(*self.plugin.events.get_nowait())
        self.assertEqual(len(self.devices), 1)
        dev = self.devices[1]
        self.assertEqual(dev.name, 'Garden Gate (shelly-unit) switch:0')
        dev.name = 'My Indigo gate relay'
        self.plugin._handle('identity', self.address, {**info, 'name': 'Renamed in Shelly'})
        self.assertIn('Renamed in Shelly', dict(menu())['shelly-unit'])
        self.plugin._handle('create_devices', '', {'includeSwitches': True, 'includeInputs': True})
        self.assertEqual(len(self.devices), 2)
        self.assertEqual(dev.name, 'My Indigo gate relay')
        self.assertEqual(self.devices[2].name, 'Renamed in Shelly (shelly-unit) input:0')
        self.plugin._handle('create_devices', '', {'includeSwitches': True, 'includeInputs': True})
        self.assertEqual(len(self.devices), 2)

    def test_menu_scope_and_offline_skip_do_not_enable_future_creation(self):
        self.devices.clear()
        second = {**self.info, 'id': 'second-unit'}
        address = '192.0.2.2:80'
        self.plugin._handle('ready', address, (second, self.status))
        self.plugin._handle('create_devices', '', {'includeSwitches': False, 'includeInputs': True, 'shellyId': 'second-unit'})
        self.assertEqual(len(self.devices), 1)
        self.assertEqual(self.devices[1].pluginProps['shellyId'], 'second-unit')
        self.assertEqual(self.devices[1].pluginProps['component'], 'input:0')
        self.plugin._handle('status', address, {'switch:2': {'output': True}})
        self.assertEqual(len(self.devices), 1)
        with self.assertLogs('test-plugin', level='WARNING'):
            self.plugin._handle('offline', self.address, 'offline')
        self.plugin._handle('create_devices', '', {'includeSwitches': True, 'includeInputs': True, 'shellyId': 'shelly-unit'})
        self.assertEqual(len(self.devices), 1)
        with self.assertLogs('test-plugin', level='WARNING'):
            self.assertFalse(self.plugin.createDiscoveredDevices({'includeSwitches': False, 'includeInputs': False}))
        self.assertTrue(self.plugin.events.empty())
