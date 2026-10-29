# Phase 2: memory-helper and submission inspection

This is offline inspection of the preserved game binary, pinned SDK source and
the first live CPU recording. No game/SDK rebuild, GPU run, renderer optimization,
synchronization change, commit or push was performed for this inspection.

## Memory helper: refine the hypothesis

The live recording attributed 420 sampled leaves to the `PhysicalHostOffset`
family. Reading its compiled AArch64 instructions gives a narrower conclusion
than assuming a costly static-initialization guard on every access:

- The standalone helper has an unconditional stack/frame-register prologue.
  For addresses below `0xE0000000`, it then returns zero and restores that frame.
  This path does not read the static guard or query the page size.
- High addresses check the function-local static with an acquire byte load and
  a branch, then read the cached offset. Guard acquisition and the page-size
  query are cold initialization paths, not operations repeated on every access.
- The 420 PCs comprise 309 distinct instruction addresses. Mnemonics include 343
  `mov` samples, 30 `str`, 14 `ldp`, and only two `ldarb` samples. Most `mov`
  samples zero a register on the no-offset path. Some inline attribution also
  spans neighboring compiler-generated instructions.

These are timer-sampling locations, **not instruction cycle measurements**.
Sampling skid, inlining and code motion preclude attributing 13.8% of CPU time to
an acquire load, or predicting savings from deleting a particular instruction.
The evidence supports examining repeated address checks, register pressure and
out-of-line call/frame costs; it does not establish static-guard contention.

The correction remains mandatory: guest physical mappings on this 16 KiB host
require the `0x1000` compensation. The existing CPU-only physical-alias executable
was rerun without rebuilding: it passed with host page size 16384 and offset
4096. Any future proposal must preserve low-address zero offsets, large-page
physical aliases, 4 KiB-host behavior and safe initialization across threads.
Do not disable thread-safe statics or remove the address correction as a test.

Relevant source: the pinned SDK's `include/rex/system/xmemory.h`,
`src/core/memory_posix.cpp`, and the repository's
[`linux-16k-sdk.patch`](../tools/asahi-sdk-lab/linux-16k-sdk.patch).

## Submission thread: not merely vkQueueSubmit

The pinned SDK's `src/graphics/vulkan/command_processor.cpp` shows:

1. `EndSubmission` transfers the deferred streams into a queued job.
2. `SubmissionThread` removes a job and calls `ReplayAndSubmit`.
3. The ordinary path resets/begins the Vulkan command buffer, replays the deferred
   stream into native driver calls, ends recording, acquires the queue and calls
   `vkQueueSubmit`.
4. Only after that host-side path returns does it publish
   `submission_thread_submitted_`. GPU completion is tracked separately by fences.

Of the 167 recorded user-space samples on this thread, caller chains contain:

| Caller presence | Samples |
| --- | ---: |
| `ReplayAndSubmit` | 167 |
| Deferred `ExecuteStream` replay | 146 |
| Command-pool reset | 8 |
| Queue-submit path | 4 |

These are inclusive caller-presence counts and can overlap. Missing callers can
also reflect incomplete unwinding. Driver draw/state/allocation operations are
visible in the sampled leaves. This explains why the thread name is insufficient
to assign all its CPU to the final queue-submit call.

Conversely, the small number of queue-submit **user** samples cannot assign the
previously observed 11.74 s of system CPU to replay, submission or allocation.
Kernel stacks were not captured. Disabling threaded submission would move this
work, not demonstrate its removal; no such experiment was performed.

### Existing counters do not separate the critical path

- `gpu_wait_ms` accumulates both `AwaitSubmissionThread` and `vkWaitForFences`.
  The first may wait for CPU replay/driver work; the second waits for completion.
  Neither is a direct GPU execution timestamp.
- `fh1_submission_watch::Submitted` is recorded in `EndSubmission` **before**
  enqueueing the threaded job. Its timestamp is not the later native Vulkan
  queue-submit timestamp, despite the label. A reported submission age can
  therefore include host queueing and replay.
- The integrated-GPU draw splitter is intended for small carve-outs with another
  usable system-memory heap. The recorded Asahi layout logs a single device-local
  heap and memory staying device-local. Do not assume a 1000-draw split solely
  because the Vulkan device type is integrated, or infer job counts from draws.

The smallest useful measurement plan is a bounded per-job/aggregate record of
job count, enqueue/dequeue delay, reset/begin, replay, end, queue acquisition and
queue-submit wall/CPU costs, plus separate command-processor waits for host
submission and fence completion. Preserve submission IDs for correlation.

No instrumentation was inserted yet. If implemented in Phase 2, worker data must
be thread-owned and published through a bounded, synchronized handoff: the
existing frame-cost `Current()` is shared command-processor state, not a safe
place for writes from the submission thread. Avoid per-draw logging or weakening
any existing lock, fence, memory ordering or device-loss handling.

## Resolution qualification discovered during inspection

The first CPU session requested `--resolution=720p --resolution_scale=1`, but its
log records an initial 1270x1034 swapchain and then **2560x1080** before gameplay,
with no subsequent swapchain-size change logged. Its host presentation output
was therefore not a controlled physical 1280x720 surface.

The pinned SDK's `fullscreen` default is true and `fullscreen_exclusive` is false;
borderless presentation can follow the desktop mode despite a 720p preset.
Internal guest/draw resolution and physical presentation extent are different
controls. The captured command requested 720p/scale 1 and the log has no ultrawide
activation message, but there was no independent native frame capture in this
profiling run to re-establish every internal target extent.

The CPU recording remains useful. It is **not** an exact-output 720p benchmark,
nor evidence of an isolated performance change against earlier routes. The
larger swapchain alone does not prove a particular GPU cost or explain the old
termination failures. No display mode or global compositor setting was changed.

Before a future controlled GPU capture, use explicit windowed/per-app dimensions
and verify the final **physical** swapchain extent after world entry. Logical
1280x720 alone is insufficient on a scaled compositor. Retain the first recording
with this qualification rather than replacing or silently reclassifying it.

## Evidence and next step

Instruction-word decoding, narrow disassembly, source checks and the physical
alias run are retained locally under ignored `out/`. Concatenated instruction
words were used only to classify mnemonics; branch targets were inspected in the
original-address disassembly, not inferred from that concatenated blob.

Priority remains: characterize address-translation/code-generation costs and
specify the missing submission measurements. These findings do not establish a
unique CPU/GPU bottleneck and do not start Phase 3 optimization.

The subsequent [optional submission instrumentation](asahi-submission-instrumentation.md)
implements the measurement plan in an isolated source overlay with CPU tests;
it has not replaced the working SDK/plugin. A subsequently linked, isolated
candidate passed nine synthetic GPU integration cases and one disabled-recorder
game session's technical checks, with correct gameplay reported by the owner.
The subsequent complete live trace localizes most measured submission-thread
system CPU to `vkQueueSubmit`, not deferred replay. See the instrumentation notes
for the stage evidence and limitations, including unpaired driving scenes,
unisolated observer overhead and the frame-rate hypothesis.
