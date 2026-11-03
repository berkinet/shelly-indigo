"""Native button subscriptions and dispatch; invoked by the Indigo adapter."""
import threading
from .bindings import expected_device_type, selection_key
from .catalog import NO_CHANNEL


class ButtonEvents:
    _expected_device_type = staticmethod(expected_device_type)
    _selection_key = staticmethod(selection_key)

    def __init__(self, devices, lookup, trigger_lookup, communicating, identity, execute, logger, dictionary):
        self._devices, self.lookup, self.trigger_lookup = devices, lookup, trigger_lookup
        self._device_communicating, self.identity, self.execute = communicating, identity, execute
        self.logger, self.dictionary = logger, dictionary
        self.button_triggers = {}
        self.trigger_lock = threading.Lock()

    def _button_input(self, device_id):
        try:
            dev = self.lookup(int(device_id))
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
            return False, valuesDict, self.dictionary({"inputDeviceId": message, "showAlertText": message})
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
        identity = self.identity(address)
        for trigger_id, device_id in subscriptions:
            try:
                trigger = self.trigger_lookup(trigger_id)
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
            data = self.dictionary({"source": "com.berkinet.indigoplugin.shelly",
                                "inputDeviceId": device_id, "shellyId": identity,
                                "component": channel.key, "event": "btn_down"})
            if isinstance(event.get("ts"), (int, float)):
                data["ts"] = event["ts"]
            try:
                self.execute(trigger_id, ignoreConditions=False, trigger_data=data)
            except Exception:
                self.logger.exception("Could not execute Button Pressed trigger %s", trigger_id)


