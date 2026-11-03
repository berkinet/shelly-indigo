"""Stable physical/channel bindings and display labels; no Indigo dependency."""

def expected_device_type(props):
    component = props.get("component", "")
    if not isinstance(component, str):
        return None
    kind, separator, number = component.partition(":")
    if not separator or not number.isdecimal():
        return None
    return {"switch": "shellySwitch", "light": "shellyDimmer", "input": "shellyInput"}.get(kind)


def selection_key(props):
    identity, component = props.get("shellyId"), props.get("component")
    return f"{identity}/{component}" if identity and component else ""


def channel_properties(info, address, channel):
    return {"shellyId": info["id"], "component": channel.key,
            "host": address, "model": info.get("model", ""),
            "generation": str(info["gen"]), "SupportsOnState": True,
            "SupportsStatusRequest": True,
            "address": f"{info['id']}/{channel.key}"}


def device_label(info):
    name = info.get("name")
    name = " ".join(name.split()) if isinstance(name, str) else ""
    return f"{name} ({info['id']})" if name and name != info["id"] else info["id"]


def channel_label(info, component, address):
    return f"{device_label(info)} / {component} ({address})"


