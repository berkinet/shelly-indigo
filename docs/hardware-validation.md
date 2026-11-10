# Hardware validation

## 9 November 2026 — field address-change diagnosis, 0.2.3

After a power failure, a configured Gen3 1PM Mini appeared at a different DHCP
address. Logs and a live MCP read confirmed that the existing Indigo device was
online with its error cleared at the new address. The 0.2.3 log omitted recovery
because its outage state was indexed by the previous endpoint; a vacated-address
probe then kept retrying the empty old address and logged it as unassigned.

The 0.2.4 fix is validated by lifecycle regressions and a real loopback WebSocket
move/recovery test. The field event establishes real hardware address migration
and the original defect; the new code has not been installed or used to force
another physical DHCP move in this session. No device output or network setting
was changed during this diagnosis.

## Historical hardware validation — installed 0.2.2 beta, 3 November 2026

The owner installed **0.2.2** after publication. MCP confirms it is enabled and
running on Indigo 2026.1/API 3.9; the installed plugin's 20 Python/XML source files
match the released checkout. The selected Gen3 dimmer was subsequently exercised
through MCP controls and the installed native Indigo adapter, with actual Shelly
status checked after each command and the starting state restored.

Earlier Pro relay/dimmer and Gen4 input checks below used an isolated 0.2.2 worker
alongside the then-installed 0.2.1 plugin. Those checks retain that narrower scope.
MCP access was restored by starting its stopped local tunnel, then installing an
owner-approved login service. No credential or Indigo device configuration changes
were needed to restore MCP.

| Area | Evidence for this change | Remaining validation |
| --- | --- | --- |
| Gen4 input startup and press/release | Released startup sample known Off; real btn_down/btn_up gave On/Off in revised worker/model; actual installed Indigo input also On for the physical hold and Off on release | Held-across-startup with this asynchronous version (deferred to the next owner session) |
| Selected Pro relay | On, Off, Toggle and change from a separate RPC connection; starting Off restored and verified | Physical load/reference measurement; network-only isolation without reboot |
| Selected Pro dimmer | 20%, brighten +10 to 30%, dim -5 to 25%, Off/On/Toggle; external change; starting Off with retained 100% restored and verified | Reference meter comparison |
| Selected Gen3 dimmer | Installed 0.2.2: 20 → 30 → 25%, On/Off/Toggle via MCP; device reports checked; starting Off/retained 3% restored | Reference meter comparison |
| Firmware response compatibility | Sanitized Gen4 Mini, Gen3 Dimmer, Gen2 Pro Dimmer and Pro 4PM captures replayed through production protocol/worker/inventory/adapter | Additional models/firmware as hardware becomes available |
| Native Button Pressed trigger | Retained simulator/adapter tests and earlier owner evidence; this test sent no synthetic button events | Conditions and event execution with the installed 0.2.2 archive |
| Passwords/communication-off | Save/Cancel, authentication and shared-worker stop/resume regressions retained | Protected hardware and native dialog/checkbox checks after upgrade |
| Dialogs and release installation | Owner-installed 0.2.2 confirmed running; installed source/XML matches release; selected existing binding controlled successfully | Open/check each native dialog; broader upgrade/binding checks |
| DHCP/network recovery | Installed 0.2.2: owner-operated Pro 4PM power loss/reboot; all four channels Offline then automatic recovery; rejected offline On was not replayed | Network-only isolation without reboot; actual DHCP reassignment |

### Physical control evidence

- Relay: owner-selected Pro 4PM, model SPSW-104PE16EU,
  firmware **2.2.0-beta2**, channel switch:0. Initial/final output Off. Five
  worker command observations completed in approximately **0.068–0.089 s**;
  a separate connection's Off change was observed in **0.137 s**.
- Dimmer: owner-selected Pro Dimmer (replacement test target), model SPDM-001PE01EU, firmware **2.0.3**, light:0.
  Initial/final output Off, retained brightness 100. Absolute and relative steps
  produced 20 → 30 → 25, with On/Off/Toggle state confirmations in approximately
  **0.044–0.636 s**. An external Off was observed in **0.429 s**.
- Gen3 dimmer: owner-selected S3DM-0A101WWL, firmware **2.0.3**, light:0.
  Controls ran through MCP into the installed 0.2.2 Indigo plugin: SetBrightness
  20, BrightenBy 10 (30), DimBy 5 (25), Off, On, Toggle Off, Toggle On. Each action
  was verified against a fresh physical Shelly status. Initial output Off with
  retained brightness 3 was restored and verified; Indigo then reported online,
  Off, displayed brightness 0 and no error. The retained-level restoration used an
  identity-checked direct RPC so it did not require briefly turning the lamp on
  at its remembered level. Metering was present, without a reference accuracy test.
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
lamp brightness. The earlier isolated-worker checks and installed 0.2.1 input
observations are distinct from the later Gen3 controls through the installed 0.2.2
adapter. Those output checks do not establish native dialog rendering or held-at-startup
behavior. The subsequent physical power-loss/recovery test is recorded separately below. Real WebSocket notification captures also remain
outstanding; the current fixtures contain recorded HTTP result bodies.

The initial parallel diagnostic used a shared RPC source and failed to receive an
external notification before its deadline. Starting states were restored even on
that failure. Unique per-session source IDs resolved concurrent notification delivery
on the repeat; an independent session now cannot share the plugin's routing identity.
No firmware, input mode, relay behavior or other device configuration was changed.

### Physical power-loss/recovery evidence — 3 November 2026

The owner selected all four existing channels of the Pro 4PM (SPSW-104PE16EU,
firmware **2.2.0-beta2**) and manually turned off its power, then restored power
when requested. The installed plugin was **0.2.2**. All channels started enabled,
online and Off; their communication settings remained enabled. No plugin restart,
router/firewall changes, new Indigo devices or hardware configuration writes were used.

A read-only Indigo host monitor sampled the four channel states/errors every
200 ms and recorded matching plugin log entries. A separate Gen4 input was
monitored as an unaffected reference. Observations in Europe/Paris local time:

- **17:23:48:** all four channels became Offline, retaining their last known Off
  state and existing IDs/names. The plugin logged the first status-call timeout.
- **17:23:57:** a different connection-timeout reason produced one further outage
  warning. Four subsequent identical failures were logged at Debug, not Warning.
- **17:24:08:** one On request was sent through MCP to channel 0 while the unit was
  powered off. MCP accepted the asynchronous request; the installed plugin then
  explicitly logged that it was rejected because the Shelly was offline.
- **17:25:32:** the plugin logged recovery and all four original channels became
  online with errors cleared. All four remained Off for at least another 30 seconds;
  the rejected On was not replayed. The reference input stayed online throughout.
- A final identity-checked, read-only hardware status confirmed all four physical
  outputs Off, with a low uptime consistent with the owner's power cycle.

This verifies a real device power-loss/reboot and reconnection through the installed
adapter, including shared-channel error handling, repeat-warning suppression,
offline command rejection and recovery without replay of that rejected request.
It does not exercise a command transmitted just before transport loss, a network-only
outage with the device still powered, or DHCP reassignment. The time from actual
power removal/restoration was not instrumented, so the observed Offline-to-recovered
interval includes the owner's powered-off period, reboot and retry backoff; it is
not a measured detection/reconnection latency bound.

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


## Inventory report / account authorization — 10 November 2026

The 0.2.6 implementation is covered by deterministic physical-history/merge tests
and real loopback HTTP callback tests. Tests verify local/default reporting,
persisted dates with availability re-established after restart, cloud-only Never,
MAC/identity deduplication independent of reused IPs, cancellation, callback state,
allowed HTTPS cloud hosts and credential-free failure reporting. Existing connection,
worker, outage and address-change regressions are retained. Native inventory-menu
rendering has not yet been verified. No output commands, live plugin installation
or plugin restart are claimed for this feature.

Account-derived validation details and inventory data are intentionally excluded
from the public repository. The API schema examples are synthetic and follow the
published Shelly documentation; they do not expose an owner's devices or account.
