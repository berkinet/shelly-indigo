"""Capture sanitized, read-only Shelly RPC payloads as firmware regression fixtures."""
from datetime import datetime
import argparse
import ipaddress
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Shelly.indigoPlugin/Contents/Server Plugin'),
                str(ROOT / 'Shelly.indigoPlugin/Contents/Packages')]
from shelly.model import endpoint

READ_METHODS = frozenset(('Shelly.GetDeviceInfo', 'Shelly.GetStatus', 'Sys.GetConfig',
                          'Input.GetConfig', 'Modbus.GetConfig'))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise HTTPError(request.full_url, code, 'Capture redirects are disabled', headers, fp)


def request(address, method, params=None):
    if method not in READ_METHODS:
        raise ValueError('Capture supports only the fixed read-only RPC allowlist')
    address = endpoint(address)
    query = '?' + urlencode(params) if params else ''
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(f'http://{address}/rpc/{method}{query}', timeout=3) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('Capture response exceeds 1 MiB')
    value = json.loads(raw)
    if not isinstance(value, dict) or 'error' in value:
        raise ValueError(f'{method} did not return a successful object')
    return value


class Sanitizer:
    """Preserve structure/nulls and identifier relationships; never persist raw data."""
    secret_keys = frozenset(('password', 'passwd', 'pass', 'pwd', 'token', 'access_token',
                             'refresh_token', 'auth_token', 'secret', 'api_key', 'client_key',
                             'username', 'user', 'certificate', 'private_key'))

    def __init__(self, info, address, ordinal=1):
        if not 1 <= ordinal <= 200:
            raise ValueError('Fixture ordinal must be between 1 and 200')
        self.mac = f'02000000{ordinal:04X}'
        raw_mac = str(info.get('mac', '')).replace(':', '').replace('-', '')
        identity = str(info['id'])
        prefix = identity.rsplit('-', 1)[0] if '-' in identity else 'shelly-fixture'
        if not re.fullmatch(r'shelly[a-z0-9_]+', prefix):
            prefix = 'shellyfixture'
        self.identity = f'{prefix}-{self.mac.lower()}'
        self.replacements = {identity: self.identity}
        if raw_mac:
            for value, fake in ((raw_mac.upper(), self.mac), (raw_mac.lower(), self.mac.lower()),
                                (':'.join(raw_mac[i:i+2] for i in range(0, len(raw_mac), 2)).upper(),
                                 ':'.join(self.mac[i:i+2] for i in range(0, len(self.mac), 2)))):
                self.replacements[value] = fake
        self.addresses = {urlsplit('//' + endpoint(address)).hostname: f'192.0.2.{ordinal + 9}'}
        self.labels = {}
        self.macs = {}

    def string(self, value, key):
        if not value:
            return value
        if key in self.secret_keys or any(part in key for part in ('password', 'private_key', 'auth_token')):
            return '<redacted>'
        if key in ('name', 'label', 'ssid', 'sta_ssid', 'ap_ssid'):
            group = 'SSID' if 'ssid' in key else 'name'
            return self.labels.setdefault((group, value), f'Fixture {group} {len(self.labels) + 1}')
        if key in ('tz', 'timezone'):
            return 'Etc/UTC'
        for old, new in sorted(self.replacements.items(), key=lambda item: -len(item[0])):
            value = value.replace(old, new)
        def replace_mac(match):
            original = match.group()
            compact = original.replace(':', '').replace('-', '').upper()
            fake = self.mac if compact == self.mac else self.macs.setdefault(compact, f'02000001{len(self.macs) + 1:04X}')
            delimiter = ':' if ':' in original else '-' if '-' in original else ''
            return delimiter.join(fake[i:i+2] for i in range(0, 12, 2)) if delimiter else fake
        value = re.sub(r'(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])|(?<![\w])[0-9A-Fa-f]{12}(?![\w])', replace_mac, value)
        if '://' in value:
            parsed = urlsplit(value)
            return f'{parsed.scheme}://fixture.example.invalid/'
        def replace_ip(match):
            address = match.group()
            try:
                ipaddress.ip_address(address)
            except ValueError:
                return address
            if address == '0.0.0.0' or address.startswith('255.'):
                return address
            return self.addresses.setdefault(address, f'192.0.2.{len(self.addresses) + 20}')
        if key not in ('ver', 'fw_id', 'firmware', 'firmware_id'):
            value = re.sub(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])', replace_ip, value)
        if ':' in value:
            try:
                ipaddress.ip_address(value.split('%', 1)[0])
            except ValueError:
                pass
            else:
                return self.addresses.setdefault(value, f'2001:db8::{len(self.addresses) + 1}')
        if key in ('server', 'host', 'hostname', 'dst_addr', 'dst', 'src') and value != self.identity:
            if value != self.identity + '.local' and not value.startswith(('192.0.2.', '2001:db8:')):
                return 'fixture.example.invalid'
        return value

    def clean(self, value, key=''):
        if isinstance(value, dict):
            return {self.string(str(k), ''): self.clean(v, str(k).lower()) for k, v in value.items()}
        if isinstance(value, list):
            return [self.clean(item, key) for item in value]
        if isinstance(value, str):
            return self.string(value, key)
        if key in ('lat', 'lon', 'latitude', 'longitude', 'utc_offset') and isinstance(value, (int, float)):
            return type(value)(0)
        return value


def capture(address, ordinal=1):
    info = request(address, 'Shelly.GetDeviceInfo')
    records = [{'method': 'Shelly.GetDeviceInfo', 'params': {}, 'result': info}]
    status = request(address, 'Shelly.GetStatus')
    records.append({'method': 'Shelly.GetStatus', 'params': {}, 'result': status})
    records.append({'method': 'Sys.GetConfig', 'params': {}, 'result': request(address, 'Sys.GetConfig')})
    for key in sorted(status):
        if re.fullmatch(r'input:\d+', key):
            params = {'id': int(key.split(':')[1])}
            records.append({'method': 'Input.GetConfig', 'params': params,
                            'result': request(address, 'Input.GetConfig', params)})
    if info.get('gen') == 4:
        records.append({'method': 'Modbus.GetConfig', 'params': {}, 'result': request(address, 'Modbus.GetConfig')})
    final = request(address, 'Shelly.GetDeviceInfo')
    if any(info.get(key) != final.get(key) for key in ('id', 'gen', 'model', 'ver', 'fw_id')):
        raise ValueError('Identity or firmware changed during capture; no fixture saved')
    sanitizer = Sanitizer(info, address, ordinal)
    return {'provenance': {'kind': 'recorded-device', 'transport': 'read-only HTTP RPC GET',
                           'capture_date': datetime.now(ZoneInfo('Europe/Paris')).date().isoformat(),
                           'gen': info.get('gen'), 'model': info.get('model'),
                           'firmware': info.get('ver'), 'firmware_id': info.get('fw_id'),
                           'redactions': ['synthetic identity/MAC and network addresses',
                                          'device names, SSIDs, location and credentials sanitized',
                                          'keys, nulls and nonsensitive values retained']},
            'responses': sanitizer.clean(records)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('host')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ordinal', type=int, default=1)
    args = parser.parse_args()
    result = capture(args.host, args.ordinal)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(f'Saved sanitized fixture: {args.output.name} (Gen{result["provenance"]["gen"]})')


if __name__ == '__main__':
    main()
