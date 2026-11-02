"""Stable channel identities and translation of partial Shelly status objects."""
from dataclasses import dataclass, field
import re
from urllib.parse import urlsplit


def endpoint(value):
    """Accept a host or host:port, never credentials, paths, or remote URLs."""
    parsed = urlsplit("//" + value.strip())
    if (not parsed.hostname or parsed.username or parsed.password or parsed.path
            or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
        raise ValueError("Enter a hostname or IP address, optionally followed by :port")
    port = parsed.port or 80
    if not 1 <= port <= 65535:
        raise ValueError("Invalid port")
    host = parsed.hostname
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


@dataclass
class Channel:
    key: str
    values: dict = field(default_factory=dict)

    @property
    def kind(self):
        return self.key.split(":")[0]

    @property
    def number(self):
        return int(self.key.split(":")[1])

    def states(self):
        result = {}
        source = self.values
        flag = "output" if self.kind == "switch" else "state"
        if isinstance(source.get(flag), bool):
            result["onOffState"] = source[flag]
        for raw, state in (("apower", "powerWatts"), ("voltage", "voltage"),
                           ("current", "currentAmps")):
            value = source.get(raw)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                result[state] = float(value)
        energy_data = source.get("aenergy")
        energy = energy_data.get("total") if isinstance(energy_data, dict) else None
        if isinstance(energy, (int, float)):
            result["energyWh"] = float(energy)
        return result


class Inventory:
    def __init__(self):
        self.channels = {}

    def merge(self, status):
        """Notifications are deltas: absent fields must never reset known state."""
        changed = []
        for key, values in status.items():
            if not re.fullmatch(r"(?:switch|input):\d+", key) or not isinstance(values, dict):
                continue
            channel = self.channels.setdefault(key, Channel(key))
            for name, value in values.items():
                if isinstance(value, dict) and isinstance(channel.values.get(name), dict):
                    channel.values[name] = {**channel.values[name], **value}
                else:
                    channel.values[name] = value
            changed.append(channel)
        return changed
