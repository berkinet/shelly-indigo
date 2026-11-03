"""Device selection and staged credential edits, separate from network lifecycle."""
from .bindings import expected_device_type, selection_key
from .catalog import NO_CHANNEL
from .model import endpoint


class DeviceDialogs:
    _expected_device_type = staticmethod(expected_device_type)
    _selection_key = staticmethod(selection_key)

    def __init__(self, catalog, devices, lookup, edits, owners, emit, logger, dictionary):
        self.catalog_service, self._devices, self.lookup = catalog, devices, lookup
        self.credential_edits, self.owners = edits, owners
        self._emit, self.logger, self.dictionary = emit, logger, dictionary

    def _channel_choices(self, typeId):
        return self.catalog_service.channels(typeId)

    def _saved_channel(self, devId, typeId):
        try:
            dev = self.lookup(int(devId))
        except (TypeError, ValueError, KeyError):
            return None
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
        values = self.dictionary(pluginProps)
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
        return values, self.dictionary()

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
            return False, valuesDict, self.dictionary({"selectedChannel": message, "showAlertText": message})
        valuesDict = self._fill_channel_selection(valuesDict, selection, choices[selection])
        try:
            token = self.credential_edits.stage(valuesDict["shellyId"], valuesDict.get("devicePassword", ""),
                                                valuesDict.get("clearDevicePassword", False),
                                                valuesDict.get("credentialEditToken", ""))
        except ValueError as exc:
            return False, valuesDict, self.dictionary({"devicePassword": str(exc), "showAlertText": str(exc)})
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
            return self.owners.get(newDev.pluginProps.get("shellyId")) != host
        return False

    def getCredentialDevices(self, filter="", valuesDict=None, typeId="", targetId=0):
        return self.catalog_service.credential_choices()

    def setDevicePassword(self, valuesDict=None, typeId=""):
        values = valuesDict or {}
        valid = self.catalog_service.contains(values.get("shellyId"))
        if not valid or not isinstance(values.get("devicePassword", ""), str):
            self.logger.warning("Select a discovered physical device and enter a local password")
            return False
        return self._emit("credentials", "", (values["shellyId"], values.get("devicePassword", "")))


