"""Wire-level checks for the read-only, identity-verified Gen4 input sampler."""
import struct
import unittest
from unittest.mock import patch
import support
from shelly.modbus import read_button_inputs


class Socket:
    def __init__(self, state, fault=None, swapped=True):
        self.state, self.fault, self.swapped = state, fault, swapped
        self.received = b''
        self.requests = []
    def __enter__(self):
        return self
    def __exit__(self, *unused):
        pass
    def settimeout(self, value):
        pass
    def sendall(self, request):
        tx, protocol, size, unit, function, address, count = struct.unpack('!HHHBBHH', request)
        self.requests.append((function, address, count))
        assert protocol == 0 and size == 6 and unit == 1
        if function == 4:
            data = b'E4B06365AF10' if self.fault != 'identity' else b'112233445566'
            if self.swapped:
                data = b''.join(data[index:index+2][::-1] for index in range(0, 12, 2))
        else:
            data = bytes([int(self.state)])
        body = bytes([function, len(data)]) + data
        if self.fault == 'exception':
            body = bytes([function | 128, 2])
        if self.fault == 'transaction':
            tx += 1
        self.received = struct.pack('!HHHB', tx, 0, len(body)+1, 1) + body
    def recv(self, size):
        if self.fault == 'timeout':
            raise TimeoutError('simulated timeout')
        if self.fault == 'closed':
            return b''
        # Fragment headers and payloads, as a TCP connection is allowed to do.
        chunk, self.received = self.received[:min(size, 3)], self.received[min(size, 3):]
        return chunk


class ModbusTests(unittest.TestCase):
    def test_pressed_and_released_samples_with_both_register_byte_orders(self):
        for state in (False, True):
            for swapped in (False, True):
                with self.subTest(state=state, swapped=swapped):
                    sock = Socket(state, swapped=swapped)
                    with patch('shelly.modbus.socket.create_connection', return_value=sock):
                        self.assertEqual(read_button_inputs('host', 'E4:B0:63:65:AF:10', [0]), {0: state})
                    self.assertEqual(sock.requests, [(4, 0, 6), (2, 100, 1)])

    def test_wrong_device_malformed_and_failed_reads_never_become_false(self):
        for fault in ('identity', 'transaction', 'exception', 'timeout', 'closed'):
            with self.subTest(fault=fault):
                sock = Socket(False, fault=fault)
                with patch('shelly.modbus.socket.create_connection', return_value=sock):
                    with self.assertRaises((ValueError, OSError)):
                        read_button_inputs('host', 'E4B06365AF10', [0])
                if fault == 'identity':
                    self.assertEqual(sock.requests, [(4, 0, 6)])
