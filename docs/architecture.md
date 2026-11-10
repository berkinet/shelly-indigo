# Architecture

Independent implementation using Shelly's documented RPC APIs and Indigo's native
plugin API. No source from kw123/SHELLY has been incorporated.

## Design philosophy

Shelly for Indigo follows a simple design philosophy: small, lightweight software
built to meet a well-defined need. It provides direct, local communication between
Indigo and Shelly Wi-Fi devices, with a focus on discovery, state reporting and
control.

The goal is an easy-to-maintain integration with few moving parts: Indigo, the
plugin and your Shelly devices. Device setup and administration—including firmware
updates—remain the responsibility of the Shelly app or each device’s web interface.

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
- A physical worker is allowed when at least one configured channel on its unit
  has communications enabled. A unit whose configured channels are all disabled
  has no persistent worker. Discovery of unassigned units remains independent.
- Network workers do not call Indigo APIs. The plugin event loop owns connections,
  inventories and publication. UI catalogs are locked derived snapshots; trigger
  registration and credential access have their own narrow locks.

## Modules and ownership

| Module | Responsibility |
| --- | --- |
| `plugin.py` | Indigo lifecycle, thin native callbacks, channel publication and explicit creation |
| `bindings.py` | Pure physical/channel binding and display helpers |
| `inventory_report.py` | Adapter-owned physical history, whitelisted cloud merge, formatted reports and one bounded background scan |
| `cloud_inventory.py` | On-demand localhost OAuth callback and fixed HTTPS account-inventory reads; no persistent tokens |
| `catalog.py` | Locked discovery/channel catalog, lifecycle updates and detached dialog snapshots |
| `dialogs.py` | Selection, duplicate validation and staged password UI, through injected host interfaces |
| `buttons.py` | Locked native button subscriptions, validation and condition-aware dispatch |
| `connections.py` | Worker slots, physical ownership, endpoint generations and last discovery time |
| `worker.py` | One interruptible RPC session per endpoint, ordered reads/actions, heartbeat/configuration refresh |
| `rpc.py` | Frame validation, correlated replies, call deadlines, scoped digest challenges and nonce reuse |
| `commands.py` | Translation from neutral action intent to worker operation |
| `model.py` | Channel state, full snapshots versus deltas, button event deduplication |
| `credentials.py` | Thread-safe per-identity credentials and restricted legacy fallback |
| `diagnostics.py` | Component summaries and unsupported-channel warning deduplication |
| `discovery.py` | Continuous `_shelly._tcp.local.` resolution |
| `modbus.py` | Read-only Gen4 physical-input wire reads with deadline and MAC consistency check |
| `sampling.py` | One optional background sample per worker, bounded result mailbox and wakeup |

The thread-per-endpoint model is appropriate for the expected home installation.
It keeps each unit's actions serialized without making an asynchronous framework
part of the plugin's public design. Short-lived public probes may coexist during
address migration; they do not create Indigo devices.

## Identity and lifecycle

Each RPC session uses its own random source ID, stable within that session.
Sharing a source across concurrent connections can redirect firmware notifications;
independent sources let diagnostics and other Indigo instances coexist.

A worker reads public `Shelly.GetDeviceInfo` without credentials before protected
calls. An identity mismatch terminates that worker and schedules a fresh public
probe for the actual occupant. Every worker captures an endpoint generation;
queued or late messages from a retired worker are ignored.

A ready worker claims the physical identity and retires workers for its former
addresses, including workers that never completed setup. Inventories, component
summaries and catalogs for retired endpoints are removed. Last verified physical
addresses survive worker retirement, while outage deduplication uses physical ID
(or endpoint only until identity is known). A ready transition logs one address
change, or recovery after an address change when its loss was reported, before
clearing that unit's outage.
The previous saved Indigo address is a fallback on the first connection after startup.

Old endpoints are probed once for different units so an earlier Bonjour update is
not lost during a DHCP swap. A failed vacated-address probe emits `probe_failed`,
retires quietly at Debug, and never becomes an indefinitely retrying unassigned
worker, including during queue overflow. A same-identity alias stops before auth;
a different public identity finishes the probe and follows normal onboarding,
including password-required reporting and subsequent connection retries.

A typed vacated-endpoint record remembers the previous identity and whether a fresh
Bonjour hint was deferred while the verified owner was online. Deferred hints are
reconsidered if that owner later fails, permitting a genuine return to the former
address. A verified replacement clears the old marker. Endpoint ownership is removed
for any displaced identity, while its physical outage remains available for its
own eventual recovery. Worker generations continue to fence all late events.

The adapter owns a small outage record with first-failure monotonic time, latest
reason/address/identity and the last reported reason. `lossNotificationDelay` is a
validated integer number of seconds (0–86400, default 0). Saved changes are queued
to the adapter, so the UI thread never mutates pending outages. Each event-loop
turn checks deadlines after processing device messages; it does not depend on
retries or the sixty-second maintenance interval. Repeated failures do not reset
the deadline. Pending outages produce no loss/recovery log records, including at
Debug, if the unit returns before reporting. Reported outages retain normal reason
deduplication and one recovery. Offline states and command admission are immediate.
An endpoint-only outage adopts a subsequently verified physical identity without
restarting its grace period. Disable, stale-endpoint pruning and shutdown clear pending
records; IP moves preserve physical identity, while healthy address changes remain
visible. New timing regressions use an explicit clock and the real adapter loop;
existing transport, address-swap, communication and logging regressions are retained.

Unassigned offline endpoints expire after fifteen minutes without rediscovery.
Saved Indigo hosts and explicit hosts retain reconnect attempts. A powered-off
configured unit logs its first failure after the configured delay, then changes
in failure reason and recovery;
unchanged repeated failures use Debug. Outage, repeat and recovery messages identify
the physical unit using its known local name/ID and address, then list active Indigo
bindings or explicitly state that no Indigo devices are assigned. Before the first
ready snapshot, public identity metadata from the catalog can supply the label;
an unidentified failed probe uses its address. Unknown identities are not inferred
from another endpoint's catalog record.

Each concurrent-thread invocation has its own closing event. Cleanup fences and
stops workers, joins within a total deadline and marks communication unavailable.
A later invocation on the same plugin object rebuilds workers from current bindings
and explicit hosts. Old producers retain the old closing event and cannot write
into the new run.

## Communication enable/disable

`deviceStopComm` and `deviceStartComm` enqueue lifecycle changes for the owner loop.
Stopped channel IDs protect against old IOM copies still showing enabled. A
locked component policy cancels already-queued actions for stopped channels while
leaving enabled siblings' controls and the shared transport working. Disabling
the last channel fences/stops the shared worker and preserves offline selector
entries. Disabling one channel leaves any enabled sibling's worker intact; its
outage warning identifies the active Indigo channels.

Connection admission checks current binding demand for startup, explicit hosts,
Bonjour, retry and password paths. A locked identity-policy snapshot lets a worker
recognize an unknown endpoint's disabled identity after public GetDeviceInfo and
stop before authentication or status reads. Bonjour names are only hints for
skipping paused units and remembering endpoints; RPC still verifies identity before
credentials or binding changes. Re-enable uses the current physical owner/endpoint,
including when a disabled sibling's saved address is old.

A learned host-property update does not restart the already-verified worker that
published it. Genuine binding changes still run the usual stop/start lifecycle.
On concurrent-thread retry, stopped-ID hints are rebuilt from current IOM enabled
states so a lost callback cannot leave an enabled device permanently paused.

## Ordered reconciliation

`Session` dispatches frames in receive order. While a complete read is being
enriched with configuration, the worker buffers notifications
and records the position of the `Shelly.GetStatus` reply in that buffer.

For a live read, buffered notifications before the reply are published first,
then the complete snapshot, then notifications after the reply. Startup
publishes an initial ready snapshot, drops superseded earlier status deltas and
replays button events with trigger execution suppressed. Optional Modbus reads run
on a separate sampling thread. The owning RPC worker alone applies their results,
after checking connection epoch, configuration change count/cached settings and
per-input event version. Events received after submission invalidate the affected
sample; reconnects or configuration reads invalidate the whole task. Samples establish
position without firing native triggers. A failed sample never fabricates OFF.
Only one task/result slot exists per worker, and shutdown fences late completions.
The underlying read has a total network deadline; hostname resolution still depends
on the system resolver, but neither holds up RPC commands.

Full snapshots replace raw component data and remove absent channels; deltas merge
without resetting omitted fields. Input configuration is cached separately in the
worker and carried as explicit annotations into the model. All inputs, including
binary inputs, are configured on initial connection and configuration refresh.
Revision changes, configuration events and newly seen input components request a
refresh; a change received during that read remains dirty for another read. A boolean input report is positive evidence of an enabled switch input.

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
runs every fifteen seconds. Configuration with a known revision has a fifteen-minute
fallback; missing revisions or optional read failures use sixty seconds. Optional
button sampling has its own sixty-second cadence, independent of configuration.
Configuration reads remain serialized RPC work, so they can still delay commands;
optional Modbus reads no longer do. Each RPC has an eight-second total
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
no shared credential. Masked fields in all three device dialogs and the password menu provide explicit
per-device setup. Device-dialog edits are staged in a bounded in-memory store and
committed by the dialog's Save notification; validation removes plaintext from
saved device properties. Blank keeps the existing password; the explicit No password
choice clears it. Cancel discards the edit. A password save does not resume a paused
unit. The old shared-password preference field is removed; stored legacy fallback
remains for compatibility.

Digest challenges must match the checked identity; authenticated sessions reuse
nonces with increasing counters. Passwords and challenges are not logged. Remote
error messages are bounded, flattened and redact the configured password.

These checks prevent accidental credential use with unrelated discoveries; they
are not cryptographic server authentication. Device IDs, realms and Modbus MACs
can be imitated on a hostile LAN. Plain WebSocket traffic is not encrypted and
passwords in Indigo preferences are not encrypted by this plugin. Trusted-LAN
operation is the current security boundary. The owner explicitly deferred credential
storage/encryption changes on 3 November 2026; this release preserves storage and
legacy fallback behavior.

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
semantics. Four sanitized real firmware result sets are replayed through Session, Worker,
inventory and the strict Indigo adapter. Their generated transport envelopes are
explicitly distinguished from recorded HTTP result bodies. Physical controls and
external changes passed on the selected Pro outputs, with live Gen4 button events
and startup sampling. A subsequent installed-plugin Pro 4PM power cycle verified
shared-channel Offline/recovery, repeat-warning suppression and rejection without
replay of an offline command. Metering accuracy, network-only isolation and DHCP
reassignment remain in the hardware matrix. Broader components and
performance tuning should follow that evidence. See [hardware validation](hardware-validation.md).

## Focused suite review — 3 November 2026

The revision cache, asynchronous sampler and service extraction prompted this review;
advance scope notice was given to the owner. Existing schema, state ordering,
credentials, communication-off, trigger and lifecycle regressions remain relevant
and were retained. Synchronous-sample tests now wait for asynchronous completion and
still verify both physical positions, unknown-on-failure and startup ordering.
New coverage protects revision-triggered/fallback reads, events during configuration,
optional-read retry without a hot loop, commands during a deliberately blocked
sample, stale-result fences and source isolation. Captured firmware payloads protect
actual component/null/name shapes. Release/capture boundary tests cover successive
beta tags and privacy/allowlist behavior. No tests were removed solely for age or
passing consistently; the complete local suite remains a few seconds.

## Focused lifecycle test review — 9 November 2026

The physical recovery and finite-probe changes prompted a focused lifecycle review;
advance notice was given before the review. Existing same-address recovery,
communication-off, generations, swaps, overload and authentication regressions
remain relevant and were retained. New coverage reproduces failed-old-IP to
successful-new-IP recovery, one log per transition, unchanged Indigo IDs/names,
quiet finite probes, old hints while online, deferred return, verified address
reuse, protected replacements, disabled siblings and probe queue overflow.
A real loopback WebSocket/event-loop test also moves one simulated identity between
two endpoints, closes the former transport, verifies probe retirement, and confirms
that a subsequent command reaches only the new endpoint. This is automated
transport evidence; it does not claim a repeat of the owner's physical DHCP move
using the new release.

## Inventory reporting and account authorization

`DeviceHistory` belongs to the adapter loop. Verified identification records local
contact and metadata; ready/status/event messages mark the unit online and advance
last-seen time. Offline/retirement removes online state without replacing its date.
Records follow physical identity across IP changes and are serialized through an
explicit metadata whitelist to `deviceHistory`, never with online flags or cloud
responses. Periodic maintenance, report completion and shutdown flush dirty history.
Restart does not infer availability from a saved timestamp. Pre-feature bindings
are included with Never when there is no retained local observation.

The menu queues a request; one `InventoryJob` browses Bonjour for five seconds and
optionally performs browser authorization/cloud reads. It emits only whitelisted
report rows or fixed credential-free messages. A request generation fences queued
results after cancellation, a replacement report or concurrent-thread restart.
The existing adapter verifies newly
announced endpoints and enforces communication policy. Neither discovery nor cloud
metadata operates an output. Reports merge on full MAC/physical ID, never reused IPs.
Only the adapter formats/logs the final table and touches Indigo preferences.
Current Indigo device names are attached to the report snapshot by physical binding,
including disabled channels. They are sorted/deduplicated within each physical row,
kept separate from the Shelly name and never stored in device history. Renaming an
Indigo device therefore changes the next report without a plugin restart.

The OAuth listener is temporary, bound to 127.0.0.1 on a random port with a random
path and state. Callback bodies/request lines are bounded, connections time out,
access/error logging is suppressed and responses disable caching/referrers. A
nonce-authorized script clears the callback query from the browser history entry.
The listener ends on completion, denial, cancellation or five-minute timeout. JWT
claims are parsed only to find the designated cloud server; they are not accepted
as cryptographic proof. Routing is restricted to HTTPS `*.shelly.cloud` hostnames
with no credentials, path/query or nonstandard port. HTTPS validation remains on;
redirects are refused so tokens cannot follow a Location to another destination.
Only `/oauth/auth` (exchange) and `/device/all_status?show_info=true&no_shared=true`
(read) are called. Network responses are bounded and failures never include URLs,
response bodies or exceptions containing codes/tokens. No token refresh/caching,
credential storage changes, new dependencies or hosted web service are introduced.

The focused review for this feature retained connection/recovery regressions and
updated creation-menu selectors to use their command ID rather than menu position.
New regressions cover persistence, identity merges/address reuse, cloud-only Never,
status independent of cloud, optional scans, single-job admission/cancellation,
callback state/path validation, host restrictions, expiry and credential redaction.
Real-account authorization and native dialog evidence are recorded separately from
the loopback/simulated test evidence.

## API references

- [Shelly RPC and notifications](https://shelly-api-docs.shelly.cloud/gen2/General/Notifications/)
- [Shelly authentication](https://shelly-api-docs.shelly.cloud/gen2/General/Authentication/)
- [Shelly Input](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Input/)
- [Shelly Light](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Light/)
- [Indigo lifecycle](https://docs.indigodomo.com/2025.2/plugin-dev/guide/)
- [Indigo configuration fields](https://docs.indigodomo.com/2025.2/plugin-dev/reference/xml/configui/text-field/)
