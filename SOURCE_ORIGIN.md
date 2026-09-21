# Source boundary and license transition

This source tree contains the 1Cat Studio application. Its application license
is `LicenseRef-1Cat-Community-1.0`; see [LICENSE](LICENSE). This is source-available
software with specific distribution restrictions, not OSI open-source software.

## Historical baseline

The previous Studio release, including commit
`4b7a44245e860703a22bcaeed57cdfe595bcecce`, incorporated Unsloth Studio from
`unslothai/unsloth` at `afeb2778f4a7e43dc3a0bdf2d2323b8dba6fd13d`.
That historical code remains under its historical licenses. Nothing in this
transition withdraws recipients' rights to earlier AGPL-covered releases.
The earlier Git history is retained separately in the private historical
repository as a record, not relicensed by the new root license. The public
repository starts from the current implementation snapshot, based on private
main commit `26e2720e74cf7a77f0d82bc842f82c110f0bf248`.
Historical implementation and audit documents describe those earlier releases.

## Current implementation

| Boundary | Current source / dependency | Compatibility retained |
| --- | --- | --- |
| Conversation storage | `studio/backend/onecat/chat_store.py`, Python SQLite | Existing database tables, JSON fields, IDs, timestamps and unknown columns |
| Credentials | `studio/backend/onecat/passwords.py`, Python hashlib/hmac | PBKDF2-SHA256, 100,000 iterations, textual UTF-8 salt and hex digest |
| Controls, dialogs, tooltips | `studio/frontend/src/onecat/ui.tsx`, upstream Radix (MIT) | Existing application props, keyboard interaction and layout |
| Language and errors | `locale.ts`, `http-error.ts` | Saved language preference and FastAPI error responses |
| Markdown | Official Streamdown / Shiki / KaTeX packages and 1Cat adapters | Streaming blocks, code themes, math, safe links and preview actions |
| Download status | `download-progress.tsx` and existing 1Cat rate measurement | Progress, size, throughput and remaining time |
| Styles and build entry | `styles/base.css`, Vite configuration and HTML entry | Existing 1Cat pages, colors, navigation and interactions |

The Unsloth Python packages, CLI, frontend component tree, storage/auth/utils
modules, forked renderer and training/inference implementations are excluded
from this tree and from the release source allowlist. The Studio application
does not import or install Unsloth. The inference engine, downloaded model
weights and optional creative runtimes remain separate components under their
own licenses.

The infinite canvas viewport is an explicitly attributed MIT adaptation; see
`studio/third_party/infinite-canvas/LICENSE`. Frontend npm packages retain their
own licenses and generated notices. The standalone Codex runtime remains Apache-2.0.
Inter and Space Grotesk are OFL-1.1 fonts. Hellix is no longer redistributed:
its redistribution permission was not present in the repository. Space Grotesk
is the existing heading fallback; English heading shapes can therefore differ.

## Verification and limits

The transition audit compared application imports and tracked paths against the
historical baseline, identified and replaced the copied password helper, and
checked for exact overlapping blocks of 20 significant normalized source lines
against that upstream commit. No such blocks remained in the application after
replacement. This comparison is evidence about that snapshot, not a guarantee
of authorship or an exhaustive copyright analysis.

`python3 studio/scripts/check-source.py` enforces the application import and
release boundaries. Packaging uses an explicit allowlist rather than all Git
files, works from an extracted source archive, rejects symlinks escaping that
boundary, and runs the check before packaging. Frontend builds include
`license.txt` and `third-party-notices.txt`. Permissively licensed components
keep their notices and permissions; application restrictions do not apply to
their independently licensed source.

Account/login fixtures and older database fixtures test data compatibility.
The existing backend/frontend suites and browser flows check application
behavior. Browser checks use mocked model services; they do not establish
real GPU throughput or model quality. Record exact results in
`studio/docs/independent-source-validation.md`.

Before offering a commercial license, the project owners should verify their
rights to license contributor-owned application changes and have the final
commercial terms reviewed. Automated source checks cannot establish either.
