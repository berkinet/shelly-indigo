# Project working instructions

- Use zsh for commands on this computer.
- Commit and push each completed project change to GitHub unless the owner
  explicitly instructs otherwise. Keep source and documentation current there.
- For each plugin release, publish a GitHub Release with release notes and the
  complete installable `.indigoPlugin.zip` asset. Standing authorization covers
  committing, pushing, uploading and publishing; do not ask each time.
- Build releases with `tools/build.py` and validate them with
  `tools/check_bundle.py`. Also run `tools/check_bundle.py --source`: the
  checked-in plugin must remain directly installable, with portable dependencies
  included. Refresh dependencies with `tools/vendor.py` when requirements change.
  Preserve bundled dependency license notices.
- Run checks appropriate to the change. Review relevant test coverage at stage
  transitions and substantial design changes, with advance notice to the owner.
- Roadmap decision (2 November 2026): target Gen3 and Gen4 for new development.
  Gen1 implementation is removed from the roadmap. Retain existing Gen2
  compatibility and regression coverage, with no planned Gen2-specific expansion.
- Per-device credentials are stored by physical identity in Indigo preferences.
  Keep legacy shared-password fallback limited to existing Indigo bindings and
  explicitly configured hosts; never send it automatically to new unassigned
  Bonjour discoveries.

- Keep the project focused on direct local discovery, state reporting and control
  with few moving parts. Device setup and administration, including firmware
  updates, belong in the Shelly app or device web interfaces.
- Discovery must run continuously without creating Indigo devices implicitly.
  Create devices only through the explicit plugin menu command or an individual
  New Device save. Ignore older automatic-creation preferences.
- Prefer names assigned locally on the Shelly device as prefixes for internal
  IDs in new Indigo names and selectors. Preserve existing Indigo names. Do not
  assume app-only cloud labels are available through the local device API.

- Respect Indigo device communication enable/disable callbacks. Stop and fence a
  physical worker when its last enabled channel is disabled; Bonjour and explicit
  hosts must not restart it. Keep enabled sibling channels working.
- Device-dialog password changes apply only on Save, share one credential per
  physical identity, and must not persist plaintext in device properties. Blank
  keeps the saved credential; explicit No password clears it. Password saves must
  not enable disabled communications.

- Credential-at-rest work is explicitly deferred by the owner (3 November 2026).
  Preserve the documented preferences storage and legacy fallback in this scope;
  do not add encryption, Keychain integration or a credential migration.
- Give every RPC session a distinct source ID. A shared source can redirect
  notifications when a diagnostic session or another Indigo server connects.
- Optional Modbus samples run outside the RPC command thread. Apply results only
  in the owning worker, fenced by connection/configuration and input-event versions.
- Hardware validation uses only outputs explicitly identified by the owner. Restore
  starting output/retained brightness states and record what was actually tested.
  Building or publishing does not install or restart the live Indigo plugin.
