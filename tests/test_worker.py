"""Worker reconciliation contracts and real disconnect/reconnect behavior."""
from copy import deepcopy
import queue
import socket
import threading
import unittest
from unittest.mock import patch
import support
from shelly.model import Inventory
from shelly.rpc import RpcError
from shelly.worker import Worker
from simulator import LocalShelly, next_event


class ReadSession:
    """Method-aware RPC responses; unknown calls are failures."""
    def __init__(self, status, name=None, input_type='button', enabled=True, modbus=True):
        self.status, self.name, self.input_type = status, name, input_type
        self.enabled, self.modbus = enabled, modbus
        self.calls = []
        self.name_error = None

    def call(self, method, params=None):
        self.calls.append(method)
        if method == 'Shelly.GetStatus':
            return deepcopy(self.status)
        if method == 'Sys.GetConfig':
            if self.name_error:
                raise self.name_error
            return {'device': {'name': self.name}}
        if method == 'Input.GetConfig':
            return {'type': self.input_type, 'enable': self.enabled, 'invert': False}
        if method == 'Modbus.GetConfig':
            return {'enable': self.modbus}
        raise AssertionError(f'Unexpected RPC: {method}')


class WorkerTests(unittest.TestCase):
    def test_disconnect_discards_pending_commands_and_reconnects(self):
        gate = threading.Event()
        disconnected = threading.Event()
        def disconnect(handler, request):
            if request['method'] == 'Switch.Set' and not disconnected.is_set():
                gate.wait(2)
                disconnected.set()
                handler.server.controls.put((0, request))
                handler.connection.shutdown(socket.SHUT_RDWR)
                return True
        with LocalShelly(on_request=disconnect) as sim:
            events = queue.Queue()
            worker = Worker(sim.endpoint, lambda *args: events.put(args))
            try:
                worker.start()
                next_event(events, lambda e: e[0] == 'ready')
                worker.command('Switch.Set', {'id': 0, 'on': True})
                worker.command('Switch.Set', {'id': 0, 'on': False})
                gate.set()
                next_event(events, lambda e: e[0] == 'offline')
                next_event(events, lambda e: e[0] == 'ready')
                self.assertEqual(sim.server.controls.qsize(), 1)
                self.assertTrue(worker.commands.empty())
            finally:
                gate.set()
                worker.stop()
                worker.join(3)
            self.assertFalse(worker.is_alive())

    def test_changed_device_identity_is_rejected_before_reading_status(self):
        with LocalShelly(identity='different-unit') as sim:
            events = queue.Queue()
            worker = Worker(sim.endpoint, lambda *args: events.put(args), expected_id='original-unit')
            try:
                worker.start()
                event = next_event(events, lambda e: e[0] == 'mismatch')
                self.assertEqual(event[2]['id'], 'different-unit')
            finally:
                worker.stop()
                worker.join(3)
            self.assertEqual(list(sim.server.methods.queue), ['Shelly.GetDeviceInfo'])
            self.assertFalse(worker.online)

    def test_local_shelly_name_is_read_and_refreshed_without_cloud_or_writes(self):
        emitted = []
        worker = Worker('192.0.2.1:80', lambda *args: emitted.append(args))
        worker.device_info = {'id': 'unit', 'gen': 4}
        worker.online = True
        session = ReadSession({'switch:0': {'output': False}}, name='  Garden Gate\nButton  ')
        worker._reconcile(session, refresh_config=True)
        self.assertEqual(worker.device_info['name'], 'Garden Gate Button')
        old_info = emitted[0][2]
        session.name = None
        worker._reconcile(session, refresh_config=True)
        self.assertIsNone(worker.device_info['name'])
        self.assertEqual(old_info['name'], 'Garden Gate Button')
        self.assertTrue(all(method in ('Sys.GetConfig', 'Shelly.GetStatus') for method in session.calls))

    def test_unavailable_local_name_does_not_disable_device_status(self):
        worker = Worker('192.0.2.1:80', lambda *args: None)
        worker.device_info = {'id': 'unit', 'gen': 3}
        session = ReadSession({'switch:0': {'output': True}})
        session.name_error = RpcError('unsupported')
        self.assertTrue(worker._read_status(session).status['switch:0']['output'])
        self.assertNotIn('name', worker.device_info)

    def test_gen4_modbus_initializes_both_positions_without_enabling_anything(self):
        for held in (False, True):
            with self.subTest(held=held):
                emitted = []
                worker = Worker('192.0.2.1:80', lambda *args: emitted.append(args))
                worker.device_info = {'id': 'unit', 'gen': 4, 'mac': 'E4B06365AF10'}
                session = ReadSession({'input:0': {'state': None}})
                with patch('shelly.worker.read_button_inputs', return_value={0: held}) as read:
                    worker._reconcile(session, initial=True, refresh_config=True)
                read.assert_called_once_with('192.0.2.1', 'E4B06365AF10', [0])
                ready = next(event for event in emitted if event[0] == 'ready')
                channel, = Inventory().merge(ready[2][1])
                self.assertTrue(channel.states()['stateKnown'])
                self.assertIs(channel.states()['onOffState'], held)
                self.assertTrue(all('Get' in method for method in session.calls))

    def test_optional_modbus_disabled_or_unreachable_keeps_unknown_not_off(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                worker = Worker('192.0.2.1:80', lambda *args: None)
                worker.device_info = {'id': 'unit', 'gen': 4, 'mac': 'E4B06365AF10'}
                session = ReadSession({'input:0': {'state': None}}, modbus=enabled)
                with patch('shelly.worker.read_button_inputs', side_effect=TimeoutError) as read:
                    snapshot = worker._read_status(session)
                self.assertEqual(read.call_count, int(enabled))
                self.assertFalse(snapshot.samples)
                channel, = Inventory().merge(snapshot.status)
                self.assertFalse(channel.states()['stateKnown'])
                self.assertNotIn('onOffState', channel.states())
