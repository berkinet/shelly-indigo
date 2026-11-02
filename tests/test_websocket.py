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
            head = self.rfile.read(2)
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
                result = {'id': 'shelly-simulator', 'gen': 3, 'model': 'test'}
            elif method == 'Switch.Set':
                output = request['params']['on']
                self.send_frame({'method': 'NotifyStatus', 'params': {'switch:0': {'output': output}}})
                result = {'was_on': not output}
            else:
                result = {'switch:0': {'id': 0, 'output': output}}
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
