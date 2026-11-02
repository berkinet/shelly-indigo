"""Component diagnostics, separate from state translation and device creation."""
import re


class Diagnostics:
    def __init__(self, logger):
        self.logger = logger
        self.reports = {}
        self.warnings = {}

    def report(self, address, info, inventory, status, devices, label):
        components, previous = self.reports.get(address, (set(), None))
        # Keep names across partial notifications; never log raw status/config values.
        components.update(key for key, value in status.items() if isinstance(value, dict)
                          and re.fullmatch(r"[a-z][a-z0-9_]*(?::[0-9]+)?", key))
        channels = inventory.channels
        components.update(channels)
        available = sorted(key for key, channel in channels.items() if channel.supported)
        omitted = []
        for key in sorted(components):
            # Retain these components in Debug inventory without treating them as
            # missing Indigo switch/input/dimmer channels.
            if ":" not in key or key.split(":")[0] in ("script", "infino") or key in available:
                continue
            channel = channels.get(key)
            reason = ("disabled" if channel and channel.values.get("_enabled") is False
                      else "input mode not supported" if channel else "type not supported")
            omitted.append(f"{key} ({reason})")
        configured = {}
        for dev in devices:
            component = dev.pluginProps.get("component")
            if dev.pluginProps.get("shellyId") == info["id"] and component in available:
                configured.setdefault(component, []).append(f"Indigo {dev.id}: {dev.name}")
        assigned = [f"{key} ({'; '.join(sorted(configured[key]))})" for key in sorted(configured)]
        unassigned = [key for key in available if key not in configured]
        summary = (tuple(sorted(components)), tuple(available), tuple(omitted), tuple(assigned))
        if summary != previous:
            self.logger.debug(
                "Queried %s at %s (Gen%s): components: %s; supported channels: %s; "
                "already configured: %s; unassigned channels: %s",
                label, address, info["gen"], ", ".join(sorted(components)) or "none",
                ", ".join(available) or "none", ", ".join(assigned) or "none", ", ".join(unassigned) or "none")
        self.reports[address] = (components, summary)
        # Once per physical unit/problem combination, including across reconnects.
        # A changed or resolved problem updates the signature so it can be reported again.
        problem = (tuple(omitted), not available)
        if omitted or not available:
            if self.warnings.get(info["id"]) != problem:
                if available:
                    self.logger.warning(
                        "Unsupported Indigo channels on %s at %s: %s. Supported channels: %s; "
                        "unassigned channels: %s.", label, address,
                        ", ".join(omitted), ", ".join(available), ", ".join(unassigned) or "none")
                else:
                    self.logger.warning(
                        "No supported Indigo channels on %s at %s. Unavailable channels: %s. "
                        "The Shelly remains in the discovered-device menu.",
                        label, address, ", ".join(omitted) or "none reported")
            self.warnings[info["id"]] = problem
        else:
            self.warnings.pop(info["id"], None)
