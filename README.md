# Shelly for Indigo

An independent native Indigo plugin for local control of Shelly devices.
No Home Assistant, MQTT broker or Shelly cloud account is required.

**Installable download:** [Shelly 0.1.8 plugin package](https://github.com/berkinet/shelly-indigo/releases/download/v0.1.8-alpha.1/Shelly-0.1.8.indigoPlugin.zip).
The release asset and the repository's `Shelly.indigoPlugin` directory both
include the libraries required for discovery and communication. You can install
either bundle directly through Indigo.

**Status: development alpha.** Indigo 2026.1 has confirmed discovery and RPC
connections for 11 Shelly devices, with automatic creation of eight switches and
one binary input. Physical output control is still awaiting validation. Gen1–Gen4
support is the project goal, not a claim of complete support in this release.
See [hardware validation](docs/hardware-validation.md) for the current evidence. This is a new project, not a fork of kw123/SHELLY.

## Initial alpha features

- Automatic IPv4 Bonjour discovery of Gen2–Gen4 RPC devices.
- A plugin menu command to create selected Indigo switch/input devices on demand.
- Discovered-channel dropdowns in Indigo device dialogs, with automatic detail
  population and duplicate validation. Discovery never creates devices implicitly.
- Local Shelly-assigned names prefix internal IDs in selectors and newly created
  Indigo names when available.
- Indigo on/off/toggle control; WebSocket state notifications.
- Momentary button input tracking: ON while held, OFF when released, independent
  of the relay output.
- Switch power, voltage, current and energy readings where provided by hardware.
- Reconnect backoff, offline indication and periodic state reconciliation.
- Stable channel identities across rediscovery and IP address changes.
- Optional shared admin password; SHA-256 RPC authentication.

Gen4 devices must be running Shelly's Wi-Fi/Ethernet RPC firmware. Gen1, covers,
dimmers/lights, standalone meters, analog inputs, click/long-press events and sleeping
sensors are planned. Unimplemented components are ignored. Devices are never
automatically deleted. Unsupported fields do not produce fabricated readings.

## Requirements and installation

Indigo **2026.1** (server API 3.9), Python 3.13, and LAN access to the devices.
Bonjour discovery works on the local IPv4 multicast network; additional hosts
can be specified when multicast does not cross subnets.

1. Download the `.indigoPlugin.zip` archive from [GitHub Releases](https://github.com/berkinet/shelly-indigo/releases).
2. Extract it and double-click `Shelly.indigoPlugin` to install through Indigo.
3. In the plugin preferences, enable discovery and enter the shared Shelly admin
   password if required. Optional additional hosts are comma-separated hostnames
   or IP addresses, with optional ports.
4. Restart the plugin after changing connection preferences. Discovery runs continuously
   and fills selectors as devices connect; it does not create Indigo devices.
5. Choose **Shelly → Create Indigo Devices…** from the plugin menu, select one
   Shelly or all discovered Shellys, choose switches and/or inputs, then click
   **Create Devices**. Inputs are unchecked initially.

Test on a development Indigo installation first. Installing/enabling the plugin
starts discovery and updates existing Indigo devices. Creating additional Indigo
devices requires the menu command above or individually saving a New Device
dialog. It does not change Shelly settings or operate outputs until you issue
an Indigo action. This alpha
supports one shared password, not different credentials for each physical unit.
Indigo stores the password in plugin preferences; the UI masks it.

## Selecting a Shelly in Indigo

New Device and Edit Device dialogs now include a **Shelly channel** dropdown.
Choose the Shelly plugin and either **Shelly Switch** or **Shelly Input**;
the dropdown lists matching discovered channels. Selecting one fills its device
ID, channel, host, model and generation. Click **Refresh discovered channels**
if discovery completed after opening the dialog.

Discovery is always separate from creation. **Create Indigo Devices…** performs a
one-time creation of missing channels for the chosen Shelly devices and channel
types. From source build 0.1.9 (unreleased), the physical-device selector also
shows discovered units that have no supported channels, labeled **no supported
channels**. Discovery can find a dimmer even though this alpha cannot yet create
its light output as an Indigo dimmer. If discovery finishes while the creation
dialog is open, click **Refresh discovered devices** to reload its list without
closing the dialog. Refresh preserves your choices and does not create devices.
Debug logging records the exact ID/name choices supplied to Indigo whenever the
list is generated. Rerun the creation command whenever you want to add more channels. Previously stored
`autoCreate` preferences are ignored from v0.1.6; restarts, new discoveries and
button events cannot create additional devices implicitly. Existing Indigo
devices continue receiving updates, and their names and IDs are preserved.

You can also create an individual device using the New Device dropdown.
Channels already configured show their Indigo device names; edit those devices
to avoid duplicates. Existing devices have their channel preselected, including
while offline or before discovery completes after restart. The selector offers
switches, binary inputs and button inputs; unsupported components are omitted.

### Names assigned in Shelly

The plugin reads the device's local configured name from
`Sys.GetConfig → device.name`, the name stored on the device and exposed through
its web interface. When populated, new Indigo names use the format
`Garden Gate (shelly…ID) input:0`. If unset, the internal ID is used. Selectors
refresh local names during normal status reconciliation; changing a Shelly name
does not rename existing Indigo devices.

A name assigned in the Shelly app is usable here when it is also stored as the
device's local name. Labels kept only in the cloud account are not exposed by
this local name field. This plugin does not connect to the Shelly cloud account
or retrieve app-only cloud labels. Read-only checks of all 12 devices discovered
on the owner's network returned null device names and null input/switch/light
channel names. If friendly names are visible in the app, they are not being
exposed in those local configuration fields. No names or settings were changed.

See [Shelly's system configuration API](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Sys/).

### Device type and channel must match

Use **Shelly Switch** for `switch:0` (relay and metering) and **Shelly Input** for
`input:0` (physical input/button). Changing a relay's Indigo type to Shelly Input
can leave its old relay binding behind. If this happened in an older alpha,
restore that device's type to Shelly Switch and select its switch channel; use
the separately created input device for the button.

From v0.1.5 the plugin clears incompatible selections when changing device type,
rejects invalid saved bindings, and blocks updates/control for inconsistent
existing devices with a message explaining the required type. State definitions
are refreshed for both switches and inputs on startup and when needed before an
update; existing valid device IDs and names are preserved.

## Momentary buttons

For a Shelly configured with Input type **Button**, use its separate `input:0`
Indigo device to monitor the physical button. The input becomes ON on `btn_down`
and OFF on `btn_up`. The `switch:0` device continues showing the relay output,
which may stay ON after the button is released, depending on Shelly settings.
The plugin does not change those settings or send relay commands when tracking
button presses.

Shelly button mode reports `state: null` through the regular RPC status call.
For Gen4 devices with Modbus already enabled, the plugin can also sample the
physical input at startup/reconnect and during reconciliation. It verifies the
Modbus device MAC against RPC identity before using the reading. This optional
path currently covers non-inverted button inputs at the standard RPC endpoint
(port 80) with reachable Modbus port 502. It never enables Modbus, changes device
configuration or sends write commands.

A readable binary RPC state or verified Modbus bit establishes ON/OFF immediately.
Otherwise, the input displays **Unknown** with a neutral icon and `stateKnown`
false until a press or release arrives. Unknown position is not a disabled device
or a communication error. No assumed OFF state or fabricated release timer is
used. Real connection failures still appear as errors. Normal RPC null polls
preserve a known press/release state.

The `inputStatus` display reports On, Off, Unknown or Connecting. Automations
using the raw `onOffState` should also check `stateKnown` when startup or missing
readings matter: the last raw binary value is retained while unknown, with no
fake state-change event. Gen2/Gen3, inverted inputs, disabled/unreachable Modbus
and failed identity verification use the event-based fallback.

See [Shelly's Input API](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Input/)
and [Modbus API](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Modbus/)
for the status and input-register semantics.

### Button Pressed event (unreleased, source build 0.1.9)

Create an Indigo trigger, choose the **Shelly → Button Pressed** event, then select
an existing **Shelly Input** configured in button mode. Add a **Toggle** action for
the Indigo device you want to control. Indigo evaluates the trigger's conditions
normally. Disable an older input-state-change trigger for the same action to
avoid toggling twice.

The event fires once for each live physical press, independently of the relay.
Release and click/long-press classifications do not fire it. Startup/reconnect
samples and notifications buffered during connection setup update input state
without firing this event. It uses the existing input device; no additional
Indigo device is created. This feature has not been published in a release yet.

## Logging (unreleased, source build 0.1.9)

In **Plugins → Shelly → Configure…**, choose **Info** (the default) or **Debug**
for the **Event Log level**. Saving applies the change immediately; cancelling
leaves the level unchanged. Connection preference changes still need a restart.

Info shows normal activity, including explicit device creation, plus warnings
and errors. Debug also shows discovery startup, discovered endpoints and device
queries. A Debug query summary lists all reported component/service names and
which channels are available for Indigo creation. It is logged on connection and
when that summary changes, rather than on every unchanged poll. Raw status and
configuration values are not dumped.

Warnings identify omitted device channels and their reasons (unsupported type,
unsupported input mode or disabled input), even if other channels are supported.
A Shelly with no supported channels also produces a warning. Warnings remain
visible at Info and Debug, but the same problem is not repeated on routine polls
or unchanged reconnects. A changed or resolved problem updates that suppression.
Wi-Fi, Cloud and other services appear in the Debug summary without individual
unsupported-device warnings. All queried devices remain in the discovery menu.
The plugin's separate log file always includes Debug messages, regardless
of the Event Log setting. This setting is not yet in a published release.

## Troubleshooting discovery

Gen2–Gen4 discovery is implemented. Gen1 discovery is still planned.

If Indigo logs `Bonjour discovery unavailable (ModuleNotFoundError)`, check the
installed `Shelly.indigoPlugin/Contents/Packages` directory. It must contain
`zeroconf`, `ifaddr` and `websocket`, along with their distribution metadata.
An installed copy lacking this directory cannot discover or communicate with
devices. From v0.1.2, dependencies are included in the checked-in bundle as well
as the release asset. Install the complete current bundle to replace an older
incomplete installation. Startup now reports the missing library explicitly.

With the Event Log level set to **Debug**, after installation and restart look for `Shelly discovery started` in the Indigo
Event Log. That confirms the browser started. A `Querying ...` message confirms
successful device-information and status reads; it does not mean an Indigo device
was created. `Created ...` messages appear when the explicit creation command adds
a channel. If discovery
starts but finds nothing, verify the devices are Gen2–Gen4 and that IPv4 Bonjour
multicast can reach the Indigo server. Additional hosts can be entered in plugin
preferences when multicast does not cross subnets.

## Development

Use zsh with Python 3.13:

```zsh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/build.py
.venv/bin/python tools/check_bundle.py
```

The build writes `dist/Shelly-0.1.9.indigoPlugin.zip` using the checked-in portable
Python dependencies and their license notices, with no network download needed.
`tools/check_bundle.py --source` also validates direct installation of the
repository bundle. To update dependencies, edit `requirements.txt`, run
`.venv/bin/python tools/vendor.py`, then review and commit the resulting files.

Tests use an Indigo API double and a real localhost WebSocket connection to a
simulated Shelly; they never operate home devices. CI runs the same tests and
validates both bundles, including executing the entry point without `__file__`
as Indigo does.

For a read-only network diagnostic using only the bundled libraries, run:

```zsh
.venv/bin/python -I -S tools/probe_discovery.py --read-status
```

This discovers endpoints and reads identity/status without creating Indigo
devices or sending output-control commands.

See [architecture and milestones](docs/architecture.md) and
[third-party licenses](docs/third-party.md). Project code is [MIT licensed](LICENSE).
