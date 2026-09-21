> Historical record of the pre-transition implementation. Current source and licensing: [SOURCE_ORIGIN.md](../SOURCE_ORIGIN.md).

# Unified 1Cat Studio

## Consolidated source

The Studio and “1cat-studio Agent 集成” tasks used the same working repository;
the latter was a task name, not a separate Git branch. The shared working branch
was `onecat/studio-v1`, based on Unsloth commit
`afeb2778f4a7e43dc3a0bdf2d2323b8dba6fd13d`.

All 5,156 source files of deployed build `0.4.0-40183fb74f` matched the shared
working tree when the private repository was prepared. This baseline already
incorporated the Agent follow-up fixes from build `0.4.0-19c174d528` and the
creative-canvas audit fixes. The Agent user manual is also explicitly included.

The private repository consolidates this content on `main` with one product
entry point; no parallel Agent application or second inference manager is added.
Upstream code, copyright notices and licenses are preserved. The initial import
does not claim a synthetic merge history for changes that were previously uncommitted.

## Shared integration points

- `backend/onecat/app.py`: authenticated management, Agent and creative API routers.
- `frontend/src/onecat/app.tsx`: shared Chat, Agent, canvas and management navigation.
- `backend/onecat/agent/`: Codex adapter, project storage, lifecycle and model bridge.
- `backend/onecat/creative/`: canvas storage, assets, generation jobs and model services.
- Shared runtime selection, telemetry, authentication, task persistence and installation.

The import makes the Agent browser executable configurable and derives the host
home path in the sandbox acceptance test, avoiding machine-specific cache paths.
Neither change alters inference, GPU policy or user data.

## Validation and boundaries

The baseline passed 254 backend tests, 10 1Cat frontend tests, TypeScript and
production build, plus CPU-only Agent/canvas/chat browser acceptance. Details and
the 14 pre-existing upstream frontend test failures are recorded in the two audit
documents. Real GPU model quality/performance acceptance remains separate.

Source and private runtime state remain separate. Repository visibility controls
GitHub access; an already deployed Studio instance has its own access controls.
Older deployments served their bundled `source.tar.gz` through the public static
handler. Current `main` requires a Studio administrator session for this archive,
including encoded-path aliases and range requests. The Settings download remains
available to administrators. Upgrading an instance installs this check; private
repository visibility alone does not update older deployments or revoke copies
of previously distributed source.
