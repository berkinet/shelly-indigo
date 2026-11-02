# Project review — 2 November 2026

Reviewed commit: `d7ad3d396f6aaca1b58b215e26d02fb23e34751e` on `main`.
Runtime version: 0.1.12; public release: `v0.1.12-alpha.1`.

## Assessment

This is a useful early alpha with a sound basic decomposition and insufficiently
rigorous state and recovery semantics. It is not ready to be described as a
production-quality integration. Keep the transport/model/Indigo separation, but
pause feature expansion while the defects below are corrected.

The main weakness is not Python style or the choice of threads. It is that the
implementation handles individual messages reasonably well without consistently
handling sequences of messages, configuration changes and connection lifetimes.
Those are central requirements for a home automation integration.

Five behavioral defects were reproduced offline using production code, synthetic
RPC frames and the existing Indigo host double. A sixth finding is a documented
Indigo XML contract mismatch. These findings do not depend on interpreting the
owner's earlier discovery problems as plugin failures; some earlier symptoms had
other causes, including a copied device identity and an offline device.

No live hardware was controlled, no live Indigo device was modified, and no
runtime fix or new release is part of this review.

## Findings

Priority P1 means a correctness defect to fix before further feature releases.
P2 means a significant defect to address during the same stabilization work.
Source paths and line numbers below refer to the reviewed commit. Paths starting
with `Server Plugin/` are under `Shelly.indigoPlugin/Contents/`.

### 1. P1 — A status refresh can overwrite a newer live state

Locations: `Server Plugin/shelly/worker.py:44`, `:63`, `:69`, `:142`, `:149`;
`Server Plugin/shelly/rpc.py:44`.

`_read_status()` obtains a full `Shelly.GetStatus` response, then makes additional
RPC calls to enrich input configuration. `Session` delivers notifications received
while those calls wait immediately. Once enrichment finishes, the worker publishes
the older status object after those newer notifications.

Reproduction used the real `Session` and `Worker._read_status()` with these frames:

1. `Sys.GetConfig` returns an empty local-name configuration.
2. `Shelly.GetStatus` returns `switch:0.output = false` and a null-state input.
3. While waiting for `Input.GetConfig`, a live notification reports
   `switch:0.output = true`.
4. `Input.GetConfig` returns, and the worker publishes the earlier status snapshot.

Observed publication order: **true, false**. The Indigo double ends **OFF** even
though the newer device report says **ON**. A subsequent notification or periodic
read may correct it, but automations can already have acted on the false change.
This affects a physical unit with both an output and a null-state input.

Fix direction: establish an explicit snapshot boundary and message ordering rule.
A delayed snapshot must not overwrite updates received after that snapshot.
Account for startup buffering as well as periodic and post-command reads; simply
replaying every buffered notification after every snapshot is not sufficient when
a notification preceded the snapshot. Add regression scenarios on both sides of
the boundary. Keep the separate Modbus sampling boundary explicit too.

### 2. P2 — Address reuse can leave discovery permanently stuck

Locations: `Server Plugin/plugin.py:73`, `:210`;
`Server Plugin/shelly/worker.py:109`.

A worker pins the first accepted Shelly identity. Rejecting a different device at
that address is the right safety behavior. However, the error tells the user
rediscovery is required while `_connect()` ignores every rediscovery for an
address already present in `self.workers`.

Reproduction: a worker expecting A receives device information for B at the same
address. It rejects B. A subsequent discovery event for that address creates
**zero** replacement workers and leaves the expected identity set to A.

A DHCP address swap can therefore leave both workers rejecting their new address
occupants indefinitely. The existing DHCP move test covers a device moving to a
fresh address; it does not cover this case. A fresh-address move can work while
address reuse remains broken.

Fix direction: manage connection ownership by physical identity, with addresses
as replaceable endpoints. Preserve rejection of commands for the wrong identity,
but provide an explicit identity-mismatch recovery path. Identify each connection
incarnation so delayed messages from retired workers cannot claim ownership again.
Test address swaps, replacement devices, stale disconnects and repeated Bonjour
updates, not only a move to an unused address.

### 3. P2 — Rapid relative dimmer commands lose adjustments

Location: `Server Plugin/plugin.py:195`.

The adapter converts Brighten By and Dim By into absolute targets immediately,
using the last reported brightness. The worker executes those requests later.
Multiple relative requests admitted before the first acknowledgement use the same
starting value.

Reproduction: starting at **35%**, issue Brighten By 10 twice before processing a
new report. The adapter sends `Light.Set(45)` followed by `Light.Set(45)`.
The intended final target is **55%**. A held button or automation issuing several
steps can therefore appear to ignore commands.

Fix direction: preserve relative intent until ordered execution, or maintain a
clearly defined pending target reconciled with command results and external
changes. Preserve the current rule that displayed actual state comes from device
reports. Define failure semantics; blindly accumulating optimistic actual state
would trade this defect for another one.

### 4. P2 — Re-enabling a binary input leaves it unavailable

Locations: `Server Plugin/shelly/worker.py:65`;
`Server Plugin/shelly/model.py:36`, `:80`.

For null-state inputs, the worker reads configuration and annotates `_enabled`.
For boolean-state inputs, it skips that configuration entirely. Inventory updates
merge fields, retaining previous annotations.

Reproduction:

1. A disabled switch input returns `state = null`; configuration returns
   `type = switch`, `enable = false`.
2. After the input is enabled externally, a later status returns `state = true`.
3. The worker skips configuration because the state is boolean, leaving the old
   `_enabled = false` in the inventory.

Observed: raw state is **true**, but `supported = false` and
`stateKnown = false`. The channel remains absent from the creation catalog until
its inventory is rebuilt, normally on reconnect. The regular poll does not repair
it. Shelly documents disabled-input state as null, so this is a valid transition,
not just malformed simulated data. [Shelly Input API](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Input/).

Fix direction: separate configuration from status, define how configuration is
refreshed or invalidated, and make enable/type transitions replace stale metadata.
Test disabled-to-enabled and switch-to-button transitions through the worker and
inventory together.

### 5. P2 — Indigo's concurrent-thread recovery cannot recover this plugin

Locations: `Server Plugin/plugin.py:101`, `:127`, `:62`.

The concurrent thread's `finally` block sets `self.closing`, stops all workers and
leaves them in `self.workers`. A subsequent invocation neither clears the closing
flag nor rebuilds that worker registry.

Reproduction injected an unexpected exception from the loop's sleep call, then
invoked `runConcurrentThread()` again on the same plugin object, as Indigo's
recovery contract permits. The old worker was stopped; the second invocation
created no replacement; `_emit()` then silently accepted **no** new event because
`closing` was still set.

This is distinct from a full plugin disable/enable, which creates a fresh process
and can restore operation. The failure requires an unexpected concurrent-thread
exit; it is not claimed to occur on every normal restart. Indigo documents that
it automatically calls the method again after an uncaught exception.
[Indigo plugin lifecycle guide](https://docs.indigodomo.com/2025.2/plugin-dev/guide/).

Fix direction: define initialization and cleanup for each thread invocation,
including stopped workers, pending events and discovery subscriptions, or fail in
a way that requests a full plugin restart. Make the selected recovery path
explicit and test it with the same plugin object.

### 6. P2 — The device dialog uses the wrong documented read-only attribute

Locations: `Server Plugin/Devices.xml:19`, `:82`, `:145` and the following detail
fields for each device type.

Fifteen fields use `readOnly="true"`. Indigo documents **`readonly="true"`**.
XML attribute names are case-sensitive. The XML parser tests only establish that
the document is well formed; they do not validate this host-specific contract.
[Indigo text-field reference](https://docs.indigodomo.com/2025.2/plugin-dev/reference/xml/configui/text-field/).

The implementation rejects a new binding entered only in the detail fields.
Presenting those same fields as editable is consequently misleading, and is
consistent with the owner's experience of entering values that Save rejected.
The documentation mismatch is confirmed. Whether this Indigo build recognizes an
undocumented camel-case alias was not tested against a native dialog during this
review, so that runtime detail remains unverified.

Fix direction: use the documented attribute and validate the dialog in Indigo.
Make any intended manual-configuration workflow a separate deliberate feature;
currently the supported path requires selecting a discovered channel.

## Systems design

### Keep these decisions

- Local RPC/WebSockets with no cloud account dependency fits the purpose.
- Separate transport, domain model, discovery, Modbus reader and Indigo adapter
  modules provide useful boundaries. There is no justification here for a rewrite
  or an asynchronous framework migration.
- One worker per physical device is reasonable for this installation. Serialized
  RPC, bounded queues and discarding commands on disconnect are valuable defaults.
- Physical identity plus component is the right persistent binding. Existing
  Indigo IDs and user names are preserved, and duplicate bindings are rejected.
- Continuous discovery is separated from explicit device creation. Unassigned
  channels can remain available even when another channel on the unit is bound.
- Actual output state is report-driven; missing readings are generally not
  fabricated. Button position uncertainty is explicitly represented.
- The optional Modbus path is bounded, read-only and identity checked. It does
  not silently change hardware settings to obtain a startup state.
- Installable source and release bundles include portable dependencies and their
  licenses. Testing the unusual Indigo loader without `__file__` is valuable.

### Change these structural weaknesses

**The registry is fragmented.** Workers, device information, inventories, physical
menu entries and channel menu entries have different ownership and lifetime rules.
Persistent bindings use physical IDs, but much of the operational state uses
addresses. Add one coherent connection/identity lifecycle rather than more
special-case dictionary cleanup. UI catalogs should remain derived snapshots.

**State messages lack sufficient meaning.** Full reads and partial notifications
share a merge path. That is useful for deltas but leaves no general reconciliation
rule for removed components or fields. Configuration annotations are mixed into
raw status; sampled input state and event-derived input state have implicit
precedence rules. Preserve these distinctions in the model. The stale enabled
flag and stale poll publication are demonstrated consequences. Component-removal
handling is an additional design gap; no supported physical removal scenario was
validated during this review.

**Command intent is resolved too early.** Relative adjustments are computed
outside the worker's ordered execution. Every command also incurs a full status
read, local-name lookup and potentially several input/Modbus reads; an idle listen
can then wait another half-second. Correctness comes first, but this is also a
latency and backlog concern. Separate configuration refresh from output-command
reconciliation, and measure response time with realistic device mixes.

**Backpressure and recovery are implicit.** The shared event queue blocks all
producers when full. That avoids silent event loss but can stall RPC processing
and discovery resolution. Status updates may be coalescible; physical button
transitions require a different policy. Define overload behavior, shutdown and
recovery before adding higher-rate sensors. This is a risk identified by code
inspection, not a reproduced queue-overload failure.

**The Indigo adapter carries too many responsibilities.** It owns discovery,
registry reconciliation, model publication, command translation, binding
validation, catalogs, triggers and preferences. Extract the registry and command
semantics first. Moving functions into extra files without clarifying ownership
would provide little benefit. The repeated XML for three device types also makes
contract mistakes easy to copy.

**Diagnostics still omit useful causes.** Transport failures often become just an
exception class, and non-authentication RPC errors discard the server message.
Provide operation, physical identity, endpoint, useful error details and recovery
state without credentials or raw sensitive payloads. Rate-limit repeated unchanged
outage warnings. Info/Debug selection itself is a reasonable current interface.

## Test-suite review

The existing **77 tests passed**. Source-bundle and existing 0.1.12 archive checks
also passed. These results establish a working baseline, not completeness.

Useful coverage includes strict checks against actual XML state definitions,
loader execution without `__file__`, real localhost WebSocket framing, command
replay prevention, identity rejection, partial metering, button transitions,
conditional trigger execution, duplicate bindings and Modbus wire failures.
Retain that coverage. No test was deleted or changed during this review.

The central gap is **sequences across boundaries**. Most adapter tests call
`_handle()` directly with convenient preconstructed payloads. Worker tests mostly
end at queued messages. The real WebSocket tests do not complete the whole path
through the plugin event loop and Indigo publication. This allowed all five
reproduced behavioral defects to coexist with a green suite.

Specific improvements, ordered by value:

1. Turn the five reproductions into behavioral regression tests and fix the
   underlying contracts. Include startup and live snapshot ordering, rapid mixed
   commands, address swaps, configuration transitions and thread recovery.
2. Add a small end-to-end simulated harness connecting the real worker, event
   loop and a stricter Indigo double. Deliberately interleave messages and inject
   disconnects at defined boundaries. Keep all waits bounded by total deadlines.
3. Tighten the simulator: unknown RPC methods currently return a plausible status
   object, even for a configuration call. Fail unknown methods and return realistic
   method-specific responses. Include a successful authenticated exchange through
   this path; existing digest/rejection tests do not establish that integration.
4. Improve fixture ownership. `test_dimmer.py` manually instantiates another
   `TestCase` and invokes its `setUp()`. Extract reusable host fixtures instead.
   The fake Indigo device mutates in place and cannot establish real host cache,
   callback or dialog behavior; document and probe those limits.
5. Add narrow XML contract checks for attributes and callback references, plus a
   native Indigo smoke checklist. Parsing XML alone missed `readOnly`. The bundle
   checker verifies loader/import packaging; it does not validate the native UI.
6. Add a CI job timeout and total deadlines to event-wait loops. The dimmer
   WebSocket test can keep receiving wrong messages indefinitely despite having
   a timeout on each individual queue read. Consider a macOS Python job for the
   shipped platform; it still cannot replace a native Indigo check.

Do not pursue a larger test count or prune passing regressions merely because
these gaps exist. New tests should assert externally meaningful results and
recovery, not freeze incidental RPC call order or repeat implementation formulas.

## Implementation and delivery quality

The code is compact and understandable, the dependency footprint is restrained,
and the packaging discipline is better than the state/recovery discipline.
There is real regression work here; calling the suite worthless would be wrong.
However, green tests have been given more weight than their coverage justifies.

Development has been reactive to individual symptoms. A local correction often
arrived before its wider state-machine implications were examined. The unknown
button state, discovery-versus-creation menus, and channel/type mismatch work show
why requirements and host contracts should be written down before another patch.
The interaction should also have identified exactly which dialog was being used
earlier, rather than repeatedly changing nearby selector behavior. That is a
weakness in my engineering and diagnosis, not a burden the owner should absorb.

The existing hardware log is appropriately cautious, but historical entries need
clear current status. Earlier claims that dimmers were unsupported are historical,
while physical dimmer output validation is still not documented as complete.
The owner later reported that the startup input behavior worked; that confirmation
should be captured with its tested version and conditions where available.
Separate user-observed operation, read-only probes, simulations and unvalidated
claims. Successful packaging does not establish physical control correctness.

Keep the alpha designation. Before broader distribution, record a short hardware
matrix for available Gen3/Gen4 units covering creation/edit, physical input events,
output control, external changes, metering and network recovery. Retain existing
Gen2 regression support; do not revive Gen1 work. Per-device credentials remain
an acknowledged unfinished requirement, not an unexpected newly discovered defect.

For delivery, run the behavior suite, source-loader check and freshly built archive
check against one commit before tagging. Associate the release asset with that
commit and verify the upload. The current workflow builds artifacts on pushes but
does not enforce this relationship for manually published releases. No new release
is justified solely by this review document.

## Recommended next work

1. Correct snapshot ordering and define status/configuration precedence.
2. Repair identity/address ownership and concurrent-thread recovery.
3. Correct relative command sequencing and input re-enable reconciliation.
4. Correct the XML attribute and run a native Indigo dialog smoke check.
5. Strengthen the simulator/integration boundaries, then perform the focused
   hardware validation and publish one stabilization release.
6. Resume component expansion and per-device credentials after those gates pass.

The project is salvageable without a rewrite. Its next milestone should be
trustworthy state and recovery under ordinary change, rather than a longer list
of supported component names.

## Verification record

Commands run from the repository root with the existing virtual environment:

```zsh
.venv/bin/python -m unittest discover -s tests
.venv/bin/python tools/check_bundle.py --source
.venv/bin/python tools/check_bundle.py
```

All passed. Separate offline probes invoked production `Session`,
`Worker._read_status()`, `Worker.run()`, adapter `_handle()` and
`runConcurrentThread()` with the existing test fixtures. Their observed results:

```text
Poll/notification ordering: published [true, false]; expected final true
Relative brightness: start 35; targets [45, 45]; expected final target 55
Input re-enable: raw true; cached enable false; supported false; known false
Identity mismatch rediscovery: replacement worker count 0; still pinned to A
Concurrent-thread retry: closing true; replacement count 0; new event not accepted
```

These are deterministic simulated failures. Their frequency on the owner's
network was not measured. The XML mismatch was checked against official Indigo
documentation; its native runtime behavior remains a separate verification step.
