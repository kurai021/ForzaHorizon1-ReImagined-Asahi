# Asahi performance Phase 2: first live CPU profile

The first owner-driven, instrumented session used the preserved executable with
720p/scale 1 requested, MSAA Off, normal vsync/guest vblank and synchronous
shaders. Later inspection found a 2560x1080 host swapchain: this was not a
controlled physical-720p-output benchmark. See the
[source/assembly inspection](asahi-phase2-inspection.md) for the qualification.
No renderer optimization, game rebuild, upstream integration or global
perf/graphics configuration change was made.

## Session and measurement quality

- One 180.8 s session, including menu navigation, then 30.0 s kernel observation.
- Game exit 0; readable kernel logs with no errors in the observed interval.
- Minimum available physical RAM: 6.62 GiB. No gameplay resource abort, memory PSI
  stall pressure or remaining owned game/collector processes was recorded.
- Swap was already in use; its monitored increase stayed below 75 MiB. Do not
  describe this host/session as swap-free.
- User-space `cpu-clock:u`, 49 Hz, frame-pointer callchains: 3041 samples, no
  foreign-process samples or unparsed headers; 9 unknown leaf symbols.
- Every sample aligned with the frame CSV using the monotonic/realtime bridge.
  Observed bridge offset drift was below 1 us, not a claim of nanosecond-accurate
  GPU/display timing.
- CPU sample span: 29.957 s, approximately CSV t=79.223-109.180 s. Do not equate
  the harness's 80 s request with CSV t=80 s.

| Frame-cost window | Frames | FPS | Mean ms | P95 ms |
| --- | ---: | ---: | ---: | ---: |
| Actual CPU sample span | 751 | 25.10 | 39.84 | 50.92 |
| Retained CSV t=80-170 s | 2460 | 27.33 | 36.58 | 49.94 |

Neither window created shaders or pipelines. This is a useful warmed in-game
profile, not stable 30 FPS. Route/scene differences within and between manual
drives prevent these averages from isolating profiler overhead or an improvement.

## CPU signals worth investigating

### Physical-address translation helper

The `PhysicalHostOffset` family accounts for 420 sampled leaves, about **13.8% of
recorded user CPU samples**: 341 inline instances, 78 standalone helper samples
and one entry veneer. Inline leaves were associated with their containing DSO
at the same instruction address, not counted again as caller-frame costs.

This is the 16 KiB correction required for working physical aliases. It is **not
removable**. Its Linux implementation already caches the page-size decision in a
function-local static; the data does not show a page-size syscall on every access.
The candidate is helper/guard/code-generation overhead, not bypassing the address
correction or changing emulated memory semantics.

Subsequent instruction inspection does not support assigning the sample share
specifically to guard loads/contention: most attributed PCs are on no-offset
paths. The acquire load accounted for only two of the 420 attributed samples.

The three busiest guest threads account for about 60.9% of user CPU samples;
the command-processor thread accounts for 12.7% and Vulkan submission 5.5%.
These are sampled user CPU shares, not frame wall time, GPU utilization or proof
of single-core saturation. A 13.8% sample share does not predict a 13.8% FPS gain.

### Vulkan submission's system CPU

Task snapshots span about 31.14 s, slightly wider than sampling. Vulkan submission
accumulated **3.25 s user CPU and 11.74 s system CPU** over that interval; the
command-processor thread recorded 8.27 s user CPU and 0.44 s system CPU.

System CPU is kernel execution charged to the thread, **not** blocked fence time
or GPU execution time. Correlate submission frequency, driver-call duration and
completion waits before assigning this to a specific syscall/driver function.
Kernel stacks were not sampled; global permissions were not relaxed. Tick
intervals can omit edge time for threads created/exited between observations.

## Offline export resource incident and mitigation

After gameplay ended, full inline-symbol export ran concurrently with CPU tests.
System available memory fell from about 10.0 to 5.9 GiB and swap grew by about
830 MiB. The resource guard aborted its owned test processes; the symbol exporter
also finished. No game/perf processes remained and pressure returned to zero.
Exact attribution of all host memory activity was not isolated. This incident is
separate from GPU stability.

Perf enables inline expansion by default. `export_cpu_samples.py` now defaults
to `--no-inline`, requires new output under project `out/`, uses a private
build-id directory, caps address space at 2 GiB, output at 64 MiB and runtime at
60 s, and monitors RAM/swap/PSI. Partial diagnostics are preserved without retries.

A serial re-export completed in 0.30 s, with maximum child RSS 75.7 MiB, no swap
increase and no leftover processes. The original expanded export is retained for
the inline-helper attribution; the bounded export is not equivalent inline
detail. Future sampler commands also disable build-id-cache updates and scope
the build-id directory under the recording's output directory.

## Next decision within Phase 2

1. Prioritize source/assembly inspection and CPU-only measurement of the physical
   translation helper, preserving all 16 KiB alias regression invariants.
2. Inspect existing submission counters/timers to identify the smallest missing
   measurement for the submission thread's system CPU.
3. Add direct GPU/submission/presentation timing only for a specific unanswered
   critical-path question. Host fence waits are not a substitute.
4. Do not run another GPU session merely to repeat this capture. The previous
   termination-associated fault remains open despite this clean exit.

These are profiling candidates, not authorization to implement Phase 3 changes.
Raw data, frame CSV, task snapshots, checksums and local report remain ignored
under `forza-recomp/out/asahi-profiling/sessions/20261027T003552Z/` and the run
directory linked in `checkpoint.json`. No new commit or push was made.

The [follow-up inspection](asahi-phase2-inspection.md) refines the helper
hypothesis, separates replay from native submission, identifies counter gaps and
documents the presentation-extent limitation without discarding the recording.
