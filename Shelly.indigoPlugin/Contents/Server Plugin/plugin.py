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
        self.workers = {}
        self.info = {}
        self.inventory = {}
        self.catalog = {}
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
            self.logger.debug("Discovered Shelly endpoint %s", address)
            worker.start()

    def deviceStartComm(self, dev):
        address = dev.pluginProps.get("host")
        if address:
            # Refresh static XML states for both device types after upgrades.
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
                    self.logger.debug("Shelly discovery started (Gen2–Gen4 switches and inputs)")
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
        elif kind == "create_devices":
            created = 0
            kinds = set()
            if payload.get("includeSwitches"):
                kinds.add("switch")
            if payload.get("includeInputs"):
                kinds.add("input")
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
                for key, entry in list(self.catalog.items()):
                    if entry["host"] == address:
                        self.catalog[key] = {**entry, "shellyName": payload.get("name") or "",
                                             "label": self._channel_label(payload, entry["component"], address)}
        elif kind == "command":
            dev_id, action = payload
            dev = indigo.devices[dev_id]
            if not dev.enabled or self._block_invalid_binding(dev):
                return
            if dev.deviceTypeId != "shellySwitch":
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
            self.logger.debug("Querying %s (Gen%s)", info["id"], info["gen"])
            self.inventory[address] = Inventory()
            with self.catalog_lock:
                self.catalog = {key: entry for key, entry in self.catalog.items()
                                if entry["shellyId"] != info["id"]}
            self._update(address, status)
        elif kind == "status" and address in self.info:
            self._update(address, payload)
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
                    if self._block_invalid_binding(dev):
                        continue
                    dev.updateStateOnServer("online", False)
                    if dev.deviceTypeId == "shellyInput":
                        dev.updateStateOnServer("stateKnown", False)
                        dev.updateStateOnServer("inputStatus", "Offline")
                    dev.setErrorStateOnServer("Offline")
        elif kind == "command_error":
            self.logger.error("Shelly %s: %s", address, payload)

    def _update(self, address, status):
        self._publish_channels(address, self.inventory[address].merge(status))

    def _publish_channels(self, address, channels, create=False):
        created = 0
        info = self.info[address]
        devices = {}
        for dev in self._devices():
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
                dev = indigo.device.create(
                    protocol=indigo.kProtocol.Plugin,
                    address=f"{info['id']}/{channel.key}",
                    name=f"{self._device_label(info)} {channel.key}",
                    deviceTypeId="shellySwitch" if channel.kind == "switch" else "shellyInput",
                    props=props)
                devices[key] = dev
                created += 1
                self.logger.info("Created %s", dev.name)
            if not dev.enabled or self._block_invalid_binding(dev):
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
            if (dev is None or not dev.enabled or dev.pluginProps.get("shellyId") != identity
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
            devices = {entry["shellyId"]: self._device_label(
                {"id": entry["shellyId"], "name": entry.get("shellyName")})
                for entry in self.catalog.values() if entry["online"]}
        return [(ALL_DEVICES, "All discovered Shelly devices")] + sorted(devices.items(), key=lambda item: item[1].casefold())

    def createDiscoveredDevices(self, valuesDict=None, typeId=""):
        values = valuesDict or {}
        switches = values.get("includeSwitches", True)
        inputs = values.get("includeInputs", False)
        if not switches and not inputs:
            self.logger.warning("Select switches and/or inputs before creating Indigo devices")
            return False
        self._emit("create_devices", "", {"includeSwitches": bool(switches),
                   "includeInputs": bool(inputs), "shellyId": values.get("shellyId") or ALL_DEVICES})
        return True

    @staticmethod
    def _expected_device_type(props):
        component = props.get("component", "")
        if not isinstance(component, str):
            return None
        kind, separator, number = component.partition(":")
        if not separator or not number.isdecimal():
            return None
        return {"switch": "shellySwitch", "input": "shellyInput"}.get(kind)

    def _block_invalid_binding(self, dev):
        expected = self._expected_device_type(dev.pluginProps)
        if expected == dev.deviceTypeId and self._selection_key(dev.pluginProps):
            self.reported_binding_errors.pop(dev.id, None)
            return False
        label = {"shellySwitch": "Shelly Switch", "shellyInput": "Shelly Input"}.get(expected)
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
        kind = {"shellySwitch": "switch", "shellyInput": "input"}.get(typeId)
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
        return [(NO_CHANNEL, placeholder)] + sorted(options, key=lambda item: item[1].casefold())

    def getDeviceConfigUiValues(self, pluginProps, typeId, devId):
        values = indigo.Dict(pluginProps)
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
