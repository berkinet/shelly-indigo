"""Replay recorded HTTP RPC result bodies through Session, Worker and the adapter.

The request-ID envelopes below are generated, not claimed as recorded WebSocket
traffic. Firmware result bodies are recorded and sanitized; no network is used.
"""
import json
from pathlib import Path
import unittest
import support
from indigo_host import IndigoHost
from shelly.rpc import Session
from shelly.worker import Worker

FIXTURES = Path(__file__).parent / 'fixtures' / 'recorded'


class RecordedSocket:
    def __init__(self, records):
        self.records = {(r['method'], json.dumps(r['params'], sort_keys=True)): r['result'] for r in records}
        self.reply = None
        self.sent = []

    def send(self, raw):
        request = json.loads(raw)
        self.sent.append(request['method'])
        key = (request['method'], json.dumps(request.get('params', {}), sort_keys=True))
        if key not in self.records:
            raise AssertionError(f'No recorded response: {key}')
        self.reply = {'id': request['id'], 'result': self.records[key]}

    def recv(self):
        return json.dumps(self.reply)

    def settimeout(self, seconds):
        pass


class RecordedDeviceTests(unittest.TestCase):
    def test_recorded_firmware_responses_create_correct_channels_and_states(self):
        paths = sorted(FIXTURES.glob('*.json'))
        self.assertTrue(paths, 'Recorded hardware fixtures must be shipped')
        for path in paths:
            with self.subTest(fixture=path.name):
                fixture = json.loads(path.read_text())
                self.assertEqual(fixture['provenance']['kind'], 'recorded-device')
                host = IndigoHost(seed=False)
                try:
                    address = '192.0.2.1:80'
                    sock = RecordedSocket(fixture['responses'])
                    worker = Worker(address, host.plugin._handle)
                    session = Session(sock, worker._notify)
                    info = session.call('Shelly.GetDeviceInfo')
                    worker.device_info = info
                    worker._reconcile(session, initial=True)
                    host.plugin._handle('create_devices', '', {'includeInputs': True, 'includeSwitches': True,
                                                               'includeDimmers': True})
                    status = next(r['result'] for r in fixture['responses'] if r['method'] == 'Shelly.GetStatus')
                    configs = {f"input:{r['params']['id']}": r['result'] for r in fixture['responses'] if r['method'] == 'Input.GetConfig'}
                    expected = {key for key in status if key.startswith(('switch:', 'light:'))}
                    expected.update(key for key, config in configs.items() if config.get('enable', True)
                                    and config.get('type') in ('switch', 'button'))
                    actual = {dev.pluginProps['component']: dev for dev in host.devices.values()}
                    self.assertEqual(set(actual), expected)
                    sys_config = next(r['result'] for r in fixture['responses'] if r['method'] == 'Sys.GetConfig')
                    self.assertEqual(worker.device_info.get('name'), sys_config['device'].get('name'))
                    for key, dev in actual.items():
                        self.assertIsNone(dev.error)
                        self.assertTrue(dev.states['online'])
                        if key.startswith(('switch:', 'light:')):
                            self.assertEqual(dev.states['onOffState'], status[key]['output'])
                            self.assertEqual(dev.states['powerWatts'], status[key]['apower'])
                            self.assertEqual(dev.states['energyWh'], status[key]['aenergy']['total'])
                        else:
                            self.assertEqual(dev.states['inputMode'], configs[key]['type'])
                            if configs[key]['type'] == 'button':
                                self.assertFalse(dev.states['stateKnown'])
                    if info['gen'] == 4:
                        self.assertIn('Modbus.GetConfig', sock.sent)
                    self.assertFalse(any(method.endswith(('.Set', '.Toggle')) for method in sock.sent))
                finally:
                    host.close()
