Separates continuous Shelly discovery from Indigo device creation.

- Discovery keeps running and fills device selectors as new supported Shellys
  join the network. It never creates Indigo devices implicitly.
- Use Shelly → Create Indigo Devices… in the plugin menu to create missing
  channels for one Shelly or all discovered devices. Select switches, inputs,
  or both; inputs start unchecked. The command runs once and is safe to rerun.
- The old automatic-creation preference is removed and stored legacy values are
  ignored. Existing Indigo devices, names and IDs are preserved and keep updating.
- Individual creation through New Device remains available.

The plugin now reads the local user-assigned name from Sys.GetConfig.device.name
and uses it ahead of the internal ID, for example:
Garden Gate (shelly1pmminig4-…) input:0.

Names are refreshed during status reconciliation and appear in selectors and
newly created Indigo devices. Existing Indigo names are not overwritten. Names
assigned in the app are available when stored locally on the device; cloud-only
app labels are not exposed by this local API and are not retrieved. Two sampled
devices on the owner's network returned null local names, so fallback to the
internal ID is expected unless a local name has been set.

Validation: creation tests now explicitly invoke the menu command; new coverage
verifies discovery/reconnect/events never create devices even with legacy
preferences, switch/input and physical-device scoping, duplicate avoidance,
local-name refresh, and preservation of Indigo renames. Existing protocol,
button, binding/schema and lifecycle regression coverage is retained. Automated
checks and isolated source/release startup tests pass. Menu UI operation still
needs confirmation in the actual Indigo client.

Requires Indigo 2026.1. No Shelly settings or physical outputs were changed by
the naming diagnostics or by this workflow change.
