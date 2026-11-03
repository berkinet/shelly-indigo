"""One bounded optional Modbus task; the RPC worker owns result application."""
import threading


class Sampler:
    def __init__(self, reader, wake):
        self.reader, self.wake = reader, wake
        self.lock = threading.Lock()
        self.busy = False
        self.closed = False
        self.result = None

    def submit(self, token, host, mac, inputs):
        with self.lock:
            if self.busy or self.closed:
                return False
            self.busy = True
        def sample():
            values = {}
            try:
                values = self.reader(host, mac, inputs)
            except (OSError, ValueError):
                values = {}  # Unknown is never fabricated as OFF.
            finally:
                # Completion always releases the slot, including unexpected failures.
                with self.lock:
                    if not self.closed:
                        self.result = (token, values)
                self.wake()
        threading.Thread(target=sample, name="Shelly-button-sample", daemon=True).start()
        return True

    def take(self):
        with self.lock:
            result, self.result = self.result, None
            if result is not None:
                self.busy = False
            return result

    def stop(self):
        with self.lock:
            self.closed = True
            self.result = None
