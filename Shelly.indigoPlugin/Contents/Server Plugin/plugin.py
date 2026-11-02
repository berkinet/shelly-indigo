"""Indigo adapter. Network workers never call Indigo APIs directly."""
import os
import queue
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Packages"))
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
            worker.start()

    def deviceStartComm(self, dev):
        address = dev.pluginProps.get("host")
        if address:
            dev.setErrorStateOnServer("Connecting")
            self._emit("discover", address, dev.pluginProps.get("shellyId"))

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
                    self.logger.error("Bonjour discovery unavailable (%s); explicit hosts remain active",
                                      type(exc).__name__)
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
            if worker is not None:
                worker.command("Shelly.GetStatus", {})
        elif kind == "ready":
            info, status = payload
            # A rediscovered device may have moved after DHCP renewal.
            for old_address, old_info in list(self.info.items()):
                if old_address != address and old_info["id"] == info["id"]:
                    self.workers.pop(old_address).stop()
                    del self.info[old_address]
            self.info[address] = info
            self.inventory[address] = Inventory()
            self._update(address, status)
        elif kind == "status" and address in self.info:
            self._update(address, payload)
        elif kind == "offline":
            self.logger.warning("Shelly %s unavailable: %s", address, payload)
            for dev in self._devices():
                if dev.enabled and dev.pluginProps.get("host") == address:
                    dev.updateStateOnServer("online", False)
                    dev.setErrorStateOnServer("Offline")
        elif kind == "command_error":
            self.logger.error("Shelly %s: %s", address, payload)

    def _update(self, address, status):
        info = self.info[address]
        devices = {(dev.pluginProps.get("shellyId"), dev.pluginProps.get("component")): dev
                   for dev in self._devices()}
        for channel in self.inventory[address].merge(status):
            key = (info["id"], channel.key)
            dev = devices.get(key)
            if dev is None:
                # Only binary inputs are exposed in this milestone; analog/button-only
                # inputs must not be presented as an always-off binary sensor.
                if channel.kind == "input" and not isinstance(channel.values.get("state"), bool):
                    continue
                props = {"shellyId": info["id"], "component": channel.key,
                         "host": address, "model": info.get("model", ""),
                         "generation": str(info["gen"]), "SupportsOnState": True}
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
                states = {k: v for k, v in states.items() if k == "onOffState"}
            states["online"] = True
            dev.updateStatesOnServer([{"key": k, "value": v} for k, v in states.items()])
            dev.setErrorStateOnServer(None)

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
