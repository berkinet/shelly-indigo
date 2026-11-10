"""On-demand Shelly OAuth inventory. Codes/tokens never enter preferences or logs."""
import base64
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import re
import secrets
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
import webbrowser

from .inventory_report import cloud_rows

CLIENT_ID = "shelly-diy"  # Shelly's documented personal/experimental account client.
LOGIN_URL = "https://my.shelly.cloud/oauth_login.html"
MAX_BODY = 2_000_000


class CloudError(Exception):
    """Contains only a fixed, credential-free message safe for the Indigo log."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CloudError("Shelly Cloud redirected an authenticated request; sign in again.")


def server_url(value):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        valid = (parsed.scheme == "https" and re.fullmatch(r"[a-zA-Z0-9-]+\.shelly\.cloud", host)
                 and parsed.port in (None, 443) and not parsed.username and not parsed.password
                 and parsed.path in ("", "/") and not parsed.query and not parsed.fragment)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise CloudError("Shelly authorization returned an invalid cloud server.")
    return "https://" + host.lower()


def claims(token):
    try:
        if not isinstance(token, str) or len(token) > 16000 or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token):
            raise ValueError()
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError()
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        if not isinstance(payload, dict):
            raise ValueError()
        return payload
    except (ValueError, TypeError, UnicodeError):
        raise CloudError("Shelly returned an invalid authorization token.") from None


def request_json(url, *, token=None, opener=None):
    opener = opener or build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
    request = Request(url, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with opener.open(request, timeout=10) as response:
            body = response.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise CloudError("Shelly Cloud inventory exceeds the response size limit.")
        data = json.loads(body)
        if not isinstance(data, dict) or data.get("isok") is False:
            raise CloudError("Shelly Cloud declined the inventory request; sign in again.")
        return data
    except CloudError:
        raise
    except (HTTPError, URLError, OSError, ValueError):
        raise CloudError("Could not read Shelly Cloud inventory; check Internet access and try signing in again.") from None


def authorize(stopped, *, browser=None, timeout=300):
    if stopped.is_set():
        raise CloudError("Shelly cloud inventory cancelled.")
    state = secrets.token_urlsafe(32)
    path = "/shelly/" + secrets.token_urlsafe(24)
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass  # The request URL contains the authorization code.

        def do_GET(self):
            query = urlsplit(self.path)
            values = parse_qs(query.query)
            valid_state = values.get("state", [])
            valid = (query.path == path and len(self.path) <= 20000 and len(valid_state) == 1
                     and valid_state[0].isascii() and hmac.compare_digest(valid_state[0], state))
            if not valid:
                self.send_error(400, "Invalid authorization callback")
                return
            codes = values.get("code", [])
            if "error" not in values and len(codes) == 1 and codes[0] and len(codes[0]) <= 16000:
                result["code"] = codes[0]
            else:
                result["error"] = True
            nonce = secrets.token_urlsafe(24)
            body = ("<!doctype html><title>Shelly inventory</title>"
                    f'<script nonce="{nonce}">history.replaceState(null,"","/complete");</script>'
                    "<p>Authorization received. Return to Indigo; the inventory result will appear in its Event Log.</p>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{nonce}'; frame-ancestors 'none'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class LocalServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(1)
            return connection, address

        def handle_error(self, request, client_address):
            pass  # Never let HTTPServer print a callback URL or exception context.

    try:
        server = LocalServer(("127.0.0.1", 0), Callback)
    except OSError:
        raise CloudError("Could not open the temporary local Shelly sign-in callback.") from None
    try:
        server.timeout = 0.25
        redirect = f"http://127.0.0.1:{server.server_port}{path}"
        url = LOGIN_URL + "?" + urlencode({"client_id": CLIENT_ID, "redirect_uri": redirect, "state": state})
        try:
            opened = (browser or webbrowser.open)(url)
        except Exception:
            opened = False
        if not opened:
            raise CloudError("Could not open Shelly sign-in. Run the cloud inventory on the Indigo server Mac with a browser available.")
        deadline = time.monotonic() + timeout
        while not result and not stopped.is_set() and time.monotonic() < deadline:
            server.handle_request()
        if stopped.is_set():
            raise CloudError("Shelly cloud inventory cancelled.")
        if not result:
            raise CloudError("Shelly sign-in timed out after five minutes; run the inventory command again.")
        if "error" in result:
            raise CloudError("Shelly sign-in was cancelled or denied.")
        return result.pop("code")
    finally:
        result.clear()
        server.server_close()


def fetch_inventory(stopped, *, browser=None, request=request_json):
    code = token = ""
    try:
        code = authorize(stopped, browser=browser)
        code_claims = claims(code)  # Routing metadata only; never treated as proof of identity.
        if code_claims.get("sub") != CLIENT_ID:
            raise CloudError("Shelly authorization used a different application; sign in again.")
        server = server_url(code_claims.get("user_api_url"))
        if stopped.is_set():
            raise CloudError("Shelly cloud inventory cancelled.")
        response = request(server + "/oauth/auth?" + urlencode({"client_id": CLIENT_ID, "grant_type": "code", "code": code}))
        token = response.get("access_token")
        token_claims = claims(token)
        expires = token_claims.get("exp")
        if (isinstance(expires, bool) or not isinstance(expires, (int, float))
                or not math.isfinite(expires) or expires <= time.time()):
            raise CloudError("Shelly authorization has expired; sign in again.")
        server = server_url(token_claims.get("user_api_url"))
        if stopped.is_set():
            raise CloudError("Shelly cloud inventory cancelled.")
        try:
            return cloud_rows(request(server + "/device/all_status?show_info=true&no_shared=true", token=token))
        except ValueError:
            raise CloudError("Shelly Cloud returned an invalid device inventory.") from None
    finally:
        code = token = ""  # Drop references; no caching, persistence or refresh-token flow.
