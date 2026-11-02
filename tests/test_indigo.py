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
        self.error = None
    def updateStatesOnServer(self, states):
        self.states.update({state['key']: state['value'] for state in states})
    def updateStateOnServer(self, key, value):
        self.states[key] = value
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
    def tearDown(self):
        self.patcher.stop()
        sys.modules.pop('plugin', None)

    def test_create_only_supported_channels_and_avoid_duplicates(self):
        self.assertEqual(len(self.devices), 2)
        self.plugin._handle('ready', self.address, (self.info, self.status))
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
