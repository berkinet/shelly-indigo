Clarifies why an already configured Shelly channel is missing from New Device.

When manually entered Shelly ID/channel fields match an existing Indigo device,
validation now names that device and its Indigo ID instead of showing only the
generic selection error. Assigned channels remain hidden from New Device;
Edit Device retains its own channel. New bindings still require selecting a
channel from the dropdown. This change does not add manual-host configuration.

Regression checks cover blank/placeholder selections with an existing binding,
continued duplicate prevention, and successful editing of the original device.
Existing discovery, control and input behavior is preserved. Tests and source/
archive bundle checks pass.

Requires Indigo 2026.1 / API 3.9. Install the attached
`Shelly-0.1.12.indigoPlugin.zip`. Existing Indigo IDs, names and bindings are
preserved. No home devices were modified or operated. This is an alpha release.
