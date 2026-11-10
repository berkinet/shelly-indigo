# Shelly 0.2.8 beta 1

The inventory report now leaves IP Address blank for offline devices, including
those whose Last seen value is Never. Online devices continue to show their current
address. The report footer reflects this display rule.

Cached endpoints remain intact for identity matching, history and reconnection.
Existing inventory regressions verify visible online addresses, hidden offline and
Never addresses, retained timestamps and retained internal endpoints. Source/archive
bundle checks and Ubuntu/macOS CI gate publication. Requires Indigo 2026.1/API 3.9.
Install the archive to activate the update; building/publishing does not install or
restart the live plugin.
