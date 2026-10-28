# Phase 2: first live DRM attribution

After the [CPU preparation](asahi-drm-ioctl-instrumentation.md), the owner
authorized a short synthetic GPU gate and coordinated stationary game sessions.
The gate and all three game sessions completed with clean 30 s post-exit kernel
observations, verified cleanup and no resource abort. The owner confirmed correct
visuals and no manual controls/camera changes in each game session.

**New finding:** a single SDK `vkQueueSubmit` scope contains hundreds of native
`drmIoctl(DRM_IOCTL_ASAHI_SUBMIT)` invocations. This is host-driver/kernel work,
not measured GPU execution or proof that any specific call is redundant. No
optimization, batching, barrier removal or global setting change was applied.

## Synthetic reachability/correctness gate

`drm_ioctl_gpu_gate.cpp` is a separate executable around the existing frozen
candidate probe main. It runs the unchanged integration workload and teardown,
then remains alive for a fixed 3 s exporter grace. It does not add GPU work or
change game shutdown. Only this small executable was compiled, using the
candidate's existing compile/link recipe; candidate/game/SDK libraries were not
rebuilt or installed. Its frozen build inputs, commands and hashes are retained.

Run: `out/asahi-sdk-lab/logs/20261028T154034.480715Z-phase2-drm-ioctl-live-gpu-gate/`.
All **9/9 integration cases** passed, with zero validation errors/warnings and
30 s of clean, readable post-exit kernel observation. Its 2 s DRM window retained
705 events, including 11 successful native-submit calls, without loss. This
established real Mesa reachability, not game performance or an overhead bound.

Private gate inputs/results:
`out/asahi-profiling/drm-ioctl-gates/20261028T154007Z/`.
Gate executable SHA-256:
`fafc6b0e06988de46fc67212e7c6dc4e54b56adf74d1ba087823df3f9d526b59`.

## Stationary sessions and bounded overflow

All sessions used candidate `063119c3d4972f3a`, the same frozen initial save and
warmed MSAA-Off cache/Mesa seeds in separate profiles, native physical 1280×720,
scale 1, 30 FPS target, no validation/perf and normal synchronization. Menus were
scripted identically; handbrake A was held from input t=55 to 180 s. The SDK
submission recorder was enabled in the reference too, so the requested
comparison concerned the additional DRM instrumentation.

Under `out/asahi-sdk-lab/logs/`:

| Run | Execution / observation | Qualified evidence |
| --- | --- | --- |
| `20261028T154455.690923Z-drm-20261028T154430Z-reference` | 180.9 s / 30 s | Physical 720p; complete SDK trace: 915 jobs, 282 waits; no DRM preload |
| `20261028T155020.046830Z-drm-20261028T154430Z-recorded` | 181.0 s / 30 s | Physical 720p; SDK qualified; DRM export complete but **event loss**, rejected for full attribution |
| `20261028T155615.077026Z-drm-20261028T154430Z-recorded-short` | 180.9 s / 30 s | Physical 720p; qualified SDK/DRM pair: 139 jobs, 276 waits, zero event loss |

The first game DRM window was 40 s. Its fixed buffer retained 262144 events and
reported **291332 dropped events**. Of the retained records, 241112 were native
submits, but that is not the full native count. A complete export footer does
not mean lossless telemetry. The analyser rejected the join as designed; no
complete-window/per-job count distribution is inferred from that file.

After review and explicit owner authorization, one final session reduced the
SDK window from 30 to **5 s**, with the existing 5 s margins giving **15 s of DRM
recording**. No buffer or memory limit was enlarged and no interposer rebuild
was necessary. This retained **209831 events**, of which **192961** were native
submit invocations, with no loss, clock errors, thread-name overflow or failed
native returns. No further GPU session was chained.

The three sessions' sampled available-memory minima were approximately 6720,
6544 and 5783 MiB; swap growth was approximately 176, 352 and 60 MiB respectively.
They were **not swap-free**. The 3072 MiB abort reserve and 512 MiB swap-growth
limit were retained; no owned processes or new suspension marker remained.

## Counts inside the short capture's Vulkan scopes

The same-run join verified full DRM coverage of SDK scopes, consistent
monotonic/realtime bridges, a unique retained `Vulkan Submissi` thread and exact
call containment. No calls crossed a queue-scope boundary. All 139 admitted
queue scopes are fully covered by the wider DRM window, including the last
job crossing the narrower SDK admission-window end.

| Measure | Result |
| --- | ---: |
| SDK queue-submit jobs | 139 |
| Native-submit invocations inside these scopes | **64862** |
| Mean / median calls per job | **466.63 / 467** |
| P95 / minimum / maximum calls per job | **472 / 459 / 475** |
| Jobs with no native-submit calls | 0 |
| Native calls outside queue scopes, fully inside SDK window | 0 |
| Native-call wall total / containing queue-submit wall total | 1995.49 / 2051.29 ms |
| Native-call share of queue-submit wall | **97.28%** |
| Native-call system CPU / containing queue-submit system CPU | 1892.60 / 1923.09 ms |
| Native-call share of queue-submit system CPU | **98.41%** |
| Mean native-call wall / containing queue-submit wall per job | 14.356 / 14.757 ms |
| Mean native-call wall per scoped invocation | **30.77 µs** |

These are `drmIoctl` invocations, **not libdrm's internal syscall retry attempts**.
Timing includes unisolated observer cost. Nested scopes are compared, not added.
The 15 s whole-DRM-window mean of 31.99 µs/call is a different cohort from the
30.77 µs/call within the 139 SDK scopes. No payload/control-stream count, kernel
stack/function or GPU duration was captured.

This strongly supports investigating native-submission frequency rather than
assuming each Vulkan call becomes one kernel submission. It is consistent with
the reference Mesa route's conservative splitting policy, but does not establish
the precise stream-producing SDK command or independently reconstruct the
installed Fedora driver source.

## Overhead comparison is still confounded

The common frame-CSV t=80–85 s interval gives:

| Session | Frames | Mean FPS | Mean draws/frame |
| --- | ---: | ---: | ---: |
| Reference | 150 | 29.99 | 1488.94 |
| Overflowed recording | 135 | 27.00 | 2114.41 |
| Qualified short recording | 137 | 27.40 | 2136.61 |

Although the owner remained stationary and seeds matched, **rendered workloads
did not match**. The cause is not established. Short/reference SDK export
endpoints also differ. Do not report this FPS delta as isolated interposer
overhead or optimization performance. The qualified short interval has no
shader/pipeline creation or readbacks, but those exclusions do not equalize the
draw workload. Frame cadence is not physical presentation timing or sustained
driving performance. No additional run is required merely to fill this gap.

## First optimization target and preservation

The next bounded code experiment should look for **provably unnecessary command
stream/render-pass fragmentation on the SDK side**, preserving all drawing,
memory dependencies, readbacks and synchronization. It must demonstrate
equivalent work, not merely fewer calls. This is a selected investigation target,
not yet a proven redundant transition or implemented optimization.

An initial read-only check finds that the pinned command processor already
merges identical pending barriers and reuses compatible render passes.
`SubmitBarriers(false)` already avoids ending a pass when no barrier is pending;
blindly removing `force_end_render_pass` or required barriers is not a valid
optimization. Mesa batching remains excluded because of its documented
correctness failures. Do not promise a gain from the call counts alone.

The subsequent [offline render-pass reuse review](asahi-render-pass-reuse-review.md)
identifies a narrow duplicate-cache-identity candidate for compatible color
transfer/draw passes. It proposes CPU-only equivalence work before any renderer
patch or new GPU session; live applicability and benefit are not established.

All profiles, traces, plans, qualified reviews and owner confirmations remain
under ignored `out/asahi-profiling/candidate-play/drm-20261028T154430Z/`.
`qualified-comparison-and-next-target.json` contains the aggregate comparison;
`recorded-short-drm-submission-join.json` retains the per-job results. Source
hashes for the qualified short capture are:

- SDK trace: `f04657c729f301b4f0798948bb396513281556210b6225d3f59e09083a06e87a`.
- DRM trace: `0c817556ab4c61c9a76f9930e5edcfdd7f768cef9bdd5cd0a26e8f411fc9c44c`.
- Frame CSV: `538ecd285dd759e668f097aaba1f326f2c925d163d25e505bc617eb344376f6d`.

Baseline/H0, frozen seeds, SDK lock/three patches, installed libraries, candidate
hashes and the earlier driving capture were verified unchanged. The interposer
is still the CPU-tested `41a24a167d171350` build. No commit, push, release,
baseline SDK patch or driver/global configuration change was made. Historical
termination failures and workload-independent instrumentation overhead remain
open.
