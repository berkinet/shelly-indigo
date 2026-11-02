"""Credentials scoped to explicitly configured physical identities."""
import json
import threading
import secrets
import time


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


class CredentialEdits:
    """Bounded in-memory edits: no plaintext password is saved in device properties."""
    def __init__(self):
        self.lock = threading.Lock()
        self.pending = {}

    def stage(self, identity, password="", clear=False, token=""):
        if not isinstance(password, str) or not isinstance(clear, bool) or not isinstance(token, str):
            raise ValueError("Enter a local device password or choose No password")
        if password and clear:
            raise ValueError("Enter a password or choose No password, not both")
        with self.lock:
            cutoff = time.monotonic() - 600
            self.pending = {key: value for key, value in self.pending.items() if value[2] >= cutoff}
            old = self.pending.get(token)
            if old is not None and old[0] != identity:
                self.pending.pop(token, None)
                old = None
            if not password and not clear:
                return token if old is not None else ""
            self.pending.pop(token, None)
            while len(self.pending) >= 32:
                self.pending.pop(next(iter(self.pending)))
            token = secrets.token_hex(16)
            self.pending[token] = (identity, "" if clear else password, time.monotonic())
            return token

    def finish(self, token, cancelled=False):
        with self.lock:
            value = self.pending.pop(token, None)
        return value[:2] if value is not None and not cancelled else None
