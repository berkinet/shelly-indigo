# Review follow-through — 0.2.0 beta

This records the implementation response to the project review and the supplied
independent review. The original review remains a historical assessment of commit
`d7ad3d396f6aaca1b58b215e26d02fb23e34751e`.

| Finding / agreed proposal | Implementation | Evidence |
| --- | --- | --- |
| Delayed full read overwrites newer notification | Explicit reply boundary, prefix/snapshot/suffix publication, separate later physical sample | `test_stabilization` covers both sides of the boundary and input sampling |
| Address reuse, DHCP swaps, old worker messages | Connection registry, per-worker generation, fresh public mismatch probes, excluded-identity probes of vacated addresses | Registry regressions cover swaps after earlier Bonjour events, never-connected workers and late old messages; actual WebSocket alias rejection |
| Concurrent-thread retry remains closed | Per-invocation closing event; old producers fenced; worker registry rebuilt | Same-object failure/retry test checks event admission during the second invocation |
| Relative dimmer increments collapse | Keep relative intent until ordered worker execution and read the actual light there | Full event-loop/WebSocket test sends 35, +10, +10, -5 and observes targets 35, 45, 55, 50; also checks limits |
| Re-enabled or retyped inputs retain stale configuration | Refresh every input's configuration and observe configuration revision changes | Worker and full-loop disabled/enabled/type-transition regressions |
| UTC correction suppresses buttons | Receive-order semantics; bounded duplicate history reset on a backwards clock step | Native trigger adapter test for press at 100/release at 90; repeated timestamps after rollback |
| Wrong `readOnly` XML spelling | Documented `readonly` attribute; source/archive contract checks | All three device types checked; native rendering remains unverified |
| Up to 500 ms idle command delay | Socket-pair wakeup of `select` | Real idle-worker wakeup test and separate 30-command loopback measurement |
| Expensive full configuration/Modbus read after every action | Targeted output verification; coalesced requested reads; separate configuration schedule | Real socket test checks command path; full-loop relative-action test |
| Silent half-open connection | Fifteen-second lightweight reconciliation/heartbeat with bounded RPC response deadline | Real socket peer stops replying; scaled interval verifies offline detection |
| Repeated outages, stale workers and opaque command errors | Unchanged outages move to Debug; recovery logged; stale unassigned workers expire; structured command errors identify bindings | Outage, stale-retirement and no-replay regressions |
| Unscoped shared credential / arbitrary realm | Per-device password menu; restricted legacy fallback; public identity before authentication; realm consistency check and nonce reuse | Real protected discovery receives no digest until explicit individual setup; successful authenticated control; mismatched realm gets none |
| Shared test assumptions and permissive simulator | Reusable host double; strict method-specific peer; real worker → event loop → adapter scenarios; total test deadlines | Existing useful regressions retained; changed UTC expectations no longer require the bug |
| Linux-only CI and weak release association | Ubuntu/macOS matrix; action commits and runtime archive hashes pinned; dependency content manifest; clean-commit release gate with archive/commit manifest | Local source/archive checks; remote CI and release gate required before publication |
| Documentation drift | Current-state README/architecture; historical hardware notes separated from current beta matrix | Hardware status explicitly distinguishes owner observations, read-only probes, simulations and unperformed checks |

## Responsiveness measurement

On this development Mac, thirty sequential `Switch.Set` requests to the strict
localhost simulator measured worker queue admission to simulator receipt at
**0.07–0.56 ms, median 0.12 ms**. The harness matched requests rather than consuming
stale status events. This excludes the Indigo callback/event-loop tick, physical
LAN latency and hardware response time. It demonstrates removal of the former
fixed idle wait; it is not a physical switch latency claim.

## Test maintenance at the beta milestone

Retained the useful schema, loader, identity, command-discard, switch/input/dimmer,
trigger and Modbus regressions. Updated the old assumption that backwards UTC
must be rejected. Extracted shared Indigo fixtures instead of invoking another
test case's setup, made simulated device IDs monotonic, and removed the simulator's
unknown-method success fallback. No regression was removed merely for passing.

Both unit and cross-boundary tests use the same portable libraries shipped in the
plugin. New coverage focuses on state after sequences and failures. Native Indigo
rendering/cache semantics, physical control, metering accuracy, multicast behavior
on the actual LAN and physical network interruption remain hardware checks.

The dependency lock pins upstream runtime archive hashes. The content manifest
checks the committed portable result; isolated build dependencies are not fully
pinned. This is an integrity improvement, not a reproducible-build or third-party
security-audit claim. Device identity/realm checks likewise preserve a trusted-LAN
boundary; they do not cryptographically authenticate a spoofed peer.
