# Shelly 0.2.1 beta 1

Fixes disabled-device communication and adds password controls to device dialogs.
Existing Indigo IDs, names and bindings are preserved.

## Communication checkbox

Disabling communication now stops the physical worker when its last enabled
Indigo channel is disabled. Pending messages are fenced, retries stop and a queued
timeout cannot continue producing warnings. Startup, Bonjour discovery, explicit
hosts, password saves and recovery paths respect that state.

If another channel on the same Shelly remains enabled, its shared connection stays
active. Queued controls for the stopped channel are cancelled without issuing
hardware commands, including a stop that overlaps a relative-brightness read.
Outage warnings identify the active Indigo channels so the reason is clear.
Discovery continues for other and unassigned units. A new endpoint may receive one
public identity query to recognize a disabled unit, then stops before authentication,
polling or control. Paused channel choices remain available offline.

Re-enable resumes communication using the current endpoint. Learned address updates
avoid restarting their already-verified worker. Deleted devices release their
channels for later discovery and creation.

## Passwords in device configuration

Shelly Switch, Dimmer and Input dialogs now have a masked **Local device password**
field and an explicit **No password** checkbox. Blank keeps the saved credential;
No password removes it. Save applies the change; Cancel leaves it unchanged.
All channels on one physical Shelly share that credential. Offline devices can
have credentials saved before rediscovery, without enabling their communications.

Validation keeps typed plaintext out of saved device properties; the credential
remains in Indigo plugin preferences. This does not encrypt those preferences or
change the hardware's password. The plugin preference password field is removed;
existing stored shared fallback is retained for compatibility. **Set Device Password…**
remains available for initial authentication of units whose channels are not yet
available to create.

## Validation

The reported disabled-device behavior was reproduced before fixing it. Regression
coverage includes shared siblings, stopped callbacks with old enabled IOM copies,
startup/Bonjour admission, late timeouts, changed addresses, deletion, real worker
stop/re-enable, and public-only recognition of a disabled identity. Credential tests
cover all three XML dialogs, Save/Cancel, keep/clear, shared credentials and offline
configuration. Existing Gen2/Gen3/Gen4, protocol, trigger, state and packaging checks
remain in the suite. Hosted macOS checks use the supported standard Intel runner
pool after the ARM runner remained capacity-queued; the complete Ubuntu/macOS
workflow is still required before publication.

Native dialog rendering and the installed-plugin checkbox/password flows still
need owner verification. The live Indigo connector was rate-limited during this
session; no live device was modified and no physical output was operated. See
[hardware validation](hardware-validation.md).
