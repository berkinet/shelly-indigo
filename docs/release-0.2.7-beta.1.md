# Shelly 0.2.7 beta 1

The device inventory report now includes **Indigo device name** immediately after
the Shelly Name column. A physical Shelly with multiple Indigo channels lists all
assigned Indigo device names, separated by semicolons, in its single inventory row.
Offline and disabled channels remain included; unassigned devices show `—`.

Each report reads the current Indigo names, so renames appear on the next report.
Indigo names are kept separate from the locally configured Shelly name and are
not saved in the physical-device history or sent to Shelly Cloud. Matching continues
to use physical identity/MAC rather than IP address. Both report modes use the column.

A focused adapter regression covers multiple channels, disabled/offline assignments,
cloud identity merging, unassigned devices, renames and history persistence. Tests,
source/archive bundle checks and Ubuntu/macOS CI gate publication. Requires Indigo
2026.1/API 3.9. Install the archive to activate the update; building/publishing does
not install or restart the live plugin.
