# Recorded device responses

These are sanitized read-only HTTP RPC result bodies captured on 3 November 2026
using `tools/capture_device.py`. Each file records its actual model, generation,
firmware build, capture date and methods. Gen2 Pro fixtures preserve compatibility;
new feature development remains focused on Gen3/Gen4.

Identities, MAC/Bluetooth/BSSID addresses, IP addresses, SSIDs, device names,
location and any credential values are synthetic. Keys, nulls, component sets,
configuration flags and metering values retain the captured firmware structure.
The capture verifies identity and firmware again at completion. No setup,
administration or output commands are issued by this tool.

`test_recorded_devices.py` wraps these result bodies in generated request-ID
response envelopes and feeds the production Session, Worker, inventory and Indigo
adapter. It does not present the envelopes as recorded WebSocket traffic. Loopback
simulator tests cover interleaving, notifications and failures; neither fixture
replay nor simulation replaces physical control/press-release validation.

To add a fixture, use a unique ordinal from 1–200, then inspect all output before
committing it:

```zsh
python3 tools/capture_device.py HOST --ordinal N --output tests/fixtures/recorded/NAME.json
```
 Never save unsanitized responses to the repository.
