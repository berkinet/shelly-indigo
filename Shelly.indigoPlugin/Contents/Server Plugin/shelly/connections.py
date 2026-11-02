"""Connection ownership. Mutated only by the Indigo adapter's event loop."""
import itertools
import time
import threading


class Connections:
    def __init__(self):
        self.workers = {}
        self.generations = {}
        self.owners = {}
        self.seen = {}
        self._sequence = itertools.count(1)

    def touch(self, address):
        self.seen[address] = time.monotonic()

    def reserve(self, address):
        generation = next(self._sequence)
        self.generations[address] = generation
        return generation

    def current(self, address, generation):
        return self.generations.get(address) == generation

    def retire(self, address):
        # Fence queued and late messages before stopping their producer.
        self.generations.pop(address, None)
        self.seen.pop(address, None)
        worker = self.workers.pop(address, None)
        if worker is not None:
            worker.stop()
        for identity, owner in list(self.owners.items()):
            if owner == address:
                del self.owners[identity]
        return worker

    def claim(self, identity, address):
        obsolete = [host for host, worker in self.workers.items() if host != address
                    and (self.owners.get(identity) == host or worker.expected_id == identity)]
        self.owners[identity] = address
        return obsolete


class CommunicationPolicy:
    """Read-only admission snapshot for workers; Indigo objects stay on the owner thread."""
    def __init__(self):
        self.lock = threading.Lock()
        self.disabled = set()
        self.disabled_channels = set()

    def replace(self, enabled_by_identity, enabled_by_channel=None):
        with self.lock:
            self.disabled = {identity for identity, enabled in enabled_by_identity.items() if not enabled}
            self.disabled_channels = {key for key, enabled in (enabled_by_channel or {}).items() if not enabled}

    def permits(self, identity):
        with self.lock:
            return identity not in self.disabled

    def permits_command(self, identity, method, params):
        namespace = method.split(".")[0].lower()
        component = f"{namespace}:{params['id']}" if "id" in params else None
        with self.lock:
            return identity not in self.disabled and (identity, component) not in self.disabled_channels
