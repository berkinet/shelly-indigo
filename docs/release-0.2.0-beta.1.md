# Shelly 0.2.0 beta 1

Stabilization following the independent project reviews. Discovery remains
continuous and device creation remains explicit. Existing Indigo names, IDs,
channel bindings and trigger bindings are preserved; devices need not be deleted.

## Correctness and recovery

- Keep newer notifications from being overwritten by an older status read.
  Distinguish full snapshots from deltas and retire absent channels.
- Handle identity changes, DHCP swaps and vacated addresses with public re-probes,
  connection generations and cleanup of workers that never completed setup.
- Rebuild communication after Indigo retries a failed concurrent thread on the
  same plugin object. Reject messages from retired workers.
- Execute relative dimmer actions in worker order against actual device status.
- Reconcile input enable/type changes and preserve button operation across UTC
  clock corrections. Exact duplicate tracking is bounded by clock epoch.
- Correct the documented `readonly` UI attribute for connection detail fields.

## Responsiveness, diagnostics and credentials

- Wake idle workers immediately for commands. Verify only the affected output
  after ordinary controls; keep configuration and Modbus reads off that path.
- Coalesce status requests per physical unit. Use fifteen-second lightweight
  reconciliation/heartbeat and sixty-second configuration refresh, accelerated by
  observed configuration revision changes.
- Bound command age and queue admission. Report overload and reset communication
  instead of indefinitely blocking worker publications; discard queued controls.
- Deduplicate unchanged outage warnings, log recovery and identify failed command,
  endpoint and mapped Indigo device. Preserve useful sanitized RPC error details.
- Add **Set Device Password…** for each physical unit. Retain legacy shared
  fallback for existing bindings and explicit hosts; do not send it automatically
  to newly discovered, unassigned devices. Check identity before authentication,
  validate challenge realms and reuse authenticated nonces with increasing counts.

A password setting changes the plugin's stored credential, not the Shelly itself.
Blank explicitly selects no password. Preferences are not encrypted by this plugin.
Identity/realm checks do not defeat a peer spoofing both on a hostile network;
trusted-LAN operation remains the security boundary.

## Verification and release process

The suite retains existing useful regressions and adds cross-boundary tests using
real localhost WebSockets, the plugin event loop and a strict Indigo host double.
The simulator rejects unknown methods and supports successful digest authentication;
wait loops have total deadlines. CI runs Python 3.13 on Ubuntu and macOS using the
same portable dependencies shipped in the archive.

Source/archive checks cover the unusual Indigo loader, documented UI contracts,
callback references, dependency versions and checked-in content hashes. Upstream
runtime archive hashes and CI action commits are pinned. Release publication requires
a clean pushed commit, successful CI and a fresh archive; the accompanying manifest
records the source commit and SHA-256. Library versions are unchanged.

No live hardware was controlled or reconfigured for this release. Native dialog
rendering, physical output control, metering accuracy and real-LAN recovery remain
subject to the [hardware validation matrix](hardware-validation.md). Earlier user
observations are distinguished from beta verification there.

Gen3/Gen4 remain the new-development focus; existing Gen2 compatibility is retained.
Gen1 support and new Gen2-specific expansion remain off the roadmap.
