# Phase 2 follow-up: submission cost versus slow frames

This is **offline analysis of the retained live capture**, after the source
checkpoint at `2b007ef`. No game/GPU run, SDK rebuild, renderer optimization,
driver installation or configuration change was performed. The earlier
[submission-stage attribution](asahi-submission-instrumentation.md) remains
valid, but it is not sufficient to name a unique frame-critical bottleneck.

## What changed in the interpretation

1. Native queue submission remains expensive and mostly charged as system CPU.
2. Its cost does **not** grow proportionally with the worst frame-time spikes.
3. All measured readback-wait time in the aligned cohort occurs in its 17 frames
   above 66.667 ms. Twelve of those frames perform readbacks.
4. Ordinary near-50-ms frames have no recorded readbacks. Readback stalls alone
   therefore do not explain the common 33-to-50-ms cadence changes.
5. Reference Mesa source allows one Vulkan submission to become multiple native
   kernel submissions. The trace does not count those calls, and the conservative
   batching limit has a documented correctness reason. It must not be overridden
   as an unmeasured speed fix.

These narrow two investigations—ongoing host submission cost and occasional
readback synchronization stalls—without bypassing required work or waits.

## Reproducible timeline join

`tools/asahi-sdk-lab/analyse_submission_timeline.py` first requires a complete,
loss-free ordinary-job trace accepted by the existing analyser. It additionally
validates phase ordering/containment, consistent job IDs/counters, serialized
worker jobs and export after admitted work finishes. It refuses missing readback
counters rather than treating them as zero.

The pinned command processor increments its frame counter before calling
`Fh1EndFrameCost`; that recorder skips its first frame while initializing. Thus
CSV row index 0 corresponds to job frame ID 2 in this single-context recording.
The tool explicitly uses offset 2 and validates **all 813 enqueues** against
their tagged CSV frame intervals through the monotonic/realtime bridge, with a
1 ms clock guard. It does not search for a convenient offset or fit another lag.
A different SDK/recording contract requires inspection, not automatic matching.

The same 740 fully contained frames from the initial qualified analysis remain;
the structurally incomplete final CSV row stays excluded. No crossing job
censors another selected frame. Epoch-minus-relative-time spread remains
0.1004 ms. Inputs retain SHA-256:

- Trace: `335d9e3bb260918cf38a0d323d0b1fc851aabcb42f2fc193d72aa77add16c349`.
- Frame CSV: `10f4aba107d5db04f713aabb53b4e37b3a041a36897f4b502a662394f83ac353`.

**Frame ownership is not execution within that frame's interval.** Of the 812
jobs tagged to selected frames, 745 finish their queue-submit scope more than
1 ms after the tagged command-processor frame ends. The worker is asynchronous.
The tool reports both tagged-job totals and actual wall-time overlap with frame
intervals, without prorating CPU time across boundaries or calling it GPU time.
Neither is automatically on the critical path, and neither should be added to
nested frame/wait counters.

```sh
# Offline only; use retained files from the same recording and the CPU supervisor.
python3 -B tools/asahi-sdk-lab/analyse_submission_timeline.py TRACE.csv FRAME-COST.csv
```

Input files are each capped at 64 MiB, frame rows at 100000, and trace records
by the recorder/analyser limits. CLI output is a JSON report; save it under
ignored `out/`. Regression tests cover rank ties, constant/empty correlations,
tagged versus temporal overlap, wrong joins, missing counters, clock drift,
incomplete capture, phase/worker consistency, early export and boundary censoring.
The complete CPU suite passed **71 tests** after this addition; those tests do
not start a real game, create a Vulkan device or change the compositor.

## Frame cohorts: measured means, not an additive breakdown

The near-33 and near-50 cohorts use descriptive +/-2 ms bands. Submit columns
sum the jobs **tagged** to that frame, even if execution finishes afterward.

| Cohort | Frames | Frame ms | Tagged submit wall ms | Tagged submit system CPU ms | GPU-related host wait ms | Readback host wait ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Near 33.333 ms | 424 | 33.49 | 14.48 | 13.89 | 1.10 | 0.00 |
| Near 50 ms | 194 | 49.80 | 15.89 | 15.27 | 5.70 | 0.00 |
| Above 66.667 ms | 17 | 100.92 | 16.89 | 16.01 | 52.45 | 46.62 |

The largest frame is 131.197 ms, with 69.189 ms of readback wait and 15.106 ms
of tagged native submit wall time. The readback-wait timer surrounds
`AwaitAllQueueOperationsCompletion` in the pinned command processor: it can
include nested host-submission/fence waits. Its time is not independent of the
GPU-related wait counter, not a GPU timestamp and not automatically removable.
The `some` readback mode still synchronizes on its documented cache-miss path;
this capture does not identify readback keys, cache misses or copy necessity.
Do not disable readback or change guest-memory visibility to test a guess.

Exploratory associations over this one window:

- Frame duration versus tagged submit wall: Pearson 0.211, Spearman 0.261.
- Frame duration versus temporal submit wall overlap: Pearson 0.139, Spearman
  0.275.
- Frame duration versus readback wait: Pearson 0.733, Spearman 0.219. Sparse
  outliers and many zero-wait frames affect these summaries; this is not a
  general predictor or a causal regression.
- Submit wall versus its system CPU: Pearson 0.993, Spearman 0.980. Total
  user+system CPU/wall is 0.992. Individual scopes sometimes report CPU above
  wall because of snapshot ordering/accounting granularity; no negative blocked
  time is inferred.

The initial global word-count/submit-wall Pearson value is 0.819, but Spearman
is only 0.331. Restricting to the 713 jobs whose captured frame has one job
reduces these to **0.181 / 0.061**. This refinement matters: the global
association mixes small auxiliary jobs with ordinary jobs and is not proof of
proportional submit cost. Deferred word counts are not native control-stream,
draw, ioctl or kernel payload-byte counts.

## Read-only driver-route inspection

The installed driver package identifies itself as
`mesa-vulkan-drivers 26.2.5-1.fc44`, source RPM
`mesa-26.2.5-1.fc44.src.rpm`. Four small reference files from the public
`mesa-26.2.5` tag were retained, with URLs/checksums and a snapshot of the current
installed Asahi DRM UAPI header, under ignored
`out/asahi-profiling/submission-inspection/mesa-26.2.5-reference/`.
They are **reference-tag source**, not a verified reconstruction of Fedora's
source RPM/patches or proof of the capture-time driver binary's identity. No
reference source was applied to the SDK, compiled or installed.

The relevant route in that reference is:

- `src/vulkan/runtime/vk_queue.c`: the common runtime can call `driver_submit`
  directly or dispatch through deferred/threaded modes. The live mode is not
  independently recorded here.
- `src/asahi/vulkan/hk_queue.c`: `hk_queue_submit` / `queue_submit` count native
  control streams, build a submission payload and either submit once or use
  `queue_submit_looped` to split it. `max_commands_per_submit` defaults to 1
  unless an explicit performance-debug batching option is selected.
- The batching comment documents rare CTS failures with lossless compression
  and a possible missing cache flush/barrier. This is a correctness warning,
  **not permission to enable batching**.
- `src/asahi/lib/agx_device.c`: the native `.submit` operation calls
  `drmIoctl(..., DRM_IOCTL_ASAHI_SUBMIT, ...)`. Other paths can also issue syncobj
  or memory-binding ioctls. No actual ioctl count, payload/control-stream count,
  syscall duration or kernel stack was recorded by the live overlay.

Reference links:
[queue](https://github.com/chaotic-cx/mesa-mirror/blob/mesa-26.2.5/src/asahi/vulkan/hk_queue.c),
[native DRM backend](https://github.com/chaotic-cx/mesa-mirror/blob/mesa-26.2.5/src/asahi/lib/agx_device.c),
[common Vulkan queue](https://github.com/chaotic-cx/mesa-mirror/blob/mesa-26.2.5/src/vulkan/runtime/vk_queue.c).

## Next measurement decision

Before any optimization, determine which part of native submission consumes
system CPU: syscall frequency, per-call work or another path within the Vulkan
call. A later narrowly scoped, owner-coordinated measurement could count/timestamp
only the owned process's native submit ioctls and relate them to control-stream
work, with explicit overhead and correctness validation. This follow-up does
**not** implement or launch that measurement, enable driver debugging/batching,
attach broad ptrace/syscall tracing, require privileged kernel sampling or relax
permissions.

Readback keys/miss reasons and vblank-phase timing remain separate gaps. The
current evidence cannot justify merging submissions, skipping readback, weakening
memory ordering/fences or predicting an FPS gain. No additional GPU session,
commit or push is part of this offline follow-up.

The subsequent [bounded DRM instrumentation preparation](asahi-drm-ioctl-instrumentation.md)
implements a process-local `drmIoctl` recorder and CPU-only forwarding/launch/join
tests. The later [live DRM findings](asahi-drm-ioctl-findings.md) validate a short
same-run join after one longer capture overflowed. Invocation counts must not be
called raw kernel syscall counts; overhead remains unisolated.
