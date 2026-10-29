# Asahi: bounded stability review and Phase 2 entry

The owner authorized a bounded review of the existing GPU failures, followed by
performance profiling rather than an open-ended attempt to reproduce a desktop
freeze. This checkpoint closes Phase 1 **provisionally**, with its 6/9 stationary
matrix incomplete and stability caveats retained. Phase 2 begins with offline
analysis of existing in-game telemetry, not a new live profiling run.

## Stability review: evidence and stopping decision

Nineteen retained MSAA/manual run results were inventoried, including exploratory
attempts and warmups. Two recorded kernel faults; this inventory is not a
controlled reliability sample or a failure-rate estimate. The review launched no
GPU workload, changed no driver/runtime settings and cleared no suspension marker.

The historical kernel journal was re-read with microsecond output for the two
faulting sessions and both owner-driven sessions, extending observation to 30 s
after each supervisor's reported finish. Local raw evidence and checksums are
under ignored `forza-recomp/out/asahi-stability-review/`.

| Session (UTC, 2026-10-25) | Exit-imminent game log | First kernel timeout log | Difference |
| --- | --- | --- | ---: |
| Driving-v1, 2x, 15:20:36 start | 15:22:36.719 | 15:22:36.789790 | +70.790 ms |
| Stationary-v3, game/4x, 16:17:21 start | 16:19:21.567 | 16:19:21.640632 | +73.632 ms |

These are **log timestamps**, not GPU fault-onset timestamps. They support a
termination-associated failure, not proof that process termination caused the
unmapped access. In both cases the normal 120 s wrapper limit had been reached.

Source inspection of the pinned SDK found this existing close path:

1. SDL translates SIGINT/SIGTERM into quit events.
2. The UI close path calls `ReXApp::OnClosing` in `src/ui/rex_app.cpp`.
3. It requests guest title termination, logs `Title terminated; hard-exiting
   process.`, flushes logging, then calls `std::_Exit(0)` instead of normal
   subsystem teardown, to avoid an existing shutdown deadlock.

The same exit path occurs in clean runs, including both manual drives. It is a
specific shutdown hypothesis to retain, **not** an isolated cause or permission
to replace it with unbounded `WaitIdle`/teardown. Exit code 0 is not evidence of
GPU health, nor does it prove an orderly Vulkan device shutdown.

The first incidence produced 166 timeout messages (counter 0 through 165); the
second began with counter 166 and then reported an RTKit coprocessor crash.
Both incident intervals also have the same journal boot identity. Consequently,
the second incidence should not be described as an independent
clean-boot reproduction. Device state after the first recovery is a confounder.

Other findings:

- No OOM messages were found in the four bounded historical kernel intervals.
  The faulting stationary run retained at least 6116 MiB available RAM. This
  argues against physical-RAM exhaustion in that run, not all possible GPU
  allocation/lifetime problems. CPU/GPU thermal measurements remain unavailable.
- Scoped sleep inhibition did not prevent the second incidence; the sampled
  suspend clock did not establish a suspend during that run.
- Both manual sessions remained free of timeout/unmapped/RTKit messages through
  the extra 30 s observation. Their normal close logs still use `_Exit(0)`.
- Captures, presenter telemetry, route, duration, cache state and recovered-device
  state differ across series; none is isolated as the culprit.
- No retained Forza process core was found in the incident/manual interval.
  The RTKit dump is firmware context, not a symbolizable Forza user-space core.
- Frozen and functional executable/runtime/plugin/facade hashes still match the
  Phase 0 baseline. No experimental rebuild replaced the working game.

**Decision:** retain an open termination-associated stability issue and stop
fault investigation at this evidence boundary. Do not provoke a freeze, repeat
4x trials or modify shutdown/synchronization merely to test a guess. It does not
block offline profiling, but any fresh GPU error stops subsequent live sessions
and requires review; never auto-clear `STOP-GPU.json`.

## Phase 2: offline in-game telemetry triage

`tools/asahi-sdk-lab/profile_frames.py` reads retained frame-cost CSVs only. It
reports nearest-rank percentiles, budget misses, counter means, creation versus
non-creation cohorts and the slowest frames. It rejects malformed interior rows
and non-finite data, while explicitly counting a structurally truncated final
row left by hard exit. It neither initializes graphics nor launches the game.

```sh
python3 -B tools/asahi-sdk-lab/profile_frames.py PATH/TO/frame-cost.csv \
  --window 80:170
```

The two manual drives, using their previously selected 80-170 s window:

| Metric | Off | 2x |
| --- | ---: | ---: |
| Frames | 2413 | 2179 |
| Mean frame cost | 37.30 ms | 41.30 ms |
| P95, nearest rank | 50.16 ms | 66.14 ms |
| Frames above 50 ms | 123 (5.10%) | 293 (13.45%) |
| Frames with shader/pipeline creation | 42 | 46 |
| Mean cost of creation cohort | 86.67 ms | 84.90 ms |
| Mean cost of non-creation cohort | 36.43 ms | 40.35 ms |
| Frames above 50 ms without creation | 92 | 263 |

The P95 convention here differs slightly from the older manual summary's floor
index (50.12/66.13 ms); samples and arithmetic means are unchanged. Cohort
exclusion is descriptive, **not** a prediction of removing compilation costs.
The original manual routes differed, so their delta is not an isolated MSAA cost.

The largest Off frame was 249.88 ms with 197.49 ms in the pipeline-creation scope.
However, even the no-creation cohort remains above the 33.33 ms target on average.
The two accepted stationary Off observations also have no measured pipeline
creation and mean costs of 36.28/35.90 ms. Cache warming alone is therefore not
demonstrated to solve the steady-state frame budget.

GPU-related wait counters rise in the slow non-creation cohort. These measure
host waits (including submission-thread/fence waits), **not GPU execution time**.
`cpu_ms` measures only the command-processor thread's CPU consumption; the game
threads, scheduler delays and presentation are not fully characterized. Readback
waits and other wall-time scopes overlap; do not sum the counters into a frame
breakdown or conclude the remaining unexplained time is GPU work.

## Next live profiling scope

1. Keep native 720p/scale 1, MSAA Off, normal vsync/guest vblank semantics and
   synchronous shaders as the reference. Retain 2x as a later quality comparison.
2. Prepare user-space ARM64 CPU sampling in the local lab before a GPU session.
   The built-in profiler in `forza-recomp/src/debug/cpu_profile.cpp` is x86-64-only;
   sending `PROF:START` on this host would not collect samples. Neither `perf`
   nor `samply` was on PATH during review. Do not change global perf permissions
   or install system graphics packages to work around this.
3. Use one coordinated, bounded owner-driven session with per-thread CPU samples
   aligned to the existing frame CSV. Separate startup/compilation and a short
   warmed driving interval. Record sampling overhead and resource headroom.
4. Only add GPU timestamp/submission/presentation instrumentation if CPU samples
   leave a specific unanswered question and the backend supports it. Do not
   infer GPU execution from fence-wait time alone or start with a heavy capture.
5. Observe kernel health for at least 30 s after process exit, not only until exit
   code 0. Stop on any renewed GPU fault; do not launch a replacement trial.

No live profiling session, renderer optimization, upstream import, automatic GPU
retry, new commit or push was performed for the stability review itself.

## Phase 2 preparation: local ARM64 sampler

After authorization to proceed, signed Fedora RPMs for `perf` and its missing
user-space dependencies were extracted under ignored
`forza-recomp/out/asahi-profiling/perf-root/`. Their versions and checksums are in
`tools/asahi-sdk-lab/profiling-tools.lock.json`; `profiling_tools.py` verifies all
RPM hashes/signatures before extraction. Nothing was installed globally, and
`perf_event_paranoid` remains 2. The local library path is passed only to perf,
never to the game. No graphics library, ICD, kernel or renderer was changed.

CPU-only experiments checked process attachment, per-thread identification and
symbol resolution against a known C worker. DWARF unwinding resolved the hot
function but produced unknown upper caller addresses on this ARM64 host. Frame
pointer sampling resolved the test worker's chain without unknown frames and
produced a much smaller file. The integrated sampler smoke test collected 49
samples, all identifying the known hot function, and left no owned processes.
This establishes sampler readiness, not complete unwinding of optimized game
modules or an in-game overhead bound. Use leaf samples if caller chains are
incomplete; do not invent names for unresolved addresses.

`play_probe.py --cpu-profile` now requests:

- The existing native-720p game, without recompiling or changing its renderer.
- A single explicitly owned game PID, user-space `cpu-clock:u`, 49 Hz,
  `CLOCK_MONOTONIC` timestamps and frame-pointer callchains.
- Default collection from approximately 80 to 110 s after harness launch, with a
  hard 45 s maximum collection request and a 256 MiB recording ceiling.
- PID/start-time checks, per-thread CPU tick snapshots and the normal RAM/swap/PSI
  limits. Collector failure aborts the session rather than silently retrying.
- No screenshots or ptrace stack stops during this profiling run. Ambient input
  FIFO/capture/presenter-telemetry variables are cleared; only explicitly supplied
  menu presses are scripted.
- Kernel preflight, checks about every 5 s during GPU work and 30 s observation
  after command exit. Unreadable diagnostics also fail closed. Late kernel faults
  still create `STOP-GPU.json` even if the game returns 0.

The prepared first session uses a fresh copy of the frozen profile and a copy of
the prior manual Off caches; originals are not modified. Its duration is 180 s
plus post-exit observation. Sampling boundaries must be aligned using the saved
monotonic/realtime bridge and actual sample timestamps, not assumed to equal the
CSV's startup-relative `t`. Route differences and observer overhead remain
limitations; this is profiling, not an optimization A/B result.

The CPU-only test suite additionally covers collector success/failure, target
identity, private environment, package-hash rejection, journal failures and a
simulated post-exit kernel fault following a CPU command that returns 0. No real
GPU dispatch is made by those tests. Live gameplay starts only when the owner is
ready to drive; preparing the sampler does not itself validate new game behavior.

The first coordinated live session has since completed. See the
[Phase 2 CPU profiling checkpoint](asahi-performance-phase2.md) for its measured
window, CPU candidates, post-exit stability check and offline export safeguards.
