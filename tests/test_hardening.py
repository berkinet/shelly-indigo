"""Revision caching, asynchronous sampling, and release/capture boundary contracts."""
import importlib.util
import json
from pathlib import Path
import queue
import threading
import time
import unittest
from unittest.mock import patch
import support
from shelly.rpc import RpcError, Session
from shelly.worker import Worker
from test_worker import ReadSession
from simulator import LocalShelly, next_event, wait_for

ROOT = Path(__file__).resolve().parents[1]


def tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConfigurationTests(unittest.TestCase):
    def worker(self):
        worker = Worker('192.0.2.1:80', lambda *args: None)
        worker.device_info = {'id': 'unit', 'gen': 3}
        return worker

    def test_same_revision_skips_config_new_revision_or_event_refreshes(self):
        worker = self.worker()
        session = ReadSession({'sys': {'cfg_rev': 10}, 'input:0': {'state': None}})
        worker._read_status(session)
        session.calls.clear()
        worker._read_status(session, False)
        self.assertEqual(session.calls, ['Shelly.GetStatus'])
        session.status['sys']['cfg_rev'] = 11
        worker._read_status(session, False)
        self.assertEqual(session.calls.count('Input.GetConfig'), 1)
        session.calls.clear()
        worker._notify('NotifyEvent', {'events': [{'component': 'sys', 'event': 'config_changed'}]})
        worker._read_status(session, False)
        self.assertIn('Input.GetConfig', session.calls)
        self.assertFalse(worker.config_dirty)

    def test_periodic_fallback_is_slow_with_revision_and_short_without_or_on_error(self):
        for revision, error, interval in ((8, False, 900), (None, False, 60), (8, True, 60)):
            with self.subTest(revision=revision, error=error):
                worker = self.worker()
                session = ReadSession({'sys': {'cfg_rev': revision}, 'input:0': {'state': None}})
                session.name_error = RpcError('unsupported') if error else None
                with patch('shelly.worker.time.monotonic', return_value=100):
                    worker._read_status(session)
                self.assertEqual(worker.next_config, 100 + interval)
                session.calls.clear()
                worker._read_status(session, False)
                self.assertEqual(session.calls, ['Shelly.GetStatus'])

    def test_event_during_config_read_remains_dirty_and_new_channel_is_configured(self):
        worker = self.worker()
        session = ReadSession({'sys': {'cfg_rev': 10}, 'input:0': {'state': None}})
        original = session.call
        def changed(method, params=None):
            if method == 'Sys.GetConfig':
                worker._notify('NotifyEvent', {'events': [{'event': 'config_changed', 'component': 'sys'}]})
            return original(method, params)
        session.call = changed
        worker._read_status(session)
        self.assertTrue(worker.config_dirty)
        session.call = original
        worker._read_status(session, False)
        self.assertFalse(worker.config_dirty)
        session.calls.clear()
        session.status['input:1'] = {'state': None}
        worker._read_status(session, False)
        self.assertEqual(session.calls.count('Input.GetConfig'), 2)

    def test_unavailable_input_config_does_not_hot_loop_on_each_status(self):
        worker = self.worker()
        session = ReadSession({'sys': {'cfg_rev': 1}, 'input:0': {'state': None}})
        original = session.call
        def unavailable(method, params=None):
            if method == 'Input.GetConfig':
                session.calls.append(method)
                raise RpcError('temporarily unavailable')
            return original(method, params)
        session.call = unavailable
        worker._read_status(session)
        session.calls.clear()
        worker._read_status(session, False)
        self.assertEqual(session.calls, ['Shelly.GetStatus'])


class SamplingTests(unittest.TestCase):
    def test_slow_modbus_does_not_block_real_rpc_command(self):
        started, finish = threading.Event(), threading.Event()
        def reader(*args):
            started.set()
            finish.wait(2)
            return {0: False}
        with LocalShelly(gen=4, button=True) as sim:
            import websocket
            connector = lambda *a, **kw: websocket.create_connection(f'ws://{sim.endpoint}/rpc', **kw)
            events = queue.Queue()
            worker = Worker('127.0.0.1:80', lambda *e: events.put(e), connector=connector, sample_reader=reader)
            try:
                worker.start()
                next_event(events, lambda e: e[0] == 'ready')
                self.assertTrue(started.wait(1), 'optional sample did not start')
                worker.command('Switch.Set', {'id': 0, 'on': True})
                next_event(events, lambda e: e[0] == 'status' and e[2].get('switch:0', {}).get('output') is True, timeout=1)
                self.assertFalse(finish.is_set(), 'command should finish while sample is still blocked')
            finally:
                finish.set()
                worker.stop()
                worker.join(3)
            self.assertFalse(worker.is_alive())

    def test_sample_event_versions_track_only_configured_inputs(self):
        worker = Worker('192.0.2.1:80', lambda *e: None)
        worker.device_info = {'id': 'unit', 'gen': 4}
        worker._reconcile(ReadSession({'input:0': {'state': None}}), initial=True)
        worker._notify('NotifyEvent', {'events': [
            {'component': [], 'event': 'btn_down'},
            {'component': 'input:999', 'event': 'btn_down'},
            {'component': 'input:0', 'event': 'btn_up'}]})
        self.assertEqual(worker.input_versions, {'input:0': 1})

    def test_late_samples_cannot_overwrite_event_config_or_connection_changes(self):
        for change in ('press', 'configuration', 'connection', 'stop'):
            with self.subTest(change=change):
                started, finish = threading.Event(), threading.Event()
                emitted = []
                def reader(*args):
                    started.set()
                    finish.wait(1)
                    return {0: False}
                worker = Worker('192.0.2.1:80', lambda *e: emitted.append(e), sample_reader=reader)
                worker.device_info = {'id': 'unit', 'gen': 4, 'mac': '020000000001'}
                worker._reconcile(ReadSession({'input:0': {'state': None}}), initial=True)
                worker._sample_buttons(time.monotonic())
                self.assertTrue(started.wait(1))
                if change == 'press':
                    worker._notify('NotifyEvent', {'events': [{'component': 'input:0', 'event': 'btn_down', 'ts': 1}]})
                elif change == 'configuration':
                    worker._notify('NotifyEvent', {'events': [{'event': 'config_changed'}]})
                elif change == 'connection':
                    worker._discard_commands()
                    worker.online = True
                else:
                    worker.stop()
                finish.set()
                if change != 'stop':
                    wait_for(lambda: worker.sampler.result is not None)
                    worker._sample_buttons(time.monotonic())
                self.assertFalse(any(e[0] == 'status' and '_buttonState' in e[2].get('input:0', {}) for e in emitted))
                worker.stop()


class ToolTests(unittest.TestCase):
    def test_release_tags_support_second_beta_and_stable_without_version_mismatch(self):
        release = tool('release')
        for tag, prerelease in (('v0.2.2-beta.2', True), ('v0.2.2-rc.3', True), ('v0.2.2', False)):
            plan = release.release_plan('0.2.2', tag)
            self.assertEqual(plan[0], tag)
            self.assertEqual(plan[1].name, f'release-{tag[1:]}.md')
            self.assertEqual(plan[3], prerelease)
            self.assertNotIn('stabilization', plan[2])
        self.assertEqual(release.release_plan('0.2.2', 'v0.2.2-beta.2', Path('notes.md'), 'Title')[1:3], (Path('notes.md'), 'Title'))
        for invalid in ('v0.2.3-beta.1', 'v0.2.2-beta.0', 'v0.2.2-beta.2/../bad', '--draft'):
            with self.assertRaises(ValueError):
                release.release_plan('0.2.2', invalid)

    def test_capture_allowlist_and_sanitizer_preserve_nulls_and_relationships(self):
        capture = tool('capture_device')
        with self.assertRaises(ValueError):
            capture.request('192.0.2.1', 'Switch.Set', {'on': True})
        info = {'id': 'shellytest-aabbccddeeff', 'mac': 'AABBCCDDEEFF'}
        raw = {'id': info['id'], 'mac': info['mac'], 'ble': {'addr': 'aa:bb:cc:dd:ee:f1,p'},
               'device': {'name': 'Private room'}, 'name': 'Private room', 'ssid': 'Private wifi',
               'host': 'private.local', 'sta_ip': '10.1.2.3', 'ipv6': 'fe80::1234',
               'password': 'secret', 'lat': 48.2, 'state': None, 'enable': True, 'ver': '1.2.3.4'}
        result = capture.Sanitizer(info, '10.1.2.3').clean(raw)
        text = json.dumps(result).lower()
        for secret in ('private', 'aabbcc', 'aa:bb:cc', '10.1.2.3', 'fe80::', 'secret', '48.2'):
            self.assertNotIn(secret, text)
        self.assertEqual(result['name'], result['device']['name'])
        self.assertIsNone(result['state'])
        self.assertTrue(result['enable'])
        self.assertEqual(result['ver'], '1.2.3.4')
        self.assertTrue(result['ble']['addr'].endswith(',p'))
        self.assertEqual(result['id'].split('-')[-1], result['mac'].lower())
