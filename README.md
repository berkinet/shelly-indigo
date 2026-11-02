# Shelly for Indigo

An independent native Indigo plugin for local control of Shelly devices.
No Home Assistant, MQTT broker or Shelly cloud account is required.

**Installable download:** [Shelly 0.1.2 plugin package](https://github.com/berkinet/shelly-indigo/releases/download/v0.1.2-alpha.1/Shelly-0.1.2.indigoPlugin.zip).
The release asset and the repository's `Shelly.indigoPlugin` directory both
include the libraries required for discovery and communication. You can install
either bundle directly through Indigo.

**Status: development alpha.** A read-only network probe has confirmed Bonjour
discovery of 11 Shelly endpoints on the development network. Full Indigo device
creation and physical output control are still awaiting validation. Gen1–Gen4
support is the project goal, not a claim of complete support in this release. This is a new project, not a fork of kw123/SHELLY.

## Initial alpha features

- Automatic IPv4 Bonjour discovery of Gen2–Gen4 RPC devices.
- Automatic creation of separate Indigo devices for switches and binary inputs.
- Indigo on/off/toggle control; WebSocket state notifications.
- Switch power, voltage, current and energy readings where provided by hardware.
- Reconnect backoff, offline indication and periodic state reconciliation.
- Stable channel identities across rediscovery and IP address changes.
- Optional shared admin password; SHA-256 RPC authentication.

Gen4 devices must be running Shelly's Wi-Fi/Ethernet RPC firmware. Gen1, covers,
dimmers/lights, standalone meters, analog inputs, button events and sleeping
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
4. Restart the plugin after changing preferences. Supported channels appear
   automatically once a device connects successfully.

Test on a development Indigo installation first. Installing/enabling the plugin
with discovery enabled creates Indigo devices automatically. It does not change
Shelly settings or operate outputs until you issue an Indigo action. This alpha
supports one shared password, not different credentials for each physical unit.
Indigo stores the password in plugin preferences; the UI masks it.

## Troubleshooting discovery

Gen2–Gen4 discovery is implemented. Gen1 discovery is still planned.

If Indigo logs `Bonjour discovery unavailable (ModuleNotFoundError)`, check the
installed `Shelly.indigoPlugin/Contents/Packages` directory. It must contain
`zeroconf`, `ifaddr` and `websocket`, along with their distribution metadata.
An installed copy lacking this directory cannot discover or communicate with
devices. From v0.1.2, dependencies are included in the checked-in bundle as well
as the release asset. Install the complete current bundle to replace an older
incomplete installation. Startup now reports the missing library explicitly.

After installation and restart, look for `Shelly discovery started` in the Indigo
Event Log. That confirms the browser started; successful device connections and
`Created ...` messages confirm that supported channels were found. If discovery
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

The build writes `dist/Shelly-0.1.2.indigoPlugin.zip` using the checked-in portable
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
