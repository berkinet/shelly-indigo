# Architecture and project decisions

This is an independent implementation, not a fork of kw123/SHELLY. No source
from that plugin has been incorporated. Shelly's official APIs define the wire
protocol; locally installed Indigo plugins provide examples of the host API.

## Target

Native Indigo integration with automatic discovery, explicit channel creation and local
bidirectional communication, without Home Assistant or a cloud dependency.
Gen2–Gen4 RPC/WebSockets first; Gen1 HTTP REST and callbacks follow separately.

## Initial alpha

- Indigo 2026.1 / server API 3.9 / Python 3.13.
- IPv4 Bonjour `_shelly._tcp.local.` discovery, plus optional explicit hosts.
- One WebSocket worker per physical unit, serialized RPC calls, bounded command
  queues, exponential reconnect backoff and a 60-second reconciliation read.
- Stable Indigo identity uses Shelly device ID + component key, never IP address.
- A locked snapshot of supported discovered channels feeds Indigo's device-dialog
  dropdowns. Saved devices keep their selections while offline. Validation fills
  connection details and rejects duplicate or mismatched channel bindings.
- From v0.1.6, discovery never creates Indigo devices implicitly. Creation is an
  explicit one-time plugin menu command, scoped by physical unit and channel
  type, or an individual New Device save. Legacy autoCreate preferences have no
  effect. Existing mapped devices keep receiving updates.
- Version 0.1.10 keeps a physical discovery snapshot separate
  from the supported-channel catalog. The creation menu includes online units
  without supported channels and labels them accordingly; it does not create
  unsupported channels. Names, reconnects and address changes update that snapshot.
  The creation dialog has an explicit refresh button with dynamic list reload;
  Debug records the exact menu choices supplied to Indigo for UI diagnosis.
- Version 0.1.10 maps `light:*` to native Indigo dimmers with
  brightness, On/Off/Toggle, bounded relative adjustments and optional metering.
  An off output displays 0% without discarding Shelly's retained turn-on level.
  Native universal status/energy-update actions request a fresh status read.
- New Device hides assigned channels; Edit Device retains its own current binding.
  Disabled devices still reserve channels, deleted devices release them, and
  unused inputs on the same physical unit remain available. Save-time duplicate
  validation handles dialogs that were opened before another device claimed a channel.
- Device-local names are read through Sys.GetConfig.device.name at connection
  and during reconciliation. They prefix internal IDs in selectors and new
  Indigo device names. Existing Indigo names are not overwritten. App-only cloud
  labels are outside the local API integration.
- Switches, dimmers, binary inputs and button inputs become separate Indigo
  relay/dimmer/sensor devices. Output metering is exposed where present. Unsupported
  components are ignored. Button channels are identified with read-only Input.GetConfig calls
  and track btn_down/btn_up independently of relay state.
- Binary RPC input states initialize immediately. Gen4 non-inverted button inputs
  can also initialize from an identity-verified read through already-enabled
  Modbus, using only read functions and a bounded timeout. Unsupported/failed
  optional reads fall back to events; no missing sample becomes an assumed OFF.
- Unknown input position is a normal neutral state, separate from device errors.
  Input display uses inputStatus; stateKnown gates the retained raw onOffState.
- Null RPC polls preserve known held/released state. Event order is preserved
  for quick transitions in one frame, and older timestamped events are ignored.
  Connection-setup notifications are buffered until identity/inventory is ready;
  a later Modbus sample supersedes earlier buffered button events.
- Version 0.1.10 adds a native Button Pressed event bound to an
  existing Indigo button-input ID. Only accepted live btn_down notifications
  execute enabled matching triggers, with Indigo conditions honored. Repeated
  notifications, initial reads and buffered setup events do not execute triggers.
- Notifications are partial updates. Missing fields preserve known values.
- Indigo calls and discovery reconciliation happen in the concurrent plugin
  thread; actions and network workers communicate with it through an event queue.
- Control state is updated from device reports, never optimistically. Commands
  are discarded at disconnect and are never automatically replayed.
- Optional shared admin password with SHA-256 RPC challenge authentication.
  Passwords stay in Indigo preferences and are never logged or committed.
- Connection preference changes require a plugin restart. Version 0.1.10 adds an Info/Debug Event Log preference that applies immediately.
  Discovery startup, endpoint and query messages use Debug. The plugin file
  handler always retains Debug diagnostics; warnings and errors remain visible
  at both selectable Event Log levels.
- Query diagnostics list reported component/service names separately from channels
  available for creation. Unsupported/disabled channels and units with no supported
  channels produce warnings, deduplicated by physical identity and current problem
  across polls/reconnects. Partial notifications preserve earlier component names;
  full connection reads reset the summary. Services do not generate per-channel
  warnings, and raw status/configuration values are not logged.
- The repository's plugin bundle includes unmodified portable dependencies.
  Source installation and release installation follow the same dependency path;
  startup checks for missing libraries before starting network workers.

## Following milestones

Hardware validation; per-device credentials; color/adjustable-white lights, covers and standalone
meters; click/long-press event triggers; Gen1 discovery/REST/callback listener; sleeping
sensors, IPv6 and broader component coverage. Gen4 support here means devices
running the Shelly Wi-Fi/Ethernet RPC firmware, not Zigbee-only operation.

## Sources

- https://shelly-api-docs.shelly.cloud/gen2/General/RPCChannels/
- https://shelly-api-docs.shelly.cloud/gen2/General/RPCProtocol/
- https://shelly-api-docs.shelly.cloud/gen2/General/Authentication/
- https://shelly-api-docs.shelly.cloud/gen2/General/Notifications/
- https://shelly-api-docs.shelly.cloud/gen2/General/mDNS/

## Releases

The owner authorizes committing, pushing and publishing releases with an
installable plugin archive and release notes without repeated confirmation.
Do not install or enable this alpha in the live Indigo server as a side effect
of building or publishing it. Record hardware testing status honestly.

## Indigo configuration API references

- https://docs.indigodomo.com/2025.2/plugin-dev/reference/xml/configui/dynamic-lists/
- https://docs.indigodomo.com/2025.2/plugin-dev/reference/xml/configui/button/
- https://docs.indigodomo.com/2025.2/plugin-dev/reference/plugin-py/device-methods/

- https://docs.indigodomo.com/2025.2/plugin-dev/reference/xml/menuitems/
- https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Sys/

- https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Input/
- https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Modbus/

## Dimmer and selector test review — source build 0.1.10

Retained switch/input, identity, reconnect, authentication and trigger regressions.
Updated selector expectations for hiding assigned channels while preserving edits
and duplicate validation. Former unsupported-light fixtures now use unsupported
covers, so those diagnostics still protect an applicable requirement. Added dimmer
coverage for explicit/manual creation, native schemas, partial status and metering,
brightness boundaries and invalid values, disabled/identity checks, native actions,
and real WebSocket notification/reconciliation. Control tests use only simulators.

- https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Light/
- https://github.com/IndigoDomotics/IndigoSDK/tree/main/Example%20Device%20-%20Relay%20and%20Dimmer.indigoPlugin
