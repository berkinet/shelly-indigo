Fixes undefined metering-state updates after changing an Indigo device's type.

The reported device had Indigo type Shelly Input but retained its switch:0 relay
binding. Input devices do not define powerWatts, voltage, currentAmps or energyWh.
An older validation fallback allowed this incompatible binding to be saved.

- Changing type clears an incompatible old selection and connection details.
- Offline saved-channel fallback now requires a channel matching the Indigo type.
- Existing mismatched bindings receive an actionable error, with state updates
  and control commands blocked. The plugin does not delete or recreate them.
- Both switch and input state definitions refresh on startup. Valid devices with
  stale state lists also refresh before updates, preserving their IDs and names.

For an existing switch:0 device accidentally typed as Shelly Input, change it
back to Shelly Switch and select its switch:0 channel. Use the existing input:0
Shelly Input device to monitor the physical button. No Shelly hardware settings
need changing.

Validation: read-only Indigo inspection confirmed the actual mismatch. Regression
tests reproduce that configuration and stale metering schemas, reject undefined
state updates, and check invalid bindings cannot send relay commands. The full
automated suite and isolated source/release startup checks pass. The corrected
release still needs confirmation in the actual Indigo Plugin Host.
