# Shelly 0.2.2 beta 1

Configuration now follows device revision changes and configuration events, with
fifteen-minute fallback reads (sixty seconds when revisions are absent or optional
reads fail). Optional Gen4 button sampling runs outside the RPC command thread;
connection/configuration and per-input event fences reject stale results.

Device dialogs, catalog lifecycle and button subscriptions now have separate
services behind explicit Indigo callbacks. Credential storage and legacy password
fallback remain unchanged, as requested. Each RPC session has a distinct client
source ID so concurrent diagnostic/Indigo connections do not compete for routing.

Four sanitized real-device RPC captures are replayed through the production
protocol, worker, inventory and Indigo adapter. The revised worker passed physical
On/Off/Toggle, absolute/relative dimming and external-change checks on the selected
Pro outputs; original output and retained brightness states were restored. A real
Gen4 press/release produced On/Off in the worker/model and installed Indigo input.

Release tooling accepts successive beta/rc tags, alternate notes and titles, and
stable tags without replacing existing assets. Local tests, source/archive loader
and dependency checks, and the Ubuntu/macOS CI workflow gate publication.

This is a beta. The installed Indigo plugin remained 0.2.1 during isolated worker
hardware tests; native 0.2.2 archive installation/dialogs, held-at-startup input,
protected hardware, meter accuracy and real network interruption remain explicitly
unverified. See [hardware validation](hardware-validation.md) for evidence and limits.
No device administration functions were added. Requires Indigo 2026.1/API 3.9.
