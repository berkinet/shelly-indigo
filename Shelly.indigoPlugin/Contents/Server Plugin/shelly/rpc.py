"""Serial RPC sessions: correlate replies while delivering interleaved notifications."""
import hashlib
import json
import secrets
import time


class RpcError(Exception):
    pass


class AuthenticationError(RpcError):
    pass


def digest_auth(challenge, password, cnonce=None):
    if challenge.get("algorithm", "SHA-256").upper() != "SHA-256":
        raise AuthenticationError("Unsupported authentication algorithm")
    realm, nonce = challenge["realm"], challenge["nonce"]
    cnonce = secrets.randbits(31) if cnonce is None else cnonce
    nc = challenge.get("nc", "00000001")
    def sha(value):
        return hashlib.sha256(value.encode()).hexdigest()
    ha1 = sha(f"admin:{realm}:{password}")
    ha2 = sha("dummy_method:dummy_uri")
    return {"realm": realm, "username": "admin", "nonce": nonce,
            "cnonce": cnonce, "nc": nc, "algorithm": "SHA-256",
            "response": sha(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")}


class Session:
    def __init__(self, socket, notify, password="", timeout=8):
        self.socket, self.notify = socket, notify
        self.password, self.timeout = password, timeout
        self.sequence = 0

    def _receive(self):
        raw = self.socket.recv()
        if not raw:
            raise ConnectionError("WebSocket closed")
        frame = json.loads(raw)
        if not isinstance(frame, dict):
            raise RpcError("Invalid RPC frame")
        if frame.get("method") in ("NotifyStatus", "NotifyFullStatus", "NotifyEvent"):
            self.notify(frame["method"], frame.get("params", {}))
        return frame

    def call(self, method, params=None):
        self.sequence += 1
        request = {"id": self.sequence, "src": "shelly-indigo", "method": method,
                   "params": params or {}}
        # Never replay a control request on transport failure.
        for attempt in range(2):
            self.socket.send(json.dumps(request))
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("RPC response timed out")
                self.socket.settimeout(remaining)
                frame = self._receive()
                if frame.get("id") == request["id"]:
                    break
            error = frame.get("error")
            if not error:
                return frame.get("result", {})
            if error.get("code") != 401:
                raise RpcError(f"{method} failed (code {error.get('code')})")
            if attempt or not self.password:
                raise AuthenticationError("Shelly authentication failed; check plugin password")
            request["auth"] = digest_auth(json.loads(error["message"]), self.password)
        raise AuthenticationError("Shelly authentication failed")

    def listen(self):
        self.socket.settimeout(0.5)
        self._receive()
