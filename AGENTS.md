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
- Per-device credentials are the intended design; the alpha's shared password
  preference is temporary.
