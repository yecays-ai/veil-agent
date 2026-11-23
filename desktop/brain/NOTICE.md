# Third-party notice · mnemory UI

`desktop/brain/ui/` contains a modified copy of the mnemory management UI.

- Source: https://github.com/fpytloun/mnemory
- Fixed revision downloaded for this prototype: `0ff5a8d` (tarball snapshot, 2026-11-23)
- License: Apache License 2.0, full text in `LICENSE-mnemory.txt`
- Copyright: Filip Pytloun and mnemory contributors

## Changes made by VEIL

The following files were modified after vendoring:

- `ui/index.html` — Chinese labels for the tabs/detail/health strings used by this
  prototype; the VEIL read-only banner; VEIL's status/source/event/version rows in
  the memory detail dialog; a `?tab=` deep-link helper; modal transitions disabled
  so the desktop capture is stable.
- `ui/js/app.js` — `?tab=` deep-link support; one `mnemory:tab-changed` dispatch for
  deep links; Chart.js animation disabled for the desktop prototype.
- `ui/js/memories.js` — `?memory=` and `?detail=` deep links used by the entry and by
  screenshot verification.
- `ui/js/fsck.js` — `?check=1` deep link; Chinese group labels.
- `ui/js/veil-readonly.js` — new file; hides controls the read-only server rejects.

`ui/css/app.css` and the vendored libraries (`alpine.min.js`, `chart.umd.min.js`,
`d3.min.js`) are unchanged. The compatibility API and all brain.db mapping live in
`desktop/brain/server.cjs`, not in the upstream UI.

No mnemory memory backend, Qdrant, embedding model, or mnemory Python package is
included or required.
