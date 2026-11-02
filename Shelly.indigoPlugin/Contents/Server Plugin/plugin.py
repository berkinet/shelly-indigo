"""Indigo adapter. Network workers never call Indigo APIs directly."""
from pathlib import Path
import importlib
import queue
import sys
import threading
import time

# Indigo executes this entry point without __file__. Imported helper modules
# have normal module metadata, so anchor bundled dependencies to our package.
import shelly
sys.path.insert(0, str(Path(shelly.__file__).resolve().parents[2] / "Packages"))
import indigo
from shelly.discovery import Discovery
from shelly.model import Inventory, endpoint
from shelly.worker import Worker


class Plugin(indigo.PluginBase):
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        super().__init__(pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
        self.events = queue.Queue(maxsize=1024)
        self.closing = threading.Event()
        self.workers = {}
        self.info = {}
        self.inventory = {}
        self.catalog = {}
        self.catalog_lock = threading.Lock()

    def startup(self):
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
        while not self.closing.is_set():
            try:
                self.events.put((kind, address, payload), timeout=0.5)
                return
            except queue.Full:
                continue

    def _devices(self):
        return indigo.devices.iter("self")

    def _connect(self, address, expected_id=None):
        address = endpoint(address)
        if address not in self.workers:
            worker = Worker(address, self._emit, self.pluginPrefs.get("password", ""), expected_id)
            self.workers[address] = worker
            self.logger.info("Discovered Shelly endpoint %s; connecting", address)
            worker.start()

    def deviceStartComm(self, dev):
        address = dev.pluginProps.get("host")
        if address:
            if dev.deviceTypeId == "shellyInput":
                dev.stateListOrDisplayStateIdChanged()
            dev.setErrorStateOnServer("Connecting")
            self._emit("discover", address, dev.pluginProps.get("shellyId"))
            self._emit("refresh", address)

    def runConcurrentThread(self):
        discovery = None
        try:
            for address in self.pluginPrefs.get("hosts", "").split(","):
                if address.strip():
                    self._connect(address.strip())
            if self.pluginPrefs.get("discovery", True):
                try:
                    discovery = Discovery(lambda address: self._emit("discover", address))
                    self.logger.info("Shelly discovery started (Gen2–Gen4 switches and inputs)")
                except Exception as exc:
                    self.logger.error("Bonjour discovery unavailable: %s; explicit hosts remain active", exc)
            while True:
                self.sleep(0.05)
                for _ in range(100):
                    try:
                        event = self.events.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        self._handle(*event)
                    except Exception:
                        self.logger.exception("Could not process Shelly device update")
        except self.StopThread:
            pass
        finally:
            self.closing.set()
            if discovery is not None:
                discovery.close()
            for worker in self.workers.values():
                worker.stop()
            deadline = time.monotonic() + 10
            for worker in self.workers.values():
                worker.join(max(0, deadline - time.monotonic()))

    def _handle(self, kind, address, payload):
        if kind == "discover":
            self._connect(address, payload)
        elif kind == "command":
            dev_id, action = payload
            dev = indigo.devices[dev_id]
            if not dev.enabled or dev.deviceTypeId != "shellySwitch":
                return
            host = dev.pluginProps["host"]
            worker = self.workers.get(host)
            if worker is None or self.info.get(host, {}).get("id") != dev.pluginProps["shellyId"]:
                raise ConnectionError("Shelly is offline or its address now belongs to another device")
            method = "Switch.Toggle" if action == "toggle" else "Switch.Set"
            params = {"id": int(dev.pluginProps["component"].split(":")[1])}
            if action != "toggle":
                params["on"] = action == "on"
            worker.command(method, params)
        elif kind == "refresh":
            worker = self.workers.get(address)
            if worker is not None and worker.online:
                worker.command("Shelly.GetStatus", {})
        elif kind == "ready":
            info, status = payload
            # A rediscovered device may have moved after DHCP renewal.
            for old_address, old_info in list(self.info.items()):
                if old_address != address and old_info["id"] == info["id"]:
                    self.workers.pop(old_address).stop()
                    del self.info[old_address]
            self.info[address] = info
            self.logger.info("Connected to %s (Gen%s)", info["id"], info["gen"])
            self.inventory[address] = Inventory()
            with self.catalog_lock:
                self.catalog = {key: entry for key, entry in self.catalog.items()
                                if entry["shellyId"] != info["id"]}
            self._update(address, status)
        elif kind == "status" and address in self.info:
            self._update(address, payload)
        elif kind == "event" and address in self.info:
            for event in payload.get("events", []):
                channel = self.inventory[address].apply_input_event(event)
                if channel is not None:
                    # Publish each transition separately, even when a frame contains
                    # both a quick press and release for the same component.
                    self._publish_channels(address, [channel])
        elif kind == "offline":
            if address in self.inventory:
                self.inventory[address].invalidate_buttons()
            with self.catalog_lock:
                for key, entry in list(self.catalog.items()):
                    if entry["host"] == address:
                        self.catalog[key] = {**entry, "online": False}
            self.logger.warning("Shelly %s unavailable: %s", address, payload)
            for dev in self._devices():
                if dev.enabled and dev.pluginProps.get("host") == address:
                    dev.updateStateOnServer("online", False)
                    if dev.deviceTypeId == "shellyInput":
                        dev.updateStateOnServer("stateKnown", False)
                    dev.setErrorStateOnServer("Offline")
        elif kind == "command_error":
            self.logger.error("Shelly %s: %s", address, payload)

    def _update(self, address, status):
        self._publish_channels(address, self.inventory[address].merge(status))

    def _publish_channels(self, address, channels):
        info = self.info[address]
        devices = {(dev.pluginProps.get("shellyId"), dev.pluginProps.get("component")): dev
                   for dev in self._devices()}
        for channel in channels:
            key = (info["id"], channel.key)
            props = self._channel_properties(info, address, channel)
            selection = self._selection_key(props)
            supported = channel.supported
            with self.catalog_lock:
                if supported:
                    self.catalog[selection] = {
                        **props, "online": True,
                        "label": f"{info.get('name') or info['id']} / {channel.key} ({address})"}
                else:
                    self.catalog.pop(selection, None)
            dev = devices.get(key)
            if dev is None:
                if not supported or not self.pluginPrefs.get("autoCreate", True):
                    continue
                dev = indigo.device.create(
                    protocol=indigo.kProtocol.Plugin,
                    address=f"{info['id']}/{channel.key}",
                    name=f"{info.get('name') or info['id']} {channel.key}",
                    deviceTypeId="shellySwitch" if channel.kind == "switch" else "shellyInput",
                    props=props)
                devices[key] = dev
                self.logger.info("Created %s", dev.name)
            if not dev.enabled:
                continue
            if dev.pluginProps.get("host") != address:
                props = dict(dev.pluginProps)
                props["host"] = address
                dev.replacePluginPropsOnServer(props)
            states = channel.states()
            # Inputs do not expose metering fields in Devices.xml.
            if channel.kind == "input":
                states = {k: v for k, v in states.items() if k in ("onOffState", "stateKnown", "inputMode")}
            states["online"] = True
            dev.updateStatesOnServer([{"key": k, "value": v} for k, v in states.items()])
            unknown = channel.kind == "input" and not states.get("stateKnown", False)
            dev.setErrorStateOnServer("Waiting for input press/release" if unknown else None)

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
        kind = {"shellySwitch": "switch", "shellyInput": "input"}.get(typeId)
        with self.catalog_lock:
            return {key: dict(entry) for key, entry in self.catalog.items()
                    if entry["component"].split(":")[0] == kind}

    def _saved_channel(self, devId, typeId):
        if devId and int(devId) in indigo.devices:
            dev = indigo.devices[int(devId)]
            if dev.deviceTypeId == typeId and self._selection_key(dev.pluginProps):
                return dict(dev.pluginProps)
        return None

    def getShellyChannels(self, filter="", valuesDict=None, typeId="", targetId=0):
        choices = self._channel_choices(typeId)
        saved = self._saved_channel(targetId, typeId)
        if saved:
            key = self._selection_key(saved)
            choices.setdefault(key, {**saved, "online": False,
                                     "label": f"{saved['shellyId']} / {saved['component']}"})
        claimed = {self._selection_key(dev.pluginProps): dev for dev in self._devices()}
        options = []
        for key, entry in choices.items():
            label = entry["label"]
            owner = claimed.get(key)
            if owner is not None and owner.id != int(targetId or 0):
                label += f" [already configured: {owner.name}]"
            if not entry["online"]:
                label += " [offline]"
            options.append((key, label))
        placeholder = "Select a Shelly channel" if options else "No supported channels discovered yet"
        return [("", placeholder)] + sorted(options, key=lambda item: item[1].casefold())

    def getDeviceConfigUiValues(self, pluginProps, typeId, devId):
        values = indigo.Dict(pluginProps)
        values["selectedChannel"] = self._selection_key(pluginProps)
        return values, indigo.Dict()

    def shellyChannelSelected(self, valuesDict, typeId, devId):
        selection = valuesDict.get("selectedChannel", "")
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
        selection = valuesDict.get("selectedChannel", "")
        choices = self._channel_choices(typeId)
        saved = self._saved_channel(devId, typeId)
        if saved:
            choices.setdefault(self._selection_key(saved), saved)
        message = None
        if not selection or selection not in choices:
            message = "Select a discovered channel matching this Indigo device type."
        else:
            for dev in self._devices():
                if dev.id != int(devId or 0) and self._selection_key(dev.pluginProps) == selection:
                    message = f"This channel is already configured as '{dev.name}'. Edit that device instead."
                    break
        if message:
            return False, valuesDict, indigo.Dict({"selectedChannel": message, "showAlertText": message})
        valuesDict = self._fill_channel_selection(valuesDict, selection, choices[selection])
        return True, valuesDict

    def actionControlDevice(self, action, dev):
        actions = {indigo.kDeviceAction.TurnOn: "on", indigo.kDeviceAction.TurnOff: "off",
                   indigo.kDeviceAction.Toggle: "toggle"}
        if action.deviceAction in actions:
            self._emit("command", "", (dev.id, actions[action.deviceAction]))
        elif action.deviceAction == indigo.kDeviceAction.RequestStatus:
            self._emit("refresh", dev.pluginProps["host"])

    def validatePrefsConfigUi(self, valuesDict):
        try:
            for host in valuesDict.get("hosts", "").split(","):
                if host.strip():
                    endpoint(host.strip())
        except ValueError as exc:
            return False, valuesDict, indigo.Dict({"hosts": str(exc)})
        return True, valuesDict

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if not userCancelled:
            self.logger.info("Restart the Shelly plugin to apply connection preferences")
