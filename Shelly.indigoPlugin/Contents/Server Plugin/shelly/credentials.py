"""Credentials scoped to explicitly configured physical identities."""
import json
import threading


class Credentials:
    def __init__(self, shared="", saved="{}"):
        self.shared = shared
        self.lock = threading.Lock()
        try:
            values = json.loads(saved)
            if not isinstance(values, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                   for k, v in values.items()):
                raise ValueError("Invalid credential map")
        except (ValueError, TypeError) as exc:
            # Do not overwrite stored data, or print its contents, on a parse failure.
            raise ValueError("Stored per-device credentials are invalid") from exc
        self.values = values
        self.authorized = set()

    def authorize(self, identities):
        with self.lock:
            self.authorized = set(identities)

    def password(self, identity, explicit_host=False):
        with self.lock:
            if identity in self.values:
                return self.values[identity]
            return self.shared if explicit_host or identity in self.authorized else ""

    def set(self, identity, password):
        if not isinstance(identity, str) or not identity or not isinstance(password, str):
            raise ValueError("Select a physical device and enter a local password")
        with self.lock:
            self.values[identity] = password
            return json.dumps(self.values, sort_keys=True)
