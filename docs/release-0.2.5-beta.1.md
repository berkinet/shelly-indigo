# Shelly 0.2.5 beta 1

Plugin configuration now offers **Lost device notification delay (seconds)**.
Choose 0–86400 seconds after the first detected failure; the default 0 preserves
immediate logging. Saving applies the setting without a plugin restart.

A device that reconnects during the grace period produces neither an unavailable
warning nor a recovery message, in either the Indigo Event Log or plugin file log.
Indigo offline/error states still update immediately. Retries do not restart the
delay. A sustained outage logs the latest failure when the delay expires, without
waiting for another retry, followed by normal reason-change/recovery reporting.
Physical identity, IP-change messages, disabled communications and existing Indigo
bindings are preserved. Changing the delay also applies to pending outages.

Validation covers short/sustained/repeated outages, deadline delivery without new
failures, latest-reason reporting, immediate/default behavior, live Save/Cancel,
communication disable, identity discovery and IP changes. The existing transport
and recovery suite, source/archive bundle checks and Ubuntu/macOS CI gate publication.
No physical-device outage or native configuration-dialog check is claimed for this
release. Requires Indigo 2026.1/API 3.9. Install the archive to activate the update;
building/publishing does not install it on the live server.
