"""OAuth loopback callbacks, fixed read-only requests and credential-free failures."""
import base64
import json
import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen
import support
from shelly.cloud_inventory import CLIENT_ID, CloudError, NoRedirect, authorize, claims, fetch_inventory, request_json, server_url


def jwt(**payload):
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    return 'header.' + encoded + '.signature'


class CloudInventoryTests(unittest.TestCase):
    def test_callback_requires_random_state_and_path_then_returns_code_without_http_logging(self):
        results = queue.Queue()
        clients = []
        def browser(url):
            params = parse_qs(urlsplit(url).query)
            self.assertEqual(params['client_id'], [CLIENT_ID])
            target = params['redirect_uri'][0]
            self.assertTrue(target.startswith('http://127.0.0.1:'))
            def visit():
                try:
                    try:
                        urlopen(target + '?code=SECRET&state=incorrect', timeout=2)
                    except HTTPError as exc:
                        self.assertEqual(exc.code, 400)
                    with urlopen(target + '?' + urlencode({'code': 'SECRET', 'state': params['state'][0]}), timeout=2) as response:
                        self.assertEqual(response.headers['Cache-Control'], 'no-store')
                        self.assertNotIn('SECRET', response.read().decode())
                    results.put(None)
                except BaseException as exc:
                    results.put(exc)
            thread = threading.Thread(target=visit)
            clients.append(thread)
            thread.start()
            return True
        self.assertEqual(authorize(threading.Event(), browser=browser, timeout=2), 'SECRET')
        for client in clients:
            client.join(2)
        result = results.get_nowait()
        if result:
            raise result

    def test_browser_failure_timeout_cancel_and_denial_have_no_secrets(self):
        with self.assertRaisesRegex(CloudError, 'Could not open Shelly sign-in'):
            authorize(threading.Event(), browser=lambda url: False)
        with self.assertRaisesRegex(CloudError, 'timed out'):
            authorize(threading.Event(), browser=lambda url: True, timeout=0)
        stopped = threading.Event()
        stopped.set()
        with self.assertRaisesRegex(CloudError, 'cancelled'):
            authorize(stopped, browser=lambda url: True)
        clients = []
        def denied(url):
            params = parse_qs(urlsplit(url).query)
            def visit():
                urlopen(params['redirect_uri'][0] + '?' + urlencode({'state': params['state'][0], 'error': 'secret-error'}), timeout=2).close()
            thread = threading.Thread(target=visit)
            thread.start()
            clients.append(thread)
            return True
        with self.assertRaisesRegex(CloudError, 'cancelled or denied'):
            authorize(threading.Event(), browser=denied, timeout=2)
        for client in clients:
            client.join(2)

    def test_only_https_shelly_hosts_can_receive_code_or_token(self):
        self.assertEqual(server_url('https://shelly-10-eu.shelly.cloud/'), 'https://shelly-10-eu.shelly.cloud')
        for server in ('http://shelly-10-eu.shelly.cloud', 'https://shelly.cloud.evil.test', 'https://evil.test',
                       'https://user:password@shelly-10-eu.shelly.cloud', 'https://shelly-10-eu.shelly.cloud:123',
                       'https://shelly-10-eu.shelly.cloud/redirect', None):
            with self.subTest(server=server), self.assertRaises(CloudError):
                server_url(server)
        with self.assertRaises(CloudError):
            claims('not-a-token')

    def test_authorization_exchanges_code_and_reads_inventory_without_ever_issuing_controls(self):
        server = 'https://shelly-10-eu.shelly.cloud'
        code = jwt(sub=CLIENT_ID, user_api_url=server)
        token = jwt(user_api_url=server, exp=time.time() + 3600)
        request = Mock(side_effect=[{'access_token': token}, {'data': {'devices_status': {
            'ignored': {'_dev_info': {'id': '020000000001', 'code': 'S4SW-002P16EU', 'online': True}}}}}])
        with patch('shelly.cloud_inventory.authorize', return_value=code):
            rows = fetch_inventory(threading.Event(), request=request)
        self.assertEqual(len(rows), 1)
        urls = [call.args[0] for call in request.call_args_list]
        self.assertEqual(urlsplit(urls[0]).path, '/oauth/auth')
        self.assertEqual(parse_qs(urlsplit(urls[0]).query)['grant_type'], ['code'])
        self.assertEqual(urls[1], server + '/device/all_status?show_info=true&no_shared=true')
        self.assertEqual(request.call_args_list[1].kwargs, {'token': token})
        self.assertFalse(rows[0]['online'])
        self.assertNotIn(token, str(rows))
        self.assertNotIn(code, str(rows))

    def test_invalid_routing_wrong_client_and_expired_tokens_never_reach_inventory(self):
        for payload in ({'sub': CLIENT_ID, 'user_api_url': 'https://evil.test'},
                        {'sub': 'another-client', 'user_api_url': 'https://shelly-10-eu.shelly.cloud'}):
            request = Mock()
            with patch('shelly.cloud_inventory.authorize', return_value=jwt(**payload)), self.assertRaises(CloudError):
                fetch_inventory(threading.Event(), request=request)
            request.assert_not_called()
        request = Mock(return_value={'access_token': jwt(user_api_url='https://shelly-10-eu.shelly.cloud', exp=1)})
        code = jwt(sub=CLIENT_ID, user_api_url='https://shelly-10-eu.shelly.cloud')
        with patch('shelly.cloud_inventory.authorize', return_value=code), self.assertRaisesRegex(CloudError, 'expired'):
            fetch_inventory(threading.Event(), request=request)
        self.assertEqual(request.call_count, 1)

    def test_http_errors_redirects_and_oversize_responses_do_not_expose_secrets(self):
        opener = Mock()
        opener.open.side_effect = URLError('SECRET_CODE_AND_TOKEN')
        with self.assertRaises(CloudError) as exc:
            request_json('https://shelly-10-eu.shelly.cloud/oauth/auth?code=SECRET_CODE_AND_TOKEN', opener=opener)
        self.assertNotIn('SECRET', str(exc.exception))
        with self.assertRaises(CloudError):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test/?SECRET')
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b' ' * 2_000_001
        opener.open.side_effect = None
        opener.open.return_value = response
        with self.assertRaisesRegex(CloudError, 'size limit'):
            request_json('https://shelly-10-eu.shelly.cloud/device/all_status', token='SECRET', opener=opener)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.get_header('Authorization'), 'Bearer SECRET')
