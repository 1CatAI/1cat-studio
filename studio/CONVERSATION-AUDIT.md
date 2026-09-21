# Unified conversation entry — 2026-09-09

The sidebar now has one Conversations entry. Its centered Chat / Agent switch
uses the existing 200 ms selection motion and respects both the interface-motion
setting and reduced-motion preference. Existing deep links remain valid.
Browser-tab storage remembers each mode's location. Drafts and project selection
continue to use their existing storage; no history or task data is migrated.

Validation:
- 14 targeted frontend tests, TypeScript and production build.
- Actual Codex sandbox with a deterministic CPU provider: task execution, mode
  switching while running, resume, cancel, files and split preview.
- Chat generation continues across mode changes and restores the draft; the
  persisted answer completes with its original finish reason.
- New/history drafts, explicit chat-context handoff, sidebar return, refresh,
  back/forward and arrow/Home keyboard navigation.
- 1440/1024/768/390/320 px layouts with no overlapping header controls or page
  overflow; Chinese/English, light/dark and reduced motion.
- Existing long-reasoning scrolling, stream animations, code previews and
  preview isolation regressions pass; no React errors.

Second visual review found new-task Agent pages automatically following the
bottom of their welcome content on small screens. They now open at the top;
running task output retains follow-scroll behavior.

No inference settings, GPU policies, driver configuration or model weights are
changed. These checks exercise UI/integration behavior, not GPU model quality.
