"""Physical-device history and log reports. Only the adapter mutates history."""
from copy import deepcopy
from datetime import datetime
import ipaddress
import json
import math
import re
import threading
import time
from urllib.parse import urlsplit

from .discovery import Discovery


def text(value, limit=160):
    if not isinstance(value, str):
        return ""
    return " ".join("".join(c if c.isprintable() else " " for c in value).split())[:limit]


def mac_address(value):
    value = text(value, 32)
    if not re.fullmatch(r"(?:[0-9a-fA-F]{12}|(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})", value):
        return ""
    return value.replace(":", "").lower()


def ip_address(value):
    try:
        return str(ipaddress.ip_address(value))
    except (ValueError, TypeError):
        return ""


def host_address(value):
    if ip_address(value):
        return ip_address(value)
    try:
        return text(urlsplit("//" + str(value)).hostname or "")
    except ValueError:
        return ""


def cloud_rows(response):
    """Keep only report fields; never retain a cloud status/configuration body."""
    data = response.get("data", {}) if isinstance(response, dict) else {}
    statuses = data.get("devices_status") if isinstance(data, dict) else None
    if not isinstance(statuses, dict):
        raise ValueError("Shelly Cloud returned an invalid device inventory")
    rows = []
    for status in statuses.values():
        if not isinstance(status, dict) or not isinstance(status.get("_dev_info"), dict):
            continue
        info = status["_dev_info"]
        identity = text(info.get("id"))
        if not identity:
            continue
        sys = status.get("sys") if isinstance(status.get("sys"), dict) else {}
        wifi = status.get("wifi") if isinstance(status.get("wifi"), dict) else {}
        wifi_sta = status.get("wifi_sta") if isinstance(status.get("wifi_sta"), dict) else {}
        names = info.get("name")
        if isinstance(names, list):
            names = ", ".join(dict.fromkeys(text(name) for name in names if text(name)))
        rows.append({"id": "cloud:" + identity.lower(), "cloud_id": identity.lower(),
                     "mac": mac_address(sys.get("mac") or status.get("mac") or info.get("mac")),
                     "host": ip_address(wifi.get("sta_ip") or wifi_sta.get("ip")),
                     "name": text(names or status.get("name")),
                     "model": text(info.get("code") or info.get("model")),
                     "last_seen": None, "online": False})
    return rows


class DeviceHistory:
    def __init__(self, saved="{}"):
        self.records = {}
        self.online = set()
        self.dirty = False
        try:
            data = json.loads(saved) if isinstance(saved, str) and len(saved) <= 2_000_000 else {}
        except (ValueError, TypeError):
            data = {}
        if not isinstance(data, dict):
            return
        for identity, record in data.items():
            if not text(identity) or not isinstance(record, dict):
                continue
            seen = record.get("last_seen")
            if isinstance(seen, bool) or not isinstance(seen, (int, float)) or not math.isfinite(seen) or seen <= 0:
                continue
            self.records[identity] = {"id": text(identity), "host": text(record.get("host")),
                "mac": mac_address(record.get("mac")), "name": text(record.get("name")),
                "model": text(record.get("model")), "last_seen": seen}

    def observe(self, address, info, online=True):
        identity = text(info.get("id"))
        if not identity:
            return
        record = self.records.setdefault(identity, {"id": identity})
        record.update(host=address, last_seen=time.time())
        for key in ("name", "model"):
            if key in info:
                record[key] = text(info[key])
        if mac_address(info.get("mac")):
            record["mac"] = mac_address(info["mac"])
        if online:
            self.online.add(identity)
        self.dirty = True

    def offline(self, address):
        self.online.difference_update(identity for identity, record in self.records.items()
                                      if record.get("host") == address)

    def saved(self):
        self.dirty = False
        return json.dumps(self.records, ensure_ascii=False, separators=(",", ":"))

    def rows(self, bindings=(), cloud=()):
        records = deepcopy(self.records)
        indigo_names = {}
        for props in bindings:
            identity = text(props.get("shellyId"))
            if identity and identity not in records:
                records[identity] = {"id": identity, "host": text(props.get("host")),
                                     "model": text(props.get("model")), "last_seen": None}
            name = text(props.get("indigoName"))
            if identity and name:
                indigo_names.setdefault(identity, set()).add(name)
        # Match full MACs first. A full cloud Wi-Fi ID can also match an observed
        # MAC or the suffix of a verified local ID, without inventing a MAC value.
        by_mac = {record["mac"]: identity for identity, record in records.items() if record.get("mac")}
        by_suffix = {identity.rsplit("-", 1)[-1].lower(): identity for identity in records
                     if mac_address(identity.rsplit("-", 1)[-1])}
        by_cloud_id = {}
        for item in cloud:
            cloud_id = item.get("cloud_id", "")
            match = (by_mac.get(item.get("mac")) or by_mac.get(cloud_id) or by_suffix.get(cloud_id) or by_cloud_id.get(cloud_id)
                     or (cloud_id if cloud_id in records else None))
            if match:
                record = records[match]
                for key in ("host", "mac", "name", "model"):
                    if not record.get(key) and item.get(key):
                        record[key] = item[key]
            else:
                records[item["id"]] = deepcopy(item)
                match = item["id"]
            if cloud_id:
                by_cloud_id[cloud_id] = match
            if item.get("mac"):
                by_mac[item["mac"]] = match
        return [{**record, "online": identity in self.online,
                 "indigo_names": sorted(indigo_names.get(identity, ()), key=str.casefold)}
                for identity, record in records.items()]


def render_report(rows, scope):
    def sort_key(row):
        host = host_address(row.get("host", ""))
        address = ip_address(host)
        return (0, ipaddress.ip_address(address).version, int(ipaddress.ip_address(address))) if address else (1, host, row["id"])
    table = []
    for row in sorted(rows, key=sort_key):
        mac = mac_address(row.get("mac"))
        mac = ":".join(mac[i:i + 2] for i in range(0, 12, 2)).upper() if mac else "—"
        seen = row.get("last_seen")
        try:
            last = datetime.fromtimestamp(seen).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z") if seen else "Never"
        except (ValueError, OverflowError, OSError):
            last = "Never"
        online = row.get("online", False)
        table.append([host_address(row.get("host", "")) or "—", mac,
                      text(row.get("name")) or row["id"].removeprefix("cloud:"),
                      "; ".join(text(name) for name in row.get("indigo_names", ()) if text(name)) or "—",
                      text(row.get("model")) or "—", "Online" if online else "Offline", "Online" if online else last])
    headers = ["IP Address", "MAC Address", "Name", "Indigo device name", "Model", "Status", "Last seen"]
    widths = [max(len(values[i]) for values in [headers] + table) for i in range(len(headers))]
    lines = [" | ".join(value.ljust(widths[i]) for i, value in enumerate(values)) for values in [headers] + table]
    online = sum(bool(row.get("online")) for row in rows)
    return (f"Shelly device inventory — {scope}\n" + "\n".join(lines)
            + f"\n{len(rows)} physical devices: {online} online, {len(rows) - online} offline. "
              "Status and Last seen reflect local plugin communication; offline addresses are last known.")


class InventoryJob(threading.Thread):
    """One bounded on-demand discovery/auth task; never calls Indigo APIs."""
    def __init__(self, mode, emit, scan_seconds=5):
        super().__init__(name="Shelly-inventory", daemon=True)
        self.mode, self.emit, self.scan_seconds = mode, emit, scan_seconds
        self.stopped = threading.Event()

    def stop(self):
        self.stopped.set()

    def run(self):
        try:
            try:
                discovery = Discovery(lambda address, identity=None: self.emit("bonjour", address, identity)
                                      if not self.stopped.is_set() else None)
            except Exception:
                discovery = None
                self.emit("inventory_notice", "", "Bonjour scan unavailable; using the locally known inventory.")
            if discovery is not None:
                try:
                    if self.stopped.wait(self.scan_seconds):
                        return
                finally:
                    discovery.close()
            cloud = []
            if self.mode == "cloud":
                from .cloud_inventory import fetch_inventory
                cloud = fetch_inventory(self.stopped)
            if not self.stopped.is_set():
                self.emit("inventory_result", "", (self.mode, cloud))
        except Exception as exc:
            if not self.stopped.is_set():
                # Only our deliberately credential-free messages may cross into logs.
                from .cloud_inventory import CloudError
                message = str(exc) if isinstance(exc, CloudError) else "Inventory scan failed; check local discovery/network availability."
                self.emit("inventory_error", "", message)
