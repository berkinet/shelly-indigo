# Shelly 0.2.6 beta 1

**Plugins → Shelly → Log Device Inventory…** writes one table per physical device:
IP Address, MAC Address, Name, Model, Status and Last seen, sorted by IP address.
Local LAN only is the default and uses five seconds of Bonjour browsing plus the
plugin's existing identity-checked discovery/communication. Known offline devices
and existing Indigo bindings remain included. It is not a full subnet-address sweep.

Last seen is Online, a retained last-successful-local-contact date/time, or Never.
Only local observations determine online status. History persists across restarts,
while online state is verified again. The feature cannot recover observations from
before its installation for offline bindings, which initially show Never.

Optional **Local LAN + Shelly account** uses direct browser sign-in with Shelly on
the Indigo server Mac and merges the account inventory. It can include cloud-only
offline devices the plugin has never seen. Codes/tokens remain in memory only for
that report, and each report requires fresh authorization. A temporary localhost
callback, random state/path, five-minute timeout, bounded HTTPS reads and no
redirect following protect the flow. Cancel Inventory Report ends a pending job.

Cloud mode is experimental and uses Shelly's personal/DIY client ID. App-only names
appear only if supplied by the inventory endpoint. Cloud status never overrides
local status or last-seen dates. The account query cannot control/configure devices,
start workers from cloud metadata or create Indigo bindings. Existing local device
password handling remains unchanged.

Focused regression, source/archive bundle and Ubuntu/macOS CI checks gate release.
The OAuth callback and inventory behavior have automated regression coverage.
Assigned names are optional in the documented response; local names and identity
fallbacks remain available. Native menu rendering remains unverified. Account
inventory data is excluded from public validation notes.
Requires Indigo 2026.1/API 3.9. Install the archive to activate this update; building
and publishing do not install or restart the live plugin.
