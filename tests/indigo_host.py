"""Shared Indigo host contract double; no tests or live Indigo access in this module."""
import importlib
import logging
from logging.handlers import BufferingHandler
import sys
import threading
import types
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
    next_id = 1
    def iter(self, unused):
        return iter(list(self.values()))
    def create(self, **kwargs):
        device = Device(self.next_id, **kwargs)
        self.next_id += 1
        self[device.id] = device
        return device


class StopThread(Exception):
    pass


class Base:
    StopThread = StopThread

    def sleep(self, seconds):
        if self.thread_stop.wait(seconds):
            raise self.StopThread()

    def stopConcurrentThread(self):
        self.thread_stop.set()

    def __init__(self, unused_id, unused_name, unused_version, prefs):
        self.thread_stop = threading.Event()
        self.pluginPrefs = prefs
        self.logger = logging.getLogger('test-plugin')
        self.indigo_log_handler = BufferingHandler(1000)
        self.plugin_file_handler = BufferingHandler(1000)
        self.logger.handlers = [self.indigo_log_handler, self.plugin_file_handler]


class IndigoHost:
    def __init__(self, seed=True, prefs=None):
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
        self.plugin = importlib.import_module('plugin').Plugin('id', 'name', 'version', prefs or {})
        self.info = {'id': 'shelly-unit', 'model': 'test', 'gen': 3}
        self.address = '192.0.2.1:80'
        self.status = {'switch:0': {'output': True, 'apower': 10},
                       'input:0': {'state': False}, 'input:1': {'state': None}}
        if seed:
            self.plugin._handle('ready', self.address, (self.info, self.status))
            self.plugin._handle('create_devices', '', {'includeSwitches': True, 'includeInputs': True})
    def close(self):
        self.patcher.stop()
        sys.modules.pop('plugin', None)
