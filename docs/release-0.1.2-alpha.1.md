Fixes discovery startup failures caused by installing an incomplete plugin bundle.

- The GitHub repository now includes all portable runtime libraries in
  Shelly.indigoPlugin/Contents/Packages, with their original licenses. Installing
  the repository's bundle directly now includes the same dependencies as the
  release asset. No pip installation or internet download is needed at runtime.
- Startup checks required libraries and reports a specific missing dependency
  rather than continuing with a generic discovery error.
- Event Log messages identify discovered endpoints and successful RPC connections.
- CI validates both the repository bundle and the release archive in an isolated
  Indigo-style execution context, without relying on site-packages or __file__.

Read-only hardware validation on the development network: Bonjour discovery found
11 Shelly endpoints and RPC successfully read device information and status from
all 11 using only the bundled libraries. No physical outputs were operated and
no Indigo devices were created by this validation. Full Indigo device creation
and output control still need validation. Automated tests and package checks pass.

Install the attached Shelly-0.1.2.indigoPlugin.zip, or the complete plugin directory
from the repository, replacing the older installation. Requires Indigo 2026.1.
Gen2–Gen4 switches, binary inputs and switch metering are supported in this alpha;
dimmers/lights, Gen1 and other component types remain planned.
