import importlib
import logging
from logging.handlers import BufferingHandler
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
        self.ui_values = {}
        self.error = None
    def updateStatesOnServer(self, states):
        assert all(state['key'] in self.states for state in states), 'Undefined Indigo state update'
        self.history.append({state['key']: state['value'] for state in states})
        self.states.update({state['key']: state['value'] for state in states})
    def updateStateOnServer(self, key, value, **kwargs):
        assert key in self.states, f'Undefined Indigo state: {key}'
        self.states[key] = value
        if 'uiValue' in kwargs:
            self.ui_values[key] = kwargs['uiValue']
    def stateListOrDisplayStateIdChanged(self):
        from pathlib import Path
        from xml.etree import ElementTree
        xml = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin/Devices.xml'
        definition = ElementTree.parse(xml).find(f"Device[@id='{self.deviceTypeId}']/States")
        defaults = {'Boolean': False, 'Number': 0.0, 'String': ''}
        registered = {state.get('id'): defaults[state.findtext('ValueType')] for state in definition}
        registered['onOffState'] = False
        if self.deviceTypeId == 'shellyDimmer':
            registered['brightnessLevel'] = 0
        self.states = {key: self.states.get(key, default) for key, default in registered.items()}
    def updateStateImageOnServer(self, image):
        self.image = image
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
        self.indigo_log_handler = BufferingHandler(1000)
        self.plugin_file_handler = BufferingHandler(1000)
        self.logger.handlers = [self.indigo_log_handler, self.plugin_file_handler]


class IndigoTests(unittest.TestCase):
    def setUp(self):
        self.devices = Devices()
        module = types.SimpleNamespace(PluginBase=Base, devices=self.devices,
                 triggers={}, trigger=types.SimpleNamespace(execute=Mock()),
                 device=types.SimpleNamespace(create=self.devices.create),
                 kProtocol=types.SimpleNamespace(Plugin='plugin'), Dict=dict,
                 kDeviceAction=types.SimpleNamespace(**{name: name for name in (
                     'TurnOn', 'TurnOff', 'Toggle', 'SetBrightness', 'BrightenBy', 'DimBy')}),
                 kUniversalAction=types.SimpleNamespace(RequestStatus='RequestStatus', EnergyUpdate='EnergyUpdate'),
                 kStateImageSel=types.SimpleNamespace(SensorOff='neutral', SensorOn='on'))
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
                device_id = 1 if type_id == 'shellySwitch' else 2
                self.assertEqual(len(generate(typeId=type_id)), 1)
                options = generate(typeId=type_id, targetId=device_id)
                self.assertEqual([key for key, label in options[1:]], [f'shelly-unit/{component}'])
                self.assertNotIn('already configured', options[1][1])
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
        self.assertIn('offline', dict(self.plugin.getShellyChannels(typeId='shellySwitch', targetId=1))[key])
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
        self.assertIsNone(button.error)
        self.assertEqual(button.states['inputStatus'], 'Unknown')
        self.assertEqual(button.image, 'neutral')
        self.assertIn('shelly-unit/input:0', dict(self.plugin.getShellyChannels(typeId='shellyInput', targetId=2)))
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
        self.assertIsNone(button.error)
        self.assertEqual(button.states['inputStatus'], 'Unknown')
        self.assertEqual(button.image, 'neutral')

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
        self.assertTrue(values['selectedChannel'])
        self.assertFalse(self.plugin.validateDeviceConfigUi(values, 'shellyInput', dev.id)[0])
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
            self.assertFalse(self.plugin.createDiscoveredDevices({'includeSwitches': False, 'includeInputs': False, 'includeDimmers': False}))
        self.assertTrue(self.plugin.events.empty())

    def test_dynamic_menu_ids_are_legal_including_all_and_placeholder_rows(self):
        from pathlib import Path
        from xml.etree import ElementTree
        server = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin'
        creation_field = ElementTree.parse(server / 'MenuItems.xml').find("MenuItem/ConfigUI/Field[@id='shellyId']")
        channels = ElementTree.parse(server / 'Devices.xml')
        for empty in (False, True):
            if empty:
                self.plugin.catalog.clear()
                self.plugin.discovered_devices.clear()
            menus = [self.plugin.getDiscoveredShellyDevices()]
            for type_id in ('shellySwitch', 'shellyInput'):
                options = self.plugin.getShellyChannels(typeId=type_id)
                menus.append(options)
                field = channels.find(f"Device[@id='{type_id}']/ConfigUI/Field[@id='selectedChannel']")
                self.assertEqual(field.get('defaultValue'), options[0][0])
                self.assertFalse(self.plugin.validateDeviceConfigUi({'selectedChannel': options[0][0]}, type_id, 0)[0])
            self.assertEqual(creation_field.get('defaultValue'), menus[0][0][0])
            for options in menus:
                for key, label in options:
                    self.assertIsInstance(key, str)
                    self.assertTrue(key.strip(), label)
                    self.assertNotIn(',', key)
                    self.assertNotIn(';', key)

    def test_all_devices_menu_default_creates_all_matching_channels(self):
        from pathlib import Path
        from xml.etree import ElementTree
        xml = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin/MenuItems.xml'
        field = ElementTree.parse(xml).find("MenuItem/ConfigUI/Field[@id='shellyId']")
        self.devices.clear()
        second = {**self.info, 'id': 'second-unit'}
        self.plugin._handle('ready', '192.0.2.2:80', (second, self.status))
        self.plugin.createDiscoveredDevices({'shellyId': field.get('defaultValue'),
                                            'includeSwitches': True, 'includeInputs': False})
        self.plugin._handle(*self.plugin.events.get_nowait())
        self.assertEqual({dev.pluginProps['shellyId'] for dev in self.devices.values()},
                         {'shelly-unit', 'second-unit'})
        self.assertTrue(all(dev.deviceTypeId == 'shellySwitch' for dev in self.devices.values()))

    def test_input_display_uses_normal_unknown_and_polled_binary_state_at_startup(self):
        from pathlib import Path
        from xml.etree import ElementTree
        xml = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin/Devices.xml'
        definition = ElementTree.parse(xml).find("Device[@id='shellyInput']")
        self.assertEqual(definition.findtext('UiDisplayStateId'), 'inputStatus')
        dev = self.devices[2]
        # Binary input already has a readable position and needs no event.
        self.assertFalse(dev.states['onOffState'])
        self.assertEqual(dev.states['inputStatus'], 'Off')
        self.assertTrue(dev.states['stateKnown'])
        self.plugin.deviceStartComm(dev)
        self.assertEqual(dev.states['inputStatus'], 'Connecting')
        self.assertIsNone(dev.error)
        self.plugin._handle('ready', self.address, (self.info,
            {'input:0': {'state': None, '_inputType': 'button', '_enabled': True, '_buttonState': True}}))
        self.assertEqual(dev.states['inputStatus'], 'On')
        self.assertTrue(dev.states['onOffState'])
        self.assertTrue(dev.states['stateKnown'])
        self.assertIsNone(dev.error)
        # Restart without an available position must not carry the old ON display forward.
        self.plugin._handle('ready', self.address, (self.info,
            {'input:0': {'state': None, '_inputType': 'button', '_enabled': True}}))
        self.assertEqual(dev.states['inputStatus'], 'Unknown')
        self.assertFalse(dev.states['stateKnown'])
        self.assertIsNone(dev.error)
        self.assertEqual(dev.image, 'neutral')
        self.assertEqual(dev.ui_values['onOffState'], 'Unknown')
        self.assertTrue(dev.enabled)
        with self.assertLogs('test-plugin', level='WARNING'):
            self.plugin._handle('offline', self.address, 'timeout')
        self.assertEqual(dev.error, 'Offline')

    def _button_trigger(self, identifier=101, device_id=2):
        self.plugin._handle('status', self.address, {
            'input:0': {'state': None, '_inputType': 'button', '_enabled': True}})
        trigger = types.SimpleNamespace(id=identifier, enabled=True, pluginTypeId='buttonPressed',
                                        pluginProps={'inputDeviceId': str(device_id)})
        sys.modules['indigo'].triggers[identifier] = trigger
        self.plugin.triggerStartProcessing(trigger)
        return trigger

    def _press_event(self, name='btn_down', timestamp=1, component='input:0', initial=False):
        self.plugin._handle('event', self.address, {'_initial': initial, 'events': [
            {'component': component, 'event': name, 'ts': timestamp}]})

    def test_button_event_dialog_lists_only_existing_button_inputs(self):
        from pathlib import Path
        from xml.etree import ElementTree
        xml = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin/Events.xml'
        definition = ElementTree.parse(xml).find("Event[@id='buttonPressed']")
        self.assertEqual(definition.findtext('Name'), 'Button Pressed')
        field = definition.find("ConfigUI/Field[@id='inputDeviceId']")
        generate = getattr(self.plugin, field.find('List').get('method'))
        self.assertEqual(generate(), [('_none_', 'No configured button inputs found')])
        self.assertFalse(self.plugin.validateEventConfigUi({'inputDeviceId': '2'}, 'buttonPressed', 0)[0])
        self._button_trigger()
        self.devices[2].name = 'Garden button'
        self.assertEqual(generate()[1:], [('2', 'Garden button')])
        self.assertTrue(all(key for key, _ in generate()))
        self.assertTrue(self.plugin.validateEventConfigUi({'inputDeviceId': '2'}, 'buttonPressed', 0)[0])
        for invalid in ('_none_', '1', '999', '', None):
            self.assertFalse(self.plugin.validateEventConfigUi({'inputDeviceId': invalid}, 'buttonPressed', 0)[0])

    def test_button_press_executes_once_per_press_and_honors_conditions(self):
        self._button_trigger()
        execute = sys.modules['indigo'].trigger.execute
        self._press_event()
        self.assertTrue(self.devices[2].states['onOffState'])
        self.assertEqual(execute.call_count, 1)
        execute.assert_called_with(101, ignoreConditions=False, trigger_data={
            'source': 'com.berkinet.indigoplugin.shelly', 'inputDeviceId': 2,
            'shellyId': 'shelly-unit', 'component': 'input:0', 'event': 'btn_down', 'ts': 1})
        self._press_event()  # Retransmitted notification.
        self._press_event('long_push', 2)
        self._press_event('btn_up', 3)
        self._press_event('single_push', 4)
        self._press_event(timestamp=2)  # Older than the accepted release.
        self.assertFalse(self.devices[2].states['onOffState'])
        self.assertEqual(execute.call_count, 1)
        self._press_event(timestamp=5)
        self.assertEqual(execute.call_count, 2)

    def test_startup_samples_and_buffered_events_never_fire_button_trigger(self):
        self._button_trigger()
        execute = sys.modules['indigo'].trigger.execute
        status = {'input:0': {'state': None, '_inputType': 'button', '_buttonState': True}}
        self.plugin._handle('ready', self.address, (self.info, status))
        self.plugin._handle('status', self.address, status)
        self._press_event(timestamp=1, initial=True)
        self.assertTrue(self.devices[2].states['onOffState'])
        execute.assert_not_called()
        self._press_event('btn_up', 2)
        self._press_event(timestamp=3)
        execute.assert_called_once()
        execute.reset_mock()
        self.plugin._handle('status', self.address, status)
        self._press_event(timestamp=4)  # A live edge still counts if a sample read ON first.
        execute.assert_called_once()

    def test_quick_press_release_pair_fires_one_button_event(self):
        self._button_trigger()
        self.plugin._handle('event', self.address, {'events': [
            {'component': 'input:0', 'event': 'btn_down', 'ts': 1},
            {'component': 'input:0', 'event': 'btn_up', 'ts': 2}]})
        sys.modules['indigo'].trigger.execute.assert_called_once()
        self.assertFalse(self.devices[2].states['onOffState'])

    def test_button_event_registration_and_source_filtering(self):
        trigger = self._button_trigger()
        execute = sys.modules['indigo'].trigger.execute
        self.plugin.triggerStartProcessing(trigger)  # Idempotent registration.
        self._press_event(component='input:1')
        self.plugin._handle('ready', '192.0.2.2:80', ({**self.info, 'id': 'other-unit'}, self.status))
        self.plugin._handle('event', '192.0.2.2:80', {'events': [
            {'component': 'input:0', 'event': 'btn_down', 'ts': 1}]})
        execute.assert_not_called()
        self._press_event()
        execute.assert_called_once()
        self.plugin.triggerStopProcessing(trigger)
        self.plugin.triggerStopProcessing(trigger)
        self._press_event(timestamp=2)
        execute.assert_called_once()

    def test_deleted_disabled_or_changed_trigger_and_input_do_not_fire(self):
        trigger = self._button_trigger()
        execute = sys.modules['indigo'].trigger.execute
        trigger.enabled = False
        self._press_event(timestamp=1)
        trigger.enabled = True
        self.devices[2].enabled = False
        self._press_event(timestamp=2)
        self.devices[2].enabled = True
        trigger.pluginProps['inputDeviceId'] = '1'
        self._press_event(timestamp=3)
        trigger.pluginProps['inputDeviceId'] = '2'
        del sys.modules['indigo'].triggers[101]
        self._press_event(timestamp=4)
        sys.modules['indigo'].triggers[101] = trigger
        del self.devices[2]
        self._press_event(timestamp=5)
        execute.assert_not_called()

    def test_failing_button_trigger_does_not_block_other_triggers(self):
        self._button_trigger(101)
        self._button_trigger(102)
        execute = sys.modules['indigo'].trigger.execute
        execute.side_effect = [RuntimeError('Indigo action failure'), None]
        with self.assertLogs('test-plugin', level='ERROR'):
            self._press_event()
        self.assertEqual([call.args[0] for call in execute.call_args_list], [101, 102])

    def test_logging_defaults_and_live_preferences_route_discovery_messages(self):
        self.plugin.startup()
        event_log = self.plugin.indigo_log_handler.buffer
        file_log = self.plugin.plugin_file_handler.buffer
        for level in ('INFO', 'DEBUG', 'INFO'):
            self.plugin.closedPrefsConfigUi({'logLevel': level}, False)
            event_log.clear()
            file_log.clear()
            with patch.object(sys.modules['plugin'], 'Worker'):
                self.plugin.workers.clear()
                self.plugin._connect(self.address)
            self.plugin._handle('ready', self.address, (self.info, self.status))
            self.plugin.logger.warning('A device is unavailable')
            self.plugin.logger.error('An operation failed')
            self.plugin._handle('create_devices', '', {'includeSwitches': True})
            visible = [record.getMessage() for record in event_log]
            detailed = [record.getMessage() for record in file_log]
            for prefix in ('Discovered Shelly endpoint', 'Querying'):
                self.assertEqual(any(message.startswith(prefix) for message in visible), level == 'DEBUG')
                self.assertTrue(any(message.startswith(prefix) for message in detailed))
            self.assertTrue(any(message.startswith('Create Indigo devices completed') for message in visible))
            self.assertIn('A device is unavailable', visible)
            self.assertIn('An operation failed', visible)
            self.assertFalse(any('Restart' in message for message in visible))
        self.assertEqual(self.plugin.indigo_log_handler.level, logging.INFO)

    def test_logging_startup_uses_saved_level_and_cancel_preserves_it(self):
        for saved, expected in ((None, logging.INFO), ('DEBUG', logging.DEBUG), ('invalid', logging.INFO)):
            self.plugin.pluginPrefs = {} if saved is None else {'logLevel': saved}
            self.plugin.startup()
            self.assertEqual(self.plugin.indigo_log_handler.level, expected)
            self.assertEqual(self.plugin.plugin_file_handler.level, logging.DEBUG)
            self.plugin.closedPrefsConfigUi({'logLevel': 'DEBUG' if expected == logging.INFO else 'INFO'}, True)
            self.assertEqual(self.plugin.indigo_log_handler.level, expected)
        self.plugin.closedPrefsConfigUi({'logLevel': 'INFO', 'hosts': '192.0.2.3'}, False)
        self.assertTrue(any('Restart' in record.getMessage() for record in self.plugin.indigo_log_handler.buffer))

    def test_logging_preference_xml_and_validation(self):
        from pathlib import Path
        from xml.etree import ElementTree
        xml = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin/PluginConfig.xml'
        field = ElementTree.parse(xml).find("Field[@id='logLevel']")
        self.assertEqual(field.get('defaultValue'), 'INFO')
        choices = [option.get('value') for option in field.findall('List/Option')]
        self.assertEqual(choices, ['INFO', 'DEBUG'])
        for level in choices:
            self.assertTrue(self.plugin.validatePrefsConfigUi({'logLevel': level})[0])
        self.assertFalse(self.plugin.validatePrefsConfigUi({'logLevel': 'INVALID'})[0])

    def test_discovered_unsupported_device_is_visible_without_creation(self):
        info = {'id': 'test-cover', 'gen': 3, 'model': 'unsupported-cover'}
        address = '192.0.2.50:80'
        status = {'cover:0': {'state': 'stopped'},
                  'input:0': {'state': None, '_inputType': 'button', '_enabled': False}}
        self.plugin._handle('ready', address, (info, status))
        self.assertIn('no supported channels', dict(self.plugin.getDiscoveredShellyDevices())['test-cover'])
        self.assertFalse(any(entry['shellyId'] == 'test-cover' for entry in self.plugin.catalog.values()))
        self.plugin._handle('create_devices', '', {'includeSwitches': True, 'includeInputs': True,
                                                  'shellyId': 'test-cover'})
        self.assertEqual(len(self.devices), 2)
        self.plugin._handle('identity', address, {**info, 'name': 'Bedroom blind'})
        self.assertIn('Bedroom blind', dict(self.plugin.getDiscoveredShellyDevices())['test-cover'])
        with self.assertLogs('test-plugin', level='WARNING'):
            self.plugin._handle('offline', address, 'test disconnect')
        self.assertNotIn('test-cover', dict(self.plugin.getDiscoveredShellyDevices()))
        self.plugin._handle('ready', address, (info, status))
        self.assertIn('test-cover', dict(self.plugin.getDiscoveredShellyDevices()))
        self.plugin._handle('status', address, {'input:0': {'state': None, '_inputType': 'button', '_enabled': True}})
        self.assertNotIn('no supported channels', dict(self.plugin.getDiscoveredShellyDevices())['test-cover'])
        self.assertEqual(len(self.devices), 2)

    def test_discovered_device_identity_survives_address_move_and_reuse(self):
        info = {'id': 'test-dimmer', 'gen': 3}
        status = {'light:0': {'output': False}}
        old_address, new_address = '192.0.2.50:80', '192.0.2.51:80'
        self.plugin._handle('ready', old_address, (info, status))
        self.plugin.workers[old_address] = Mock()
        self.plugin._handle('ready', new_address, (info, status))
        with self.assertLogs('test-plugin', level='WARNING'):
            self.plugin._handle('offline', old_address, 'stale worker')
        self.assertIn('test-dimmer', dict(self.plugin.getDiscoveredShellyDevices()))
        self.plugin._handle('ready', new_address, ({**info, 'id': 'replacement-unit'}, status))
        options = dict(self.plugin.getDiscoveredShellyDevices())
        self.assertNotIn('test-dimmer', options)
        self.assertIn('replacement-unit', options)

    def test_query_report_lists_components_and_warns_about_omitted_channels(self):
        self.plugin.pluginPrefs['logLevel'] = 'DEBUG'
        self.plugin.startup()
        records = self.plugin.indigo_log_handler.buffer
        records.clear()
        info = {'id': 'test-cover', 'name': 'Bedroom', 'gen': 3}
        status = {'cover:0': {'output': False, 'brightness': 100},
                  'wifi': {'ssid': 'private-value'}, 'cloud': {'connected': True},
                  'script:1': {'running': True}, 'infino:0': {'id': 0}}
        self.plugin._handle('ready', '192.0.2.50:80', (info, status))
        messages = [record.getMessage() for record in records]
        summary = next(message for message in messages if message.startswith('Queried'))
        for text in ('Bedroom (test-cover)', '192.0.2.50:80', 'Gen3', 'cover:0', 'wifi', 'cloud', 'script:1',
                     'supported channels: none'):
            self.assertIn(text, summary)
        warnings = [record.getMessage() for record in records if record.levelno == logging.WARNING]
        self.assertEqual(len(warnings), 1)
        self.assertIn('cover:0 (type not supported)', warnings[0])
        self.assertIn('remains in the discovered-device menu', warnings[0])
        self.assertNotIn('script:1', warnings[0])
        self.assertNotIn('infino:0', warnings[0])
        self.assertNotIn('wifi', warnings[0])
        self.assertFalse(any('private-value' in message for message in messages))
        self.assertIn('test-cover', dict(self.plugin.getDiscoveredShellyDevices()))
        self.assertEqual(len(self.devices), 2)

    def test_partial_poll_and_reconnect_do_not_repeat_unsupported_warnings(self):
        self.plugin.startup()
        records = self.plugin.plugin_file_handler.buffer
        info = {'id': 'mixed-unit', 'gen': 3}
        address = '192.0.2.50:80'
        status = {'switch:0': {'output': False}, 'cover:0': {'output': True}, 'wifi': {}}
        self.plugin._handle('ready', address, (info, status))
        records.clear()
        self.plugin._handle('status', address, {'switch:0': {'apower': 12}})
        self.plugin._handle('status', address, {'wifi': {'rssi': -50}})
        self.assertEqual(records, [])
        self.plugin._handle('ready', address, (info, status))
        self.assertTrue(any(record.getMessage().startswith('Queried') for record in records))
        self.assertFalse(any(record.levelno == logging.WARNING for record in records))
        records.clear()
        self.plugin._handle('status', address, {'pm1:0': {'apower': 0}})
        warnings = [record.getMessage() for record in records if record.levelno == logging.WARNING]
        self.assertEqual(len(warnings), 1)
        self.assertIn('cover:0', warnings[0])
        self.assertIn('pm1:0', warnings[0])
        self.assertIn('Supported channels: switch:0', warnings[0])
        self.assertNotIn('no supported channels', dict(self.plugin.getDiscoveredShellyDevices())['mixed-unit'])

    def test_disabled_input_warning_recovers_and_reappears_at_both_log_levels(self):
        self.plugin.startup()
        info = {'id': 'input-unit', 'gen': 3}
        address = '192.0.2.50:80'
        disabled = {'input:0': {'state': None, '_inputType': 'button', '_enabled': False}}
        enabled = {'input:0': {'state': None, '_inputType': 'button', '_enabled': True}}
        for level in ('INFO', 'DEBUG'):
            self.plugin.closedPrefsConfigUi({'logLevel': level}, False)
            self.plugin._handle('ready', address, (info, enabled))
            records = self.plugin.indigo_log_handler.buffer
            records.clear()
            self.plugin._handle('status', address, disabled)
            warnings = [record.getMessage() for record in records if record.levelno == logging.WARNING]
            self.assertEqual(len(warnings), 1)
            self.assertIn('input:0 (disabled)', warnings[0])
            records.clear()
            self.plugin._handle('status', address, disabled)
            self.assertEqual(records, [])
            self.plugin._handle('status', address, enabled)
            records.clear()
            self.plugin._handle('status', address, disabled)
            self.assertEqual(sum(record.levelno == logging.WARNING for record in records), 1)

    def test_services_only_device_warns_that_no_channels_are_available(self):
        self.plugin.startup()
        records = self.plugin.indigo_log_handler.buffer
        records.clear()
        self.plugin._handle('ready', '192.0.2.50:80', ({'id': 'empty-unit', 'gen': 3},
                                                  {'wifi': {}, 'cloud': {}, 'sys': {}, 'script:1': {}, 'infino:0': {'id': 0}}))
        warnings = [record.getMessage() for record in records if record.levelno == logging.WARNING]
        self.assertEqual(len(warnings), 1)
        self.assertIn('No supported Indigo channels', warnings[0])
        self.assertIn('empty-unit', dict(self.plugin.getDiscoveredShellyDevices()))

    def test_creation_dialog_refresh_loads_later_discovery_and_preserves_choices(self):
        from pathlib import Path
        from xml.etree import ElementTree
        xml = Path(support.__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents/Server Plugin/MenuItems.xml'
        ui = ElementTree.parse(xml).find('MenuItem/ConfigUI')
        field = ui.find("Field[@id='shellyId']/List")
        self.assertEqual(field.get('dynamicReload'), 'true')
        generate = getattr(self.plugin, field.get('method'))
        refresh = getattr(self.plugin, ui.findtext("Field[@id='refreshDiscoveredDevices']/CallbackMethod"))
        self.plugin.startup()
        self.plugin.catalog.clear()
        self.plugin.discovered_devices.clear()
        self.devices.clear()
        self.assertEqual(generate(), [('_all_', 'All discovered Shelly devices')])
        values = {'shellyId': '_all_', 'includeSwitches': False, 'includeInputs': True}
        info = {'id': 'test-dimmer', 'name': 'Séjour chandelier', 'gen': 3}
        status = {'light:0': {'output': False},
                  'input:0': {'state': None, '_inputType': 'button', '_enabled': True},
                  'input:1': {'state': False}}
        self.plugin._handle('ready', '192.0.2.50:80', (info, status))
        saved = dict(values)
        returned = refresh(values, 'createDiscoveredDevices', 0)
        self.assertEqual(returned, saved)
        records = self.plugin.plugin_file_handler.buffer
        records.clear()
        options = generate(valuesDict=returned)
        self.assertEqual(dict(options)['test-dimmer'], 'Séjour chandelier (test-dimmer)')
        logs = [record.getMessage() for record in records]
        self.assertTrue(any('Create Indigo Devices menu choices:' in message and
                            'Séjour chandelier (test-dimmer)' in message for message in logs))
        self.assertEqual(self.devices, {})
        self.assertTrue(self.plugin.events.empty())

    def test_infino_inventory_keeps_existing_input_valid_and_reports_real_assignments(self):
        self.plugin.pluginPrefs['logLevel'] = 'DEBUG'
        self.plugin.startup()
        self.devices[2].name = 'Garden button'
        records = self.plugin.indigo_log_handler.buffer
        records.clear()
        status = {'input:0': {'state': None, '_inputType': 'button', '_enabled': True},
                  'switch:0': {'output': False}, 'infino:0': {'id': 0}}
        self.plugin._handle('ready', self.address, ({**self.info, 'gen': 4}, status))
        messages = [record.getMessage() for record in records]
        summary = next(message for message in messages if message.startswith('Queried'))
        self.assertIn('infino:0', summary)
        self.assertIn('supported channels: input:0, switch:0', summary)
        self.assertIn('already configured: input:0 (Indigo 2: Garden button)', summary)
        self.assertIn('unassigned channels: none', summary)
        self.assertFalse(any(record.levelno >= logging.WARNING for record in records))
        self.assertIsNone(self.devices[2].error)
        self.assertTrue(self.devices[2].states['online'])
        self.plugin._handle('event', self.address, {'events': [
            {'component': 'input:0', 'event': 'btn_down', 'ts': 1}]})
        self.assertTrue(self.devices[2].states['onOffState'])
        self.assertNotIn('shelly-unit/input:0', dict(self.plugin.getShellyChannels(typeId='shellyInput')))
        # A disabled Indigo device still reserves its channel, just as the selector does.
        self.devices[1].enabled = False
        self.plugin._handle('status', self.address, {})
        self.assertIn('unassigned channels: none', records[-1].getMessage())
        del self.devices[1]
        self.plugin._handle('status', self.address, {})
        self.assertIn('unassigned channels: switch:0', records[-1].getMessage())
        self.assertIn('shelly-unit/switch:0', dict(self.plugin.getShellyChannels(typeId='shellySwitch')))
        self.assertIsNone(self.devices[2].error)

    def test_manually_entered_existing_binding_explains_hidden_channel(self):
        existing = self.devices[1]
        for selection in (None, '', '_none_'):
            values = {**existing.pluginProps, 'host': '192.0.2.1', 'selectedChannel': selection}
            result = self.plugin.validateDeviceConfigUi(values, 'shellySwitch', 0)
            self.assertFalse(result[0])
            message = result[2]['showAlertText']
            self.assertIn('already configured', message)
            self.assertIn(existing.name, message)
            self.assertIn(f'Indigo ID {existing.id}', message)
        self.assertNotIn('shelly-unit/switch:0', dict(self.plugin.getShellyChannels(typeId='shellySwitch')))
        own, _ = self.plugin.getDeviceConfigUiValues(existing.pluginProps, 'shellySwitch', existing.id)
        self.assertTrue(self.plugin.validateDeviceConfigUi(own, 'shellySwitch', existing.id)[0])
        self.assertEqual(len(self.devices), 2)
