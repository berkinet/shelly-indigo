Corrects misleading discovery warnings and makes existing Indigo assignments visible.

- `infino:*` remains in Debug inventory but no longer produces an unsupported-
  channel warning on a working Shelly. Its presence does not invalidate input,
  switch or dimmer channels.
- Query summaries distinguish supported channels, channels already assigned to
  Indigo devices (with IDs/names), and unassigned channels. Disabled Indigo
  devices still reserve their channels, matching selector behavior.
- Warnings identify unsupported channels on partially supported units. Devices
  with no supported channels retain a separate warning. Routine polls/reconnects
  still suppress unchanged warnings.

Regression coverage checks a configured button input alongside `infino:0`, normal
press updates, assignment changes, and preservation of real unsupported-channel
and no-supported-channel warnings. Tests and source/archive bundle checks pass.

Requires Indigo 2026.1 / API 3.9. Install the attached
`Shelly-0.1.11.indigoPlugin.zip`. Existing Indigo device IDs, names and bindings are
preserved; no replacement devices are needed. No hardware settings or outputs were
changed during this correction. This remains an alpha release.
