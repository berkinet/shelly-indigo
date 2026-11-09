"""Indigo adapter. Network workers never call Indigo APIs directly."""
from pathlib import Path
import importlib
import logging
import queue
import sys
import threading
import time

# Indigo executes this entry point without __file__. Imported helper modules
# have normal module metadata, so anchor bundled dependencies to our package.
import shelly
sys.path.insert(0, str(Path(shelly.__file__).resolve().parents[2] / "Packages"))
import indigo
from shelly.commands import translate
from shelly.bindings import expected_device_type, selection_key, channel_properties, device_label, channel_label
from shelly.catalog import Catalog, ALL_DEVICES
from shelly.dialogs import DeviceDialogs
from shelly.buttons import ButtonEvents
from shelly.connections import CommunicationPolicy, Connections
from shelly.credentials import CredentialEdits, Credentials
from shelly.diagnostics import Diagnostics
from shelly.discovery import Discovery
from shelly.model import Inventory, endpoint
from shelly.worker import Worker

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
        self.catalog_service = Catalog()
        self.diagnostics = Diagnostics(self.logger)
        self.component_reports = self.diagnostics.reports
        self.reported_channel_warnings = self.diagnostics.warnings
        self._next_maintenance = 0
        self.reported_binding_errors = {}
        self.dialogs = DeviceDialogs(self.catalog_service, self._devices, lambda key: indigo.devices[key],
                                     self.credential_edits, self.connections.owners, self._emit, self.logger, indigo.Dict)
        self.button_events = ButtonEvents(self._devices, lambda key: indigo.devices[key],
                                          lambda key: indigo.triggers[key], self._device_communicating,
                                          lambda host: self.info[host]["id"], indigo.trigger.execute,
                                          self.logger, indigo.Dict)
        self.connection_preferences = {key: pluginPrefs.get(key, default) for key, default in
                                       (("discovery", True), ("hosts", ""), ("password", ""))}

    @property
    def catalog(self):
        return self.catalog_service.entries

    @property
    def discovered_devices(self):
        return self.catalog_service.devices

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
        enabled, channels = {}, {}
        for dev in bindings:
            identity = dev.pluginProps["shellyId"]
            active = self._device_communicating(dev)
            enabled[identity] = enabled.get(identity, False) or active
            key = (identity, dev.pluginProps["component"])
            channels[key] = channels.get(key, False) or active
        self.communication.replace(enabled, channels)
        return bindings

    def _communication_bindings(self, address, identity=None, bindings=None):
        bindings = self._sync_communication() if bindings is None else bindings
        return [dev for dev in bindings if (dev.pluginProps["shellyId"] == identity if identity else
                bool(dev.pluginProps.get("host")) and endpoint(dev.pluginProps["host"]) == address)]

    def _communication_allowed(self, address, identity=None, bindings=None):
        matches = self._communication_bindings(address, identity, bindings)
        return not matches or any(self._device_communicating(dev) for dev in matches)

    def _connection_log_context(self, address, identity=None, info=None):
        info = info or self.info.get(address) or self.catalog_service.get_device(identity)
        identity = info.get("id") or identity
        label = f"{self._device_label({**info, 'id': identity})} at {address}" if identity else f"at {address}"
        matches = self._communication_bindings(address, identity)
        active = [dev for dev in matches if self._device_communicating(dev)]
        if active:
            context = "active Indigo devices: " + ", ".join(f"{dev.name} (Indigo {dev.id})" for dev in active)
        elif matches:
            context = "assigned Indigo devices (communication disabled): " + ", ".join(
                f"{dev.name} (Indigo {dev.id})" for dev in matches)
        else:
            context = "no Indigo devices assigned"
        return label, context

    @staticmethod
    def _outage_key(address: str, identity: str | None = None) -> tuple[str, str]:
        # A known physical unit can recover at a different endpoint.
        return ("device", identity) if identity else ("endpoint", address)

    def _clear_outage(self, address, identity=None):
        self.outages.pop(self._outage_key(address, identity), None)
        self.outages.pop(self._outage_key(address), None)

    def _pause(self, address):
        # Preserve offline selector entries so another channel can be explicitly
        # added later, while removing the worker and fencing all of its messages.
        identity = self.info.get(address, {}).get("id")
        if identity is None and address in self.workers:
            identity = self.workers[address].expected_id
        cached = self.catalog_service.pause_snapshot(address)
        self._retire(address)
        self.pending_probes.pop(address, None)
        self._clear_outage(address, identity)
        self.catalog_service.restore_paused(address, cached)
        self.logger.debug("Shelly communication stopped at %s: no enabled Indigo channels", address)

    def _cache_paused_identity(self, address, info):
        self.catalog_service.cache_paused(address, info)

    def _suspend_identity(self, address, info):
        self._pause(address)
        self._cache_paused_identity(address, info)

    def _connect(self, address, expected_id=None, exclude_id=None, hint_id=None):
        address = endpoint(address)
        previous = self.connections.vacated.get(address)
        if previous is not None and expected_id is None and exclude_id is None:
            owner = self.connections.owners.get(previous.identity)
            worker = self.workers.get(owner)
            if hint_id == previous.identity:
                if worker is not None and worker.online:
                    # Remember a fresh hint in case the current connection later fails.
                    previous.rediscovered = True
                    self.logger.debug("Ignoring former Shelly endpoint %s for %s; active at %s",
                                      address, previous.identity, owner)
                    return
                expected_id = previous.identity
            else:
                exclude_id = previous.identity
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
                        permitted=self.communication.permits, permitted_command=self.communication.permits_command)
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
        self.catalog_service.retire(address)
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
                worker = self.workers[address]
                if worker.exclude_id is not None:
                    self._handle("probe_failed", address, "Event queue overloaded; vacated-address probe retired", generation)
                    continue
                expected = worker.expected_id
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
                self._clear_outage(address, identity)
                self.catalog_service.forget(address)

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

    def _claim_endpoint(self, address, info):
        """Claim a verified address and report one physical-device transition."""
        previous = self.connections.last_addresses.get(info["id"])
        if previous is None:
            saved = {endpoint(dev.pluginProps["host"]) for dev in self._communication_bindings("", info["id"])
                     if dev.pluginProps.get("host")}
            previous = next(iter(saved)) if len(saved) == 1 else None
        recovering = (self._outage_key(address, info["id"]) in self.outages
                      or self._outage_key(address) in self.outages)
        obsolete = set(self.connections.claim(info["id"], address))
        obsolete.update(host for host, item in self.info.items() if host != address and item["id"] == info["id"])
        if previous and previous != address:
            occupant = self.info.get(previous, {}).get("id")
            if occupant is None and previous in self.workers:
                occupant = self.workers[previous].expected_id
            if occupant is None or occupant == info["id"]:
                obsolete.add(previous)
        for old_address in obsolete:
            self._retire(old_address)
            self._clear_outage(old_address)
            self.connections.vacate(old_address, info["id"])
            # Catch an address swap even if Bonjour arrived before old-worker retirement.
            self.pending_probes[old_address] = info["id"]
        if previous and previous != address:
            _, context = self._connection_log_context(address, info=info)
            transition = "recovered after address change" if recovering else "address changed"
            self.logger.info("Shelly %s %s from %s to %s; %s",
                             self._device_label(info), transition, previous, address, context)
        elif recovering:
            label, context = self._connection_log_context(address, info=info)
            self.logger.info("Shelly %s recovered; %s", label, context)
        self._clear_outage(address, info["id"])

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
            entry = self.catalog_service.get_device(identity)
            host = self.connections.owners.get(identity) or entry.get("host") or address
            self._connect(host, identity)
            self._handle("refresh", host, None)
        elif kind == "suspended":
            self._suspend_identity(address, payload)
        elif kind == "credentials":
            identity, password = payload
            self.pluginPrefs["devicePasswords"] = self.credentials.set(identity, password)
            entry = self.catalog_service.get_device(identity)
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
                online = self.catalog_service.online_keys()
                channels = [channel for channel in inventory.channels.values()
                            if channel.kind in kinds and f"{info['id']}/{channel.key}" in online]
                created += self._publish_channels(host, channels, create=True)
            self.logger.info("Create Indigo devices completed: %s new devices; existing bindings preserved", created)
        elif kind == "identity" and address in self.info:
            self.info[address] = dict(payload)
            self.catalog_service.rename(address, payload)
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
            self.catalog_service.identified(address, payload)
        elif kind == "alias":
            # This address still serves a unit already moved to another endpoint.
            self.logger.debug("Retired alternate endpoint %s for %s", address, payload["id"])
            self._retire(address)
        elif kind == "probe_failed":
            # A vacated address is checked once for reuse, not monitored as a new outage.
            self.logger.debug("Vacated Shelly endpoint %s did not answer; retiring probe: %s", address, payload)
            self._retire(address)
            self.pending_probes.pop(address, None)
            self._clear_outage(address)
        elif kind == "mismatch":
            self._handle("offline", address, "Address now belongs to another Shelly; re-querying its public identity")
            self._retire(address)
            self._connect(address, payload["id"])
        elif kind == "auth_required":
            self._handle("offline", address, payload["reason"] + "; use Set Device Password in the Shelly menu")
            self.catalog_service.auth_required(address, payload.get("id"))
        elif kind == "ready":
            info, status = payload
            if not self._communication_allowed(address, info["id"]):
                self._suspend_identity(address, info)
                return
            self._claim_endpoint(address, info)
            self.info[address] = info
            self.logger.debug("Querying %s (Gen%s)", info["id"], info["gen"])
            self.inventory[address] = Inventory()
            self.component_reports[address] = (set(), None)
            self.catalog_service.ready(address, info)
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
            self.catalog_service.offline(address)
            label, context = self._connection_log_context(address, identity)
            outage_key = self._outage_key(address, identity)
            if self.outages.get(outage_key) != payload:
                self.logger.warning("Shelly %s unavailable; %s: %s", label, context, payload)
                self.outages[outage_key] = payload
            else:
                self.logger.debug("Shelly %s still unavailable; %s: %s", label, context, payload)
            for dev in self._devices():
                if self._device_communicating(dev) and dev.pluginProps.get("host") == address:
                    if self._block_invalid_binding(dev):
                        continue
                    dev.updateStateOnServer("online", False)
                    if dev.deviceTypeId == "shellyInput":
                        dev.updateStateOnServer("stateKnown", False)
                        dev.updateStateOnServer("inputStatus", "Offline")
                    dev.setErrorStateOnServer("Offline")
            if identity and self.connections.owners.get(identity) == address:
                for candidate in self.connections.return_candidates(identity):
                    self._connect(candidate, expected_id=identity)
        elif kind == "command_cancelled":
            self.logger.debug("Shelly %s command %s cancelled: channel communication disabled", address, payload)
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
            self.catalog_service.remove_channels(identity, removed)
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
            supported = channel.supported
            self.catalog_service.publish(props, info, address, supported)
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
        return self.button_events._button_input(device_id)


    def getButtonInputs(self, filter='', valuesDict=None, typeId='', targetId=0):
        return self.button_events.getButtonInputs(filter, valuesDict, typeId, targetId)


    def validateEventConfigUi(self, valuesDict, typeId, eventId):
        return self.button_events.validateEventConfigUi(valuesDict, typeId, eventId)


    def triggerStartProcessing(self, trigger):
        return self.button_events.triggerStartProcessing(trigger)


    def triggerStopProcessing(self, trigger):
        return self.button_events.triggerStopProcessing(trigger)


    def _fire_button_pressed(self, address, channel, event):
        return self.button_events._fire_button_pressed(address, channel, event)


    _device_label = staticmethod(device_label)


    _channel_label = staticmethod(channel_label)


    def getDiscoveredShellyDevices(self, filter="", valuesDict=None, typeId="", targetId=0):
        options = self.catalog_service.device_choices()
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

    def getCredentialDevices(self, filter='', valuesDict=None, typeId='', targetId=0):
        return self.dialogs.getCredentialDevices(filter, valuesDict, typeId, targetId)


    def setDevicePassword(self, valuesDict=None, typeId=''):
        return self.dialogs.setDevicePassword(valuesDict, typeId)


    _expected_device_type = staticmethod(expected_device_type)


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

    _selection_key = staticmethod(selection_key)


    _channel_properties = staticmethod(channel_properties)


    def _channel_choices(self, typeId):
        return self.dialogs._channel_choices(typeId)


    def _saved_channel(self, devId, typeId):
        return self.dialogs._saved_channel(devId, typeId)


    def getShellyChannels(self, filter='', valuesDict=None, typeId='', targetId=0):
        return self.dialogs.getShellyChannels(filter, valuesDict, typeId, targetId)


    def getDeviceConfigUiValues(self, pluginProps, typeId, devId):
        return self.dialogs.getDeviceConfigUiValues(pluginProps, typeId, devId)


    def shellyChannelSelected(self, valuesDict, typeId, devId):
        return self.dialogs.shellyChannelSelected(valuesDict, typeId, devId)


    @staticmethod
    def _fill_channel_selection(valuesDict, selection, entry):
        return DeviceDialogs._fill_channel_selection(valuesDict, selection, entry)


    def refreshShellyChannels(self, valuesDict, typeId, devId):
        return self.dialogs.refreshShellyChannels(valuesDict, typeId, devId)


    def validateDeviceConfigUi(self, valuesDict, typeId, devId):
        return self.dialogs.validateDeviceConfigUi(valuesDict, typeId, devId)


    def closedDeviceConfigUi(self, valuesDict, userCancelled, typeId, devId):
        return self.dialogs.closedDeviceConfigUi(valuesDict, userCancelled, typeId, devId)


    def didDeviceCommPropertyChange(self, origDev, newDev):
        return self.dialogs.didDeviceCommPropertyChange(origDev, newDev)


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
