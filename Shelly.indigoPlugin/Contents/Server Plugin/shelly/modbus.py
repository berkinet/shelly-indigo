"""Read-only Gen4 physical-input sampling through an already enabled Modbus server."""
import re
import socket
import struct
import time


def read_button_inputs(host, expected_mac, input_ids, timeout=1.5):
    """Verify identity, then read discrete inputs; no write functions are supported."""
    if not isinstance(expected_mac, str):
        raise ValueError('Invalid expected Shelly MAC')
    expected = expected_mac.replace(':', '').replace('-', '').upper()
    if not re.fullmatch(r'[0-9A-F]{12}', expected):
        raise ValueError('Invalid expected Shelly MAC')
    ids = sorted(set(input_ids))
    if any(not isinstance(index, int) or index < 0 or 100 + index * 10 > 65535 for index in ids):
        raise ValueError('Input outside the Modbus address range')
    deadline = time.monotonic() + timeout
    with socket.create_connection((host, 502), timeout=timeout) as sock:
        transaction = 0

        def remaining():
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TimeoutError('Modbus input read timed out')
            sock.settimeout(seconds)

        def read_exact(size):
            result = b''
            while len(result) < size:
                remaining()
                part = sock.recv(size - len(result))
                if not part:
                    raise ConnectionError('Modbus connection closed')
                result += part
            return result

        def read(function, address, count):
            nonlocal transaction
            transaction += 1
            remaining()
            sock.sendall(struct.pack('!HHHBBHH', transaction, 0, 6, 1, function, address, count))
            reply_id, protocol, size, unit = struct.unpack('!HHHB', read_exact(7))
            if reply_id != transaction or protocol != 0 or unit != 1 or not 3 <= size <= 254:
                raise ValueError('Invalid Modbus response header')
            body = read_exact(size - 1)
            expected_size = count * 2 if function == 4 else (count + 7) // 8
            if (body[0] != function or body[1] != expected_size
                    or len(body) != expected_size + 2):
                raise ValueError('Unexpected Modbus read response')
            return body[2:]

        # Input-register reference 30000 is protocol offset 0. Shelly firmware
        # may encode ASCII bytes within each 16-bit register in either order.
        raw = read(4, 0, 6)
        swapped = b''.join(raw[index:index + 2][::-1] for index in range(0, len(raw), 2))
        identities = {value.decode('ascii', errors='replace').upper() for value in (raw, swapped)}
        if expected not in identities:
            raise ValueError('Modbus device identity does not match the Shelly RPC device')
        return {index: bool(read(2, 100 + index * 10, 1)[0] & 1) for index in ids}
