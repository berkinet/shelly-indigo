import hashlib
import json
import unittest
import support
from shelly.model import Inventory, endpoint
from shelly.rpc import AuthenticationError, RpcError, Session, digest_auth


class Socket:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.sent = []
    def recv(self):
        return json.dumps(next(self.frames))
    def send(self, raw):
        self.sent.append(json.loads(raw))
    def settimeout(self, seconds):
        pass


class ProtocolTests(unittest.TestCase):
    def test_partial_notifications_keep_output_and_metering(self):
        inventory = Inventory()
        inventory.merge({'switch:0': {'output': True, 'apower': 14, 'aenergy': {'total': 30}}})
        channel, = inventory.merge({'ts': 1, 'switch:0': {'voltage': 230}})
        self.assertEqual(channel.states(), {'onOffState': True, 'powerWatts': 14.,
                                          'energyWh': 30., 'voltage': 230.})
        inventory.merge({'switch:0': {'output': False, 'apower': 0}})
        self.assertFalse(channel.states()['onOffState'])
        self.assertEqual(channel.states()['powerWatts'], 0)

    def test_distinct_channels_and_unsupported_components(self):
        inventory = Inventory()
        channels = inventory.merge({'switch:0': {'output': False}, 'switch:1': {'output': True},
                                    'input:0': {'state': True}, 'wifi': {}, 'cover:0': {}})
        self.assertEqual([c.key for c in channels], ['switch:0', 'switch:1', 'input:0'])
        self.assertEqual([c.states()['onOffState'] for c in channels], [False, True, True])

    def test_reject_credentials_urls_and_paths(self):
        self.assertEqual(endpoint('shelly.local'), 'shelly.local:80')
        self.assertEqual(endpoint('[::1]:8080'), '[::1]:8080')
        for value in ('http://host', 'user:pass@host', 'host/rpc', 'host?x=y', '', 'host:70000'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                endpoint(value)

    def test_interleaved_notifications_and_unrelated_reply(self):
        sock = Socket([{'method': 'NotifyStatus', 'params': {'switch:0': {'output': True}}},
                       {'id': 99, 'result': {}}, {'id': 1, 'result': {'ok': True}}])
        updates = []
        session = Session(sock, lambda *args: updates.append(args))
        self.assertEqual(session.call('Shelly.GetStatus'), {'ok': True})
        self.assertEqual(len(updates), 1)
        self.assertEqual(sock.sent[0]['src'], 'shelly-indigo')

    def test_digest_matches_independent_known_calculation_old_and_new_nonce(self):
        sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
        for nonce, nc in [('base64nonce==', '00000001'), (12345, 1)]:
            challenge = {'realm': 'shelly-test', 'nonce': nonce, 'algorithm': 'SHA-256'}
            if isinstance(nc, int):
                challenge['nc'] = nc
            result = digest_auth(challenge, 'secret', cnonce=42)
            expected = sha(':'.join([sha('admin:shelly-test:secret'), str(nonce), str(nc),
                                     '42', 'auth', sha('dummy_method:dummy_uri')]))
            self.assertEqual(result['response'], expected)
            self.assertNotIn('secret', json.dumps(result))

    def test_authentication_retries_only_once(self):
        challenge = {'realm': 'unit', 'nonce': 'abc', 'algorithm': 'SHA-256'}
        error = {'id': 1, 'error': {'code': 401, 'message': json.dumps(challenge)}}
        sock = Socket([error, error])
        with self.assertRaises(AuthenticationError):
            Session(sock, lambda *a: None, 'wrong').call('Switch.Set', {'id': 0, 'on': True})
        self.assertEqual(len(sock.sent), 2)
        self.assertIn('auth', sock.sent[1])
        sock = Socket([error])
        with self.assertRaises(AuthenticationError):
            Session(sock, lambda *a: None).call('Shelly.GetStatus')
        self.assertEqual(len(sock.sent), 1)

    def test_rpc_failure_is_not_retried(self):
        sock = Socket([{'id': 1, 'error': {'code': -103, 'message': 'bad params'}}])
        with self.assertRaises(RpcError):
            Session(sock, lambda *a: None).call('Switch.Set')
        self.assertEqual(len(sock.sent), 1)

    def test_nullable_meter_values_do_not_break_channel_update(self):
        channel, = Inventory().merge({'switch:0': {'output': False, 'aenergy': None, 'voltage': None}})
        self.assertEqual(channel.states(), {'onOffState': False})
