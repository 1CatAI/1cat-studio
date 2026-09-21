# Product experience upgrade · 2026-09-07

Authorized scope: implement the product review in `.artifacts/product-review-20260907/review.md`.
Keep ModelScope downloads, measured token timing, existing animation and preview isolation.

- [x] Persistent chat generation; reconnect, cancellation and navigation tests.
- [x] Model discovery, compatible-first cards, download-to-chat actions and preserved browsing state.
- [x] Basic/advanced profile editor, fixed actions, MTP segments and explicit capability/application state.
- [x] Comparable power modes, applied-state feedback and matching measurement summaries.
- [x] Service overview, scoped usage trends/filters, cache semantics and collapsible launch history.
- [x] Chat suggestions, actual acceleration labels, preview preferences and conversation organization.
- [x] Consistent controls, restrained motion, readable responsive layouts, settings and error feedback.
- [x] Regression audit, focused tests, production build and pre-upgrade data backup.
- [x] Independent migration/rollback validation, deployment and public verification.

Local baseline: `.artifacts/product-upgrade/before-20260907.tar.gz`.
Installed baseline: `0.2.8-a9011b8f31-ui`; preserve the vLLM environment and resident engine during manager updates.

## Validation before deployment

- 91 backend tests passed, including browser-independent runs, explicit cancellation,
  restart recovery, usage filtering and exact time-bucket boundaries.
- Frontend TypeScript check and production build passed; 6 artifact/streaming unit tests passed.
- Full browser regression passed: preview isolation, React and HTML, incomplete code,
  long reasoning and code animation, user scrolling, mobile and English/dark mode.
- Product journeys passed: routes, threads, offline/reconnect and reload keep one run;
  drafts restore; explicit stop works; model tabs and fixed profile actions remain usable.
- The final response toolbar keeps its DOM identity across completion; x/y/width/height
  change by less than 1.5 px in the desktop continuity replay.
- Download journeys passed: rates, cancellation/retry, restored progress, runtime
  guidance, exact recommended profiles, mobile and English.
- Audit fixes included dialog centering, preserving newly typed drafts during startup,
  server/browser message ordering, scoped in-flight totals and inclusive timeline edges.
- A final missing-hardware check preserves all three power cards with an explicit
  unavailable reason when the saved GPU selection disappears; hardware writes remain
  blocked. The observability suite passes with this additional regression.
- Public verification found the resize handle overlapped the first 5 px of the
  preview, intercepting small controls at the left edge. The handle now occupies a
  dedicated gap between the panes, clear of both preview content and chat scrolling;
  an 8 px inline-handler button and pane geometry checks cover this regression.

Browser continuity does not imply process restart continuity: a Studio manager restart
interrupts its active requests and preserves partial output with an explicit status.
GPU/vLLM restarts and power changes are not part of the UI update. Tests use isolated
fixtures unless explicitly identified as production verification. The accelerated long
reasoning stress replay retained animation with 988 active nodes (zero after settling);
its maximum recorded long task was 828 ms, so this is not a claim of constant 60 fps.

Pre-upgrade data copy: `before-product-upgrade-1788792031` in the remote Studio backups
directory; includes both SQLite databases and the attachment tree. Validation logs and
screenshots are in `.artifacts/product-upgrade/`.

## Deployment receipt

Published `0.3.0-7e3364551c` at `http://dx.1catai.com:52475/`. The public index bytes
match the release; the installed source archive checksum matches the local package.
The manager service is active. The original `0.2.8-a9011b8f31-ui` installation remains
available. Final data backup: `product-upgrade-1788794009`.

An independent copy of the actual SQLite databases passed migration and old-version
readback without changing user-record hashes: 16 threads, 80 messages, 4 profiles,
4 model records, 3 benchmark groups, runtime records, request history and credentials.
Public browser checks passed model tabs, fixed profile actions, power cards, scoped
usage, interactive split preview (including the left-edge button), mobile layout and
release matching, with zero captured JavaScript errors. Temporary preview conversations
were removed after each run. Detailed receipts: `.artifacts/product-upgrade/deployment.json`
and `.artifacts/product-upgrade/public-verification.json`.

Hardware limitation observed before activation and reconfirmed afterward: the server
enumerates only its Quadro P400; all four V100 devices are absent, and the previous
vLLM process had already exited. No GPU inference run was performed for the production
acceptance. Browser-independent generation was verified with isolated streaming
fixtures. GPU visibility and inference recovery are separate outstanding server work.

This receipt was appended after packaging; it records deployment validation of the
named source snapshot rather than changing the deployed application.
