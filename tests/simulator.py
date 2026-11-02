"""Strict localhost Shelly RPC peer. Unknown methods fail instead of fabricating status."""
from copy import deepcopy
import base64
import hashlib
import json
import queue
import socket
import socketserver
import struct
import threading
import time


def wait_for(predicate, timeout=3, message="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.005)
    raise AssertionError(f"Timed out waiting for {message}")


def next_event(events, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    observed = []
    while time.monotonic() < deadline:
        try:
            event = events.get(timeout=max(.001, deadline - time.monotonic()))
        except queue.Empty:
            break
        observed.append(event[0])
        if predicate(event):
            return event
    raise AssertionError(f"Expected event not received; observed {observed}")


class Simulator(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connection.settimeout(30)
        server = self.server
        if not hasattr(server, 'lock'):
            server.lock = threading.RLock()
            server.requests, server.controls = queue.Queue(), queue.Queue()
            server.state = {'switch:0': {'id': 0, 'output': False}, 'sys': {'cfg_rev': 1}}
            if getattr(server, 'dimmer', False):
                server.state['light:0'] = {'id': 0, 'output': False, 'brightness': 70}
            if getattr(server, 'button', False):
                server.state['input:0'] = {'id': 0, 'state': None}
            server.input_configs = {'input:0': {'id': 0, 'type': 'button', 'enable': True}}
        self.nonce, self.last_nc = 'simulator-nonce', 0
        self.send_lock = threading.Lock()

    def send_frame(self, frame):
        raw = json.dumps(frame).encode()
        header = bytes([129, len(raw)]) if len(raw) < 126 else bytes([129, 126]) + struct.pack('!H', len(raw))
        with self.send_lock:
            self.wfile.write(header + raw)

    def notify(self, method, params):
        self.send_frame({'src': getattr(self.server, 'identity', 'shelly-simulator'),
                         'method': method, 'params': deepcopy(params)})

    def respond(self, request, result=None, error=None):
        self.send_frame({'id': request['id'], 'src': getattr(self.server, 'identity', 'shelly-simulator'),
                         **({'error': error} if error else {'result': deepcopy(result)})})

    def authenticated(self, request):
        password = getattr(self.server, 'password', '')
        if not password or request['method'] == 'Shelly.GetDeviceInfo':
            return True
        realm = getattr(self.server, 'challenge_realm', getattr(self.server, 'identity', 'shelly-simulator'))
        auth = request.get('auth', {})
        valid = False
        try:
            sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
            count = int(auth['nc'], 16)
            expected = sha(f"{sha(f'admin:{realm}:{password}')}:{self.nonce}:{auth['nc']}:"
                           f"{auth['cnonce']}:auth:{sha('dummy_method:dummy_uri')}")
            valid = (auth['realm'] == realm and auth['nonce'] == self.nonce and auth['response'] == expected
                     and count > self.last_nc)
            if valid:
                self.last_nc = count
        except (KeyError, ValueError, TypeError):
            pass
        if not valid:
            self.respond(request, error={'code': 401, 'message': json.dumps(
                {'realm': realm, 'nonce': self.nonce, 'algorithm': 'SHA-256'})})
        return valid

    def handle(self):
        self.rfile.readline()
        headers = {}
        while line := self.rfile.readline().strip():
            key, value = line.decode().split(':', 1)
            headers[key.lower()] = value.strip()
        accept = base64.b64encode(hashlib.sha1((headers['sec-websocket-key'] +
                                 '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.wfile.write(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n'
                          'Connection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n').encode())
        while True:
            try:
                head = self.rfile.read(2)
                if len(head) < 2 or head[0] & 15 == 8:
                    return
                size = head[1] & 127
                if size == 126:
                    size = struct.unpack('!H', self.rfile.read(2))[0]
                if size == 127 or size > 65535:
                    return
                mask = self.rfile.read(4)
                raw = self.rfile.read(size)
                request = json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(raw)))
                method, params = request['method'], request.get('params', {})
                self.server.methods.put(method)
                self.server.requests.put((time.monotonic(), deepcopy(request)))
                if getattr(self.server, 'drop_replies', False):
                    continue
                if not self.authenticated(request):
                    continue
                hook = getattr(self.server, 'on_request', None)
                if hook is not None and hook(self, request):
                    continue
                with self.server.lock:
                    result = self.dispatch(request, method, params)
                if result is NotImplemented:
                    self.respond(request, error={'code': -32601, 'message': 'Method not found'})
                else:
                    self.respond(request, result)
            except (OSError, ValueError, IndexError, KeyError):
                return

    def dispatch(self, request, method, params):
        server = self.server
        if method == 'Shelly.GetDeviceInfo':
            result = {'id': getattr(server, 'identity', 'shelly-simulator'),
                      'gen': getattr(server, 'gen', 3), 'model': 'test', 'auth_en': bool(getattr(server, 'password', ''))}
            if result['gen'] == 4:
                result['mac'] = 'E4B06365AF10'
            return result
        if method == 'Shelly.GetStatus':
            return deepcopy(server.state)
        if method == 'Sys.GetConfig':
            return {'device': {'name': getattr(server, 'device_name', None)}}
        if method == 'Modbus.GetConfig':
            return {'enable': getattr(server, 'modbus_enabled', True)}
        if method == 'Input.GetConfig':
            if (getattr(server, 'send_press_during_config', getattr(server, 'button', False))
                    and not getattr(self, 'pressed_sent', False)):
                self.pressed_sent = True
                self.notify('NotifyEvent', {'events': [{'component': 'input:0', 'event': 'btn_down', 'ts': 1}]})
            return deepcopy(server.input_configs.get(f"input:{params['id']}", {}))
        if method in ('Switch.GetStatus', 'Light.GetStatus', 'Input.GetStatus'):
            if method == 'Input.GetStatus' and getattr(server, 'button', False):
                self.notify('NotifyEvent', {'events': [{'component': 'input:0', 'event': 'btn_up', 'ts': 2}]})
            return deepcopy(server.state.get(f"{method.split('.')[0].lower()}:{params['id']}", {}))
        if method in ('Switch.Set', 'Switch.Toggle', 'Light.Set', 'Light.Toggle'):
            server.controls.put((time.monotonic(), deepcopy(request)))
            key = f"{method.split('.')[0].lower()}:{params['id']}"
            channel = server.state[key]
            if method.endswith('.Toggle'):
                channel['output'] = not channel['output']
            else:
                if 'on' in params:
                    channel['output'] = params['on']
                if 'brightness' in params:
                    channel['brightness'] = params['brightness']
            if not getattr(server, 'suppress_notifications', False):
                self.notify('NotifyStatus', {key: channel})
            return None
        return NotImplemented


class LocalShelly:
    """Context-managed real TCP/WebSocket simulator, restricted to loopback."""
    def __init__(self, **options):
        self.server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Simulator)
        self.server.daemon_threads = True
        self.server.methods = queue.Queue()
        for key, value in options.items():
            setattr(self.server, key, value)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.endpoint = f'127.0.0.1:{self.server.server_address[1]}'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *unused):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
