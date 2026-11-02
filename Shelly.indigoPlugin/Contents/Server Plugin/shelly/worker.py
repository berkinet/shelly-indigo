"""One bounded command queue and reconnecting worker per physical device."""
import queue
import threading
import time
from .rpc import AuthenticationError, RpcError, Session


class Worker(threading.Thread):
    def __init__(self, address, emit, password="", expected_id=None, connector=None):
        super().__init__(name=f"Shelly-{address}", daemon=True)
        self.address, self.emit, self.password = address, emit, password
        self.expected_id, self.connector = expected_id, connector
        self.commands = queue.Queue(maxsize=32)
        self.stopped = threading.Event()
        self.socket = None
        self.online = False
        self.command_lock = threading.Lock()

    def command(self, method, params):
        with self.command_lock:
            if not self.online or self.stopped.is_set():
                raise ConnectionError("Shelly is offline")
            self.commands.put_nowait((method, params))

    def stop(self):
        self.stopped.set()
        if self.socket is not None:
            self.socket.abort()

    def _notify(self, method, params):
        if method in ("NotifyStatus", "NotifyFullStatus"):
            self.emit("status", self.address, params)

    def run(self):
        import websocket
        connect = self.connector or websocket.create_connection
        delay = 1
        while not self.stopped.is_set():
            try:
                self.socket = connect(f"ws://{self.address}/rpc", timeout=8,
                                      http_no_proxy=["*"], redirect_limit=0)
                if self.stopped.is_set():
                    break
                session = Session(self.socket, self._notify, self.password)
                info = session.call("Shelly.GetDeviceInfo")
                identity = info.get("id")
                if not identity or info.get("gen") not in (2, 3, 4):
                    raise RpcError("This alpha requires a Gen2, Gen3 or Gen4 Shelly")
                if self.expected_id and identity != self.expected_id:
                    raise RpcError("Shelly identity changed at this address; rediscovery required")
                self.expected_id = identity
                status = session.call("Shelly.GetStatus")
                with self.command_lock:
                    self.online = True
                self.emit("ready", self.address, (info, status))
                delay = 1
                next_poll = time.monotonic() + 60
                while not self.stopped.is_set():
                    try:
                        method, params = self.commands.get_nowait()
                    except queue.Empty:
                        pass
                    else:
                        try:
                            result = session.call(method, params)
                            status = result if method == "Shelly.GetStatus" else session.call("Shelly.GetStatus")
                            self._notify("NotifyStatus", status)
                        except AuthenticationError:
                            raise
                        except RpcError as exc:
                            self.emit("command_error", self.address, str(exc))
                    if time.monotonic() >= next_poll:
                        self._notify("NotifyStatus", session.call("Shelly.GetStatus"))
                        next_poll = time.monotonic() + 60
                    try:
                        session.listen()
                    except websocket.WebSocketTimeoutException:
                        pass
            except Exception as exc:
                if not self.stopped.is_set():
                    reason = str(exc) if isinstance(exc, RpcError) else type(exc).__name__
                    self.emit("offline", self.address, reason)
                if isinstance(exc, AuthenticationError):
                    delay = 300
            finally:
                with self.command_lock:
                    self.online = False
                    # Queued commands must never execute after reconnect.
                    while True:
                        try:
                            self.commands.get_nowait()
                        except queue.Empty:
                            break
                sock, self.socket = self.socket, None
                if sock is not None:
                    try:
                        sock.close(timeout=0)
                    except OSError:
                        pass
            if self.stopped.wait(delay):
                break
            delay = min(delay * 2, 60) if delay < 300 else 300
