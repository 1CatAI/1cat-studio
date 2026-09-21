# Licenses and attribution

The original 1Cat Studio application in this source tree uses the
1Cat Studio Community Source License 1.0; see the root `LICENSE` and
`studio/LICENSE`. Third-party components retain their own licenses.

Previous releases incorporated Unsloth Studio at
`afeb2778f4a7e43dc3a0bdf2d2323b8dba6fd13d` and were distributed under AGPL-3.0.
Those releases and their historical notices are not relicensed. The current
application replaces the previous storage, credential helper, UI wrappers,
locale/error helpers, Markdown fork and build scaffolding. See `SOURCE_ORIGIN.md`
for the migration boundary and audit limitations. Unsloth is not a dependency
of this application.

## Frontend and fonts

React/ReactDOM, Radix, TanStack Router, Streamdown, Shiki, KaTeX, Motion,
Lucide, Recharts, Sucrase, Tailwind and other npm dependencies are independently
licensed packages. Their full notices are collected during the build in
`/third-party-notices.txt`, including notices for browser code previews.
The locked package versions are in `frontend/package-lock.json`.
Inter and Space Grotesk fonts use OFL-1.1. Hellix font files are not distributed.
DOMPurify is used under its Apache-2.0 option. Applicable Apache notices are
retained. Build tools are not automatically included in the application bundle.

## Python and external runtimes

Python dependency versions and hashes are in `requirements.lock`. Their license
files remain in their distributions and in the bundled environment. Python,
uv, the separate inference engine, creative runtimes and model weights retain
their respective licenses. This application's license grants no additional
rights in those components or in model weights.

## Codex Agent runtime

Studio's adapter connects to the official OpenAI Codex 0.153.4 executable as a
separate component. Source: https://github.com/openai/codex/tree/rust-v0.153.4.
The Apache-2.0 license and NOTICE are included with the verified runtime at
`studio/vendor/codex/0.153.4/`. Studio does not include OpenAI model weights,
Codex cloud, or the proprietary IDE extension.

## Creative canvas

`frontend/src/onecat/canvas/viewport.tsx` is adapted from basketikun/infinite-canvas,
commit `d213a74614e0e4bd8a26383d1e1e907249e9c61b` (MIT, copyright 2026 basketikun).
Its MIT header and complete license in `third_party/infinite-canvas/LICENSE`
are retained. Studio changes cover theme, focus handling, pointer cleanup and
zoom limits. Canvas persistence, model adapters and generation jobs are 1Cat
implementations.

## Distribution

Each manager bundle includes corresponding application source at
`/source.tar.gz`, the application license and third-party notices. Source archives
exclude user data, model weights, caches and generated runtimes. The application
license does not restrict rights granted by third-party copyright holders.
