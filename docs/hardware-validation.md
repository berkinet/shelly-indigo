# Hardware validation

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
