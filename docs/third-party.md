# Third-party dependencies

The plugin's own code is MIT-licensed. Its separately bundled, unmodified
libraries retain their own licenses. Source Python files and distribution
metadata/license notices are included in Contents/Packages in release archives.

| Distribution | Version | License | Upstream source |
| --- | --- | --- | --- |
| websocket-client | 1.9.0 | Apache-2.0 | https://github.com/websocket-client/websocket-client/tree/v1.9.0 |
| zeroconf | 0.148.0 | LGPL-2.1-or-later | https://github.com/python-zeroconf/python-zeroconf/tree/0.148.0 |
| ifaddr | 0.2.0 | MIT | https://github.com/ifaddr/ifaddr/tree/0.2.0 |

zeroconf is built from its source distribution with its optional Cython
extensions disabled (SKIP_CYTHON=1). The distributed modules are editable Python
source, usable on Intel and Apple Silicon; no native library is linked into the
plugin. Users may replace these modules under their original licenses.
