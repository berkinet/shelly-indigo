"""Continuous Bonjour discovery. No device configuration is changed."""
import re
from .model import endpoint


class Discovery:
    def __init__(self, found):
        from zeroconf import IPVersion, ServiceBrowser, Zeroconf
        self.found = found
        self.zc = Zeroconf(ip_version=IPVersion.V4Only)
        self.browser = ServiceBrowser(self.zc, "_shelly._tcp.local.", listener=self)

    def add_service(self, zc, service_type, name):
        info = zc.get_service_info(service_type, name, timeout=2000)
        if info:
            addresses = info.parsed_addresses()
            if addresses:
                suffix = "." + service_type
                instance = name[:-len(suffix)] if name.endswith(suffix) else ""
                # Only a hint for skipping suspended units. RPC still verifies identity
                # before credentials, binding updates or control.
                identity = instance.lower() if re.fullmatch(r"shelly[a-zA-Z0-9_-]+", instance) else None
                self.found(endpoint(f"{addresses[0]}:{info.port}"), identity)

    def update_service(self, zc, service_type, name):
        self.add_service(zc, service_type, name)

    def remove_service(self, zc, service_type, name):
        # Disappearance does not prove offline; the WebSocket worker decides.
        pass

    def close(self):
        self.browser.cancel()
        self.zc.close()
