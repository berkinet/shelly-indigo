"""One interruptible, ordered RPC worker per endpoint; no Indigo calls."""
from copy import deepcopy
from dataclasses import dataclass
import math
import queue
import re
import socket
import threading
import time
from urllib.parse import urlsplit
from .modbus import read_button_inputs
from .rpc import AuthenticationError, ProtocolError, RpcError, Session


@dataclass
class Snapshot:
    status: dict
    before: list
    after: list
    samples: dict


class IdentityChanged(RpcError):
    def __init__(self, info):
        self.info = info
        super().__init__("Shelly identity changed at this address")


class Worker(threading.Thread):
    def __init__(self, address, emit, password="", expected_id=None, connector=None,
                 poll_interval=15, config_interval=60, rpc_timeout=8, exclude_id=None, permitted=None):
        super().__init__(name=f"Shelly-{address}", daemon=True)
        self.address, self.emit, self.password = address, emit, password
        self.expected_id, self.connector = expected_id, connector
        self.exclude_id, self.permitted = exclude_id, permitted
        self.poll_interval, self.config_interval, self.rpc_timeout = poll_interval, config_interval, rpc_timeout
        self.commands = queue.Queue(maxsize=32)
        self.stopped = threading.Event()
        self.socket = None
        self.online = False
        self.command_lock = threading.Lock()
        self.pending_notifications = []
        self.device_info = None
        self.input_configs = {}
        self.modbus_enabled = False
        self.config_dirty = True
        self.config_revision = None
        self.reading = False
        self._wake_reader = self._wake_writer = None
        self._refresh_pending = False
        self._last_refresh = -math.inf

    def _wake(self):
        writer = self._wake_writer
        if writer is not None:
            try:
                writer.send(b"x")
            except (OSError, BlockingIOError):
                pass  # A full wake socket already guarantees a wakeup.

    def command(self, method, params, context=""):
        with self.command_lock:
            if not self.online or self.stopped.is_set():
                raise ConnectionError("Shelly is offline")
            if method == "Shelly.GetStatus" and (self._refresh_pending or time.monotonic() - self._last_refresh < .25):
                return
            self.commands.put_nowait((method, deepcopy(params), context, time.monotonic()))
            if method == "Shelly.GetStatus":
                self._refresh_pending = True
        self._wake()

    def stop(self):
        self.stopped.set()
        self._wake()
        if self.socket is not None:
            try:
                self.socket.abort()
            except OSError:
                pass

    def _enrich(self, status):
        status = deepcopy(status)
        for key, values in status.items():
            if not re.fullmatch(r"input:\d+", key) or not isinstance(values, dict):
                continue
            config = self.input_configs.get(key, {})
            if config:
                values.update(_inputType=config.get("type"), _enabled=config.get("enable", True))
            if isinstance(values.get("state"), bool):
                if config.get("enable") is False or config.get("type", "switch") != "switch":
                    self.config_dirty = True
                # A reported binary position is positive evidence of an enabled switch input.
                values.update(_inputType="switch", _enabled=True)
        return status

    def _emit_notification(self, method, params, initial=False, sampled=()):
        if method == "NotifyEvent":
            events = params.get("events", [])
            if initial:
                events = [event for event in events if not isinstance(event, dict)
                          or event.get("component") not in sampled]
            if events:
                payload = {**params, "events": events}
                if initial:
                    payload["_initial"] = True
                self.emit("event", self.address, payload)
        else:
            kind = "snapshot" if method == "NotifyFullStatus" else "status"
            self.emit(kind, self.address, self._enrich(params))

    def _notify(self, method, params):
        if method not in ("NotifyStatus", "NotifyFullStatus", "NotifyEvent"):
            return
        revision = params.get("sys", {}).get("cfg_rev") if isinstance(params.get("sys"), dict) else None
        if revision is not None and revision != self.config_revision:
            self.config_revision = revision
            self.config_dirty = True
        if method == "NotifyEvent" and any(isinstance(event, dict) and event.get("event") == "config_changed"
                                            for event in params.get("events", [])):
            self.config_dirty = True
        if not self.online or self.reading:
            if len(self.pending_notifications) >= 256:
                raise ProtocolError("Notification buffer overflow during reconciliation")
            self.pending_notifications.append((method, deepcopy(params)))
        else:
            self._emit_notification(method, params)

    @staticmethod
    def _object(result, method):
        if not isinstance(result, dict):
            raise ProtocolError(f"{method} returned an invalid object")
        return result

    def _read_status(self, session, refresh_config=True):
        """Capture the RPC reply boundary before any slower configuration or Modbus reads."""
        self.reading = True
        if refresh_config:
            self.config_dirty = False
        try:
            status = self._object(session.call("Shelly.GetStatus"), "Shelly.GetStatus")
            boundary = len(self.pending_notifications)
            revision = status.get("sys", {}).get("cfg_rev") if isinstance(status.get("sys"), dict) else None
            if revision is not None and revision != self.config_revision:
                self.config_revision = revision
                self.config_dirty = True
            if refresh_config:
                self.config_dirty = False
            keys = [key for key, value in status.items() if re.fullmatch(r"input:\d+", key)
                    and isinstance(value, dict)]
            if refresh_config:
                try:
                    config = self._object(session.call("Sys.GetConfig"), "Sys.GetConfig")
                except AuthenticationError:
                    raise
                except RpcError:
                    pass  # A missing optional friendly name must not disable control.
                else:
                    device = config.get("device", {})
                    name = device.get("name") if isinstance(device, dict) else None
                    name = (" ".join(name.split()) or None) if isinstance(name, str) else None
                    if self.device_info is not None and name != self.device_info.get("name"):
                        self.device_info = {**self.device_info, "name": name}
                        if self.online:
                            self.emit("identity", self.address, dict(self.device_info))
                self.input_configs = {key: value for key, value in self.input_configs.items() if key in keys}
                for key in keys:
                    try:
                        config = self._object(session.call("Input.GetConfig", {"id": int(key.split(":")[1])}),
                                              "Input.GetConfig")
                    except AuthenticationError:
                        raise
                    except RpcError:
                        continue
                    self.input_configs[key] = config
                info = self.device_info or {}
                if keys and info.get("gen") == 4 and info.get("mac"):
                    try:
                        self.modbus_enabled = bool(self._object(session.call("Modbus.GetConfig"),
                                                               "Modbus.GetConfig").get("enable", False))
                    except AuthenticationError:
                        raise
                    except RpcError:
                        self.modbus_enabled = False
            status = self._enrich(status)
            samples = {}
            info = self.device_info or {}
            target = urlsplit("//" + self.address)
            readable = [int(key.split(":")[1]) for key in keys
                        if self.input_configs.get(key, {}).get("type") == "button"
                        and self.input_configs[key].get("enable", True)
                        and not self.input_configs[key].get("invert", False)]
            if (refresh_config and readable and self.modbus_enabled and info.get("gen") == 4
                    and info.get("mac") and target.port == 80):
                try:
                    samples = {f"input:{index}": {"_buttonState": state} for index, state in
                               read_button_inputs(target.hostname, info["mac"], readable).items()}
                except (OSError, ValueError):
                    pass  # Optional failures never fabricate OFF.
            pending, self.pending_notifications = self.pending_notifications, []
            return Snapshot(status, pending[:boundary], pending[boundary:], samples)
        finally:
            self.reading = False

    def _reconcile(self, session, initial=False, refresh_config=False):
        snapshot = self._read_status(session, refresh_config)
        if initial:
            status = deepcopy(snapshot.status)
            for key, sample in snapshot.samples.items():
                status[key].update(sample)
            with self.command_lock:
                self.online = True
            self.emit("ready", self.address, (dict(self.device_info), status))
            # Earlier status deltas are superseded by the full reply. Events carry
            # button position, which RPC cannot supply; sampled inputs supersede those too.
            for method, params in snapshot.before:
                if method == "NotifyEvent":
                    self._emit_notification(method, params, True, snapshot.samples)
            for method, params in snapshot.after:
                self._emit_notification(method, params, True, snapshot.samples)
            # A later physical sample also supersedes any buffered input status annotation.
            if snapshot.samples and any(method != "NotifyEvent" and any(key in params for key in snapshot.samples)
                                        for method, params in snapshot.after):
                self.emit("status", self.address, snapshot.samples)
        else:
            for method, params in snapshot.before:
                self._emit_notification(method, params)
            self.emit("snapshot", self.address, snapshot.status)
            for method, params in snapshot.after:
                self._emit_notification(method, params)
            if snapshot.samples:
                self.emit("status", self.address, snapshot.samples)

    def _component_status(self, session, namespace, index):
        method = f"{namespace}.GetStatus"
        status = self._object(session.call(method, {"id": index}), method)
        self.emit("status", self.address, self._enrich({f"{namespace.lower()}:{index}": status}))
        return status

    def _execute(self, session, method, params):
        if method == "Shelly.GetStatus":
            self._reconcile(session)
            return
        if method == "Light.Adjust":
            delta = params["delta"]
            status = self._component_status(session, "Light", params["id"])
            current = 0 if status.get("output") is False else status.get("brightness")
            if not isinstance(current, (int, float)) or isinstance(current, bool) or not math.isfinite(current):
                raise RpcError("Light brightness is unknown; relative command not sent")
            target = min(100, max(0, current + delta))
            method, params = "Light.Set", {"id": params["id"], "on": target > 0}
            if target > 0:
                params["brightness"] = target
        result = session.call(method, params)
        namespace = method.split(".")[0]
        if namespace in ("Light", "Switch") and method.endswith((".Set", ".Toggle")):
            # Targeted verification also works when a firmware omits a notification.
            self._component_status(session, namespace, params["id"])
        elif method.endswith(".GetStatus") and isinstance(result, dict):
            self.emit("status", self.address, self._enrich({f"{namespace.lower()}:{params['id']}": result}))

    def _discard_commands(self):
        with self.command_lock:
            self.online = False
            self._refresh_pending = False
            self.pending_notifications.clear()
            while True:
                try:
                    self.commands.get_nowait()
                except queue.Empty:
                    break

    def run(self):
        import websocket
        connect = self.connector or websocket.create_connection
        self._wake_reader, self._wake_writer = socket.socketpair()
        self._wake_reader.setblocking(False)
        self._wake_writer.setblocking(False)
        delay = 1
        try:
            while not self.stopped.is_set():
                session = None
                try:
                    self.socket = connect(f"ws://{self.address}/rpc", timeout=self.rpc_timeout,
                                          http_no_proxy=["*"], redirect_limit=0)
                    if self.stopped.is_set():
                        break
                    # Device identity is public. Never answer a password challenge before checking it.
                    session = Session(self.socket, self._notify, timeout=self.rpc_timeout)
                    info = self._object(session.call("Shelly.GetDeviceInfo"), "Shelly.GetDeviceInfo")
                    identity = info.get("id")
                    if not isinstance(identity, str) or not identity or info.get("gen") not in (2, 3, 4):
                        raise RpcError("Requires a Gen2, Gen3 or Gen4 Shelly RPC device")
                    if identity == self.exclude_id:
                        self.emit("alias", self.address, info)
                        break
                    if self.expected_id and identity != self.expected_id:
                        raise IdentityChanged(info)
                    if self.permitted is not None and not self.permitted(identity):
                        self.emit("suspended", self.address, info)
                        break
                    self.expected_id = identity
                    self.device_info = dict(info)
                    self.emit("identified", self.address, dict(info))
                    session.identity = identity
                    session.password = self.password(identity) if callable(self.password) else self.password
                    self.input_configs.clear()
                    self.modbus_enabled = False
                    self._reconcile(session, initial=True, refresh_config=True)
                    delay = 1
                    next_poll = time.monotonic() + self.poll_interval
                    next_config = time.monotonic() + self.config_interval
                    while not self.stopped.is_set():
                        try:
                            method, params, context, admitted = self.commands.get_nowait()
                        except queue.Empty:
                            pass
                        else:
                            try:
                                if time.monotonic() - admitted > 10:
                                    raise RpcError("Queued command expired after 10 seconds; not sent")
                                self._execute(session, method, params)
                            except AuthenticationError:
                                raise
                            except RpcError as exc:
                                namespace = method.split(".")[0].lower()
                                component = f"{namespace}:{params['id']}" if "id" in params else None
                                self.emit("command_error", self.address, {"method": method, "component": component,
                                                                         "reason": str(exc)})
                            finally:
                                if method == "Shelly.GetStatus":
                                    with self.command_lock:
                                        self._refresh_pending = False
                                        self._last_refresh = time.monotonic()
                        now = time.monotonic()
                        if self.config_dirty or now >= next_config:
                            self._reconcile(session, refresh_config=True)
                            next_config = time.monotonic() + self.config_interval
                            next_poll = time.monotonic() + self.poll_interval
                        elif now >= next_poll:
                            self._reconcile(session)
                            next_poll = time.monotonic() + self.poll_interval
                        timeout = 0 if not self.commands.empty() else min(next_poll, next_config) - time.monotonic()
                        if session.wait(self._wake_reader, timeout):
                            try:
                                session.listen()
                            except websocket.WebSocketTimeoutException:
                                pass
                except IdentityChanged as exc:
                    if not self.stopped.is_set():
                        self.emit("mismatch", self.address, exc.info)
                    break
                except Exception as exc:
                    if not self.stopped.is_set():
                        reason = (str(exc) if isinstance(exc, (RpcError, ConnectionError))
                                  else f"{type(exc).__name__}: {exc}")
                        if session is not None:
                            reason = session._safe_message(reason)
                        try:
                            if isinstance(exc, AuthenticationError):
                                self.emit("auth_required", self.address, {"id": self.expected_id, "reason": reason})
                            else:
                                self.emit("offline", self.address, reason)
                        except BufferError:
                            pass  # Adapter separately records and handles event-queue overflow.
                    if isinstance(exc, AuthenticationError):
                        delay = 300
                finally:
                    self._discard_commands()
                    sock, self.socket = self.socket, None
                    if sock is not None:
                        try:
                            sock.close(timeout=0)
                        except OSError:
                            pass
                if self.stopped.wait(delay):
                    break
                delay = min(delay * 2, 60) if delay < 300 else 300
        finally:
            self._discard_commands()
            for wake in (self._wake_reader, self._wake_writer):
                wake.close()
            self._wake_reader = self._wake_writer = None
