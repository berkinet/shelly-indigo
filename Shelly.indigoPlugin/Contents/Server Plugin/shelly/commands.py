"""Translate Indigo-neutral action intents; execution and relative reads belong to the worker."""
import math


def translate(device_type, component, action, values=()):
    namespace = "Light" if device_type == "shellyDimmer" else "Switch"
    params = {"id": int(component.split(":")[1])}
    if action in ("on", "off", "toggle"):
        method = f"{namespace}.Toggle" if action == "toggle" else f"{namespace}.Set"
        if action != "toggle":
            params["on"] = action == "on"
    elif action in ("brightness", "brighten", "dim") and namespace == "Light":
        value = values[0] if values else None
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or not 0 <= value <= 100):
            raise ValueError("Brightness must be a number from 0 to 100")
        if action == "brightness":
            method, params["on"] = "Light.Set", value > 0
            if value > 0:
                params["brightness"] = value
        else:
            method = "Light.Adjust"  # Internal intent, never sent as a Shelly RPC method.
            params["delta"] = value if action == "brighten" else -value
    else:
        raise ValueError("Unsupported Shelly device action")
    return method, params
