# Hardware validation

## Current status — 0.2.1 beta preparation, 2 November 2026

The beta has not been installed or enabled in the owner's live Indigo server by
this development session. No physical output or hardware setting was changed.
Historical observations below apply to the versions recorded there, not automatically
to the beta. The owner subsequently reported successful startup input behavior in
the seeded chat; the exact installed version and complete retest conditions were
not recorded. That confirmation is retained as user evidence rather than promoted
to a repeatable beta validation result.

| Area | Existing evidence | Beta validation still required |
| --- | --- | --- |
| Gen3/Gen4 discovery, creation, local names | Owner logs and earlier read-only probes | Recheck discovery, explicit creation and names after upgrade |
| Gen4 input press/release and startup position | Owner reported working behavior; earlier RPC/Modbus reads | Repeat held/released restart checks on the installed beta |
| Native Button Pressed trigger | Owner reported trigger-based toggling; simulator verifies native event adapter | Check beta native event, conditions, single press and clock/network recovery |
| Gen3 dimmer and existing Gen2 Pro dimmer | Earlier read-only state/metering translation | Physical On/Off/Toggle, relative steps, external changes and meter comparison |
| Per-device passwords | Strict authenticated simulator; Save/Cancel, blank/clear and offline-device configuration regressions | Masked fields in all three native dialogs, persistence and protected-device operation |
| Communication checkbox | Owner reported an offline disabled device still warning on 0.2.0; reproduced and covered with shared-worker and real loopback stop/resume tests | Disable last channel, confirm retries stop; re-enable; confirm enabled siblings retain communication |
| Native dialogs | XML contract validation and host double | Open each device type, confirm read-only details, refresh and save; password menu |
| DHCP/address reuse and communication failure | Deterministic registry and real loopback transport tests | Router/network interruption and reconnect with actual units |
| Release installation | Isolated source/archive loader checks and portable dependency integrity | Install archive in Indigo and confirm existing names, IDs and bindings remain |

For each beta check, record installed version, Indigo version, device model and
firmware, starting state, action, observed state and relevant log. Physical control
checks should be performed on suitable test outputs under the owner's control.
The beta label does not imply these unperformed checks have passed.

## Historical validation records

## 2 November 2026 — plugin 0.1.2, Indigo 2026.1

The owner's Indigo Event Log confirms:

- Plugin startup and Bonjour discovery succeeded.
- Eleven Shelly endpoints were discovered and connected through RPC.
- Eight Indigo switch devices and one binary-input device were created.
- A Gen3 dimmer and a Gen2 Pro dimmer connected; this alpha does not yet map
  their light components to Indigo dimmer devices.
- One AZ Plug connected without creating a channel. A subsequent read-only
  `Shelly.GetStatus` call confirmed that it currently reports only service
  components (BLE, cloud, MQTT, system and Wi-Fi), with no switch component.
  No device configuration was changed. The reason for that component inventory
  has not been established.

This confirms actual Indigo startup, network discovery, RPC connectivity and
automatic device creation. It does not yet validate physical output control,
external-change notifications, metering accuracy or recovery during real network
interruptions. Those remain hardware validation tasks; simulated automated tests
cover their implemented protocol and lifecycle behavior.

No physical output commands were sent during the read-only diagnostic probes.

## 2 November 2026 — Gen4 button input, development version 0.1.4

Read-only RPC queries to the owner's 1PM Mini Gen4 (S4SW-001P8EU, firmware 2.1.1)
confirmed that its enabled input is configured as `button` and returns `state:
null`. The updated worker and model recognized that actual hardware as a
supported button input, initially with `stateKnown: false`. No device settings
or physical outputs were changed.

Press/release transitions have been validated with a real localhost WebSocket
simulator and Indigo API test double. Physical press/release operation through
the installed Indigo plugin still needs the owner's confirmation.

## 2 November 2026 — channel/type mismatch diagnosis, development version 0.1.5

A read-only Indigo query confirmed that a device named as a switch channel had
Indigo type `shellyInput` while retaining a `switch:0` component binding. Its
registered states were the input state set, so relay metering updates caused
undefined-state errors. No user device was modified during diagnosis.

Version 0.1.5 prevents incompatible saved-channel fallback, blocks updates and
commands for mismatched bindings, and refreshes static state definitions for
both supported Indigo types. Regression tests use a stricter Indigo test double
that rejects any update to a state absent from the actual Devices.xml schema.
Already-inconsistent devices require correcting their type/channel in Indigo;
the plugin does not delete or recreate them as a repair.

## 2 November 2026 — local naming verification, development version 0.1.7

Read-only discovery and Shelly.GetConfig queries checked all 12 discovered
endpoints. All returned null for sys.device.name and for all returned input,
switch and light channel name fields. This confirms that default internal IDs
are the appropriate fallback for the locally available data. App-visible names
were not exposed in the fields inspected; no cloud-account lookup was performed.
Only the name fields were printed during diagnosis. No hardware settings or
outputs were changed, and no device-specific names or network configuration are
included in this repository note.

## 2 November 2026 — startup input sampling, development version 0.1.8

The owner confirmed that a button held across restart remained in the old waiting
error state and that release then established OFF. Read-only checks confirmed
that the Gen4 input's RPC state remained null while its Modbus service was
already enabled. Its physical discrete-input bit was readable, and the Modbus
MAC read matched the RPC device identity after decoding the register byte order.

The complete updated worker/model startup path was then run read-only against
that Gen4. It returned inputMode button, stateKnown true, inputStatus Off and
onOffState false without waiting for a button event. No hardware configuration
or output was changed. True/held samples and startup ordering are validated by
protocol and WebSocket simulations; held-across-restart operation in the updated
installed Indigo plugin still needs the owner's repeat test.

The input test review retained existing press/release and lifecycle regressions,
updated the former waiting-error assertions to normal Unknown display, and added
wire-level identity/read validation, both sampled positions, failed-read fallback
and protection against buffered pre-sample events overwriting a newer sample.

## 2 November 2026 — dimmer reads, development version 0.1.10

The updated worker and channel model were run read-only against the owner's
Gen3 dimmer and Gen2 Pro dimmer. Both exposed `light:0` and translated successfully
to OFF / 0% brightness with power, voltage, current and accumulated energy values.
Shelly's retained nonzero brightness while off was not confused with an active
output. No Indigo device was created and no output or device setting was changed.

Indigo creation/binding and On/Off/Toggle/brightness actions are covered with a
strict host double and a localhost WebSocket simulator. Physical dimmer control
through the installed plugin still needs owner validation. The focused test
review is recorded in architecture.md; existing switch/input regressions remain.
