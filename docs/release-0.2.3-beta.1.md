# Shelly 0.2.3 beta 1

Connection outage warnings now identify the physical Shelly by its known local
name/device ID and address. They continue to list active Indigo device names and
IDs, and explicitly say **no Indigo devices assigned** for unassigned discoveries.
Repeated Debug failures and recovery messages carry the same context. A device
identified before its first status/configuration read can use the public identity
cached in the catalog; an unidentified endpoint is reported by address.

Regression coverage exercises assigned/unassigned units, name and ID fallbacks,
pre-ready failures, unidentified probes, warning deduplication and recovery.
Existing communication-disable regressions remain in place. The two previously
noted unused plugin imports were removed.

Tests, source/archive loader and dependency checks, and the Ubuntu/macOS CI
workflow gate publication. Requires Indigo 2026.1/API 3.9. Install the archive in
Indigo to activate this update.
