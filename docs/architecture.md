# Architecture

Independent implementation using Shelly's documented RPC APIs and Indigo's native
plugin API. No source from kw123/SHELLY has been incorporated.

## Scope and invariants

Indigo 2026.1 / API 3.9 / Python 3.13. New work targets Gen3/Gen4 RPC units;
existing Gen2 compatibility remains. Gen1 and new Gen2-specific work are off the
roadmap. Discovery is IPv4 Bonjour plus explicit hosts. Devices must already be
provisioned onto the LAN.

- Persistent identity is Shelly device ID plus component, never IP address.
- Discovery and polling never create Indigo devices. Creation is explicit.
- Existing Indigo IDs, names and valid bindings survive upgrades and address moves.
- Output state comes from reports, not command intentions. Unknown input position
  is separate from transport failure. Missing readings never become fabricated OFF.
- Queued controls are bounded, expire after ten seconds, and are discarded on
  disconnect. An uncertain transmitted action is never automatically replayed.
- Network workers do not call Indigo APIs. The plugin event loop owns connections,
  inventories and publication. UI catalogs are locked derived snapshots; trigger
  registration and credential access have their own narrow locks.

## Modules and ownership

| Module | Responsibility |
| --- | --- |
| `plugin.py` | Indigo lifecycle, callbacks, channel publication, explicit creation, UI and trigger adapter |
| `connections.py` | Worker slots, physical ownership, endpoint generations and last discovery time |
| `worker.py` | One interruptible RPC session per endpoint, ordered reads/actions, heartbeat/configuration refresh |
| `rpc.py` | Frame validation, correlated replies, call deadlines, scoped digest challenges and nonce reuse |
| `commands.py` | Translation from neutral action intent to worker operation |
| `model.py` | Channel state, full snapshots versus deltas, button event deduplication |
| `credentials.py` | Thread-safe per-identity credentials and restricted legacy fallback |
| `diagnostics.py` | Component summaries and unsupported-channel warning deduplication |
| `discovery.py` | Continuous `_shelly._tcp.local.` resolution |
| `modbus.py` | Optional bounded, read-only Gen4 physical-input sample with MAC consistency check |

The thread-per-endpoint model is appropriate for the expected home installation.
It keeps each unit's actions serialized without making an asynchronous framework
part of the plugin's public design. Short-lived public probes may coexist during
address migration; they do not create Indigo devices.

## Identity and lifecycle

A worker reads public `Shelly.GetDeviceInfo` without credentials before protected
calls. An identity mismatch terminates that worker and schedules a fresh public
probe for the actual occupant. Every worker captures an endpoint generation;
queued or late messages from a retired worker are ignored.

A ready worker claims the physical identity and retires workers for its former
addresses, including workers that never completed setup. Inventories, component
summaries and catalogs for retired endpoints are removed. Old endpoints are
re-probed for different units so an earlier Bonjour update is not lost during a
DHCP swap. These probes exclude the just-moved identity before authentication,
preventing alternate-address ping-pong.

Unassigned offline endpoints expire after fifteen minutes without rediscovery.
Saved Indigo hosts and explicit hosts retain reconnect attempts. A powered-off
configured unit logs its first failure, changes in failure reason and recovery;
unchanged repeated failures use Debug.

Each concurrent-thread invocation has its own closing event. Cleanup fences and
stops workers, joins within a total deadline and marks communication unavailable.
A later invocation on the same plugin object rebuilds workers from current bindings
and explicit hosts. Old producers retain the old closing event and cannot write
into the new run.

## Ordered reconciliation

`Session` dispatches frames in receive order. While a complete read is being
enriched with configuration or a Modbus sample, the worker buffers notifications
and records the position of the `Shelly.GetStatus` reply in that buffer.

For a live read, buffered notifications before the reply are published first,
then the complete snapshot, then notifications after the reply. A later physical
sample is applied last, while live button events still reach triggers. Startup
publishes an initial ready snapshot, drops superseded earlier status deltas and
replays button events with trigger execution suppressed. A later startup sample
supersedes buffered button position.

Full snapshots replace raw component data and remove absent channels; deltas merge
without resetting omitted fields. Input configuration is cached separately in the
worker and carried as explicit annotations into the model. All inputs, including
binary inputs, refresh configuration. Configuration revision changes accelerate
refresh. A boolean input report is positive evidence of an enabled switch input.

UTC timestamps are not ordering tokens. A bounded event fingerprint history filters
exact retransmissions within a clock epoch; backwards clock steps reset that history.
Connection generations reject old producers. Null button RPC status preserves a
known physical position; failed optional samples preserve uncertainty.

## Command scheduling and liveness

A socket pair wakes the worker's `select` wait when a command arrives or shutdown
starts. One worker executes actions in queue order. Relative dimmer intent remains
relative in the adapter, then reads the light's actual status and computes its
bounded target at execution time. Each output command is followed by a targeted
component status read, including on firmware that omits notifications. Configuration
and Modbus work are not part of that ordinary command path.

Requested full status reads coalesce per physical device. A lightweight status read
runs every fifteen seconds; configuration and optional samples refresh every sixty
seconds or after observed revision changes. Each RPC has an eight-second total
response deadline, including an authentication retry. These are scheduled intervals,
not a hard global latency guarantee while other RPC work is in progress.

The plugin queue holds 1,024 events; worker command queues hold 32 controls. UI
admission waits at most 100 ms before logging rejection. A blocked worker publication
records overload, aborts the connection, and lets the owner fence/rebuild it. This
makes loss visible; it cannot guarantee physical event delivery during overload.

## Credentials and trust

Per-device password overrides are keyed by physical ID in Indigo preferences. A
blank override means no password. The legacy shared credential applies only to
existing bindings or explicit hosts. A new unassigned Bonjour responder receives
no shared credential. The password menu provides explicit per-device setup.

Digest challenges must match the checked identity; authenticated sessions reuse
nonces with increasing counters. Passwords and challenges are not logged. Remote
error messages are bounded, flattened and redact the configured password.

These checks prevent accidental credential use with unrelated discoveries; they
are not cryptographic server authentication. Device IDs, realms and Modbus MACs
can be imitated on a hostile LAN. Plain WebSocket traffic is not encrypted and
passwords in Indigo preferences are not encrypted by this plugin. Trusted-LAN
operation is the current security boundary.

## Verification and remaining work

The suite retains state-schema, loader, switch/input/dimmer, binding, trigger,
Modbus and no-replay regressions. Shared host fixtures and a strict loopback peer
exercise the real worker, event loop and Indigo adapter together. Unknown simulator
methods fail; all event waits have total deadlines. Successful authentication,
notification ordering, rapid commands, configuration transitions, DHCP swap
sequencing, overload and same-object thread recovery have focused coverage.

CI runs Python 3.13 on Ubuntu and macOS with the shipped pure-Python dependencies.
The source and archive checker validates imports under an Indigo-style loader,
documented XML attributes/callbacks, dependency versions and content hashes. Release
gates require a clean pushed commit, successful CI, fresh build and manifest.

The host double cannot establish native Indigo dialog rendering or all IOM cache
semantics. Physical control, external-change notifications, metering and actual
network interruption behavior require the hardware matrix. Broader components and
performance tuning should follow that evidence. See [hardware validation](hardware-validation.md).

## API references

- [Shelly RPC and notifications](https://shelly-api-docs.shelly.cloud/gen2/General/Notifications/)
- [Shelly authentication](https://shelly-api-docs.shelly.cloud/gen2/General/Authentication/)
- [Shelly Input](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Input/)
- [Shelly Light](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Light/)
- [Indigo lifecycle](https://docs.indigodomo.com/2025.2/plugin-dev/guide/)
- [Indigo configuration fields](https://docs.indigodomo.com/2025.2/plugin-dev/reference/xml/configui/text-field/)
