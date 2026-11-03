"""Exercise the actual WebSocket client against a small local Shelly simulator."""
import queue
import socketserver
import threading
import unittest
import support
from shelly.worker import Worker


from simulator import Simulator, next_event


class WebsocketTests(unittest.TestCase):
    def test_real_connection_discovery_snapshot_control_notification_and_shutdown(self):
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Simulator)
        server.daemon_threads = True
        server.methods = queue.Queue()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        events = queue.Queue()
        worker = Worker(f'127.0.0.1:{server.server_address[1]}', lambda *args: events.put(args))
        try:
            worker.start()
            kind, _, data = next_event(events, lambda event: event[0] != 'identified', timeout=5)
            self.assertEqual(kind, 'ready', data)
            self.assertEqual(data[0]['id'], 'shelly-simulator')
            worker.command('Switch.Set', {'id': 0, 'on': True})
            kind, _, data = next_event(events, lambda event: event[0] != 'identified', timeout=5)
            self.assertEqual(kind, 'status', data)
            self.assertTrue(data['switch:0']['output'])
        finally:
            worker.stop()
            worker.join(5)
            server.shutdown()
            server.server_close()
        self.assertFalse(worker.is_alive())
        with self.assertRaises(ConnectionError):
            worker.command('Switch.Set', {'id': 0, 'on': False})

    def test_button_startup_event_and_release_use_only_read_requests(self):
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Simulator)
        server.daemon_threads = True
        server.methods = queue.Queue()
        server.button = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        events = queue.Queue()
        worker = Worker(f'127.0.0.1:{server.server_address[1]}', lambda *args: events.put(args))
        try:
            worker.start()
            kind, _, data = next_event(events, lambda event: event[0] != 'identified', timeout=5)
            self.assertEqual(kind, 'ready', data)
            self.assertEqual(data[1]['input:0']['_inputType'], 'button')
            kind, _, data = next_event(events, lambda event: event[0] != 'identified', timeout=5)
            self.assertEqual(kind, 'event', data)
            self.assertEqual(data['events'][0]['event'], 'btn_down')
            self.assertTrue(data['_initial'])
            worker.command('Input.GetStatus', {'id': 0})
            kind, _, data = next_event(events, lambda event: event[0] != 'identified', timeout=5)
            self.assertEqual(kind, 'event', data)
            self.assertEqual(data['events'][0]['event'], 'btn_up')
            self.assertNotIn('_initial', data)
        finally:
            worker.stop()
            worker.join(5)
            server.shutdown()
            server.server_close()
        methods = []
        while not server.methods.empty():
            methods.append(server.methods.get_nowait())
        self.assertIn('Input.GetConfig', methods)
        self.assertTrue(all('Get' in method for method in methods), methods)
        self.assertFalse(worker.is_alive())

    def test_gen4_sample_supersedes_button_events_buffered_before_initial_read(self):
        from unittest.mock import patch
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Simulator)
        server.daemon_threads = True
        server.methods = queue.Queue()
        server.button, server.gen = True, 4
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        events = queue.Queue()
        # The simulator's ephemeral RPC port is mapped to a standard endpoint by
        # this connector; no real Modbus connection is made in this test.
        import websocket
        connector = lambda *a, **kw: websocket.create_connection(
            f'ws://127.0.0.1:{server.server_address[1]}/rpc', **kw)
        worker = Worker('127.0.0.1:80', lambda *args: events.put(args), connector=connector,
                        sample_reader=lambda *args: {0: False})
        with patch('shelly.worker.read_button_inputs', return_value={0: False}):
            try:
                worker.start()
                kind, _, payload = next_event(events, lambda event: event[0] != 'identified', timeout=5)
                self.assertEqual(kind, 'ready', payload)
                sample = next_event(events, lambda event: event[0] == 'status'
                                    and '_buttonState' in event[2].get('input:0', {}))
                self.assertIs(sample[2]['input:0']['_buttonState'], False)
                # The simulated press during Input.GetConfig happened before the
                # released-position sample and must not overwrite that sample.
                with self.assertRaises(queue.Empty):
                    events.get(timeout=0.1)
            finally:
                worker.stop()
                worker.join(5)
                server.shutdown()
                server.server_close()
        self.assertFalse(worker.is_alive())

    def test_dimmer_worker_receives_snapshot_and_real_websocket_control_notifications(self):
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Simulator)
        server.daemon_threads = True
        server.methods = queue.Queue()
        server.dimmer = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        events = queue.Queue()
        worker = Worker(f'127.0.0.1:{server.server_address[1]}', lambda *args: events.put(args))
        try:
            worker.start()
            kind, _, data = next_event(events, lambda event: event[0] != 'identified', timeout=5)
            self.assertEqual(kind, 'ready', data)
            self.assertEqual(data[1]['light:0']['brightness'], 70)
            self.assertFalse(data[1]['light:0']['output'])
            for method, params, expected in (
                    ('Light.Set', {'id': 0, 'on': True, 'brightness': 65}, (True, 65)),
                    ('Light.Toggle', {'id': 0}, (False, 65))):
                worker.command(method, params)
                # A full reconciliation can follow the preceding notification.
                next_event(events, lambda event: event[0] in ('status', 'snapshot') and
                           (event[2].get('light:0', {}).get('output'),
                            event[2].get('light:0', {}).get('brightness')) == expected, timeout=5)
        finally:
            worker.stop()
            worker.join(5)
            server.shutdown()
            server.server_close()
        self.assertFalse(worker.is_alive())
