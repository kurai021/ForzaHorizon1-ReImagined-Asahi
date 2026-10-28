# Asahi development in this checkout

Run these commands from the project root. The scripts locate the same root even
when invoked from another directory. Sources and patches are versioned; game
files, generated C++, binaries, profiles and records remain in ignored directories.

For the current candidate, pending GPU gate and safe continuation order, start
with the [resume checkpoint](../../docs/asahi-resume-checkpoint.md).

## Normal build cycle

The existing working SDK is under `forza-recomp/out/asahi-sdk-lab/sdk/`, installed
into `install/` beside it. The game build is `game-base/` in that same laboratory.

```sh
# Prepare/build the pinned SDK when setting up or updating SDK work:
python3 -B tools/asahi-sdk-lab/build.py sdk --jobs 3

# Check the local extraction and build the game in this workspace:
python3 -B tools/asahi-sdk-lab/build.py check-game
python3 -B tools/asahi-sdk-lab/build.py game --jobs 3

# Run the local game with a bounded session:
python3 -B tools/asahi-sdk-lab/play_probe.py --name local-play --seconds 180 \
  --no-validation --scene-msaa off
```

The executable is `forza-recomp/out/asahi-sdk-lab/game-base/forza`, next to its
facade modules, `librexruntime.so` and `librexgpu-xenos.so`. Build mode stages the
libraries from this workspace's SDK install. No sibling checkout is required.

## Checks used when changing the corresponding code

```sh
python3 -B tools/asahi-sdk-lab/verify_artifacts.py
python3 -B -m unittest discover -s tools/asahi-sdk-lab -p 'test_*.py' -v
python3 -B tools/asahi-sdk-lab/regress.py --jobs 3 --require-16k --cpu-only

# Explicit synthetic GPU regression:
python3 -B tools/asahi-sdk-lab/regress.py --jobs 3 --require-16k
```

GPU runs use the bounded project supervisor. Run records are in
`forza-recomp/out/asahi-sdk-lab/logs/`. Prior independent-reconstruction records
are retained there as history; their old command paths are not current inputs.
Compact preservation manifests and source exports live under ignored
`forza-recomp/out/asahi-preservation/`. Duplicate audit workspaces were retired.

## Resource and process safeguards

`resource_guard.py` is shared by the supervisor and game trial launchers. A GPU
test starts only with at least 6 GiB available physical RAM and stops below a
3 GiB reserve, after more than 512 MiB of new swap use, or under sustained memory
pressure by default. The swap-growth and PSI cutoffs have a checkout-local override
described below. Each run records before/after memory and its process-cleanup outcome.
An exception or interruption also cleans up that test's tracked process tree,
including session orphans. PID reuse/boot identity checks prevent signalling an
unrelated process. Kernel faults and unkillable leftovers keep GPU tests suspended.
These guards do not repair a graphics-driver/firmware failure.

### Owner-authorized local swap-growth and PSI override

After early swap-triggered resource stops, the owner explicitly requested
disabling that cutoff temporarily. An unpatched retry then stopped at 21.82 s
for PSI full avg10 of 1.81%, despite about 7 GiB available RAM and a clean kernel.
The owner explicitly selected `Desactivar también PSI`. In this checkout only, the ignored
`forza-recomp/out/asahi-sdk-lab/swap-growth-policy.local.json` now contains:

```json
{"schema": 1, "enforceSwapGrowth": false, "enforceMemoryPressure": false}
```

Swap use and memory PSI are still measured, but neither alone stops a new run. The
initial memory snapshot latches the selected policy for that run; supervisor
results and game launch plans record `swapGrowthGuardEnabled` and
`memoryPressureGuardEnabled`. Without this file both cutoffs remain enabled;
the legacy two-field policy also keeps PSI enabled. Invalid policies fail closed,
and this file cannot override other safeguards.

**Unchanged:** 6 GiB launch reserve, 3 GiB abort floor, runtime
bounds, cooldowns, kernel/GPU health checks, post-exit observation and PID-safe
cleanup. Future kernel faults still suspend GPU trials. No global swap/kernel
setting was changed. The swap-only and later PSI-only suspension markers were
owner-authorized for archival and preserved unchanged under ignored
`out/asahi-profiling/resource-policy/`. The policy checkpoint passed **122 tests**;
the subsequent helper-probe suite passed **131**.

To restore either cutoff for subsequent runs, set its boolean to `true`;
removing the local policy file restores both. Do not edit/reclassify historical
resource aborts: they occurred with the previous guards enabled.

The subsequently authorized fresh unpatched/patched pair completed both 180 s
sessions and 30 s post-exit observations cleanly, with verified physical 720p and
lossless SDK/DRM captures. The owner reported normal images and no manual input
for both. This does not establish a performance benefit; see the candidate checkpoint.

## MSAA experiment checkpoint

`msaa_bench.py` freezes existing binaries/H0, creates independent profile/cache
seeds and runs supervised `game`, `2x` and `off` sessions without recompiling the
renderer. `--stationary` selects the handbrake-held v3 series. Its `report` action
covers the historical driving-v1 series only; it is not an aggregate of the later
stationary/manual observations. Raw evidence stays under `out/`.

The stationary matrix remains 6/9. For its exclusions, gameplay measurements and
the provisional native-720p Off recommendation, read the
[Stage 1 checkpoint](../../docs/asahi-performance-phase1.md).

For offline slow-frame/compilation-cohort analysis, run `profile_frames.py` on an
existing frame-cost CSV with an explicit `--window START:END`. It launches no
graphics work. See the [stability review and Phase 2 entry](../../docs/asahi-stability-and-profiling.md)
for findings, counter limitations and the next bounded live-profiling scope.

## Bounded ARM64 CPU profiling

Place the RPMs named in `profiling-tools.lock.json` in ignored
`forza-recomp/out/asahi-profiling/packages/` using `dnf download`, not an install.
Keep DNF's cache/log directories inside the lab. Then verify/extract locally:

```sh
python3 -B tools/asahi-sdk-lab/profiling_tools.py
```

The game probe's optional `--cpu-profile` attaches the local perf only to its
owned game process: 49 Hz user-space samples, frame-pointer callchains, a 30 s
default collection window and a 256 MiB recording ceiling. Supply an isolated
`--profile`, local `--game-root`, `--seconds 180`, `--no-validation` and
`--scene-msaa off`. The normal script-free driving behavior is unchanged; menu
navigation can be explicitly supplied with `--autopress`.

The probe disables screenshots/ptrace stack captures for this mode, retains
per-thread CPU snapshots and observes the kernel for at least 30 s after exit.
The supervisor also checks kernel health during GPU work. No changes to global
perf permissions or game/renderer binaries are needed. Raw perf data and reports
must remain under ignored `out/`; CPU sample percentages are not GPU utilization.

After collection and kernel observation finish, export in a separate, serial step:

```sh
python3 -B tools/asahi-sdk-lab/export_cpu_samples.py PATH/TO/cpu-perf.data \
  --output forza-recomp/out/asahi-profiling/NEW-EXPORT.script
```

This disables memory-heavy inline expansion and caps address space, output size,
runtime and resource pressure. Do not run heavy symbol expansion alongside other
tests. The retained first full-inline export provides finer inline attribution;
do not assume a new no-inline export has that same detail. Then use
`analyse_cpu_samples.py SCRIPT --sampler CPU-SAMPLER.json --frames FRAME-COST.csv`
for per-thread/leaf sample shares and temporal frame-cohort associations. This
uses the saved monotonic/realtime bridge and refuses frame alignment after a
clock-offset drift above 10 ms. Unknown leaf symbols remain explicit. Sampling
percentages and temporal association must not be presented as additive CPU/GPU
frame costs or proof of a bottleneck.
See the [first live CPU profile](../../docs/asahi-performance-phase2.md) for
measurements and the translation-helper/submission investigation candidates.
The [follow-up inspection](../../docs/asahi-phase2-inspection.md) qualifies the
helper samples, explains the threaded replay path and distinguishes the requested
720p render configuration from the recorded 2560x1080 host presentation surface.
Do not label that recording as an exact-output 720p benchmark.

## Optional submission-stage overlay and isolated candidate

`prepare_submission_profile.py --check` verifies the pinned SDK, copies only the
two adapted source files into ignored `out/`, and syntax-checks them without
building/installing a plugin. The baseline lock and working SDK are unchanged.
`submission-profile-sdk.patch` is optional, not part of the baseline patch list.

See the [Phase 2 instrumentation notes](../../docs/asahi-submission-instrumentation.md)
for stage/clock semantics, fixed buffer limits, publication and incomplete-file
handling, CPU-only commands, completed validation milestones and remaining limits.

`build_submission_candidate.py` additionally prepares a fingerprinted candidate
plan (default) or serially rebuilds all 42 GPU-plugin translation units with
`--build`. It freezes static dependencies, uses the preserved functional runtime,
and performs candidate-local CPU loader/alias/offline-SPIR-V checks. It never
installs into `game-base/` or launches the game/GPU. Use the CPU supervisor around
the entire build; inspect a failed attempt before explicitly using `--resume`.

For coordinated candidate play, `play_probe.py --candidate DIR --physical-720p
--scene-msaa off --plan-only` checks hashes and prints the isolated command.
Live physical-window control is scoped to the owned Hyprland client; actual
1280x720 swapchain logs and candidate library mappings are required. Candidate
profiles must be under `out/asahi-profiling/candidate-play/`. Recording remains
off unless `--submission-profile` is explicitly requested; never batch a second
GPU session without reviewing the first one's post-exit health.

The Phase 2 checkpoint now includes a complete 30 s live trace with no telemetry
loss, verified physical 1280x720 output and qualified host-stage findings. This
is not an isolated instrumentation-overhead A/B, a GPU execution-time profile or
proof of sustained 30 FPS. Only source, tests and aggregate documentation belong
in the fork; binaries, game profiles, traces, captures and raw logs stay ignored
under `forza-recomp/out/`.

`analyse_submission_timeline.py TRACE.csv FRAME-COST.csv` joins a qualified
ordinary-job trace to its matching pinned-SDK frame CSV offline. It validates
the explicit frame-ID offset and enqueue intervals, reports tagged versus
temporal wall overlap separately, and refuses incomplete/mismatched input or
missing readback counters. Its correlations are exploratory associations, not
critical-path, syscall-count or GPU-time measurements. See the
[timeline and driver-route follow-up](../../docs/asahi-submission-timeline.md).

## Optional process-local DRM recorder

`prepare_drm_ioctl_profile.py --build` creates a separate fingerprinted interposer,
not an SDK/plugin install. `play_probe.py --drm-ioctl-probe DIR --submission-profile
--plan-only` verifies it and scopes the preload after `timeout`, to the game
launch only. Keep the existing candidate/physical-720p/MSAA-Off/no-validation
requirements. No ambient `LD_PRELOAD` is accepted. Do not remove `--plan-only`
without coordinating live readiness and reviewing the previous run's health.

`analyse_drm_ioctl_profile.py DRM.csv --submissions SDK.csv` validates a retained
same-run pair and reports calls within each SDK queue-submit scope. Counts are
`drmIoctl` invocations, not libdrm's possible internal syscall retry attempts.
Read the [measurement contract](../../docs/asahi-drm-ioctl-instrumentation.md) for
bounds, incomplete evidence, clock/thread qualification and CPU test records.
The 97-test CPU suite includes fake-libdrm tests that never open a device. The
separately authorized [live follow-up](../../docs/asahi-drm-ioctl-findings.md)
passed a synthetic GPU gate and obtained a qualified 5 s SDK / 15 s DRM game
capture. A 40 s DRM game window overflowed the fixed buffer and is rejected for
full attribution; do not enlarge memory limits or silently accept dropped data.
Real Mesa reachability is now established; isolated overhead is not.

## Optional render-pass cache-identity experiment

`build_submission_candidate.py --render-pass-reuse --build` composes the optional
`render-pass-reuse-sdk.patch` with the submission overlay and rebuilds a separate
candidate. Its init-only `fh1_asahi_render_pass_reuse` option is **off by default**;
it only aliases the exact single-RGBA8, slot-0, no-depth, 1x transfer key locally
in the render-pass/framebuffer caches. Guest/pipeline keys and barriers stay
unchanged. It is not part of `sdk.lock.json` or a baseline installation.

`play_probe.py --render-pass-reuse off|on` requires a hash-verified candidate
containing that experiment. Compare explicit modes on the same binary, and keep
the other candidate/physical-window/resource checks. The candidate checkpoint's
110-test CPU suite and enabled synthetic GPU gate passed. The first off game reference
aborted for swap growth before measurement. A subsequently owner-authorized retry
using the previous candidate **without the patch** completed cleanly; its old
suspension marker was archived unchanged and no new marker appeared. The patched
on session was subsequently authorized but also stopped for swap growth before
measurement. Its swap-only marker was later archived unchanged after the owner
explicitly requested the local swap-growth override above. After an additional
PSI-triggered unpatched abort, the owner also disabled PSI locally. The fresh pair
then completed cleanly: **27.63 FPS unpatched versus 27.67 FPS patched** over
t=80–170 s, and **461.72 versus 462.62 native invocations/job** in the SDK window.
The workload proxies differ and the patched long window has more >66.67 ms frames;
no performance or broad stability benefit is established. See the
[candidate checkpoint](../../docs/asahi-render-pass-reuse-candidate.md) before
any owner-authorized retry; never clear the marker or relax memory limits merely
to continue an A/B.

For first-time dependencies, private game input and publication boundaries, see
[the Asahi guide](../../docs/asahi.md). `sdk.lock.json` fixes all SDK/submodule
revisions and patch hashes. The `asahi` branch contains the port; `main` remains
upstream-compatible. Remote publication still requires explicit owner authorization.

## Independent physical-offset CPU probe

After parking render-pass reuse for lack of demonstrated benefit,
`prepare_physical_offset_probe.py` tests an extracted Linux `PhysicalHostOffset`
split-getter variant without changing the live header, recompiling the game or
initializing graphics. `--benchmark` adds bounded synthetic CPU timing, not a game
benchmark. Use the existing CPU supervisor and keep its resource/cleanup controls:

```sh
python3 -B tools/asahi_sdk_lab.py --name physical-offset-proof --seconds 100 -- \
  env TMPDIR=/tmp/opencode python3 -B tools/asahi-sdk-lab/prepare_physical_offset_probe.py --benchmark
```

The first variant passed offset/concurrent-initialization tests but did not earn
promotion to a full build: high-address paths were slower in the synthetic test.
**131 CPU tests passed.** The proof is retained for reproducibility, not a request
to repeat measurements. See [the candidate review](../../docs/asahi-physical-offset-candidate.md)
for limitations and the next readback-stall investigation. Do not remove the
mandatory 16 KiB alias compensation, assume a constant host page size or mix this
route with the render-pass experiment.

## Independent resolve-readback reason recorder

`prepare_readback_profile.py --check` prepares/syntax-checks a default-off
resolve-only observer over the submission control. `build_submission_candidate.py
--readback-profile --build` coherently rebuilds an isolated diagnostic plugin;
it cannot be combined with render-pass reuse and does not change the working SDK.
The launcher supports explicit `--readback-profile` with a verified eligible
candidate and bounded warmed window, independently of DRM/perf instrumentation.

The recorder distinguishes first-slot misses, growth waits, full/thumbnail waits,
cached-copy skips and age/count eviction, retaining opaque key generations without
raw keys/addresses or payload data. It has fixed storage below 2 MiB; losses,
overflow, failed waits and incomplete output are not accepted as complete evidence.
`analyse_readback_profile.py READBACK.csv` performs bounded offline qualification.

**146 CPU tests**, the actual-TU syntax check, all 49 build steps and five
candidate-local CPU checks passed for `63536abb44dd33dd`. GPU/game validation and
any performance benefit were pending at that checkpoint. The later lab-gate build
**`4313b69e10fd4644`** passed **148 CPU tests**, six candidate-local CPU checks and
the owner-authorized actual resolve GPU gate: 9 existing integration cases plus
2 real resolve miss/hit/full-refresh cases, lossless four-event trace, zero
validation errors/warnings and a clean 30 s post-exit observation. The original
diagnostic binary was preserved, not retroactively marked GPU-tested. Game
measurements were pending at that gate; the later capture is summarized below.
No performance benefit has been demonstrated. See the
[readback checkpoint](../../docs/asahi-readback-instrumentation.md) before any
live session. The test's `readback-layout` mode is CPU-only and checks geometry
before provider creation; the GPU fixture requires explicit `ASAHI_RUN_RESOLVE_GATE=1`.

The subsequent owner-authorized game capture completed 180.61 s plus clean 30 s
observation at verified physical 720p. FPS was **27.55**, versus the earlier 27.63
reference, with mixed tails and no demonstrated improvement. Its readback recorder
retained **8192** events but dropped **92087**: routine valid-copy skips exhausted
capacity, so complete export is **not** whole-window attribution. The frame CSV
remains complete and preserved. Do not repeat automatically or enlarge storage;
the next measurement revision should aggregate routine hits and reserve detailed
events for exceptional work, with CPU tests and a renewed synthetic gate first.

That CPU-side revision is now prepared as **`dc68ecaa53000c6c`**: schema v2
aggregates only ordinary, short, unscaled valid-copy skips per opaque key and
retains exceptional events in detail. It keeps the 8192-event / 4096-key limits
and fixed arrays below the existing 2 MiB ceiling; the v1 reader remains supported.
A 100279-hit CPU flood retains the late wait/copy with zero loss. **155 CPU tests**,
all 49 build steps and six candidate-local CPU checks passed. **No new GPU/game
session was launched and this binary is not yet GPU-validated.**

Two independent optimization options were screened CPU-side: mutation-aware
same-frame history cleanup memoization, and host invalidation guarded by the
actual allocation's coherence property. Neither is applied to the SDK/game or
included in the diagnostic candidate. See [the two-option checkpoint](../../docs/asahi-readback-next-options.md)
for the exact-state versus mock/spec evidence, limitations and next gate.
