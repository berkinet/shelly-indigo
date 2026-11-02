"""One bounded command queue and reconnecting worker per physical device."""
import queue
import re
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
        self.pending_notifications = []

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
        kind = "status" if method in ("NotifyStatus", "NotifyFullStatus") else "event" if method == "NotifyEvent" else None
        if kind is None:
            return
        if not self.online:
            if len(self.pending_notifications) >= 256:
                raise RpcError("Too many notifications during connection setup")
            self.pending_notifications.append((kind, params))
        else:
            self.emit(kind, self.address, params)

    def _read_status(self, session):
        status = session.call("Shelly.GetStatus")
        for key, values in status.items():
            if (not re.fullmatch(r"input:\d+", key) or not isinstance(values, dict)
                    or isinstance(values.get("state"), bool)):
                continue
            config = session.call("Input.GetConfig", {"id": int(key.split(":")[1])})
            values["_inputType"] = config.get("type")
            values["_enabled"] = config.get("enable", True)
        return status

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
                status = self._read_status(session)
                with self.command_lock:
                    self.online = True
                self.emit("ready", self.address, (info, status))
                for kind, params in self.pending_notifications:
                    self.emit(kind, self.address, params)
                self.pending_notifications.clear()
                delay = 1
                next_poll = time.monotonic() + 60
                while not self.stopped.is_set():
                    try:
                        method, params = self.commands.get_nowait()
                    except queue.Empty:
                        pass
                    else:
                        try:
                            if method != "Shelly.GetStatus":
                                session.call(method, params)
                            status = self._read_status(session)
                            self._notify("NotifyStatus", status)
                        except AuthenticationError:
                            raise
                        except RpcError as exc:
                            self.emit("command_error", self.address, str(exc))
                    if time.monotonic() >= next_poll:
                        self._notify("NotifyStatus", self._read_status(session))
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
                    self.pending_notifications.clear()
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
