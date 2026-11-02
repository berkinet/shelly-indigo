import queue
import threading
import unittest
from unittest.mock import Mock, patch
import support
from shelly.worker import Worker


class WorkerTests(unittest.TestCase):
    def test_disconnect_discards_pending_commands_and_reconnects(self):
        events = queue.Queue()
        connected = []
        calls = []
        sessions = []
        class Session:
            def __init__(self, sock, notify, password):
                self.index = len(sessions)
                sessions.append(self)
                self.idle = threading.Event()
            def call(self, method, params=None):
                calls.append((self.index, method))
                if method == 'Shelly.GetDeviceInfo':
                    return {'id': 'unit', 'gen': 2}
                if method == 'Switch.Set':
                    raise ConnectionError('simulated disconnect during command')
                return {'switch:0': {'output': False}}
            def listen(self):
                self.idle.wait(0.01)
        def connect(*args, **kwargs):
            sock = Mock()
            connected.append(sock)
            return sock
        worker = Worker('192.0.2.1:80', lambda *args: events.put(args), connector=connect)
        with patch('shelly.worker.Session', Session):
            try:
                worker.start()
                self.assertEqual(events.get(timeout=3)[0], 'ready')
                worker.command('Switch.Set', {'id': 0, 'on': True})
                worker.command('Switch.Set', {'id': 0, 'on': False})
                self.assertEqual(events.get(timeout=3)[0], 'offline')
                self.assertEqual(events.get(timeout=3)[0], 'ready')
            finally:
                worker.stop()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual([call for call in calls if call[1] == 'Switch.Set'], [(0, 'Switch.Set')])
        self.assertEqual(len(connected), 2)

    def test_changed_device_identity_is_rejected_before_reading_status(self):
        events = queue.Queue()
        session = Mock()
        session.call.return_value = {'id': 'different-unit', 'gen': 3}
        worker = Worker('192.0.2.1:80', lambda *args: events.put(args),
                        expected_id='original-unit', connector=Mock(return_value=Mock()))
        with patch('shelly.worker.Session', return_value=session):
            try:
                worker.start()
                event = events.get(timeout=3)
                self.assertEqual(event[0], 'offline')
                self.assertIn('identity changed', event[2])
            finally:
                worker.stop()
                worker.join(3)
        session.call.assert_called_once_with('Shelly.GetDeviceInfo')
        self.assertFalse(worker.online)

    def test_local_shelly_name_is_read_and_refreshed_without_cloud_or_writes(self):
        emitted = []
        worker = Worker('192.0.2.1:80', lambda *args: emitted.append(args))
        worker.device_info = {'id': 'unit', 'gen': 4}
        worker.online = True
        session = Mock()
        session.call.side_effect = [{'device': {'name': '  Garden Gate\nButton  '}},
                                    {'switch:0': {'output': False}}]
        worker._read_status(session)
        self.assertEqual(worker.device_info['name'], 'Garden Gate Button')
        self.assertEqual(emitted[0][0], 'identity')
        self.assertEqual(emitted[0][2]['name'], 'Garden Gate Button')
        self.assertEqual([call.args[0] for call in session.call.call_args_list], ['Sys.GetConfig', 'Shelly.GetStatus'])
        session.call.side_effect = [{'device': {'name': None}}, {'switch:0': {'output': False}}]
        worker._read_status(session)
        self.assertIsNone(worker.device_info['name'])
        # Previously published info is a snapshot, not mutated by a later refresh.
        self.assertEqual(emitted[0][2]['name'], 'Garden Gate Button')

    def test_unavailable_local_name_does_not_disable_device_status(self):
        from shelly.rpc import RpcError
        worker = Worker('192.0.2.1:80', lambda *args: None)
        worker.device_info = {'id': 'unit', 'gen': 3}
        session = Mock()
        session.call.side_effect = [RpcError('unsupported'), {'switch:0': {'output': True}}]
        self.assertTrue(worker._read_status(session)['switch:0']['output'])
        self.assertNotIn('name', worker.device_info)
