# Shelly 0.2.4 beta 1

A Shelly rediscovered at a new IP now logs one address-change message, including
the previous/new endpoints and its existing Indigo device names/IDs. If it was
previously unavailable, the message explicitly reports recovery after the address
change. Repeated ready messages do not repeat that transition. Outages follow
physical identity, so a different unit reusing an address cannot inherit its recovery.

A failed probe of the vacated address now ends after one attempt at Debug level,
instead of retrying and warning that no Indigo devices are assigned. Old Bonjour
hints do not move a healthy unit back to that address. Fresh deferred hints can be
reconsidered after the current connection fails, and a different verified unit can
reuse the address with normal discovery and password onboarding. Generation fences,
communication-disable behavior, Indigo IDs/names and explicit device creation remain.

Regression coverage includes moves, swaps, genuine returns, protected replacement
units and queue overflow. A loopback WebSocket/event-loop test verifies recovery,
finite old probing and command routing to the new endpoint. The original field
DHCP move was observed on 0.2.3; no physical DHCP move with this new code is claimed.

Tests, source/archive loader and dependency checks, and Ubuntu/macOS CI gate
publication. Requires Indigo 2026.1/API 3.9. Install the archive in Indigo to activate
this update.
