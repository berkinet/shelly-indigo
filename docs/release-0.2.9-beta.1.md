# Shelly 0.2.9 beta 1

Firmware can change the model prefix of a device's RPC ID while its MAC stays the
same. The plugin now recognizes that as the existing physical device when the
saved ID suffix, reported ID suffix and reported full MAC all agree. Existing
Indigo devices, names, component bindings, passwords and trigger references remain
in place; there is no need to delete or recreate devices.

The saved logical binding remains stable while RPC source and digest-realm checks
use the actual reported firmware ID. Missing or conflicting full-MAC evidence does
not authorize an alias or access its saved password. Different units at a reused IP
retain the existing identity-mismatch behavior. Disabled communication remains
respected under a changed prefix. These are trusted-LAN consistency checks, not
cryptographic authentication of device hardware.

Inventory history and report snapshots coalesce matching full-MAC aliases, so a
stale offline row and a current row no longer appear as separate physical devices.
The resulting row retains current observations and assigned Indigo device names.
One Info notice identifies an accepted firmware alias.

Focused loopback regressions cover protected aliases, source/realm validation,
wrong/missing MAC rejection, disabled communication, stale probes and recovery of
the original input/trigger across a prefix-plus-IP change. Full tests, source/archive
bundle checks and Ubuntu/macOS CI gate publication. Requires Indigo 2026.1/API 3.9.
Install the archive to activate the fix; building/publishing does not install or
restart the live plugin. Public notes and test fixtures contain no owner inventory.
