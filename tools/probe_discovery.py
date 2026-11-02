"""Read-only network check using bundled discovery and RPC; never sends controls."""
from pathlib import Path
import argparse
import queue
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Shelly.indigoPlugin/Contents/Server Plugin'),
                str(ROOT / 'Shelly.indigoPlugin/Contents/Packages')]
from shelly.discovery import Discovery
from shelly.worker import Worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--read-status', action='store_true', help='Also read device identity and status')
    args = parser.parse_args()
    addresses = queue.Queue()
    browser = Discovery(addresses.put)
    try:
        time.sleep(args.seconds)
    finally:
        browser.close()
    found = set()
    while not addresses.empty():
        found.add(addresses.get_nowait())
    print(f'Discovered {len(found)} endpoints', flush=True)
    if not args.read_status:
        for address in sorted(found):
            print(address)
        return
    events = queue.Queue()
    workers = [Worker(address, lambda *event: events.put(event)) for address in sorted(found)]
    pending = set(found)
    try:
        for worker in workers:
            worker.start()
        deadline = time.monotonic() + 12
        while pending and time.monotonic() < deadline:
            try:
                kind, address, payload = events.get(timeout=0.5)
            except queue.Empty:
                continue
            if address not in pending:
                continue
            if kind == 'ready':
                info, status = payload
                components = ', '.join(sorted(key for key in status if ':' in key))
                print(f'{address}: Gen{info["gen"]} {info.get("model", "")} [{components}]', flush=True)
                pending.remove(address)
            elif kind == 'offline':
                print(f'{address}: unavailable ({payload})', flush=True)
                pending.remove(address)
    finally:
        for worker in workers:
            worker.stop()
        for worker in workers:
            worker.join(10)
    if pending:
        print(f'{len(pending)} endpoints did not respond in time')


if __name__ == '__main__':
    main()
