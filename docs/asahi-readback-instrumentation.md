# Resolve-readback reasons: CPU validated and synthetic GPU gate passed

The owner authorized continuing after the split-getter candidate was screened
out. The next target is [readback staging/cache churn](asahi-physical-offset-candidate.md#next-selected-investigation-readback-stalls-not-omitted-readbacks),
not skipping readbacks or changing synchronization. The initial checkpoint
prepared/built diagnostics only. The subsequently authorized synthetic GPU gate
below passed. A later owner-authorized game capture completed cleanly but
**overflowed the diagnostic event buffer**; see the final section. It did not
demonstrate a performance benefit.

## Why measure before optimizing

The current resolve path already reuses mapped slots and skips valid cached CPU
copies in `some` mode. It also deliberately refreshes thumbnail/atlas surfaces
immediately to avoid stale or missing car images. A wait may instead be caused by:

- a first/missing delayed-read slot;
- an existing slot being replaced by a larger allocation;
- a full-mode read, including required thumbnail refresh;
- eviction followed by a later reinsert of the same SDK key.

The retained frame counters cannot separate these cases. Larger cache retention,
single-buffer substitutions, skipped thumbnail copies or narrowed waits are not
justified by the old timing data. The new observer leaves the original choices,
GPU commands, barriers, waits, memcpy calls, buffer indices and failure returns
intact.

## Independent optional overlay

`tools/asahi-sdk-lab/prepare_readback_profile.py` composes the existing submission
control with a separately named `resolve-readback-reasons` experiment. It does
not include render-pass cache reuse or the physical-offset prototype, and does
not modify the live SDK or baseline patch lock. Source transformations require
the expected pinned text and fail closed on unexpected wait/branch layouts.

The new init-only variables are:

- `fh1_readback_profile_csv`: empty by default, disabling recording;
- `fh1_readback_profile_start`: default 80 seconds after context setup;
- `fh1_readback_profile_seconds`: default 30, bounded to 1–45 seconds.

`asahi_readback_profile.h` owns the observer per command processor. All accesses
are on that command-processor thread, never its asynchronous submission worker.
There is no global shared mutable recorder or new renderer synchronization.
Disabled recording allocates no telemetry buffers, samples no clocks and opens
no output. Enabled recording observes keys even before its capture window so
opaque IDs can survive an entry's eviction/recreation.

Storage is fixed: **8192 records, 4096 distinct keys**, with bounded open-addressed
lookup. Record and identity arrays together stay below **2 MiB per recorder**.
Output is exclusively created (`wx`); an old file or partial trace is not replaced.
There is no per-draw logging, memory dump, raw key, guest address or Vulkan handle
in the output. It records scalar sizes/counters, frame/submission tags, effective
mode, opaque key ID/generation, host intervals and overlapping reason flags.

Allocation attempts/successful requested buffer bytes, actual memcpy calls/bytes
and resize/full/missing-slot waits are distinct. The observer separately records
resolve-entry eviction by age/count. It does not trace all memory frees, cache
clears, device allocations or shutdown. Generation changes identify reinserted
SDK keys, not necessarily a specifically recorded age eviction. Keys inherit
the existing SDK key's identity/collision properties.

Admission ends at the configured time. Export occurs at a later ordinary frame
tick after admitted scopes finish, never through a shutdown hook, new GPU wait or
busy-poll loop. A hard exit before export leaves an unqualified partial file.
Timing is host wall time only and includes observer overhead. Frame/wait timers
nest around this work; do not add them into an independent CPU/GPU breakdown.

## Offline qualification and tests

`analyse_readback_profile.py` caps input at 16 MiB / 8192 records and checks the
schema, numeric ranges, intervals, sequential subscopes and export completion.
It rejects attribution with event loss, key-table overflow, clock errors,
renderer allocation/map/wait failures or no recorded resolves. An event admitted
inside the window may end after it: its count is retained but its duration is
excluded from contained-window totals. Reason flags may overlap.

Coverage is explicitly **resolve only**: memexport, occlusion readback and early
returns before a valid resolve key are not captured. Requested buffer capacities
are not RSS, total allocated `VkDeviceMemory` size or process memory. A complete
trace does not prove a redundant copy, equivalent workload or a GPU bottleneck.
Live frame/resource joins and overhead qualification still need retained same-run
evidence; no fitting of a convenient frame offset is implemented here.

**146 CPU tests passed**, including 15 new tests for this route, in
`out/asahi-sdk-lab/logs/20261028T210842.392818Z-readback-reasons-final-cpu-regressions/`.
They cover:

- disabled forwarding, exclusive output, windows, pending-scope finalization,
  capacity/key overflow, clock failures and incomplete/malformed files;
- opaque identity/generation across pre-window observation and eviction;
- extracted real unscaled resolve-slot/policy/copy/eviction fragments in CPU
  mocks, compared against their uninstrumented equivalents with recording off/on;
- identical renderer operation sequences, copied bytes, slot state, counters and
  failure outcomes for `some`, hot history, full/thumbnail, growth, delayed-ready
  fast slots, age/count eviction, allocation/map/wait failures and no destination;
- bounded launcher flags, explicit off state and refusal to combine experiments.

These extracted fragments use mocked Vulkan/guest memory, simplified buffer
rounding and no real asynchronous GPU. The full scaled branch is syntax-checked,
not dynamically tested by those mocks. This is not a broad correctness claim.
The complete overlaid command-processor TU passed the pinned Release syntax check
in `20261028T210045.040100Z-readback-reasons-overlay-syntax-v3/`. Earlier source
matching/test-harness failures and their logs remain preserved; no GPU was
involved in those failures.

## Built diagnostic candidate, not installed

Candidate: `out/asahi-profiling/candidates/63536abb44dd33dd/`.
All **49 build steps**, including a coherent rebuild of **42 plugin TUs**, and
five candidate-local CPU checks passed: eager loader/default-off registry,
actual 16 KiB physical aliases, offline workloads, SPIR-V validation and dynamic
relocations. No game or runtime library was recompiled or installed; copied
game/runtime bytes match the functional baseline.

Plugin SHA-256:
`65f9a50c4b1b98d98493a828820559e60f97e8b0deb271e5a34776b68edf2b58`.
Build: `out/asahi-sdk-lab/logs/20261028T210459.847438Z-readback-reasons-isolated-candidate-build/`.
Frozen source snapshots, unlaunched command plan and final preservation review:
`out/asahi-profiling/candidates/63536abb44dd33dd/readback-review/20261028T211006.269396Z/`.
At that checkpoint this candidate had **not passed a GPU gate**. The later test
uses the separately preserved gate-capable build below; no performance benefit
is claimed for either build.

## Launcher and next gate

`build_submission_candidate.py --readback-profile` prepares this independent
experiment; `--build` additionally compiles and CPU-validates it. The builder
refuses combination with `--render-pass-reuse`. The launcher
`play_probe.py --readback-profile` requires a verified eligible candidate, physical
720p, MSAA Off, a warmed 1–45 s window and 20 s after it. It forbids concurrent
perf, DRM interposition, render-pass reuse, validation or ambient preload in a
game timing session. It explicitly disables readback recording in an off control
and refuses an existing `readback.csv` in an enabled profile.

The retained **plan only** uses 180 s, start 120 / duration 40 to cover the late
spikes seen previously; no profile was populated or game launched by that plan.
The optional submission recorder is off in it to keep observers separate.

Next, prepare a short synthetic Vulkan case that exercises the **actual
instrumented resolve path**, checks copied bytes/pixels and complete export, and
runs with validation plus clean 30 s post-exit kernel observation. Merely running
the old raster test's native image-to-buffer readback does not exercise this SDK
resolve path. Only after that gate should a coordinated fresh-seed game capture
measure reason frequency and overhead; no automatic live session is chained.

Swap/PSI remain telemetry only under the unchanged local policy. RAM reserve/floor,
bounded sessions, cooldown, owned-process cleanup and kernel/GPU checks remain
required, without promising they can prevent a firmware hard lock. Functional
baseline/H0, SDK lock/three patches and both older candidates were verified
unchanged. No active stop marker, game process, commit, push, driver install or
global configuration change was produced.

## Owner-authorized actual resolve GPU gate

The owner explicitly requested `procede con la prueba sintetica`. A lab-only
extension of the existing `AsahiSdkLabAccess` friend now calls the **real
`IssueCopy` / `IssueCopy_ReadbackResolvePath`**, rather than treating the old
native raster readback as coverage of this path. It is enabled only by the
readback candidate's laboratory compile flag and explicit
`ASAHI_RUN_RESOLVE_GATE=1` in the synthetic probe. No production resolve/wait
implementation was changed. The ordinary game does not invoke this lab entry.

The new CPU-only `readback-layout` mode returns before Vulkan provider creation.
It checks the actual pinned `GetResolveInfo` and all 4096 tiled pixel offsets
for each 64×64 rectangle, bounding the destination range to 1 MiB. It passed
with 16384 bytes for a 64×64 destination and 106496 bytes for a partial rectangle
in a 768×288 atlas-style destination. Source/include matching failures during
preparation were CPU-only and remain preserved.

The gate-capable build is **`4313b69e10fd4644`**, not an overwrite of
`63536abb44dd33dd`. Its observer/command-processor overlay file hashes are exactly
equal to the original diagnostic overlay. It adds lab fixture code and a sixth
CPU check (resolve geometry); all 49 coherent build steps and six CPU checks
passed. The full suite passed **148 CPU tests** in
`out/asahi-sdk-lab/logs/20261028T212218.050550Z-resolve-gate-full-cpu-regressions/`.
The CPU validation reports remain historical CPU-only records; the separate
`readback-resolve-gpu-review.json` records the later exact-binary GPU acceptance.

Before the one GPU run, the preflight checked clean readable kernel logs, no
active game/probe/stop marker, cooldown, ≥6144 MiB available RAM, actual 16 KiB
host pages, candidate hashes, local validation layer and the unchanged
swap/PSI-telemetry-only policy. No ambient preload or unsafe driver performance
debug option was admitted. No second run or game session was chained.

Run: `out/asahi-sdk-lab/logs/20261028T212356.004105Z-readback-resolve-20261028T212355Z/`.
Authorization, frozen command, trace, analysis and qualified review:
`out/asahi-profiling/readback-gates/20261028T212355Z/`.

### Result

- **9/9 existing integration cases passed**, including independent red raster
  pixel verification.
- **2/2 actual resolve cases passed**, each making two real `IssueCopy` calls:
  `some` first-miss/valid-hit and a forced-full atlas/thumbnail refresh.
- The first `some` resolve copied its 16384-byte CPU mirror; the second retained
  identical bytes without another CPU copy. Both GPU resolves/submissions were
  retained and explicitly completed.
- The atlas-style case copied 106496 bytes twice. After deliberately poisoning
  the CPU mirror, the repeated full read restored **every byte** of the prior
  mirror. Each case also checked the known RGBA red pixel at the tiled offset
  and **128 neighboring CPU sentinel bytes**.
- The genuine recorder exported **4 complete, lossless records**: one missing-slot
  wait/copy, one valid-copy skip, and two thumbnail/full waits/copies. It retained
  three allocation attempts (786432 requested buffer bytes) and three actual
  copies (**229376 bytes**), with zero drops, key overflows or clock errors.
- **Zero validation errors/warnings** were reported by the callback and
  supervisor. A normal informational message about the absent fresh shader
  validation-cache file is not a validation warning/error.
- Execution took approximately **10.9 s**, including a single idle grace until
  the 10 s admission window ended and explicit owner-thread export. No GPU work
  was submitted during that grace; ordinary SDK teardown followed.
- The full **30.00 s post-exit observation** completed with readable, clean kernel
  logs, no resource abort/runner error, no remaining processes and no new stop
  marker. Minimum sampled available RAM was approximately **8635 MiB**.

This is a narrow synthetic correctness/reachability gate, **not a game speedup
or broad stability result**. The synthetic source target is 1280×2048 with a
64×64 unscaled single-sample RGBA8 resolve region, not a physical-720p game
benchmark. Scaled resolves, GPU-side slot growth/eviction, game reason frequency,
instrumentation overhead and historical termination failures remain unvalidated.
In the probe export is explicitly called after the idle grace; ordinary game
frame-tick finalization still needs live qualification.

Prefer the exact tested `4313b69e10fd4644` binary for a later owner-coordinated
game capture. Do not retroactively label the older binary GPU-tested merely
because observer source hashes match. Keep the game at native physical 1280×720,
scale 1, MSAA Off, ordinary synchronization and the unchanged remaining safeguards.
All previous candidates, functional baseline/H0, SDK lock/three patches and the
local policy were reverified unchanged after the run. No commit or push occurred.

## First authorized game capture: clean session, rejected reason trace

The owner requested launching the game to inspect FPS and possible reductions in
stutters. Before launching, it was clarified that this build adds diagnostics,
**not a rendering optimization**. One session was authorized, not an automatic
off/on series. The owner subsequently reported `Similar, imagen normal`, with no
manual control/camera movement or observed visual defects.

Candidate: the exact GPU-gated `4313b69e10fd4644` binary. The session started from
fresh copies of the same immutable save/cache/Mesa seeds used in the earlier
stationary pair. Physical 1280×720, scale 1, MSAA Off, target 30 FPS, ordinary
synchronization, menu schedule and held handbrake were retained. The independent
readback window was setup t=120–160 s; SDK/DRM/perf recording and validation layers
were off. Surface extent and loaded candidate libraries were verified live.

Run: `out/asahi-sdk-lab/logs/20261028T213345.614756Z-readback-20261028T213338Z/`.
Preparation, complete frame CSV, rejected trace, reviews and owner feedback:
`out/asahi-profiling/candidate-play/readback-20261028T213338Z/`.

The game completed **180.61 s** and **30.00 s post-exit observation**, with readable
clean kernel logs, no runner/resource abort, no remaining processes and no new
stop marker. Minimum available RAM was **5488.19 MiB**. Global swap growth peaked
at **919.88 MiB** and PSI full avg10 at **1.81%** (approximately frame-relative
t=20.15 s). Both remained telemetry only, as authorized. This clean bounded
session does not establish broad stability or resolution of historical faults.

The frame CSV is complete. Descriptive comparison over CSV t=80–170 s:

| Measure | Earlier unpatched reference | New diagnostic session |
| --- | ---: | ---: |
| FPS | 27.63 | 27.55 |
| Mean / P95 frame ms | 36.199 / 50.395 | 36.295 / 50.318 |
| Frames >50 ms | 6.44% | 5.57% |
| Frames >66.667 ms | 1 / 2486 | 11 / 2479 |
| Maximum frame time | 66.828 ms | 132.764 ms |
| Mean draws/frame | 2144.62 | 2137.55 |

**No improvement is demonstrated**, including no consistent reduction in the
slow-frame tail. The reference used another plugin build and SDK/DRM observers
in a different window; overhead is not isolated and rendered work is not exact.
Do not attribute either the smaller >50 ms fraction or larger extreme tail to
this observer as an optimization/regression effect.

Of the 11 frames above 66.667 ms, nine have readbacks (at t≈143–152 s), one has
shader/pipeline creation and one has neither. The readback-bearing frames record
roughly 44–68 ms inside the existing readback-wait counter. That remains nested
host waiting, not GPU execution or proof the wait can be removed. Nearest global
memory samples around those nine readback spikes have PSI avg10 zero; the
creation-bearing maximum frame has 0.01%. Samples are up to about 0.26 s away,
so this is coarse context, not a way to rule out short unsampled resource stalls.
The overall session creates two shaders/pipelines and performs 11 readbacks;
the earlier reference long window had none of either.

### Recorder overflow is not a game failure or a complete capture

Export finished, with zero key-table overflow/clock errors, but the fixed buffer
retained **8192 events and dropped 92087**. The 100279 admission attempts over 40 s
are much more frequent than the CPU-copy counter suggests. The retained prefix
spans only **3.220 s** and contains 8191 valid-CPU-copy skips plus one age eviction;
all retained frame tags validate against the same-run frame CSV with the pinned
offset 2 and 1 ms guard. Later events are not recoverable from those rows.

The analyser correctly marks this trace **unusable for whole-window reason
attribution**, despite its complete export footer. In particular, zero retained
allocation/copy/wait events does **not** imply zero cost or no allocations in the
lost part of the window. Do not infer the cause of the late readbacks from it.
No buffer enlargement, policy relaxation or automatic repeat was attempted.

Next scope is **CPU-only recorder refinement**: aggregate/count routine valid
cache hits rather than letting them consume detailed-event capacity, retaining
detail for waits, copies, allocations and evictions within the existing memory
bound. This is a measurement change, not permission to skip GPU resolves or
readbacks. It needs new completeness/forwarding tests and a renewed synthetic
gate before another game capture. No new rendering optimization or repeat was
started by this review. Functional baseline/H0, immutable seeds, candidate bytes
and the local resource policy were verified unchanged afterwards.

The subsequent standing instruction authorized CPU-side continuation until more
than one option exists or authorization/gameplay observation is needed. The
[sparse observer and two-option review](asahi-readback-next-options.md) fixes the
routine-hit flood in a separate CPU-validated build and documents independent
history-cleanup and coherent-memory candidates. **155 CPU tests passed**; neither
optimization is applied, and the new sparse binary still needs its own GPU gate.
