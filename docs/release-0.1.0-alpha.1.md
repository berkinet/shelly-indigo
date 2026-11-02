First development alpha of the new Shelly for Indigo plugin.

Supports automatic Gen2–Gen4 discovery, switch and binary-input channel creation,
on/off/toggle actions, switch metering and WebSocket state notifications.
Includes reconnect handling, periodic reconciliation and optional shared-password
authentication. The plugin archive includes portable dependencies and licenses.

Requires Indigo 2026.1 / Python 3.13. Gen1 and other component types are not yet
implemented. See the README for installation instructions and limitations.

Validation: automated protocol/adapter/discovery tests, localhost WebSocket
integration, and archive structure/import checks. **Not yet tested on physical
Shelly devices or loaded in an Indigo Plugin Host. Use a development installation.**
