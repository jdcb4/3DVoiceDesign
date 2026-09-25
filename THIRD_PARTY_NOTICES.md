# Third-party components

The viewer bundles Three.js (MIT) and Lucide (ISC, including Feather-derived
components under MIT). Their full notices are reproduced in
`voicedesign/static/THIRD_PARTY_LICENSES.txt` in built distributions and can be read
at `/THIRD_PARTY_LICENSES.txt` in the running viewer.

Runtime dependencies are installed separately by uv. They retain their own
licenses, including CadQuery (Apache-2.0), OpenCascade (LGPL-2.1 with exception),
and their Python/native dependency stack. Consult the installed distributions for
full license terms. Packaging this app does not relicense those dependencies.

The lockfiles record exact resolved dependencies. The build bundles no remote
fonts, CDN scripts, telemetry, credentials, or personal design files.
