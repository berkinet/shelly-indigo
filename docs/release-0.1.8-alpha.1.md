Improves input startup state and removes the misleading red waiting error.

- Unknown button position is now normal Unknown text with a neutral icon, not
  an Indigo error or disabled device. Startup Connecting is neutral for inputs;
  actual offline and invalid-configuration errors remain visible.
- Binary RPC inputs initialize from their reported position as before.
- Gen4 non-inverted button inputs can initialize from the physical input bit
  through an already-enabled Modbus server. The plugin verifies the Modbus MAC
  matches the RPC device, uses read-only functions and applies a bounded timeout.
- The optional path requires the standard RPC endpoint (port 80) and reachable
  Modbus port 502. It never enables Modbus or changes relay/input configuration.
- Missing, unsupported or failed reads fall back to events. They never become
  assumed OFF. Input display uses inputStatus, and stateKnown distinguishes a
  current reading from the retained last raw onOffState.
- Initial physical samples supersede button events buffered before that sample;
  subsequent press/release events continue updating the input normally.

Hardware validation: the owner's Gen4 already had Modbus enabled. Read-only
checks verified matching identity and obtained an initial OFF reading through
this complete worker/model path while the regular RPC input state was null.
Held/ON reads and startup event ordering are covered by protocol/WebSocket
simulation. The held-across-restart test in the updated installed Indigo plugin
still needs the owner's confirmation.

Input/lifecycle test review preserved valuable regressions, updated obsolete
waiting-error expectations and added coverage for both positions, fragmented and
failed Modbus replies, identity mismatch, neutral Unknown and real Offline.
Automated tests and source/release bundle checks pass.

Requires Indigo 2026.1. Install the attached plugin package; existing Indigo
device IDs and names are preserved. No hardware settings or outputs were changed.
