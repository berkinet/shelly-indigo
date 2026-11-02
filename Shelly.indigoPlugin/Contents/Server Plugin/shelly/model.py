"""Stable channel identities and translation of partial Shelly status objects."""
from collections import deque
from dataclasses import dataclass, field
import math
import re
from urllib.parse import urlsplit


def endpoint(value):
    """Accept a host or host:port, never credentials, paths, or remote URLs."""
    parsed = urlsplit("//" + value.strip())
    if (not parsed.hostname or parsed.username or parsed.password or parsed.path
            or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
        raise ValueError("Enter a hostname or IP address, optionally followed by :port")
    port = 80 if parsed.port is None else parsed.port
    if not 1 <= port <= 65535:
        raise ValueError("Invalid port")
    host = parsed.hostname
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


@dataclass
class Channel:
    key: str
    values: dict = field(default_factory=dict)
    button_state: bool | None = None
    event_timestamp: float | None = None
    seen_events: deque = field(default_factory=lambda: deque(maxlen=64), repr=False)

    @property
    def kind(self):
        return self.key.split(":")[0]

    @property
    def number(self):
        return int(self.key.split(":")[1])

    @property
    def supported(self):
        return self.kind in ("switch", "light") or (
            self.values.get("_enabled") is not False and (
                isinstance(self.values.get("state"), bool) or self.values.get("_inputType") == "button"))

    def states(self):
        result = {}
        source = self.values
        if self.kind == "input":
            state = source.get("state")
            button = source.get("_inputType") == "button" and not isinstance(state, bool)
            state = self.button_state if button else state
            if source.get("_enabled") is False:
                state = None
            result["inputMode"] = "button" if button else "switch" if isinstance(state, bool) else "unknown"
            result["stateKnown"] = isinstance(state, bool)
            result["inputStatus"] = "On" if state is True else "Off" if state is False else "Unknown"
            if result["stateKnown"]:
                result["onOffState"] = state
        elif isinstance(source.get("output"), bool):
            result["onOffState"] = source["output"]
        if self.kind == "light":
            brightness = source.get("brightness")
            if source.get("output") is False:
                result["brightnessLevel"] = 0
            elif (source.get("output") is True and isinstance(brightness, (int, float))
                  and not isinstance(brightness, bool) and 0 <= brightness <= 100):
                result["brightnessLevel"] = round(brightness)
        for raw, state in (("apower", "powerWatts"), ("voltage", "voltage"),
                           ("current", "currentAmps")):
            value = source.get(raw)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                result[state] = float(value)
        energy_data = source.get("aenergy")
        energy = energy_data.get("total") if isinstance(energy_data, dict) else None
        if isinstance(energy, (int, float)) and not isinstance(energy, bool) and math.isfinite(energy):
            result["energyWh"] = float(energy)
        return result


class Inventory:
    def __init__(self):
        self.channels = {}

    def merge(self, status):
        """Notifications are deltas: absent fields must never reset known state."""
        changed = []
        for key, values in status.items():
            if not re.fullmatch(r"(?:switch|light|input):\d+", key) or not isinstance(values, dict):
                continue
            channel = self.channels.setdefault(key, Channel(key))
            if (isinstance(values.get("state"), bool) or values.get("_enabled") is False
                    or ("_inputType" in values and values["_inputType"] != channel.values.get("_inputType"))):
                channel.button_state = None
                channel.event_timestamp = None
                channel.seen_events.clear()
            for name, value in values.items():
                if isinstance(value, dict) and isinstance(channel.values.get(name), dict):
                    channel.values[name] = {**channel.values[name], **value}
                else:
                    channel.values[name] = value
            if isinstance(values.get("_buttonState"), bool):
                channel.button_state = values["_buttonState"]
            changed.append(channel)
        return changed

    def replace(self, status):
        """A complete snapshot replaces raw fields; input position survives null RPC state."""
        present = {key for key, values in status.items()
                   if re.fullmatch(r"(?:switch|light|input):\d+", key) and isinstance(values, dict)}
        removed = set(self.channels) - present
        for key in removed:
            del self.channels[key]
        for key in present:
            if key in self.channels:
                channel = self.channels[key]
                channel.values = {k: v for k, v in channel.values.items() if k.startswith("_")}
        return self.merge(status), removed

    def apply_input_event(self, event):
        """Apply one physical transition; click classifications are not transitions."""
        if not isinstance(event, dict):
            return None
        key, name = event.get("component", ""), event.get("event")
        if not isinstance(key, str) or not re.fullmatch(r"input:\d+", key) or name not in ("btn_down", "btn_up"):
            return None
        channel = self.channels.setdefault(key, Channel(key))
        if channel.values.get("_enabled") is False:
            return None
        timestamp = event.get("ts")
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) and math.isfinite(timestamp):
            if channel.event_timestamp is not None and timestamp < channel.event_timestamp:
                channel.seen_events.clear()
            signature = (name, timestamp)
            if signature in channel.seen_events:
                return None
            channel.seen_events.append(signature)
            # UTC can move backwards. Connection generation + TCP receive order
            # establish order; timestamps identify retransmissions within one clock epoch.
            channel.event_timestamp = timestamp
        channel.values.update({"_inputType": "button", "state": None})
        channel.button_state = name == "btn_down"
        return channel

    def invalidate_buttons(self):
        for channel in self.channels.values():
            channel.button_state = None
            channel.event_timestamp = None
            channel.seen_events.clear()
