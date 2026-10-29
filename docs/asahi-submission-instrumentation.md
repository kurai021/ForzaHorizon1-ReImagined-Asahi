# Phase 2: optional host-submission instrumentation

The measurement gap from the [source inspection](asahi-phase2-inspection.md) now
has an opt-in source overlay, Vulkan-free CPU tests and a linked, CPU-validated
isolated candidate. It is **not installed in the working SDK or game**. A later
bounded synthetic GPU integration and one disabled-recorder game's technical
checks passed. The owner confirmed correct gameplay but no substantial perceived
improvement over fullscreen/MSAA Off. A subsequent live trace completed without
loss; host-stage attribution is validated, but observer overhead is not isolated
by the two differently driven sessions.

## What it records

For a threaded submission, records carry submission/frame IDs and distinguish:

- Host enqueue-to-worker delay.
- Command-pool reset and command-buffer begin.
- Deferred replay, command-buffer end and queue acquisition.
- The native `vkQueueSubmit` call.
- Total `ReplayAndSubmit` duration, which contains the individual phase scopes.
- Command-processor waits for host submission versus fence completion.

Phase scopes record host wall time and per-thread user/system CPU accounting.
Linux CPU accounting uses `getrusage(RUSAGE_THREAD)` at microsecond resolution;
nanosecond column units do not imply nanosecond CPU accuracy. These are **not GPU
execution timestamps**. Waits can overlap work on another thread, and the total
scope contains its phases: summing every group would double-count work.

The shader/draw stream is not altered. Wrappers execute the original callbacks
once and preserve results and returned RAII lifetimes. Existing locks, fences,
timeouts, queue publication ordering and device-loss branches remain in place.
Early failures omit phases that never ran. Native/split upscale jobs continue
rendering but are explicitly marked as lacking complete phase coverage.

## Bounds and publication

- CSV path empty: recording disabled, with no profiling allocation, clock reads
  or output. This is not a claim that the optionally patched binary has zero
  branch/layout overhead compared with the preserved binary.
- Default window: 80-110 s after command-processor setup. Valid start: 0-600 s;
  duration: 1-45 s. This origin is not the harness or existing CSV startup origin.
- Maximum 16384 job slots and 8192 wait records; a compile-time assertion bounds
  their allocated storage below 8 MiB. Slots are single-assignment, never reused.
- The command processor admits tickets. The worker fills its record and publishes
  readiness with release/acquire synchronization specific to telemetry. It never
  writes the shared frame-cost `Current()` object.
- Overflow drops **telemetry only**, increments loss counters and never suppresses
  rendering, callbacks or waits. The analyser refuses an unqualified CPU-cost
  report when records were lost.
- A new output file is required; existing partial/complete evidence is not
  overwritten. Only metadata/header is written at setup, not per-job file I/O
  during the capture window.
- After the window, frame-end checks do not wait for pending worker records.
  Once all admitted records are published, export is synchronous on the command
  processor. Export timestamps identify that observer interval; exclude it from
  later frame comparisons.
- A complete footer is mandatory. Early exit, missing frame-end polling or a
  stalled worker can leave an incomplete file; it is not evidence of zero cost.
  There is no new shutdown hook, forced GPU idle or reliance on destructors that
  the existing `_Exit(0)` path skips.

The schema retains monotonic timestamps, a realtime/monotonic bridge, called
phase information, API results, unknown-result sentinel, CPU availability,
unsupported/skipped flags and cross-window qualification. Boundary-crossing
intervals are counted but excluded from duration aggregates. Compare group row
counts; they need not describe identical cohorts.

## Source isolation and CPU validation

Files in `tools/asahi-sdk-lab/`:

- `asahi_submission_profile.h`: bounded, Vulkan-independent recorder.
- `submission-profile-sdk.patch`: optional adapter over two pinned SDK files.
- `prepare_submission_profile.py`: verifies the locked SDK, copies those files
  under ignored `out/`, applies the adapter there with zero fuzz and optionally
  syntax-checks the real translation unit.
- `analyse_submission_profile.py`: validates completeness/loss/flags and reports
  host-stage aggregates; no graphics initialization.

The optional patch is **not** in `sdk.lock.json` and is not applied by the baseline
preparer. `lab.cmake` supplies its include/definition only when explicitly enabled
with `REX_ASAHI_SUBMISSION_PROFILE`. Baseline build commands leave that flag off.

```sh
# CPU-only preparation and syntax check; no install or linked plugin build:
python3 -B tools/asahi_sdk_lab.py --name submission-overlay-check --seconds 120 -- \
  env TMPDIR=/tmp/opencode python3 -B \
  tools/asahi-sdk-lab/prepare_submission_profile.py --check

# Offline analysis of a future capture:
python3 -B tools/asahi-sdk-lab/analyse_submission_profile.py PATH/TO/submission.csv
```

Syntax checking reuses the existing Release flags but removes the compiled PCH
and old object/dependency outputs. Overlay headers take precedence, the compiler
has a 2 GiB address-space limit and a 90 s timeout, and the outer supervisor
monitors memory/swap/PSI. Original SDK sources and generated build outputs are
read-only inputs; no canonical SDK install or game library is replaced.

CPU tests cover disabled mode, unchanged callback/results, move-only returns,
missing CPU clocks, invalid settings, failed operations, bounded losses,
incomplete/finalized files, refusal to overwrite, and cross-thread publication.
The analyser is also tested against recorder-produced synthetic CSVs. The
adapter passes syntax checking against the pinned Vulkan command processor;
this is **not** a linked-plugin, Vulkan-validation or gameplay test.

## Isolated candidate builder

`build_submission_candidate.py` defaults to plan-only preparation. Its immutable,
fingerprinted candidate lives under `forza-recomp/out/asahi-profiling/candidates/`:

- All 42 GPU-plugin translation units are recompiled serially with the Release
  flags and overlay header first. No original GPU objects or binary PCH are
  reused; the textual PCH include is copied locally.
- Lab sources, the adapted files and static link archives are frozen locally.
  Unmodified SDK sources/headers and generated shader inputs remain read-only
  inputs from the verified pinned tree; this is not a standalone SDK snapshot.
- The runtime is copied from the functional `game-base/`, **not** silently taken
  from the different `sdk/out/` binary. The original game and facades are copied,
  not rebuilt or launched.
- Link outputs and temporary files stay inside the candidate. Original Ninja
  dependency outputs and post-link copy commands are removed; links use
  `$ORIGIN` and `-z defs`. A plan audit rejects old object/dependency paths.
- Compilation/link steps have a 3 GiB address-space limit, disabled core dumps,
  a 300 s timeout, and the existing memory/swap/PSI supervisor. Use an outer CPU
  supervisor too, to enforce a cumulative resource/time budget across steps.
- Failed/interrupted builds require reviewed, explicit `--resume`; completed
  outputs are hash-checked and failed partial outputs retained, not discarded.

The builder rebuilds CPU probes against that same local runtime and checks the
profiling registry/defaults, eager DSO relocation, physical aliasing, offline
shader translation and SPIR-V validation. The loader checker never constructs a
graphics provider and rejects a loaded Asahi ICD. These checks do not establish
gameplay compatibility or live instrumentation overhead.

Plan and resume handling, command rewriting and object isolation have CPU tests.
The full CPU suite passed 48 tests before starting the first candidate build.
That attempt (`1980318c8a72a1e2`) compiled all 42 GPU objects, then stopped before
linking: the preparer had mistakenly classified the plugin's `-o` output as a
dependency and staged the old DSO there. The refusal to overwrite unverified
outputs preserved that evidence. Dependency extraction now excludes `-o` paths
and has a regression test (49 CPU tests pass). A new fingerprint is used for the
corrected build; the first attempt is retained, not resumed or overwritten.

### Completed candidate: `063119c3d4972f3a`

The corrected build completed in 121.7 s. All 49 build steps passed, including
fresh compilation of all 42 GPU-plugin translation units. The candidate is at:

`forza-recomp/out/asahi-profiling/candidates/063119c3d4972f3a/`

Its five CPU validation steps passed:

1. `RTLD_NOW` plugin loading with the candidate-local functional runtime; three
   profiling CVARs have defaults empty/80/30, and the Asahi ICD was not loaded.
2. Physical-alias regression on actual 16384-byte host pages, retaining the
   required 4096-byte alias compensation.
3. Synthetic offline translation of seven fixtures, without constructing a
   Vulkan instance/device or submitting commands.
4. All seven modules pass offline SPIR-V validation. The reference-only module
   remains **not executed**.
5. `ldd -r` resolves the candidate runtime with no missing libraries or undefined
   symbols. ELF dynamic metadata confirms `$ORIGIN` and no original SDK RPATH.

Plugin SHA-256:
`4b007aed023cc8e0489bf16d3d6cc04ccbd24d62f186d48a23a5198dfd7d9783`.
The runtime remains the functional `b108b25d…`, not the different `510d6c95…`
binary from `sdk/out/`.

Afterward, recorded candidate-output hashes, the original SDK adapter inputs,
SDK output libraries/static dependencies, installed runtime, functional game
artifacts and historical H0 all matched. Across the whole-build and individual
step supervisors, minimum sampled available memory was 9025 MiB. Whole-build
swap started at 2063 MiB used and showed no sampled growth; this was **not** a
swap-free system. No resource abort or remaining owned process was reported.

Private evidence under that candidate includes `plan.json`, `build.json`,
`validation-20261027T040331.065994Z.json`, `elf-dynamic.txt` and
`preservation-and-resources.json`. The outer supervisor log is
`forza-recomp/out/asahi-sdk-lab/logs/20261027T040131.475139Z-phase2-submission-candidate-build-corrected/`.

## First candidate GPU gate

After owner authorization, the isolated candidate passed **9/9 synthetic GPU
integration cases** on Apple M2 Pro/Honeykrisp, with the local Vulkan validation
layer and synchronization validation enabled. Profiling was explicitly disabled;
this does **not** exercise the recorder or measure its overhead. No game was
opened. The probe completed in 0.55 s, followed by 30.0 s of clean, readable kernel
observation. No validation warning/error-severity messages, resource abort or
remaining owned process was recorded. Swap remained in use at about 2102 MiB,
without sampled growth. Informational loader messages noted an absent private
shader-validation cache; they were not validation warning-severity reports.

Evidence:
`forza-recomp/out/asahi-sdk-lab/logs/20261027T133709.426463Z-phase2-candidate-063119c3-gpu-integration/`.
The target in this synthetic raster case is 1280x2048, **not a physical 720p game
swapchain**. This gate does not replace a presentation or gameplay test.

## Prepared physical-720p sessions

`play_probe.py --candidate ... --physical-720p --scene-msaa off` verifies the
candidate runtime-file hashes, uses a private profile under
`out/asahi-profiling/candidate-play/`, and explicitly selects the candidate's
library directory. `--plan-only` prints the command without starting a game or
creating a profile. A new session name is required when prior outputs exist.

Physical-surface control is currently Hyprland-specific. The helper finds only
the owned game PID's mapped window, checks its identity, reads that window's
monitor scale, and sends a Lua transaction targeting that address/PID only. It never
changes compositor rules, monitor settings or other clients. Nonintegral target
coordinates or ambiguous windows cause refusal, not guessed global changes.
The actual swapchain log—not the requested window size—is authoritative. After
a 30 s setup allowance, a missing/wrong extent aborts; the helper also verifies
that the game mapped the candidate-local runtime and GPU plugin. Surface evidence
is retained and the final extent must still be 1280x720. These checks are
CPU-tested; the corrected per-window procedure has not yet been validated in a
live game (see the first attempt below).

Candidate/physical runs suppress screenshots and ptrace stack stops. The default
candidate reference explicitly disables submission recording. A later
`--submission-profile` run uses the bounded recorder, requires `--no-validation`,
forbids simultaneous perf sampling, and keeps at least 20 s after its requested
window. Normal synchronization/rendering settings, sleep inhibition, RAM/swap/
PSI guards and post-exit kernel observation remain in place. A 45 s cooldown is
required after a completed GPU observation; no automatic retries are performed.

The preparation at
`forza-recomp/out/asahi-profiling/candidate-play/20261027T134454Z/` contains separate
disabled/recorded profile copies with identical initial save/cache hashes, copied
from preserved inputs. Both launch plans passed CPU-only inspection; neither was
executed during preparation. The complete CPU suite passed 56 tests before this
coordination step. Begin with one explicitly coordinated reference session, then
review it before enabling recording. For observer-cost comparisons, match the
scene and inspect creation counters; different manual routes cannot isolate
instrumentation overhead or renderer gains.

### First game attempt: aborted during window setup

The owner explicitly approved the disabled-reference session. Its run at
`forza-recomp/out/asahi-sdk-lab/logs/20261027T135532.666170Z-candidate-off-20261027T134454Z-disabled/`
was aborted during the first window-control request. The old helper used legacy
`setfloating`/`resizewindowpixel` dispatcher syntax on Hyprland 0.56.2. The
request failed; its actual reply was not retained, so the exact server error
cannot be reconstructed. The observed swapchain was **1270x1034**, never verified
as 1280x720. There is no accepted gameplay, warmed timing or recorder result.

Cleanup left no owned processes, and the game logged its existing hard-exit path.
The old abort handler also stopped the GPU supervisor during its post-exit wait,
leaving no final `result.json`. A bounded retrospective journal review, extending
more than 30 s after the game exit, was readable and found no kernel errors;
that does not turn the incomplete supervised session into an accepted run.
`abort-kernel-review.json`, `abort-kernel.log`, `physical-surface.json` and
`process-cleanup.json` retain the evidence. `STOP-GPU.json` remains active with
the harness/window-control failure; it was not automatically cleared or retried.

The CPU-side helper has since been corrected:

- A read-only Lua capability assertion runs before launching a real game.
- A single Lua transaction checks the selected window's PID, then calls
  `hl.dsp.window.float` and absolute `hl.dsp.window.resize` for that window only.
  No focus-based dispatch, global rule/config change or fallback guessing is used.
- Rejected requests now retain bounded stdout/stderr diagnostics.
- On a setup exception, only identity-checked game PIDs receive SIGINT. The
  supervisor gets up to 45 s to finish exit observation, followed by the normal
  bounded owned-tree cleanup. Memory emergencies still take priority.

The complete CPU suite now passes 60 tests, including API capability checks,
failure diagnostics, PID-safe abort and bounded observation. A read-only
capability assertion passed against the installed compositor. Actual floating/
resizing is still unvalidated. The API is based on the
[versioned 0.56 dispatchers documentation](https://wiki.hypr.land/0.56.0/Configuring/Basics/Dispatchers/)
and [Lua helpers](https://wiki.hypr.land/0.56.0/Configuring/Advanced-and-Cool/Expanding-functionality/),
not the older dispatcher names.

Before another attempt, review and explicitly authorize archival/removal of the
suspension marker, prepare a fresh session name/profile from unchanged seeds,
and coordinate readiness again. Do not reuse the aborted disabled-reference
outputs or launch the already prepared recorded session as a substitute.

### Authorized retry: disabled-recorder reference completed

The owner authorized archiving the harness-failure marker and separately
confirmed readiness. The marker was moved without changing its bytes to
`out/asahi-profiling/candidate-play/20261027T143321Z/archived-STOP-GPU.json`;
the review and fresh-profile provenance are alongside it. Original seeds, game,
SDK libraries and H0 hashes were rechecked. The retry used the corrected Lua
helper, not the failed legacy dispatcher.

The run at
`forza-recomp/out/asahi-sdk-lab/logs/20261027T143356.760278Z-candidate-off-20261027T143321Z-disabled/`
completed in 180.86 s with exit 0, followed by 30.0 s of readable kernel
observation without errors. Actual swapchain logs and the surface guard confirm
**1280x720 physical** after setup through the end; `/proc` mappings confirm the
candidate-local runtime and plugin. Two swapchain creations occurred (initial
tiled size, then the requested floating size); no later mismatch was accepted.
No screenshot/ptrace/perf observer or submission recorder ran.

Minimum available RAM was 6654 MiB (6.50 GiB). Swap was already in use at 2105 MiB
and grew by a sampled peak of 164.94 MiB. Peak sampled memory PSI avg10 was 0.68
for both some/full, below the guard thresholds but **not zero pressure**. There
was no resource abort and no remaining owned process. Original/functional/H0 and
candidate runtime-file hashes still match after the run. `STOP-GPU.json` was not
recreated. These are bounded technical acceptance checks, not a long-term
stability guarantee or a fix for the historical termination-associated issue.

Offline frame-cost results, using CSV time rather than exact harness elapsed:

| CSV interval | Frames | FPS | Mean ms | P95 ms | New shaders/pipelines |
| --- | ---: | ---: | ---: | ---: | ---: |
| 80-100 s | 478 | 23.88 | 41.88 | 65.39 | 0/0 |
| 80-110 s | 745 | 24.83 | 40.27 | 52.18 | 0/0 |
| 120-170 s | 1430 | 28.60 | 34.96 | 49.69 | 0/0 |
| 80-170 s | 2434 | 27.05 | 36.98 | 50.00 | 0/0 |

The owner was asked to remain stationary until about harness t=110 s, then
drive, but subsequently reported driving earlier. **This is not a controlled
stationary reference** for observer overhead. The game worked correctly, with no
substantial perceived visual improvement over fullscreen/MSAA Off. One
structurally incomplete final CSV row was excluded; 4999
complete rows were retained. These are command-processor cadence figures, not
physical display timestamps, live-recorder overhead, stable 30 FPS or an
optimization A/B against the older uncontrolled-output sessions.

`out/asahi-profiling/candidate-play/20261027T143321Z/disabled-reference-review.json`
retains the qualified statistics and preservation checks; subsequent owner
feedback is kept separately in `owner-feedback-and-capture-authorization.json`.
The owner explicitly approved one enabled-recorder session after this feedback.
It is for host-stage attribution, not a controlled overhead A/B against the
earlier-driven reference. No session is repeated automatically merely because a
previous one returned 0.

### Frame-rate hypothesis: target versus sustained cadence

The owner proposed distinguishing a near-30-FPS target/lock from an observed
25-27 FPS average, and noted the monitor's 60 Hz limit. The retained reference
supports keeping this distinction: CSV t=80-170 has median 33.849 ms (near the
33.333 ms target), mean 36.975 ms, and P95 49.996 ms. A common near-30 cadence
with slower frames can lower the average without contradicting the target.
Neither the median nor a configured cap establishes a sustained 30 FPS lock.

Read-only source inspection also supports an intended 30-FPS mode:

- `forza-recomp/src/main.cpp` defines `frame_rate=30`. Its SDK defaults select
  FIFO presentation; the guest refresh override for alternate rates is conditional
  on `frame_rate != 30`.
- `forza-recomp/src/platform/frame_pacing.h` selects two guest vblanks per frame
  at 30. `src/hooks/frame_time.cpp` treats 30 as `ConsoleRate()` and forwards the
  original guest vblank callback rather than taking its alternate-rate branch.
- The pinned SDK's `src/graphics/graphics_system.cpp` has a guest vblank worker
  driven by the configured guest video mode. This is distinct from measuring
  actual host presentation or GPU completion.
- The inherited `docs/framerate.md` describes the guest main-loop vblank event
  and decoupled rendering. That source/design description is not a new Asahi
  measurement of the original console's frame distribution.

There is no need to request over 60 FPS or change display modes, physics,
vblank dividers, locks/fences or shutdown behavior to test this hypothesis.
A 60 Hz monitor can display a 30 FPS stream with repeated refreshes; it cannot
by itself explain a text-on-black startup screen remaining visible. Those
screens require their own input/progress evidence if still problematic; no
specific cause is established here. The submission recorder measures host work
and waits, **not actual presentation timestamps**, so it cannot independently
prove the physical displayed-frame distribution. No frame-rate implementation
was changed during this inspection.

## Before another live capture

The candidate has passed CPU, synthetic GPU and one bounded disabled-recorder
game session's technical checks, with correct gameplay reported by the owner.
The enabled recorder also produced the complete trace described below. This does
not establish a controlled overhead bound or general compatibility. Do not copy it into
`game-base/` or use the baseline installer to stage this experiment. Keep its
build/install/profile paths under `out/`.

A later coordinated run must retain the memory/kernel guards, MSAA Off, scale 1
and ordinary synchronization, and explicitly verify a physical 1280x720
presentation surface rather than relying on the 720p preset with borderless
fullscreen. Assess instrumentation overhead before interpreting the durations.
The old termination-associated GPU issue is not fixed by this telemetry.

This remains Phase 2 measurement preparation, not Phase 3 optimization. No
translation helper, physics, scene quality, upstream revision or global driver/
kernel/perf setting was changed; no new commit or push was made.

## First complete live submission capture

After feedback and explicit readiness confirmation, the owner authorized one
enabled-recorder session. It used a separate save/cache copy matching the
reference's initial seeds, the same candidate, MSAA Off, scale 1 and a verified
1280x720 physical swapchain. The reference was driven early, so its FPS delta is
**not an isolated observer-overhead or optimization comparison**.

Run:
`forza-recomp/out/asahi-sdk-lab/logs/20261027T144702.307904Z-candidate-off-20261027T143321Z-recorded/`.
It exited 0 after 180.90 s, with 30.0 s of clean, readable post-exit kernel
observation. Candidate-local runtime/plugin mappings and physical surface checks
passed. Minimum available RAM was about 5694 MiB (5.56 GiB). Swap started already
used at 2269 MiB; peak sampled growth was 18.27 MiB. Sampled PSI some/full avg10
remained zero. There was no resource abort, no remaining owned process and no
new suspension marker. Functional, SDK-library, H0 and candidate hashes still
match. Owner feedback specific to this second session remains pending.

### Completeness and alignment

- The recorder finalized **813 jobs and 1306 waits**, with zero dropped records,
  failures, skipped jobs or unsupported-path jobs. Every expected stage is
  present. This is the first complete live trace, not a header-only capture.
- Its 30 s monotonic window maps through the realtime bridge to approximately
  CSV t=79.259-109.259 s, not t=80-110 exactly. CSV epoch stamps are rounded to
  0.1 ms and sampled after the steady-clock frame-end stamp.
- Fully contained frame intervals with a 1 ms guard at each edge retain 740
  frames, excluding two intersecting boundary frames. The observed CSV
  epoch-minus-relative-time spread is 0.1004 ms: a consistency check, not an
  independent nanosecond clock-drift measurement.
- No shader or pipeline objects were created in that aligned cohort. Trace
  export took 8.63 ms **after** the window; exclude that observer interval from
  later frame comparisons.
- The game frame CSV has 4923 complete rows and one incomplete final row,
  excluded by the offline reader.

### Host-stage attribution

All job-stage means below are **per submission job, not per frame**. Each stage
has 813 fully contained records:

| Host stage | Mean wall ms/job | P95 wall ms/job | User CPU total ms | System CPU total ms |
| --- | ---: | ---: | ---: | ---: |
| Queue delay | 0.265 | 0.688 | n/a | n/a |
| Command-pool reset | 0.328 | 0.523 | 56.14 | 1.46 |
| Deferred replay | 3.710 | 5.406 | 2660.33 | 163.55 |
| Command-buffer end | 0.055 | 0.088 | 35.03 | 0.00 |
| Queue acquisition | 0.0066 | 0.0010 | 0.96 | 0.54 |
| `vkQueueSubmit` | **13.667** | **18.716** | 362.10 | **10661.94** |
| Total replay-and-submit, containing its phases | 17.773 | 23.958 | 3119.78 | 10829.08 |

`vkQueueSubmit` contains **76.90% of replay-and-submit wall time** and **98.46%
of its system CPU**. Deferred replay contains 85.27% of this scope's user CPU.
This is consistent with the earlier user-only stacks concentrated in replay
alongside separately observed high submission-thread system CPU: user-space
stack sampling cannot attribute kernel time. The scenes differ, so their
numerical equality is not assumed.

Host-submission waits (653 records) total 23.78 ms, versus 2823.38 ms for 653
fence-completion waits. They are on another thread and can overlap submission
work. Do **not** add these as a frame breakdown or call fence waits GPU execution
durations. Queue acquisition's mean exceeding P95 reflects its sparse long tail.

This narrows the next offline investigation to host driver/kernel work during
native queue submission, rather than assuming queue-lock contention or deferred
replay explains the system CPU. It does **not** establish the dominant syscall/
kernel function, GPU execution duration, a unique frame critical path or a
predicted FPS gain. Observer cost is included and not independently bounded; no
synchronization or submission policy was changed.

### Frame-rate hypothesis after this trace

The aligned cohort averaged **24.70 FPS**, with median **34.182 ms** and P95
**50.878 ms**. Descriptive, non-overlapping +/-2 ms bands give:

- 424/740 frames (57.30%) near 33.333 ms.
- 194/740 frames (26.22%) near 50 ms.
- 6/740 frames (0.81%) near 66.667 ms.

This supports an intended/frequent near-30 cadence with slower frames, **not a
sustained 30 FPS lock**. The bands are consistent with refresh-scale spacing but
do not independently establish displayed-frame timings or every stall's cause.

Over CSV t=80-170, the recorded run averages 26.07 FPS (median 33.884 ms). Over
t=120-170 it averages 27.32 FPS, with 18 shader and 27 pipeline objects created
and one 650 ms frame. Those late-scene costs are outside the host-stage capture
and must not be attributed to the recorder by simply comparing the reference's
27.05 FPS average. Physical 720p was a presentation-control correction, not an
engine optimization or a promise of visible image-quality improvement.

Private findings under `out/asahi-profiling/candidate-play/20261027T143321Z/`:
`submission-analysis.json`, `recorded-frame-analysis.json` and
`recorded-qualified-findings.json`; the original `recorded/profile/submission.csv`
is retained. No further game/GPU session was started for this analysis.

## Source preservation checkpoint

The owner subsequently authorized preserving the substantive Phase 2 work by
commit and push on `perf/asahi-720p`. This checkpoint includes the bounded CPU
profiling/export tools, optional submission recorder/adapter, isolated candidate
builder, owned-window controls, regression tests and qualified findings. The
complete CPU suite has 60 tests; no additional GPU run is needed merely to save
these sources.

The implementation is preserved in commit `93f336e`. Earlier statements that no
commit/push had occurred refer to those measurement milestones; this subsequent
source-preservation step was separately authorized by the owner.

Raw captures, traces, profiles, binaries, SDK/build outputs and private logs stay
ignored under `forza-recomp/out/`. The functional SDK lock and three baseline
patches are unchanged; the optional overlay is not a new baseline patch. Main,
the `asahi` preservation branch, tags, releases and remote settings are outside
this checkpoint's publication scope. Unisolated observer overhead, the unique
frame critical path and the old termination-associated stability issue remain
open; this is not a Phase 3 optimization or a stable-30-FPS claim.

The [offline timeline/driver-route follow-up](asahi-submission-timeline.md)
separates ongoing host submission cost from readback-associated frame spikes,
validates tagged versus asynchronous execution intervals, and records why the
reference driver's conservative batching limit must not be overridden.
The subsequent [DRM preparation notes](asahi-drm-ioctl-instrumentation.md) describe
the optional process-local interposer, bounded CPU validation and remaining live
reachability/overhead gates; this is not another GPU capture or SDK installation.
