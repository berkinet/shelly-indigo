Fixes the Indigo error "UI dynamic list function returned illegal ID string".

The All discovered devices row and channel-selector placeholders returned an
empty ID, which Indigo rejects. Both selectors now use nonempty placeholder IDs,
with matching XML defaults. All devices still selects the full discovered set;
a channel placeholder cannot be saved as a device binding. Previously saved
valid channel bindings remain supported.

Also includes the previously committed log wording change: Discovered Shelly
endpoint … and Querying … (Gen…), to distinguish discovery/querying from creation.

Naming investigation: all 12 discovered endpoints returned null device and
channel names through their local configuration API. The plugin therefore falls
back to internal IDs. App-visible names were not exposed in those local fields;
this release does not add cloud-account name retrieval or rename existing Indigo
devices. No hardware settings were changed.

Validation: a regression test reproduced the invalid blank ID before the fix;
menu and channel ID checks now pass with populated and empty discovery lists.
The All devices menu default is tested through the creation callback. Automated
regressions and isolated source/release bundle startup checks pass. Actual menu
operation after this fix still needs confirmation in Indigo.

Requires Indigo 2026.1. Install the attached plugin package to replace 0.1.6.
