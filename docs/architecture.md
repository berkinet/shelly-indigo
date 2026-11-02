# Architecture and project decisions

This is an independent implementation, not a fork of kw123/SHELLY. No source
from that plugin has been incorporated. Shelly's official APIs define the wire
protocol; locally installed Indigo plugins provide examples of the host API.

## Target

Native Indigo integration with automatic discovery, channel creation and local
bidirectional communication, without Home Assistant or a cloud dependency.
Gen2–Gen4 RPC/WebSockets first; Gen1 HTTP REST and callbacks follow separately.

## Initial alpha

- Indigo 2026.1 / server API 3.9 / Python 3.13.
- IPv4 Bonjour `_shelly._tcp.local.` discovery, plus optional explicit hosts.
- One WebSocket worker per physical unit, serialized RPC calls, bounded command
  queues, exponential reconnect backoff and a 60-second reconciliation read.
- Stable Indigo identity uses Shelly device ID + component key, never IP address.
- Switches and binary inputs become separate Indigo relay/sensor devices. Switch
  metering is exposed where present. Unsupported components are ignored.
- Notifications are partial updates. Missing fields preserve known values.
- Indigo calls and discovery reconciliation happen in the concurrent plugin
  thread; actions and network workers communicate with it through an event queue.
- Control state is updated from device reports, never optimistically. Commands
  are discarded at disconnect and are never automatically replayed.
- Optional shared admin password with SHA-256 RPC challenge authentication.
  Passwords stay in Indigo preferences and are never logged or committed.
- Preference changes require a plugin restart.
- The repository's plugin bundle includes unmodified portable dependencies.
  Source installation and release installation follow the same dependency path;
  startup checks for missing libraries before starting network workers.

## Following milestones

Hardware validation; per-device credentials; lights/dimmers, covers and standalone
meters; button event triggers; Gen1 discovery/REST/callback listener; sleeping
sensors, IPv6 and broader component coverage. Gen4 support here means devices
running the Shelly Wi-Fi/Ethernet RPC firmware, not Zigbee-only operation.

## Sources

- https://shelly-api-docs.shelly.cloud/gen2/General/RPCChannels/
- https://shelly-api-docs.shelly.cloud/gen2/General/RPCProtocol/
- https://shelly-api-docs.shelly.cloud/gen2/General/Authentication/
- https://shelly-api-docs.shelly.cloud/gen2/General/Notifications/
- https://shelly-api-docs.shelly.cloud/gen2/General/mDNS/

## Releases

The owner authorizes committing, pushing and publishing releases with an
installable plugin archive and release notes without repeated confirmation.
Do not install or enable this alpha in the live Indigo server as a side effect
of building or publishing it. Record hardware testing status honestly.
