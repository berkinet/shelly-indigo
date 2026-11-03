"""Thread-safe discovered catalog snapshots shared with Indigo dialogs."""
from copy import deepcopy
import threading
from .bindings import device_label, channel_label

ALL_DEVICES = "_all_"
NO_CHANNEL = "_none_"


class Catalog:
    def __init__(self):
        self.entries, self.devices = {}, {}
        self.lock = threading.Lock()

    def channels(self, type_id):
        kind = {"shellySwitch": "switch", "shellyDimmer": "light", "shellyInput": "input"}.get(type_id)
        with self.lock:
            return {key: deepcopy(entry) for key, entry in self.entries.items()
                    if entry["component"].split(":")[0] == kind}

    def contains(self, identity):
        with self.lock:
            return identity in self.devices

    def credential_choices(self):
        with self.lock:
            choices = [(identity, device_label(entry)) for identity, entry in self.devices.items()]
        return [(NO_CHANNEL, "Select a Shelly device")] + sorted(choices, key=lambda item: item[1].casefold())

    def device_choices(self):
        with self.lock:
            supported = {entry["shellyId"] for entry in self.entries.values() if entry["online"]}
            choices = [(identity, device_label(entry) +
                        (" [password required — use Set Device Password]" if entry.get("requiresPassword")
                         else " [no supported channels]" if identity not in supported else ""))
                       for identity, entry in self.devices.items() if entry["online"]]
        return [(ALL_DEVICES, "All discovered Shelly devices")] + sorted(choices, key=lambda item: item[1].casefold())

    def retire(self, address):
        with self.lock:
            self.entries = {key: entry for key, entry in self.entries.items() if entry["host"] != address}
            for entry in self.devices.values():
                if entry["host"] == address:
                    entry["online"] = False

    def offline(self, address):
        with self.lock:
            for entry in self.devices.values():
                if entry["host"] == address:
                    entry["online"] = False
            for key, entry in list(self.entries.items()):
                if entry["host"] == address:
                    self.entries[key] = {**entry, "online": False}

    def ready(self, address, info):
        with self.lock:
            for entry in self.devices.values():
                if entry["host"] == address:
                    entry["online"] = False
            self.devices[info["id"]] = {"id": info["id"], "name": info.get("name"), "host": address,
                                        "online": True, "requiresPassword": False}
            self.entries = {key: entry for key, entry in self.entries.items()
                            if entry["shellyId"] != info["id"] and entry["host"] != address}

    def rename(self, address, info):
        with self.lock:
            if info["id"] in self.devices:
                self.devices[info["id"]]["name"] = info.get("name")
            for key, entry in list(self.entries.items()):
                if entry["host"] == address:
                    self.entries[key] = {**entry, "shellyName": info.get("name") or "",
                                         "label": channel_label(info, entry["component"], address)}

    def remove_channels(self, identity, removed):
        with self.lock:
            self.entries = {key: entry for key, entry in self.entries.items()
                            if not (entry["shellyId"] == identity and entry["component"] in removed)}

    def publish(self, props, info, address, supported):
        selection = f"{props['shellyId']}/{props['component']}"
        with self.lock:
            if supported:
                self.entries[selection] = {**props, "online": True, "shellyName": info.get("name") or "",
                                           "label": channel_label(info, props["component"], address)}
            else:
                self.entries.pop(selection, None)

    def forget(self, address):
        with self.lock:
            self.devices = {identity: entry for identity, entry in self.devices.items() if entry["host"] != address}

    def get_device(self, identity):
        with self.lock:
            return deepcopy(self.devices.get(identity, {}))

    def online_keys(self):
        with self.lock:
            return {key for key, entry in self.entries.items() if entry["online"]}

    def pause_snapshot(self, address):
        with self.lock:
            return {key: {**entry, "online": False} for key, entry in self.entries.items() if entry["host"] == address}

    def restore_paused(self, address, entries):
        with self.lock:
            self.entries.update(entries)
            for entry in self.devices.values():
                if entry["host"] == address:
                    entry["paused"] = True

    def cache_paused(self, address, info):
        with self.lock:
            old = self.devices.get(info["id"], {})
            name = info.get("name") or old.get("name")
            self.devices[info["id"]] = {"id": info["id"], "name": name, "host": address,
                                        "online": False, "requiresPassword": False, "paused": True}
            for key, entry in list(self.entries.items()):
                if entry["shellyId"] == info["id"]:
                    self.entries[key] = {**entry, "host": address, "online": False,
                                         "label": channel_label({"id": info["id"], "name": name}, entry["component"], address)}

    def identified(self, address, info):
        with self.lock:
            old = self.devices.get(info["id"], {})
            if not old.get("online") or old.get("host") == address:
                self.devices[info["id"]] = {"id": info["id"], "name": info.get("name"), "host": address,
                                            "online": True, "requiresPassword": bool(info.get("auth_en"))}

    def auth_required(self, address, identity):
        with self.lock:
            entry = self.devices.get(identity)
            if entry is not None and entry["host"] == address:
                entry.update(online=True, requiresPassword=True)
