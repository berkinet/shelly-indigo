"""Exercise the actual WebSocket client against a small local Shelly simulator."""
import base64
import hashlib
import json
import queue
import socketserver
import struct
import threading
import unittest
import support
from shelly.worker import Worker


class Simulator(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        self.rfile.readline()
        headers = {}
        while line := self.rfile.readline().strip():
            key, value = line.decode().split(':', 1)
            headers[key.lower()] = value.strip()
        accept = base64.b64encode(hashlib.sha1((headers['sec-websocket-key'] +
                                 '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.wfile.write(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n'
                          'Connection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n').encode())
        output = False
        while True:
            try:
                head = self.rfile.read(2)
            except (ConnectionResetError, TimeoutError):
                return
            if len(head) < 2 or head[0] & 15 == 8:
                return
            size = head[1] & 127
            if size == 126:
                size = struct.unpack('!H', self.rfile.read(2))[0]
            mask = self.rfile.read(4)
            raw = self.rfile.read(size)
            request = json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(raw)))
            method = request['method']
            self.server.methods.put(method)
            if method == 'Shelly.GetDeviceInfo':
                result = {'id': 'shelly-simulator', 'gen': getattr(self.server, 'gen', 3), 'model': 'test'}
                if result['gen'] == 4:
                    result['mac'] = 'E4B06365AF10'
            elif method == 'Modbus.GetConfig':
                result = {'enable': True}
            elif method == 'Input.GetConfig':
                if getattr(self.server, 'button', False) and not getattr(self, 'pressed_sent', False):
                    self.pressed_sent = True
                    self.send_frame({'method': 'NotifyEvent', 'params': {'events': [
                        {'component': 'input:0', 'event': 'btn_down', 'ts': 1}]}})
                result = {'id': 0, 'type': 'button', 'enable': True}
            elif method == 'Input.GetStatus':
                self.send_frame({'method': 'NotifyEvent', 'params': {'events': [
                    {'component': 'input:0', 'event': 'btn_up', 'ts': 2}]}})
                result = {'id': 0, 'state': None}
            elif method == 'Switch.Set':
                output = request['params']['on']
                self.send_frame({'method': 'NotifyStatus', 'params': {'switch:0': {'output': output}}})
                result = {'was_on': not output}
            else:
                result = {'switch:0': {'id': 0, 'output': output}}
                if getattr(self.server, 'button', False):
                    result['input:0'] = {'id': 0, 'state': None}
            self.send_frame({'id': request['id'], 'result': result})

    def send_frame(self, frame):
        raw = json.dumps(frame).encode()
        header = bytes([129, len(raw)]) if len(raw) < 126 else bytes([129, 126]) + struct.pack('!H', len(raw))
        self.wfile.write(header + raw)


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
            kind, _, data = events.get(timeout=5)
            self.assertEqual(kind, 'ready', data)
            self.assertEqual(data[0]['id'], 'shelly-simulator')
            worker.command('Switch.Set', {'id': 0, 'on': True})
            kind, _, data = events.get(timeout=5)
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
            kind, _, data = events.get(timeout=5)
            self.assertEqual(kind, 'ready', data)
            self.assertEqual(data[1]['input:0']['_inputType'], 'button')
            kind, _, data = events.get(timeout=5)
            self.assertEqual(kind, 'event', data)
            self.assertEqual(data['events'][0]['event'], 'btn_down')
            worker.command('Input.GetStatus', {'id': 0})
            kind, _, data = events.get(timeout=5)
            self.assertEqual(kind, 'event', data)
            self.assertEqual(data['events'][0]['event'], 'btn_up')
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
        worker = Worker('127.0.0.1:80', lambda *args: events.put(args), connector=connector)
        with patch('shelly.worker.read_button_inputs', return_value={0: False}):
            try:
                worker.start()
                kind, _, payload = events.get(timeout=5)
                self.assertEqual(kind, 'ready', payload)
                self.assertIs(payload[1]['input:0']['_buttonState'], False)
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
