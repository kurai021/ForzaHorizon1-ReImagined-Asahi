# Asahi development in this checkout

Run these commands from the project root. The scripts locate the same root even
when invoked from another directory. Sources and patches are versioned; game
files, generated C++, binaries, profiles and records remain in ignored directories.

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
pressure. Each run records before/after memory and its process-cleanup outcome.
An exception or interruption also cleans up that test's tracked process tree,
including session orphans. PID reuse/boot identity checks prevent signalling an
unrelated process. Kernel faults and unkillable leftovers keep GPU tests suspended.
These guards do not repair a graphics-driver/firmware failure.

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

For first-time dependencies, private game input and publication boundaries, see
[the Asahi guide](../../docs/asahi.md). `sdk.lock.json` fixes all SDK/submodule
revisions and patch hashes. The `asahi` branch contains the port; `main` remains
upstream-compatible. Remote publication still requires explicit owner authorization.
