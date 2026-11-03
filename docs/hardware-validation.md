# Hardware validation

## Current status — 0.2.2 beta preparation, 3 November 2026

The revised 0.2.2 worker was exercised in an isolated process against owner-selected
hardware. The installed Indigo plugin remained **0.2.1**, on Indigo 2026.1/API 3.9;
this session did not install, enable or restart a replacement plugin. The owner
explicitly authorized these output tests. Existing Indigo IDs/bindings were checked
through Indigo's local scripting interface. The MCP connector returned HTTP 429
and its troubleshooting was deferred at the owner's request.

| Area | Evidence for this change | Remaining validation |
| --- | --- | --- |
| Gen4 input startup and press/release | Released startup sample known Off; real btn_down/btn_up gave On/Off in revised worker/model; actual installed Indigo input also On for the physical hold and Off on release | Held-across-startup with this asynchronous version; archive upgrade |
| Selected Pro relay | On, Off, Toggle and change from a separate RPC connection; starting Off restored and verified | Physical load/reference measurement; network interruption |
| Selected Pro dimmer | 20%, brighten +10 to 30%, dim -5 to 25%, Off/On/Toggle; external change; starting Off with retained 100% restored and verified | Gen3 physical dimmer controls; reference meter comparison |
| Firmware response compatibility | Sanitized Gen4 Mini, Gen3 Dimmer, Gen2 Pro Dimmer and Pro 4PM captures replayed through production protocol/worker/inventory/adapter | Additional models/firmware as hardware becomes available |
| Native Button Pressed trigger | Retained simulator/adapter tests and earlier owner evidence; this test sent no synthetic button events | Conditions and event execution with the installed 0.2.2 archive |
| Passwords/communication-off | Save/Cancel, authentication and shared-worker stop/resume regressions retained | Protected hardware and native dialog/checkbox checks after upgrade |
| Dialogs and release installation | XML callback, strict host and isolated source/archive loader checks | Open native dialogs after upgrading; verify retained IDs/names/bindings |
| DHCP/network recovery | Generation, identity, disconnect and address-swap tests | Real router/network interruption |

### Physical control evidence

- Relay: owner-selected Pro 4PM, model SPSW-104PE16EU,
  firmware **2.2.0-beta2**, channel switch:0. Initial/final output Off. Five
  worker command observations completed in approximately **0.068–0.089 s**;
  a separate connection's Off change was observed in **0.137 s**.
- Dimmer: owner-selected Pro Dimmer (replacement test target), model SPDM-001PE01EU, firmware **2.0.3**, light:0.
  Initial/final output Off, retained brightness 100. Absolute and relative steps
  produced 20 → 30 → 25, with On/Off/Toggle state confirmations in approximately
  **0.044–0.636 s**. An external Off was observed in **0.429 s**.
- Input: owner-selected 1PM Mini Gen4,
  model S4SW-001P8EU, firmware **2.1.1**, input:0. Its relay was not operated or
  represented by a new Indigo device. Two owner-operated holds were observed in
  the installed Indigo input; after isolating RPC source IDs, the revised worker
  received real live btn_down/btn_up and reported On then Off. The startup Modbus
  read established known Off without waiting for the first press.

These timings are observations on this LAN, from queue admission to a received
matching status; they are not performance guarantees. Metering fields were present
and translated, but the relay had zero reported load and no calibrated reference
meter was used. This does **not** establish metering accuracy or visual/physical
lamp brightness. Hardware tests of worker networking and the installed 0.2.1 input
are distinct from end-to-end installation testing of the 0.2.2 native adapter.

The initial parallel diagnostic used a shared RPC source and failed to receive an
external notification before its deadline. Starting states were restored even on
that failure. Unique per-session source IDs resolved concurrent notification delivery
on the repeat; an independent session now cannot share the plugin's routing identity.
No firmware, input mode, relay behavior or other device configuration was changed.

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
