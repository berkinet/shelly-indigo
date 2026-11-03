# Shelly for Indigo — 0.2.2 beta

Shelly for Indigo follows a simple design philosophy: small, lightweight software
built to meet a well-defined need. It provides direct, local communication between
Indigo and Shelly Wi-Fi devices, with a focus on discovery, state reporting and
control.

The goal is an easy-to-maintain integration with few moving parts: Indigo, the
plugin and your Shelly devices. Device setup and administration—including firmware
updates—remain the responsibility of the Shelly app or each device’s web interface.

Communication uses RPC over WebSockets. Discovery runs continuously; creating Indigo
devices is an explicit action. No Shelly cloud account, MQTT broker or Home Assistant
is required. This is a new project, not a fork of kw123/SHELLY.

## Supported devices and channels

New development targets **Gen3 and Gen4** Wi-Fi/Ethernet RPC devices. Existing
Gen2 compatibility, including the Pro dimmer, is retained. Gen1 support and new
Gen2-specific expansion are off the roadmap. Gen4 Zigbee-only operation is outside
this implementation.

| Shelly component | Indigo device | Functions |
| --- | --- | --- |
| `switch:*` | Shelly Switch | On, Off, Toggle; available power/voltage/current/energy |
| `light:*` | Shelly Dimmer | On, Off, Toggle, absolute and relative brightness; available metering |
| `input:*`, switch mode | Shelly Input | Reported binary position |
| `input:*`, button mode | Shelly Input | Press/release position and native Button Pressed events |

Unsupported components are reported in diagnostics and omitted from channel
selectors. Covers, color lights, standalone meters and sleeping sensors are not
implemented. A physical Shelly may expose several independent channels; a relay
output and its physical button can have separate Indigo devices.

## Installation and setup

Requires **Indigo 2026.1**, server API 3.9 and Python 3.13.

1. Download the installable archive from [GitHub Releases](https://github.com/berkinet/shelly-indigo/releases).
2. Extract it and double-click `Shelly.indigoPlugin` to install through Indigo.
3. Enable discovery in the plugin preferences. Additional hosts can be entered for
   devices outside the local IPv4 Bonjour multicast network.
4. Choose **Plugins → Shelly → Create Indigo Devices…**, select a physical device
   and the channel types to create. Inputs are initially unchecked.

Discovery alone never creates devices. The creation command preserves existing
Indigo device IDs, names and bindings. An individual New Device dialog offers
unassigned channels of the selected type; Edit Device retains its current binding,
including while offline. Disabled devices reserve their channels. Deleting an
Indigo device makes its channel available again on refresh.

Connection detail fields are read-only. New bindings require a discovered-channel
selection; manually typing an address into those fields is not a supported
configuration path. The plugin does not configure Shelly Wi-Fi, input mode, relay
behavior or dimmer calibration. Provision a new unit onto the network first.

## Passwords

Unprotected devices need no password. Each **Shelly Switch**, **Shelly Dimmer** and
**Shelly Input** device configuration has a masked **Local device password** field.
Enter the local admin password and Save. Leaving it blank keeps the saved credential;
select **No password** to remove it. Cancel leaves credentials unchanged.

A password change applies to all channels on that physical Shelly and does not
change its hardware password. It is not the Shelly cloud account password.
For a protected unit that has no Indigo channels yet, use
**Plugins → Shelly → Set Device Password…** first, then create its channels.
That menu also remains available for physical-device password management; a blank
password in that menu explicitly selects no authentication.

Passwords are masked and stored centrally in Indigo preferences, not encrypted
by this plugin. Typed device-dialog passwords are kept out of saved device properties
and applied only after Save. They are never included in diagnostic logs or release files.
An already-stored legacy shared password remains a fallback for saved Indigo
bindings and explicitly entered hosts; its old field is no longer in plugin preferences. Newly discovered, unassigned units never receive
that shared credential automatically. A protected discovery remains visible with
a password-required label until its credential is configured.

The worker checks public device identity before authenticated requests and checks
challenge realms against that identity. Digest authentication does not provide
cryptographic server identity or encrypt `ws://` traffic. This integration assumes
a trusted home LAN; a spoofed matching device ID/realm is not defeated by those
consistency checks. Distinct device passwords limit credential reuse.

## Names and device state

Names configured locally on the device prefix internal IDs in new Indigo names
and selectors. The local API may not expose labels stored only in the Shelly app's
cloud account. Existing Indigo names are preserved.

Output state changes only from device reports. Commands wake the worker immediately
and verify only the affected output; they do not fetch all configuration or sample
Modbus after each action. Relative dimmer actions are resolved in execution order,
so successive increments accumulate. Brightness displays 0% while off without
changing the Shelly's retained turn-on level.

A lightweight status read runs every 15 seconds for reconciliation and detection
of silent connections. Configuration is refreshed when its revision changes, on
configuration-change events, or when new input components appear. A fifteen-minute
fallback handles missed signals; firmware without revisions and failed optional
configuration reads use a sixty-second fallback. A failed idle connection is normally
detected within the heartbeat interval plus the eight-second RPC timeout; operating
system connection errors can detect it sooner. This is not a hard bound while a
configuration read or another RPC is already in progress.

Reconnection uses bounded backoff. Commands expire after ten seconds in the worker
queue and are discarded on disconnect; uncertain actions are never replayed.
Indigo control callbacks enqueue requests; later failures appear in the Event Log,
not as synchronous script exceptions. Universal status requests are coalesced by
physical device. Repeated identical outage warnings move to Debug until recovery.

## Momentary buttons and triggers

Use the separate **Shelly Input** channel for physical press/release state. The
relay or dimmer channel continues to report its actual output. The plugin leaves
hardware relay behavior unchanged.

Button-mode RPC status is normally null. For a Gen4 non-inverted button with
Modbus already enabled, an identity-checked, read-only sample can establish its
position shortly after startup and every sixty seconds. Sampling runs separately
from command processing; a late sample cannot replace a newer received button event
or survive a configuration/connection change. The plugin never
enables Modbus. Other cases use live press/release events; a missing sample stays
**Unknown**, not an assumed OFF or a disabled/error device. `stateKnown` indicates
whether the retained raw `onOffState` is currently meaningful.

Choose the plugin's **Button Pressed** trigger event and select an existing
button-mode input. Accepted live presses execute enabled triggers with their
Indigo conditions honored. Setup events and physical samples do not fire triggers.
UTC clock adjustments do not determine event order; connection lifetime and TCP
receive order do. Duplicate tracking is bounded and resets with a backwards clock
step or reconnection.

## Logging and recovery

The Event Log preference offers **Info** and **Debug** and applies immediately.
Discovery, queries and detailed inventory summaries use Debug; warnings and errors
remain visible at both levels. The plugin file log retains Debug diagnostics.
Restart after changing discovery or additional-host preferences.
Per-device password changes reconnect that unit when communication is enabled;
saving a password never enables disabled communications.

Disabling **communication** on an Indigo channel stops its updates, controls and
triggers. When the last enabled channel on a physical Shelly is disabled, its worker
is stopped and retries/warnings cease. Bonjour still browses for other units; it
cannot restart that disabled-only unit, even if its address is in Additional hosts.
If another channel on the same unit remains enabled, the shared connection stays
active and outage warnings name the active Indigo channels.

Offline channel choices are retained. Re-enabling a channel resumes communication,
using its current discovered endpoint when available. An unidentified endpoint may
need one public identity read to recognize a disabled unit; that read does not
proceed to authentication, polling or controls for it.

Physical identities survive DHCP changes. Old connection messages are fenced by
connection generation. Vacated endpoints are probed for replacement units without
moving the same unit back through an alternate address. Unassigned unreachable
endpoints expire after fifteen minutes without rediscovery; configured hosts keep
retrying. An unexpected Indigo concurrent-thread restart rebuilds communication.

Queues are bounded. A rejected UI request is logged. Event-queue overload causes a
visible connection reset and resynchronization instead of indefinitely blocking
network readers; queued controls are discarded and event delivery during overload
cannot be guaranteed.

## Validation and development

This beta includes offline behavioral regressions, a strict real WebSocket
simulator, the actual plugin event loop with an Indigo contract double, authenticated
exchanges, Ubuntu/macOS CI and source/archive loader checks. The owner has installed
0.2.2; the installed Python/XML files match the release, and a selected Gen3 dimmer
passed absolute/relative brightness and On/Off/Toggle through the installed Indigo
adapter. Its starting output and remembered brightness were restored. Earlier
isolated-worker tests covered Pro outputs and Gen4 press/release alongside 0.2.1.
Four sanitized firmware captures feed regression tests. Native dialogs, held-at-startup
input sampling, metering accuracy and real network interruption still need the
focused checks in [hardware validation](docs/hardware-validation.md).

```zsh
python3.13 -m unittest discover -s tests
python3.13 tools/check_bundle.py --source
python3.13 tools/build.py
python3.13 tools/check_bundle.py
```

Tests import the same portable dependencies included in the plugin. Dependency
versions and upstream source hashes are pinned; bundle contents are checked against
`Contents/DEPENDENCIES.json`. To refresh them, use `tools/vendor.py`, then review and
commit the dependencies, lock and manifest together. The hash lock covers upstream
runtime archives; isolated source-build tooling is not a fully reproducible build
or a vendored-library security audit.

For a release, commit and push the intended source, wait for the complete CI
workflow, then run `python3.13 tools/release.py --tag v0.2.2-beta.1 --publish`.
Use a new tag such as `v0.2.2-beta.2` for another beta of the same plugin version;
`--notes-file` and `--title` can override defaults. A tag without a prerelease suffix
creates a stable release. The tool reruns checks and builds a fresh archive with a
source-commit and SHA-256 manifest. Existing releases/assets are not overwritten.

See [architecture](docs/architecture.md), the [original project review](docs/project-review-2026-11-02.md),
[beta release notes](docs/release-0.2.2-beta.1.md) and [dependency licenses](docs/third-party.md).

## License

MIT. Bundled dependencies retain their upstream licenses.
