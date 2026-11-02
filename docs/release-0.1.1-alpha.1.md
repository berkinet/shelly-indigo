Fixes the startup failure reported when installing v0.1.0 in Indigo 2026.1:
`InitializeMain: name '__file__' is not defined`.

Indigo executes the plugin entry point without a `__file__` global. Bundled
dependency lookup now uses the normally imported `shelly` helper package's
location, so it works without relying on the entry point's metadata or the
process working directory.

Adds a regression test reproducing Indigo's execution context and strengthens
archive validation to execute the packaged entry point without `__file__`,
without site-packages, and without preloading the bundled dependency path.

Download and install the attached Shelly-0.1.1.indigoPlugin.zip to replace 0.1.0.
Requires Indigo 2026.1 / Python 3.13. Feature scope is unchanged: Gen2–Gen4 switches,
binary inputs and switch metering. Gen1 and other components remain planned.

Validation: automated tests, including the reproduced startup condition, real
localhost WebSocket integration and isolated release archive checks. The
replacement has not yet been confirmed running in the actual Indigo Plugin Host
or validated against physical Shelly hardware.
