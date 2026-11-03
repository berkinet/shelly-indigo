"""Ordered RPC frames, bounded calls and identity-scoped digest authentication."""
import hashlib
import json
import secrets
import select
import time


class RpcError(Exception):
    """A remote RPC rejection, safe to report without a traceback."""


class AuthenticationError(RpcError):
    pass


class ProtocolError(ConnectionError):
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
    def __init__(self, socket, notify, password="", timeout=8, identity=None):
        self.socket, self.notify = socket, notify
        self.password, self.timeout, self.identity = password, timeout, identity
        self.source = "shelly-indigo-" + secrets.token_hex(6)
        self.sequence = 0
        self.challenge = None
        self.nonce_count = 0

    def _safe_message(self, value):
        message = str(value).replace(self.password, "[redacted]") if self.password else str(value)
        return " ".join(message.split())[:240]

    def _receive(self):
        raw = self.socket.recv()
        if not raw:
            raise ConnectionError("WebSocket closed")
        if len(raw) > 1024 * 1024:
            raise ProtocolError("RPC frame exceeds 1 MiB")
        try:
            frame = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ProtocolError("Invalid RPC JSON") from exc
        if not isinstance(frame, dict):
            raise ProtocolError("Invalid RPC frame")
        source = frame.get("src")
        if self.identity and source and source != self.identity:
            raise ProtocolError("RPC frame identity does not match this connection")
        if frame.get("method") in ("NotifyStatus", "NotifyFullStatus", "NotifyEvent"):
            params = frame.get("params", {})
            if not isinstance(params, dict) or (frame["method"] == "NotifyEvent"
                    and not isinstance(params.get("events", []), list)):
                raise ProtocolError("Invalid notification payload")
            self.notify(frame["method"], params)
        return frame

    def _authentication(self):
        self.nonce_count += 1
        challenge = dict(self.challenge)
        # Older firmware uses a numeric nc; current firmware requires eight hex digits.
        challenge["nc"] = (self.nonce_count if isinstance(challenge.get("nc"), int)
                           else f"{self.nonce_count:08x}")
        return digest_auth(challenge, self.password)

    def call(self, method, params=None):
        self.sequence += 1
        request = {"id": self.sequence, "src": self.source, "method": method,
                   "params": params or {}}
        if self.challenge is not None:
            request["auth"] = self._authentication()
        deadline = time.monotonic() + self.timeout
        try:
            # A 401 is an explicit rejection, never a transport retry of an uncertain action.
            for attempt in range(2):
                self.socket.send(json.dumps(request))
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
                if not isinstance(error, dict):
                    raise ProtocolError("Invalid RPC error")
                if error.get("code") != 401:
                    raise RpcError(f"{method} failed (code {error.get('code')}): "
                                   f"{self._safe_message(error.get('message', 'request rejected'))}")
                if attempt or not self.password:
                    raise AuthenticationError("Local device authentication required or password rejected")
                try:
                    challenge = json.loads(error["message"])
                    valid = (isinstance(challenge, dict) and self.identity
                             and challenge.get("realm") == self.identity and "nonce" in challenge)
                except (KeyError, ValueError, TypeError):
                    valid = False
                if not valid:
                    raise AuthenticationError("Authentication challenge does not match the expected device identity")
                self.challenge, self.nonce_count = challenge, 0
                request["auth"] = self._authentication()
        except (RpcError, ProtocolError):
            raise
        except Exception as exc:
            raise ConnectionError(f"{method}: {type(exc).__name__}: {self._safe_message(exc)}") from exc
        raise AuthenticationError("Local device authentication failed")

    def wait(self, wake_socket, timeout):
        """Wait for data or a queued command without a fixed command latency penalty."""
        ready, _, _ = select.select([self.socket.sock, wake_socket], [], [], max(0, timeout))
        if wake_socket in ready:
            try:
                while wake_socket.recv(4096):
                    pass
            except BlockingIOError:
                pass
        return self.socket.sock in ready

    def listen(self):
        # Only called after select reports readable data; bound fragmented frames too.
        self.socket.settimeout(1)
        self._receive()
