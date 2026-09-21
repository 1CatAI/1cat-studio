# Model lifecycle and GPU controls

Studio owns the text service and local H3 services it launches. Loading another model on the same GPUs cancels an unfinished Studio load, waits for teardown, and releases the previous Studio model before launching the new one. Services on other GPUs are unaffected. PID creation time and launch identity protect unrelated processes; external workloads are reported rather than terminated. Finish or cancel an active creative generation before replacing its model.

The canvas inspector and model manager show the same durable load task, phase, elapsed time, diagnostic log, cancel and unload actions. No model PID is required to display a queued request. Counted progress comes from runtime logs; phases without counts remain indeterminate. Runtime configuration announcements and an HTTP parent's startup wait are not evidence that weights or CUDA graphs have loaded. Only the matching health and model checks establish readiness.

An unload request cancels its unfinished load first, then releases tracked workers. Stubborn owned workers receive a bounded SIGTERM grace period followed by SIGKILL; an incomplete teardown remains a failure and can be retried. Loading workers that exit are failures even if their HTTP parent keeps waiting. Historical logs remain available on the server when a new launch begins.

GPU mode changes share the model-operation lock. While a model is loading/stopping or being calibrated, the main control status reports the blocking operation and supports cancelling the wait. Applying settings and confirming readback are distinct from waiting. All selected devices are validated against one hardware snapshot, and the restricted helper still independently checks each write.

## Validation

On the four-V100 host: text → H3 load cancellation → GPU eco/balanced/performance readback → full H3 readiness → automatic H3 release → text readiness and actual response. Mode submissions completed in approximately four seconds each with no conflicting model operation. Original GPU settings were restored after the check. No model downloads, external process termination, or changes to inference parameters were required.

Targeted regressions cover duplicate admission, overlapping versus disjoint GPU selections, load cancellation, orphan/reused process IDs, active creative task exclusion, queued visibility, log progress, and hardware validation. Browser regressions cover logs, cancel, retry, load errors, narrow layouts, and dialog keyboard isolation.
