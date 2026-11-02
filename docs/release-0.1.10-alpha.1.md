Adds native Shelly Dimmer support, Button Pressed triggers and selectable logging.
This release includes the changes tested in the unpublished 0.1.9 and 0.1.10 builds.

- **Shelly Dimmer** maps Gen2–Gen4 `light:*` channels to Indigo dimmers with
  On, Off, Toggle, Set Brightness, Brighten By and Dim By, plus available power,
  voltage, current and energy readings. An off output displays 0% while preserving
  Shelly's retained turn-on brightness. Setting 0% turns the output off.
- **Button Pressed** triggers select an existing Shelly Input in button mode and
  fire on live physical presses, honoring Indigo conditions. Releases, duplicate
  notifications, startup samples and buffered connection-setup events do not fire
  the trigger. Disable a previous state-change trigger for the same action to
  avoid executing it twice.
- **Info / Debug Event Log levels** apply immediately from plugin preferences.
  Discovery and query messages use Debug; warnings and errors appear at either
  setting. The separate plugin log file retains Debug detail.
- Query summaries list reported components and channels available for creation.
  Warnings explain unsupported or disabled channels, including partially supported
  devices. Unchanged polls and reconnects do not repeat the same warning.
- The physical-device selector shows discovered units even when none of their
  channels are supported. Its refresh button reloads discovery results while the
  dialog is open; Debug records the choices supplied to Indigo.
- New Device selectors hide channels already assigned to another Indigo device.
  Edit Device retains its own channel, including before rediscovery or while
  offline. Disabled devices retain their bindings; deletion releases the channel.
  Other unused channels on the same physical Shelly remain available, and
  save-time validation still prevents duplicate assignments.
- The explicit creation command includes a Dimmers option. Native universal
  status and energy-update actions request fresh readings.

Validation: protocol tests, the strict Indigo API double and a localhost Shelly
WebSocket simulator cover control dispatch, state reconciliation, metering,
binding validation, input triggers and logging. Source and packaged bundles pass
isolated Indigo-style startup and bundled-library checks; GitHub checks pass.
The focused dimmer/selector test review retained switch/input regressions and
updated former unsupported-light cases to unsupported covers.

Read-only checks of the owner's Gen3 dimmer and Gen2 Pro dimmer successfully
translated light output, brightness and metering. Physical dimmer control still
needs hardware validation; simulated control checks do not establish that. No
physical outputs or device configuration were changed during these checks.

Requires Indigo 2026.1 / API 3.9. Install the attached
`Shelly-0.1.10.indigoPlugin.zip`; the complete bundle includes its dependencies.
Choose Shelly Dimmer in New Device, or select Dimmers in
Shelly → Create Indigo Devices…. Existing Indigo device IDs and names are
preserved. Discovery continues without implicitly creating devices. This remains
an alpha; Gen1, covers, color lighting and per-device credentials are not included.
