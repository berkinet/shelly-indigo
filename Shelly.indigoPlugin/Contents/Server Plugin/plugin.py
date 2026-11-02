"""Indigo adapter. Network workers never call Indigo APIs directly."""
from pathlib import Path
import importlib
import logging
import queue
import re
import sys
import threading
import time

# Indigo executes this entry point without __file__. Imported helper modules
# have normal module metadata, so anchor bundled dependencies to our package.
import shelly
sys.path.insert(0, str(Path(shelly.__file__).resolve().parents[2] / "Packages"))
import indigo
from shelly.commands import translate
from shelly.connections import CommunicationPolicy, Connections
from shelly.credentials import CredentialEdits, Credentials
from shelly.diagnostics import Diagnostics
from shelly.discovery import Discovery
from shelly.model import Inventory, endpoint
from shelly.worker import Worker

# Indigo dynamic lists require nonempty IDs, including non-device rows.
ALL_DEVICES = "_all_"
NO_CHANNEL = "_none_"


class Plugin(indigo.PluginBase):
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        super().__init__(pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
        self.events = queue.Queue(maxsize=1024)
        self.closing = threading.Event()
        self.connections = Connections()
        self.communication = CommunicationPolicy()
        self.stopped_devices = set()
        self.workers = self.connections.workers
        self.credentials = Credentials(pluginPrefs.get("password", ""), pluginPrefs.get("devicePasswords", "{}"))
        self.credential_edits = CredentialEdits()
        self.outages = {}
        self.overflows = set()
        self.pending_probes = {}
        self.overflow_lock = threading.Lock()
        self.info = {}
        self.inventory = {}
        self.catalog = {}
        self.discovered_devices = {}
        self.diagnostics = Diagnostics(self.logger)
        self.component_reports = self.diagnostics.reports
        self.reported_channel_warnings = self.diagnostics.warnings
        self._next_maintenance = 0
        self.catalog_lock = threading.Lock()
        self.reported_binding_errors = {}
        self.button_triggers = {}
        self.trigger_lock = threading.Lock()
        self.connection_preferences = {key: pluginPrefs.get(key, default) for key, default in
                                       (("discovery", True), ("hosts", ""), ("password", ""))}

    def _configure_logging(self, level):
        # Keep detailed diagnostics in the plugin file independently of the UI.
        self.logger.setLevel(logging.DEBUG)
        self.plugin_file_handler.setLevel(logging.DEBUG)
        self.indigo_log_handler.setLevel(logging.DEBUG if level == "DEBUG" else logging.INFO)

    def startup(self):
        self._configure_logging(self.pluginPrefs.get("logLevel", "INFO"))
        for module in ("zeroconf", "ifaddr", "websocket"):
            try:
                importlib.import_module(module)
            except ImportError as exc:
                missing = getattr(exc, "name", None) or module
                message = (f"Missing Shelly dependency '{missing}'. Reinstall the complete "
                           "Shelly plugin bundle; Contents/Packages must be included.")
                self.logger.error(message)
                raise RuntimeError(message) from exc

    def _emit(self, kind, address, payload=None):
        if self.closing.is_set():
            return False
        try:
            self.events.put((kind, address, payload), timeout=0.1)
            return True
        except queue.Full:
            self.logger.error("Shelly event queue is full; %s request was not accepted", kind)
            return False

    def _devices(self):
        return indigo.devices.iter("self")

    def _authorize_credentials(self):
        self.credentials.authorize(dev.pluginProps.get("shellyId") for dev in self._devices()
                                   if self._selection_key(dev.pluginProps))

    def _device_communicating(self, dev):
        return dev.enabled and dev.id not in self.stopped_devices

    def _sync_communication(self):
        bindings = [dev for dev in self._devices() if self._selection_key(dev.pluginProps)]
        enabled = {}
        for dev in bindings:
            identity = dev.pluginProps["shellyId"]
            enabled[identity] = enabled.get(identity, False) or self._device_communicating(dev)
        self.communication.replace(enabled)
        return bindings

    def _communication_bindings(self, address, identity=None, bindings=None):
        bindings = self._sync_communication() if bindings is None else bindings
        return [dev for dev in bindings if (dev.pluginProps["shellyId"] == identity if identity else
                bool(dev.pluginProps.get("host")) and endpoint(dev.pluginProps["host"]) == address)]

    def _communication_allowed(self, address, identity=None, bindings=None):
        matches = self._communication_bindings(address, identity, bindings)
        return not matches or any(self._device_communicating(dev) for dev in matches)

    def _pause(self, address):
        # Preserve offline selector entries so another channel can be explicitly
        # added later, while removing the worker and fencing all of its messages.
        with self.catalog_lock:
            cached = {key: {**entry, "online": False} for key, entry in self.catalog.items()
                      if entry["host"] == address}
        self._retire(address)
        self.pending_probes.pop(address, None)
        self.outages.pop(address, None)
        with self.catalog_lock:
            self.catalog.update(cached)
            for entry in self.discovered_devices.values():
                if entry["host"] == address:
                    entry["paused"] = True
        self.logger.debug("Shelly communication stopped at %s: no enabled Indigo channels", address)

    def _cache_paused_identity(self, address, info):
        with self.catalog_lock:
            old = self.discovered_devices.get(info["id"], {})
            name = info.get("name") or old.get("name")
            self.discovered_devices[info["id"]] = {"id": info["id"], "name": name,
                "host": address, "online": False, "requiresPassword": False, "paused": True}
            for key, entry in list(self.catalog.items()):
                if entry["shellyId"] == info["id"]:
                    self.catalog[key] = {**entry, "host": address, "online": False,
                        "label": self._channel_label({"id": info["id"], "name": name}, entry["component"], address)}

    def _suspend_identity(self, address, info):
        self._pause(address)
        self._cache_paused_identity(address, info)

    def _connect(self, address, expected_id=None, exclude_id=None, hint_id=None):
        address = endpoint(address)
        known = self.info.get(address, {}).get("id")
        if known is None and address in self.workers:
            known = self.workers[address].expected_id
        identity = expected_id or hint_id or known
        if not self._communication_allowed(address, identity):
            paused = {host for host, worker in self.workers.items()
                      if identity and (self.info.get(host, {}).get("id") or worker.expected_id) == identity}
            owner = self.connections.owners.get(identity)
            if owner is not None:
                paused.add(owner)
            if address in self.workers and (not known or known == identity):
                paused.add(address)
            for host in paused:
                self._pause(host)
            if hint_id and (not known or known == hint_id):
                self._cache_paused_identity(address, {"id": hint_id})
            return
        self.connections.touch(address)
        self._authorize_credentials()
        if address in self.workers:
            if self.workers[address].is_alive():
                return
            expected_id = expected_id or self.workers[address].expected_id
            self._retire(address)
        generation = self.connections.reserve(address)
        closing = self.closing
        def emit(kind, host, payload=None):
            if closing.is_set():
                return
            try:
                self.events.put((kind, host, payload, generation), timeout=0.1)
            except queue.Full as exc:
                with self.overflow_lock:
                    self.overflows.add((host, generation))
                raise BufferError("Indigo event queue is full") from exc
        explicit = {endpoint(host.strip()) for host in self.pluginPrefs.get("hosts", "").split(",") if host.strip()}
        password = lambda identity: self.credentials.password(identity, address in explicit)
        worker = Worker(address, emit, password, expected_id, exclude_id=exclude_id,
                        permitted=self.communication.permits)
        self.workers[address] = worker
        self.logger.debug("Discovered Shelly endpoint %s", address)
        try:
            worker.start()
        except Exception:
            self._retire(address)
            raise

    def _retire(self, address):
        worker = self.connections.retire(address)
        self.info.pop(address, None)
        self.inventory.pop(address, None)
        self.component_reports.pop(address, None)
        with self.catalog_lock:
            self.catalog = {key: entry for key, entry in self.catalog.items() if entry["host"] != address}
            for entry in self.discovered_devices.values():
                if entry["host"] == address:
                    entry["online"] = False
        return worker

    def _process_events(self):
        for _ in range(100):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle(*event)
            except (ConnectionError, queue.Full, ValueError, KeyError) as exc:
                kind, address, payload, *_ = event
                context = kind
                if kind == "command":
                    context = f"device {payload[0]} {payload[1]}"
                    try:
                        dev = indigo.devices[payload[0]]
                    except KeyError:
                        pass
                    else:
                        context = f"{dev.name} (Indigo {dev.id}) {payload[1]}"
                        address = dev.pluginProps.get("host", address)
                self.logger.warning("Shelly %s at %s rejected: %s", context, address or "configured endpoint", exc)
            except Exception:
                self.logger.exception("Could not process Shelly device update")
        with self.overflow_lock:
            overflows, self.overflows = self.overflows, set()
        for address, generation in overflows:
            if self.connections.current(address, generation):
                expected = self.workers[address].expected_id
                self._handle("offline", address, "Event queue overloaded; connection reset, queued commands discarded")
                self._retire(address)
                self._connect(address, expected)
        probes, self.pending_probes = self.pending_probes, {}
        for address, excluded in probes.items():
            if address not in self.workers:
                self._connect(address, exclude_id=excluded)
        if time.monotonic() >= self._next_maintenance:
            self._prune_connections()
            self._next_maintenance = time.monotonic() + 60

    def _prune_connections(self):
        configured = {endpoint(dev.pluginProps["host"]) for dev in self._devices() if dev.pluginProps.get("host")}
        configured.update(endpoint(host.strip()) for host in self.pluginPrefs.get("hosts", "").split(",") if host.strip())
        cutoff = time.monotonic() - 900
        bindings = self._sync_communication()
        for address, worker in list(self.workers.items()):
            identity = self.info.get(address, {}).get("id") or worker.expected_id
            if not self._communication_allowed(address, identity, bindings):
                self._pause(address)
                continue
            if (address not in configured and not worker.online
                    and self.connections.seen.get(address, 0) < cutoff):
                self.logger.debug("Retiring stale unassigned Shelly endpoint %s", address)
                self._retire(address)
                self.outages.pop(address, None)
                with self.catalog_lock:
                    self.discovered_devices = {identity: entry for identity, entry in self.discovered_devices.items()
                                               if entry["host"] != address}

    def deviceStartComm(self, dev):
        address = dev.pluginProps.get("host")
        if address:
            # Refresh static XML states for all device types after upgrades.
            dev.stateListOrDisplayStateIdChanged()
            if self._block_invalid_binding(dev):
                return
            if dev.deviceTypeId == "shellyInput":
                dev.updateStatesOnServer([{"key": "online", "value": False},
                                         {"key": "stateKnown", "value": False},
                                         {"key": "inputStatus", "value": "Connecting"}])
                dev.updateStateOnServer("onOffState", dev.states["onOffState"],
                                        uiValue="Connecting", triggerEvents=False)
                dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)
                dev.setErrorStateOnServer(None)
            else:
                dev.setErrorStateOnServer("Connecting")
            self._emit("start_device", address, (dev.id, dev.pluginProps.get("shellyId")))

    def deviceStopComm(self, dev):
        address = dev.pluginProps.get("host")
        if address:
            self._emit("stop_device", endpoint(address), (dev.id, dev.pluginProps.get("shellyId")))

    def runConcurrentThread(self):
        discovery = None
        if self.closing.is_set():
            # Indigo retries this method on the same object after an unexpected exit.
            self.closing = threading.Event()
            self.events = queue.Queue(maxsize=1024)
            self.stopped_devices.clear()
        try:
            for dev in self._devices():
                if self._device_communicating(dev) and dev.pluginProps.get("host") and self._selection_key(dev.pluginProps):
                    self._connect(dev.pluginProps["host"], dev.pluginProps["shellyId"])
            for address in self.pluginPrefs.get("hosts", "").split(","):
                if address.strip():
                    self._connect(address.strip())
            if self.pluginPrefs.get("discovery", True):
                try:
                    closing = self.closing
                    def found(address, identity=None):
                        if not closing.is_set():
                            self._emit("bonjour", address, identity)
                    discovery = Discovery(found)
                    self.logger.debug("Shelly discovery started (Gen2–Gen4 switches, dimmers and inputs)")
                except Exception as exc:
                    self.logger.error("Bonjour discovery unavailable: %s; explicit hosts remain active", exc)
            while True:
                self.sleep(0.01)
                self._process_events()
        except self.StopThread:
            pass
        finally:
            self.closing.set()
            self.pending_probes.clear()
            try:
                if discovery is not None:
                    discovery.close()
            finally:
                workers = [self._retire(address) for address in list(self.workers)]
                deadline = time.monotonic() + 10
                for worker in workers:
                    if worker is not None:
                        worker.join(max(0, deadline - time.monotonic()))
                for dev in self._devices():
                    if self._device_communicating(dev) and self._selection_key(dev.pluginProps):
                        self._channel_unavailable(dev, "Plugin communication stopped")

    def _handle(self, kind, address, payload, generation=None):
        if generation is not None and not self.connections.current(address, generation):
            return
        if kind == "discover":
            self._connect(address, payload)
        elif kind == "bonjour":
            self._connect(address, hint_id=payload)
        elif kind == "stop_device":
            dev_id, identity = payload
            self.stopped_devices.add(dev_id)
            bindings = self._sync_communication()
            for host, worker in list(self.workers.items()):
                occupant = self.info.get(host, {}).get("id") or worker.expected_id
                if (occupant == identity or (not occupant and host == address)):
                    matches = self._communication_bindings(host, identity, bindings)
                    if not any(self._device_communicating(dev) for dev in matches):
                        self._pause(host)
        elif kind == "start_device":
            dev_id, identity = payload
            self.stopped_devices.discard(dev_id)
            self._sync_communication()
            with self.catalog_lock:
                entry = self.discovered_devices.get(identity, {})
                host = self.connections.owners.get(identity) or entry.get("host") or address
            self._connect(host, identity)
            self._handle("refresh", host, None)
        elif kind == "suspended":
            self._suspend_identity(address, payload)
        elif kind == "credentials":
            identity, password = payload
            self.pluginPrefs["devicePasswords"] = self.credentials.set(identity, password)
            with self.catalog_lock:
                entry = dict(self.discovered_devices.get(identity, {}))
            host = entry.get("host") or self.connections.owners.get(identity)
            if not host:
                matches = self._communication_bindings("", identity)
                host = next((dev.pluginProps.get("host") for dev in matches if dev.pluginProps.get("host")), None)
            if host and self._communication_allowed(endpoint(host), identity):
                host = endpoint(host)
                self._retire(host)
                self.logger.info("Updated local credentials for %s; querying again", identity)
                self._connect(host, identity)
            else:
                self.logger.info("Updated local credentials for %s; communication remains disabled or no endpoint is known", identity)
        elif kind == "create_devices":
            created = 0
            kinds = set()
            if payload.get("includeSwitches"):
                kinds.add("switch")
            if payload.get("includeInputs"):
                kinds.add("input")
            if payload.get("includeDimmers"):
                kinds.add("light")
            target = payload.get("shellyId") or ALL_DEVICES
            for host, inventory in list(self.inventory.items()):
                info = self.info.get(host)
                if info is None:
                    continue
                if target != ALL_DEVICES and info["id"] != target:
                    continue
                with self.catalog_lock:
                    online = {key for key, entry in self.catalog.items() if entry["online"]}
                channels = [channel for channel in inventory.channels.values()
                            if channel.kind in kinds and f"{info['id']}/{channel.key}" in online]
                created += self._publish_channels(host, channels, create=True)
            self.logger.info("Create Indigo devices completed: %s new devices; existing bindings preserved", created)
        elif kind == "identity" and address in self.info:
            self.info[address] = dict(payload)
            with self.catalog_lock:
                identity = payload["id"]
                if identity in self.discovered_devices:
                    self.discovered_devices[identity]["name"] = payload.get("name")
                for key, entry in list(self.catalog.items()):
                    if entry["host"] == address:
                        self.catalog[key] = {**entry, "shellyName": payload.get("name") or "",
                                             "label": self._channel_label(payload, entry["component"], address)}
        elif kind == "command":
            dev_id, action, *values = payload
            dev = indigo.devices[dev_id]
            if not self._device_communicating(dev) or self._block_invalid_binding(dev):
                return
            if dev.deviceTypeId not in ("shellySwitch", "shellyDimmer"):
                return
            host = dev.pluginProps["host"]
            worker = self.workers.get(host)
            if worker is None or self.info.get(host, {}).get("id") != dev.pluginProps["shellyId"]:
                raise ConnectionError("Shelly is offline or its address now belongs to another device")
            if dev.pluginProps["component"] not in self.inventory[host].channels:
                raise ConnectionError("Shelly channel is unavailable")
            method, params = translate(dev.deviceTypeId, dev.pluginProps["component"], action, values)
            worker.command(method, params)
        elif kind == "refresh":
            worker = self.workers.get(address)
            if worker is not None and worker.online:
                worker.command("Shelly.GetStatus", {})
        elif kind == "identified":
            if not self._communication_allowed(address, payload["id"]):
                self._suspend_identity(address, payload)
                return
            with self.catalog_lock:
                old = self.discovered_devices.get(payload["id"], {})
                if not old.get("online") or old.get("host") == address:
                    self.discovered_devices[payload["id"]] = {
                        "id": payload["id"], "name": payload.get("name"), "host": address, "online": True,
                        "requiresPassword": bool(payload.get("auth_en"))}
        elif kind == "alias":
            # This address still serves a unit already moved to another endpoint.
            self.logger.debug("Retired alternate endpoint %s for %s", address, payload["id"])
            self._retire(address)
        elif kind == "mismatch":
            self._handle("offline", address, "Address now belongs to another Shelly; re-querying its public identity")
            self._retire(address)
            self._connect(address, payload["id"])
        elif kind == "auth_required":
            self._handle("offline", address, payload["reason"] + "; use Set Device Password in the Shelly menu")
            with self.catalog_lock:
                entry = self.discovered_devices.get(payload.get("id"))
                if entry is not None and entry["host"] == address:
                    entry.update(online=True, requiresPassword=True)
        elif kind == "ready":
            info, status = payload
            if not self._communication_allowed(address, info["id"]):
                self._suspend_identity(address, info)
                return
            obsolete = set(self.connections.claim(info["id"], address))
            obsolete.update(host for host, item in self.info.items() if host != address and item["id"] == info["id"])
            for old_address in obsolete:
                self._retire(old_address)
                self.outages.pop(old_address, None)
                # A prior Bonjour update may have arrived while the old worker was
                # still registered. Probe the vacated address for a different unit.
                self.pending_probes[old_address] = info["id"]
            if address in self.outages:
                self.logger.info("Shelly %s at %s recovered", self._device_label(info), address)
                self.outages.pop(address, None)
            self.info[address] = info
            self.logger.debug("Querying %s (Gen%s)", info["id"], info["gen"])
            self.inventory[address] = Inventory()
            self.component_reports[address] = (set(), None)
            with self.catalog_lock:
                for entry in self.discovered_devices.values():
                    if entry["host"] == address:
                        entry["online"] = False
                self.discovered_devices[info["id"]] = {
                    "id": info["id"], "name": info.get("name"), "host": address, "online": True,
                    "requiresPassword": False}
                self.catalog = {key: entry for key, entry in self.catalog.items()
                                if entry["shellyId"] != info["id"] and entry["host"] != address}
            self._update(address, status, full=True)
        elif kind in ("status", "snapshot") and address in self.info:
            self._update(address, payload, full=kind == "snapshot")
        elif kind == "event" and address in self.info:
            for event in payload.get("events", []):
                component = event.get("component") if isinstance(event, dict) else None
                previous = self.inventory[address].channels.get(component) if isinstance(component, str) else None
                timestamp = event.get("ts") if isinstance(event, dict) else None
                duplicate = (previous is not None and previous.button_state is True
                             and (timestamp is None or previous.event_timestamp == timestamp))
                channel = self.inventory[address].apply_input_event(event)
                if channel is not None:
                    # Publish each transition separately, even when a frame contains
                    # both a quick press and release for the same component.
                    self._publish_channels(address, [channel])
                    if (event.get("event") == "btn_down" and not duplicate
                            and not payload.get("_initial", False)):
                        self._fire_button_pressed(address, channel, event)
            self._report_components(address, {})
        elif kind == "offline":
            identity = self.info.get(address, {}).get("id")
            if identity is None and address in self.workers:
                identity = self.workers[address].expected_id
            if not self._communication_allowed(address, identity):
                self._pause(address)
                return
            if address in self.inventory:
                self.inventory[address].invalidate_buttons()
            with self.catalog_lock:
                for entry in self.discovered_devices.values():
                    if entry["host"] == address:
                        entry["online"] = False
                for key, entry in list(self.catalog.items()):
                    if entry["host"] == address:
                        self.catalog[key] = {**entry, "online": False}
            if self.outages.get(address) != payload:
                matches = self._communication_bindings(address, identity)
                active = ", ".join(f"{dev.name} (Indigo {dev.id})" for dev in matches
                                   if self._device_communicating(dev))
                context = f"; active Indigo devices: {active}" if active else ""
                self.logger.warning("Shelly %s unavailable%s: %s", address, context, payload)
                self.outages[address] = payload
            else:
                self.logger.debug("Shelly %s still unavailable: %s", address, payload)
            for dev in self._devices():
                if self._device_communicating(dev) and dev.pluginProps.get("host") == address:
                    if self._block_invalid_binding(dev):
                        continue
                    dev.updateStateOnServer("online", False)
                    if dev.deviceTypeId == "shellyInput":
                        dev.updateStateOnServer("stateKnown", False)
                        dev.updateStateOnServer("inputStatus", "Offline")
                    dev.setErrorStateOnServer("Offline")
        elif kind == "command_error":
            if isinstance(payload, dict):
                identity = self.info.get(address, {}).get("id")
                names = [f"{dev.name} (Indigo {dev.id})" for dev in self._devices()
                         if dev.pluginProps.get("shellyId") == identity
                         and dev.pluginProps.get("component") == payload.get("component")]
                self.logger.error("Shelly command %s for %s at %s failed: %s", payload["method"],
                                  ", ".join(names) or payload.get("component") or identity or "device",
                                  address, payload["reason"])
            else:
                self.logger.error("Shelly %s: %s", address, payload)

    @staticmethod
    def _channel_unavailable(dev, reason):
        dev.updateStateOnServer("online", False)
        if dev.deviceTypeId == "shellyInput":
            dev.updateStateOnServer("stateKnown", False)
            dev.updateStateOnServer("inputStatus", reason)
        dev.setErrorStateOnServer(reason)

    def _update(self, address, status, full=False):
        devices = list(self._devices())
        if full:
            channels, removed = self.inventory[address].replace(status)
            identity = self.info[address]["id"]
            for dev in self._devices():
                if (self._device_communicating(dev) and dev.pluginProps.get("shellyId") == identity
                        and dev.pluginProps.get("component") in removed):
                    self._channel_unavailable(dev, "Channel unavailable")
            with self.catalog_lock:
                self.catalog = {key: entry for key, entry in self.catalog.items()
                                if not (entry["shellyId"] == identity and entry["component"] in removed)}
            previous = self.component_reports.get(address, (set(), None))[1]
            self.component_reports[address] = (set(), previous)
        else:
            channels = self.inventory[address].merge(status)
        self._publish_channels(address, channels, device_snapshot=devices)
        self._report_components(address, status, devices)

    def _report_components(self, address, status, devices=None):
        self.diagnostics.report(address, self.info[address], self.inventory[address], status,
                                self._devices() if devices is None else devices,
                                self._device_label(self.info[address]))

    def _publish_channels(self, address, channels, create=False, device_snapshot=None):
        created = 0
        info = self.info[address]
        devices = {}
        for dev in self._devices() if device_snapshot is None else device_snapshot:
            key = (dev.pluginProps.get("shellyId"), dev.pluginProps.get("component"))
            # Prefer a correctly typed device if an older installation contains
            # both a valid binding and an inconsistent duplicate.
            if key not in devices or self._expected_device_type(dev.pluginProps) == dev.deviceTypeId:
                devices[key] = dev
        for channel in channels:
            key = (info["id"], channel.key)
            props = self._channel_properties(info, address, channel)
            selection = self._selection_key(props)
            supported = channel.supported
            with self.catalog_lock:
                if supported:
                    self.catalog[selection] = {
                        **props, "online": True,
                        "shellyName": info.get("name") or "",
                        "label": self._channel_label(info, channel.key, address)}
                else:
                    self.catalog.pop(selection, None)
            dev = devices.get(key)
            if dev is None:
                if not supported or not create:
                    continue
                try:
                    dev = indigo.device.create(
                        protocol=indigo.kProtocol.Plugin,
                        address=f"{info['id']}/{channel.key}",
                        name=f"{self._device_label(info)} {channel.key}",
                        deviceTypeId=self._expected_device_type(props),
                        props=props)
                except Exception as exc:
                    detail = " ".join(str(exc).split())[:240]
                    self.logger.error("Could not create %s / %s in Indigo (%s: %s); other channels will continue",
                                      info["id"], channel.key, type(exc).__name__, detail)
                    continue
                devices[key] = dev
                created += 1
                self.logger.info("Created %s", dev.name)
            if not self._device_communicating(dev) or self._block_invalid_binding(dev):
                continue
            if dev.pluginProps.get("host") != address:
                props = dict(dev.pluginProps)
                props["host"] = address
                dev.replacePluginPropsOnServer(props)
            states = channel.states()
            # Inputs do not expose metering fields in Devices.xml.
            if channel.kind == "input":
                states = {k: v for k, v in states.items() if k in ("onOffState", "stateKnown", "inputMode", "inputStatus")}
            states["online"] = True
            if any(key not in dev.states for key in states):
                dev.stateListOrDisplayStateIdChanged()
                dev = indigo.devices[dev.id]
                if any(key not in dev.states for key in states):
                    dev.setErrorStateOnServer("State definitions unavailable; restart the Shelly plugin")
                    continue
            dev.updateStatesOnServer([{"key": k, "value": v} for k, v in states.items()])
            if channel.kind == "input":
                # Retain the last raw binary value when unknown, but make the
                # built-in sensor display truthful without emitting a fake edge.
                value = states.get("onOffState", dev.states["onOffState"])
                dev.updateStateOnServer("onOffState", value, uiValue=states["inputStatus"], triggerEvents=False)
                image = (indigo.kStateImageSel.SensorOn if states.get("stateKnown")
                         and states.get("onOffState") else indigo.kStateImageSel.SensorOff)
                dev.updateStateImageOnServer(image)
            dev.setErrorStateOnServer(None)
        if created:
            self._authorize_credentials()
        return created

    def _button_input(self, device_id):
        try:
            dev = indigo.devices[int(device_id)]
        except (TypeError, ValueError, KeyError):
            return None
        if (dev.deviceTypeId != "shellyInput" or self._expected_device_type(dev.pluginProps) != "shellyInput"
                or not self._selection_key(dev.pluginProps) or dev.states.get("inputMode") != "button"):
            return None
        return dev

    def getButtonInputs(self, filter="", valuesDict=None, typeId="", targetId=0):
        options = []
        for dev in self._devices():
            if self._button_input(dev.id) is not None:
                suffix = " [disabled]" if not dev.enabled else ""
                options.append((str(dev.id), f"{dev.name}{suffix}"))
        label = "Select a Shelly button input" if options else "No configured button inputs found"
        return [(NO_CHANNEL, label)] + sorted(options, key=lambda item: item[1].casefold())

    def validateEventConfigUi(self, valuesDict, typeId, eventId):
        if typeId != "buttonPressed" or self._button_input(valuesDict.get("inputDeviceId")) is None:
            message = "Select an existing Shelly Input configured in button mode."
            return False, valuesDict, indigo.Dict({"inputDeviceId": message, "showAlertText": message})
        valuesDict["inputDeviceId"] = str(int(valuesDict["inputDeviceId"]))
        return True, valuesDict

    def triggerStartProcessing(self, trigger):
        with self.trigger_lock:
            self.button_triggers.pop(trigger.id, None)
            if trigger.pluginTypeId == "buttonPressed":
                try:
                    device_id = int(trigger.pluginProps.get("inputDeviceId"))
                except (TypeError, ValueError):
                    return
                self.button_triggers[trigger.id] = device_id

    def triggerStopProcessing(self, trigger):
        with self.trigger_lock:
            self.button_triggers.pop(trigger.id, None)

    def _fire_button_pressed(self, address, channel, event):
        with self.trigger_lock:
            subscriptions = list(self.button_triggers.items())
        identity = self.info[address]["id"]
        for trigger_id, device_id in subscriptions:
            try:
                trigger = indigo.triggers[trigger_id]
            except KeyError:
                continue
            if not trigger.enabled or trigger.pluginTypeId != "buttonPressed":
                continue
            if str(trigger.pluginProps.get("inputDeviceId")) != str(device_id):
                continue
            dev = self._button_input(device_id)
            if (dev is None or not self._device_communicating(dev) or dev.pluginProps.get("shellyId") != identity
                    or dev.pluginProps.get("component") != channel.key):
                continue
            data = indigo.Dict({"source": "com.berkinet.indigoplugin.shelly",
                                "inputDeviceId": device_id, "shellyId": identity,
                                "component": channel.key, "event": "btn_down"})
            if isinstance(event.get("ts"), (int, float)):
                data["ts"] = event["ts"]
            try:
                indigo.trigger.execute(trigger_id, ignoreConditions=False, trigger_data=data)
            except Exception:
                self.logger.exception("Could not execute Button Pressed trigger %s", trigger_id)

    @staticmethod
    def _device_label(info):
        name = info.get("name")
        name = " ".join(name.split()) if isinstance(name, str) else ""
        return f"{name} ({info['id']})" if name and name != info["id"] else info["id"]

    @classmethod
    def _channel_label(cls, info, component, address):
        return f"{cls._device_label(info)} / {component} ({address})"

    def getDiscoveredShellyDevices(self, filter="", valuesDict=None, typeId="", targetId=0):
        with self.catalog_lock:
            supported = {entry["shellyId"] for entry in self.catalog.values() if entry["online"]}
            devices = {identity: self._device_label(entry) +
                       (" [password required — use Set Device Password]" if entry.get("requiresPassword")
                        else " [no supported channels]" if identity not in supported else "")
                       for identity, entry in self.discovered_devices.items() if entry["online"]}
        options = [(ALL_DEVICES, "All discovered Shelly devices")] + sorted(
            devices.items(), key=lambda item: item[1].casefold())
        self.logger.debug("Create Indigo Devices menu choices: %s", options)
        return options

    def refreshDiscoveredDevices(self, valuesDict, typeId="", targetId=0):
        # Indigo reloads dynamic lists after this button callback returns. Discovery
        # continues independently; refresh never creates devices or operates outputs.
        return valuesDict

    def createDiscoveredDevices(self, valuesDict=None, typeId=""):
        values = valuesDict or {}
        switches = values.get("includeSwitches", True)
        inputs = values.get("includeInputs", False)
        dimmers = values.get("includeDimmers", True)
        if not switches and not inputs and not dimmers:
            self.logger.warning("Select switches, dimmers and/or inputs before creating Indigo devices")
            return False
        return self._emit("create_devices", "", {"includeSwitches": bool(switches),
                   "includeInputs": bool(inputs), "includeDimmers": bool(dimmers), "shellyId": values.get("shellyId") or ALL_DEVICES})

    def getCredentialDevices(self, filter="", valuesDict=None, typeId="", targetId=0):
        with self.catalog_lock:
            devices = {identity: self._device_label(entry) for identity, entry in self.discovered_devices.items()}
        return [(NO_CHANNEL, "Select a Shelly device")] + sorted(devices.items(), key=lambda item: item[1].casefold())

    def setDevicePassword(self, valuesDict=None, typeId=""):
        values = valuesDict or {}
        with self.catalog_lock:
            valid = values.get("shellyId") in self.discovered_devices
        if not valid or not isinstance(values.get("devicePassword", ""), str):
            self.logger.warning("Select a discovered physical device and enter a local password")
            return False
        return self._emit("credentials", "", (values["shellyId"], values.get("devicePassword", "")))

    @staticmethod
    def _expected_device_type(props):
        component = props.get("component", "")
        if not isinstance(component, str):
            return None
        kind, separator, number = component.partition(":")
        if not separator or not number.isdecimal():
            return None
        return {"switch": "shellySwitch", "light": "shellyDimmer", "input": "shellyInput"}.get(kind)

    def _block_invalid_binding(self, dev):
        expected = self._expected_device_type(dev.pluginProps)
        if expected == dev.deviceTypeId and self._selection_key(dev.pluginProps):
            self.reported_binding_errors.pop(dev.id, None)
            return False
        label = {"shellySwitch": "Shelly Switch", "shellyDimmer": "Shelly Dimmer",
                 "shellyInput": "Shelly Input"}.get(expected)
        message = (f"Channel {dev.pluginProps.get('component')} requires {label}; edit the device type or channel."
                   if label else "Select a supported Shelly channel in the device configuration.")
        dev.setErrorStateOnServer(message)
        if self.reported_binding_errors.get(dev.id) != message:
            self.logger.warning("%s: %s", dev.name, message)
            self.reported_binding_errors[dev.id] = message
        return True

    @staticmethod
    def _selection_key(props):
        identity, component = props.get("shellyId"), props.get("component")
        return f"{identity}/{component}" if identity and component else ""

    @staticmethod
    def _channel_properties(info, address, channel):
        return {"shellyId": info["id"], "component": channel.key,
                "host": address, "model": info.get("model", ""),
                "generation": str(info["gen"]), "SupportsOnState": True,
                "SupportsStatusRequest": True,
                "address": f"{info['id']}/{channel.key}"}

    def _channel_choices(self, typeId):
        kind = {"shellySwitch": "switch", "shellyDimmer": "light", "shellyInput": "input"}.get(typeId)
        with self.catalog_lock:
            return {key: dict(entry) for key, entry in self.catalog.items()
                    if entry["component"].split(":")[0] == kind}

    def _saved_channel(self, devId, typeId):
        if devId and int(devId) in indigo.devices:
            dev = indigo.devices[int(devId)]
            if (dev.deviceTypeId == typeId and self._selection_key(dev.pluginProps)
                    and self._expected_device_type(dev.pluginProps) == typeId):
                return dict(dev.pluginProps)
        return None

    def getShellyChannels(self, filter="", valuesDict=None, typeId="", targetId=0):
        choices = self._channel_choices(typeId)
        saved = self._saved_channel(targetId, typeId)
        if saved:
            key = self._selection_key(saved)
            choices.setdefault(key, {**saved, "online": False,
                                     "label": f"{saved['shellyId']} / {saved['component']}"})
        claimed = {self._selection_key(dev.pluginProps) for dev in self._devices()
                   if dev.id != int(targetId or 0)}
        current = self._selection_key(saved) if saved else None
        options = []
        for key, entry in choices.items():
            if key in claimed and key != current:
                continue
            label = entry["label"]
            if not entry["online"]:
                label += " [offline]"
            options.append((key, label))
        placeholder = ("Select a Shelly channel" if options else "No unassigned Shelly channels available"
                       if choices else "No supported channels discovered yet")
        return [(NO_CHANNEL, placeholder)] + sorted(options, key=lambda item: item[1].casefold())

    def getDeviceConfigUiValues(self, pluginProps, typeId, devId):
        values = indigo.Dict(pluginProps)
        values["devicePassword"] = ""
        values["clearDevicePassword"] = False
        values["credentialEditToken"] = ""
        if self._expected_device_type(pluginProps) == typeId:
            values["selectedChannel"] = self._selection_key(pluginProps) or NO_CHANNEL
        else:
            # Changing the Indigo type must not carry an incompatible old binding
            # through the offline-device fallback in validation.
            values["selectedChannel"] = NO_CHANNEL
            self._fill_channel_selection(values, NO_CHANNEL, None)
        return values, indigo.Dict()

    def shellyChannelSelected(self, valuesDict, typeId, devId):
        selection = valuesDict.get("selectedChannel") or NO_CHANNEL
        valuesDict["selectedChannel"] = selection
        entry = self._channel_choices(typeId).get(selection)
        saved = self._saved_channel(devId, typeId)
        if entry is None and saved and self._selection_key(saved) == selection:
            entry = saved
        return self._fill_channel_selection(valuesDict, selection, entry)

    @staticmethod
    def _fill_channel_selection(valuesDict, selection, entry):
        for key in ("shellyId", "component", "host", "model", "generation", "address"):
            valuesDict[key] = entry.get(key, "") if entry else ""
        if entry:
            valuesDict["address"] = selection
            valuesDict["SupportsOnState"] = True
            valuesDict["SupportsStatusRequest"] = True
        return valuesDict

    def refreshShellyChannels(self, valuesDict, typeId, devId):
        # Bonjour runs continuously; returning refreshes the dynamic list.
        return self.shellyChannelSelected(valuesDict, typeId, devId)

    def validateDeviceConfigUi(self, valuesDict, typeId, devId):
        selection = valuesDict.get("selectedChannel") or NO_CHANNEL
        valuesDict["selectedChannel"] = selection
        choices = self._channel_choices(typeId)
        saved = self._saved_channel(devId, typeId)
        if saved:
            choices.setdefault(self._selection_key(saved), saved)
        message = None
        # A manually filled form can identify an existing binding even without a
        # dropdown selection. Explain that duplicate before the generic selector error.
        candidate = self._selection_key(valuesDict) if selection == NO_CHANNEL else selection
        for dev in self._devices():
            if candidate and dev.id != int(devId or 0) and self._selection_key(dev.pluginProps) == candidate:
                message = (f"This channel is already configured as '{dev.name}' "
                           f"(Indigo ID {dev.id}). Edit that device instead.")
                break
        if message is None and selection not in choices:
            message = "Select a discovered channel matching this Indigo device type."
        if message:
            return False, valuesDict, indigo.Dict({"selectedChannel": message, "showAlertText": message})
        valuesDict = self._fill_channel_selection(valuesDict, selection, choices[selection])
        try:
            token = self.credential_edits.stage(valuesDict["shellyId"], valuesDict.get("devicePassword", ""),
                                                valuesDict.get("clearDevicePassword", False),
                                                valuesDict.get("credentialEditToken", ""))
        except ValueError as exc:
            return False, valuesDict, indigo.Dict({"devicePassword": str(exc), "showAlertText": str(exc)})
        valuesDict["credentialEditToken"] = token
        valuesDict["devicePassword"] = ""
        valuesDict["clearDevicePassword"] = False
        return True, valuesDict

    def closedDeviceConfigUi(self, valuesDict, userCancelled, typeId, devId):
        edit = self.credential_edits.finish(valuesDict.get("credentialEditToken", ""), userCancelled)
        if edit is not None and edit[0] == valuesDict.get("shellyId"):
            self._emit("credentials", "", edit)

    def didDeviceCommPropertyChange(self, origDev, newDev):
        # Password edits reconnect through their saved-credential event. UI-only
        # fields must not restart a shared worker. A learned DHCP address update
        # already has the verified worker that published that property change.
        if origDev.deviceTypeId != newDev.deviceTypeId or any(
                origDev.pluginProps.get(key) != newDev.pluginProps.get(key) for key in ("shellyId", "component")):
            return True
        if origDev.pluginProps.get("host") != newDev.pluginProps.get("host"):
            try:
                host = endpoint(newDev.pluginProps.get("host", ""))
            except (ValueError, AttributeError):
                return True
            return self.connections.owners.get(newDev.pluginProps.get("shellyId")) != host
        return False

    def actionControlDevice(self, action, dev):
        actions = {indigo.kDeviceAction.TurnOn: "on", indigo.kDeviceAction.TurnOff: "off",
                   indigo.kDeviceAction.Toggle: "toggle"}
        if action.deviceAction in actions:
            self._emit("command", "", (dev.id, actions[action.deviceAction]))
        elif action.deviceAction in (indigo.kDeviceAction.SetBrightness, indigo.kDeviceAction.BrightenBy,
                                     indigo.kDeviceAction.DimBy) and dev.deviceTypeId == "shellyDimmer":
            command = {indigo.kDeviceAction.SetBrightness: "brightness",
                       indigo.kDeviceAction.BrightenBy: "brighten", indigo.kDeviceAction.DimBy: "dim"}
            self._emit("command", "", (dev.id, command[action.deviceAction], action.actionValue))

    def actionControlUniversal(self, action, dev):
        if not self._device_communicating(dev):
            return
        if action.deviceAction in (indigo.kUniversalAction.RequestStatus, indigo.kUniversalAction.EnergyUpdate):
            self._emit("refresh", dev.pluginProps["host"])
        else:
            self.logger.warning("%s: this universal action is not supported", dev.name)

    def validatePrefsConfigUi(self, valuesDict):
        if valuesDict.get("logLevel", "INFO") not in ("INFO", "DEBUG"):
            return False, valuesDict, indigo.Dict({"logLevel": "Choose Info or Debug."})
        try:
            for host in valuesDict.get("hosts", "").split(","):
                if host.strip():
                    endpoint(host.strip())
        except ValueError as exc:
            return False, valuesDict, indigo.Dict({"hosts": str(exc)})
        return True, valuesDict

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if not userCancelled:
            self._configure_logging(valuesDict.get("logLevel", "INFO"))
            if any(valuesDict.get(key, original) != original
                   for key, original in self.connection_preferences.items()):
                self.logger.info("Restart the Shelly plugin to apply connection preferences")
