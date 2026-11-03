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
from .sampling import Sampler
from .rpc import AuthenticationError, ProtocolError, RpcError, Session


@dataclass
class Snapshot:
    status: dict
    before: list
    after: list


class IdentityChanged(RpcError):
    def __init__(self, info):
        self.info = info
        super().__init__("Shelly identity changed at this address")


class Worker(threading.Thread):
    def __init__(self, address, emit, password="", expected_id=None, connector=None,
                 poll_interval=15, config_interval=900, rpc_timeout=8, exclude_id=None, permitted=None, permitted_command=None,
                 sample_interval=60, sample_reader=None):
        super().__init__(name=f"Shelly-{address}", daemon=True)
        self.address, self.emit, self.password = address, emit, password
        self.expected_id, self.connector = expected_id, connector
        self.exclude_id, self.permitted = exclude_id, permitted
        self.permitted_command = permitted_command
        self.poll_interval, self.config_interval, self.rpc_timeout = poll_interval, config_interval, rpc_timeout
        self.commands = queue.Queue(maxsize=32)
        self.stopped = threading.Event()
        self.socket = None
        self.online = False
        self.command_lock = threading.Lock()
        self.pending_notifications = []
        self.device_info = None
        self.input_configs = {}
        self.configured_inputs = set()
        self.modbus_enabled = False
        self.config_dirty = True
        self.config_revision = None
        self.config_changes = 0
        self.next_config = 0
        self.sample_interval = sample_interval
        self.next_sample = 0
        self.sample_epoch = 0
        self.input_versions = {}
        self.sampler = Sampler(sample_reader or read_button_inputs, self._wake)
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
        self.sampler.stop()
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

    def _emit_notification(self, method, params, initial=False):
        if method == "NotifyEvent":
            events = params.get("events", [])
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
        self._observe_revision(params)
        if method == "NotifyEvent":
            for event in params.get("events", []):
                if not isinstance(event, dict):
                    continue
                if event.get("event") == "config_changed":
                    self._config_changed()
                if event.get("event") in ("btn_down", "btn_up"):
                    key = event.get("component")
                    if isinstance(key, str) and key in self.configured_inputs:
                        self.input_versions[key] = self.input_versions.get(key, 0) + 1
        else:
            for key, value in params.items():
                if key in self.configured_inputs and isinstance(value, dict) and isinstance(value.get("state"), bool):
                    self.input_versions[key] = self.input_versions.get(key, 0) + 1
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

    def _config_changed(self):
        self.config_changes += 1
        self.config_dirty = True

    def _observe_revision(self, status):
        revision = status.get("sys", {}).get("cfg_rev") if isinstance(status.get("sys"), dict) else None
        if revision is not None and revision != self.config_revision:
            self.config_revision = revision
            self._config_changed()

    def _read_status(self, session, refresh_config=True):
        """Keep the reply boundary separate from subsequent configuration reads."""
        self.reading = True
        try:
            status = self._object(session.call("Shelly.GetStatus"), "Shelly.GetStatus")
            boundary = len(self.pending_notifications)
            self._observe_revision(status)
            keys = [key for key, value in status.items() if re.fullmatch(r"input:\d+", key)
                    and isinstance(value, dict)]
            refresh_config = refresh_config or self.config_dirty or set(keys) != self.configured_inputs
            if refresh_config:
                self.sample_epoch += 1
                self.configured_inputs = set(keys)
                changes = self.config_changes
                failed = False
                try:
                    config = self._object(session.call("Sys.GetConfig"), "Sys.GetConfig")
                except AuthenticationError:
                    raise
                except RpcError:
                    failed = True
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
                        failed = True
                        continue
                    self.input_configs[key] = config
                info = self.device_info or {}
                self.modbus_enabled = False
                if (info.get("gen") == 4 and info.get("mac")
                        and any(config.get("type") == "button" for config in self.input_configs.values())):
                    try:
                        self.modbus_enabled = bool(self._object(session.call("Modbus.GetConfig"),
                                                               "Modbus.GetConfig").get("enable", False))
                    except AuthenticationError:
                        raise
                    except RpcError:
                        failed = True
                self.config_dirty = self.config_changes != changes
                interval = min(60, self.config_interval) if failed or self.config_revision is None else self.config_interval
                self.next_config = time.monotonic() + interval
                self.next_sample = 0
            status = self._enrich(status)
            pending, self.pending_notifications = self.pending_notifications, []
            return Snapshot(status, pending[:boundary], pending[boundary:])
        finally:
            self.reading = False

    def _reconcile(self, session, initial=False, refresh_config=False):
        snapshot = self._read_status(session, refresh_config)
        if initial:
            with self.command_lock:
                self.online = True
            self.emit("ready", self.address, (dict(self.device_info), snapshot.status))
            # Earlier status deltas are superseded by the reply; startup events
            # establish position without firing Button Pressed automation.
            for method, params in snapshot.before:
                if method == "NotifyEvent":
                    self._emit_notification(method, params, True)
            for method, params in snapshot.after:
                self._emit_notification(method, params, True)
        else:
            for method, params in snapshot.before:
                self._emit_notification(method, params)
            self.emit("snapshot", self.address, snapshot.status)
            for method, params in snapshot.after:
                self._emit_notification(method, params)

    def _sample_buttons(self, now):
        result = self.sampler.take()
        if result is not None:
            token, samples = result
            epoch, changes, configs, versions = token
            if (epoch == self.sample_epoch and changes == self.config_changes
                    and configs == self.input_configs and not self.config_dirty and self.online
                    and not self.stopped.is_set()):
                accepted = {f"input:{index}": {"_buttonState": state} for index, state in samples.items()
                            if isinstance(state, bool) and f"input:{index}" in versions
                            and versions[f"input:{index}"] == self.input_versions.get(f"input:{index}", 0)}
                if accepted:
                    self.emit("status", self.address, accepted)
        if now < self.next_sample or self.config_dirty or not self.online or self.stopped.is_set():
            return
        info = self.device_info or {}
        target = urlsplit("//" + self.address)
        readable = [int(key.split(":")[1]) for key, config in self.input_configs.items()
                    if config.get("type") == "button" and config.get("enable", True) and not config.get("invert", False)]
        if not (readable and self.modbus_enabled and info.get("gen") == 4 and info.get("mac") and target.port == 80):
            self.next_sample = now + self.sample_interval
            return
        versions = {f"input:{index}": self.input_versions.get(f"input:{index}", 0) for index in readable}
        token = (self.sample_epoch, self.config_changes, deepcopy(self.input_configs), versions)
        if self.sampler.submit(token, target.hostname, info["mac"], readable):
            self.next_sample = now + self.sample_interval

    def _component_status(self, session, namespace, index):
        method = f"{namespace}.GetStatus"
        status = self._object(session.call(method, {"id": index}), method)
        self.emit("status", self.address, self._enrich({f"{namespace.lower()}:{index}": status}))
        return status

    def _command_allowed(self, method, params):
        if self.permitted_command is None or self.permitted_command(self.expected_id, method, params):
            return True
        self.emit("command_cancelled", self.address, method)
        return False

    def _execute(self, session, method, params):
        if not self._command_allowed(method, params):
            return
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
        if not self._command_allowed(method, params):
            return  # A relative read may have overlapped the stop callback.
        result = session.call(method, params)
        namespace = method.split(".")[0]
        if namespace in ("Light", "Switch") and method.endswith((".Set", ".Toggle")):
            # Targeted verification also works when a firmware omits a notification.
            if self.permitted_command is None or self.permitted_command(self.expected_id, method, params):
                self._component_status(session, namespace, params["id"])
        elif method.endswith(".GetStatus") and isinstance(result, dict):
            self.emit("status", self.address, self._enrich({f"{namespace.lower()}:{params['id']}": result}))

    def _discard_commands(self):
        self.sample_epoch += 1
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
                    self.sample_epoch += 1
                    self.config_dirty = True
                    self.config_revision = None
                    self.input_versions.clear()
                    self.configured_inputs.clear()
                    self.input_configs.clear()
                    self.modbus_enabled = False
                    self._reconcile(session, initial=True, refresh_config=True)
                    delay = 1
                    next_poll = time.monotonic() + self.poll_interval
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
                        if self.config_dirty or now >= self.next_config:
                            self._reconcile(session, refresh_config=True)
                            next_poll = time.monotonic() + self.poll_interval
                        elif now >= next_poll:
                            self._reconcile(session)
                            next_poll = time.monotonic() + self.poll_interval
                        self._sample_buttons(time.monotonic())
                        next_sample = math.inf if self.sampler.busy else self.next_sample
                        timeout = (0 if not self.commands.empty() else
                                   min(next_poll, self.next_config, next_sample) - time.monotonic())
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
            self.sampler.stop()
            self._discard_commands()
            for wake in (self._wake_reader, self._wake_writer):
                wake.close()
            self._wake_reader = self._wake_writer = None
