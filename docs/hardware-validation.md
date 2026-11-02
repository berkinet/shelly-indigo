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
