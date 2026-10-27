# Asahi 720p performance: Stage 1 checkpoint

This checkpoint preserves the experiment tools, safeguards and initial gameplay
feedback. It does **not** declare the nine-trial MSAA study complete or stable
30 FPS achieved. No renderer optimization or newer upstream performance patch
was incorporated during these trials.

## Reference and scope

- Reference source: `df074c4` on `asahi`; experimental work: `perf/asahi-720p`.
- Native Apple M2 Pro, 16 GiB unified memory, Fedora Asahi Remix 44/AArch64,
  16 KiB pages, Honeykrisp/Mesa 26.2.5.
- Base game, internal 1280x720, resolution scale 1, 30 FPS target, synchronous
  shader compilation and no Vulkan validation in performance sessions.
- Runtime/game binaries, SDK, game data and historical H0 were preserved.
- The frame-cost CSV measures the backend's frame cadence, not physical display
  timestamps. Presenter callback timing, when collected, is also a proxy.

## Experiment history

The driving-v1 protocol used identical scripted input but did not produce
deterministic routes: collisions and final positions differed. Its results remain
exploratory. An LT-held stationary pilot was rejected because the game engaged
reverse. Neither series is silently mixed into the stationary comparison.

The stationary-v3 protocol holds the ordinary handbrake after world entry, keeps
the camera fixed, and captures native output before/after the measurement window.
The car was visually checked at 0 mph. Profiles/caches and frozen binaries are
isolated under ignored `forza-recomp/out/asahi-720p-msaa/`.

### Stationary-v3: six of nine planned sessions

| MSAA | Repetition | FPS | Mean ms | P95 ms | P99 ms | Qualification |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Game/4x | 1 | 23.27 | 42.98 | 63.62 | 116.42 | Technical acceptance passed |
| 2x | 1 | 25.00 | 40.01 | 50.81 | 100.79 | New pipeline object in measured window |
| Off | 1 | 27.56 | 36.28 | 49.83 | 50.97 | Technical acceptance passed |
| 2x | 2 | 24.98 | 40.03 | 50.80 | 100.38 | New pipeline objects in measured window |
| Off | 2 | 27.85 | 35.90 | 49.86 | 50.98 | Technical acceptance passed |
| Game/4x | 2 | 22.86 | 43.74 | 64.56 | 66.79 | GPU fault at session termination; not stability-valid |

The third repetition of each profile was not performed. Do not treat these as
three accepted observations per mode. The initial captures retained road,
vegetation, barriers, car and HUD without obvious omissions; Off shows more edge
aliasing. Captures are local evidence, not assets distributed with the fork.

The kernel recorded GPU timeouts/unmapped reads and an RTKit coprocessor crash
around termination in interrupted trials. Suspend/display-off was suggested but
not established as the cause; a repeated fault also occurred with scoped sleep
inhibition. The experiment stopped on its marker. These incidents remain an
open stability issue, not a resolved consequence of MSAA settings or RAM use.

## Owner-driven gameplay

Following a fresh-boot/resource review and explicit authorization, two bounded
three-minute manual drives used the PowerA Nintendo Switch wired controller
(`20d6:a713`), recognized and mapped by the project's SDL runtime as player 1.
Only initial menu navigation was scripted; the owner handled gameplay.

| MSAA | FPS, 80-170 s | Mean frame ms | P95 ms | Minimum available physical RAM |
| --- | ---: | ---: | ---: | ---: |
| Off | 26.81 | 37.30 | 50.12 | 4.95 GiB |
| 2x | 24.22 | 41.30 | 66.13 | 4.18 GiB |

Both sessions exited normally, with no kernel errors recorded in their run
intervals, no resource abort and no remaining owned processes. Validation was
disabled; these are not zero-message Vulkan-validation results.

The owner described Off as enjoyable and fully playable for the tested opening
drive. 2x remained playable but its lower frame rate made steering harder. Audio
was reported as clear and without cuts in both sessions; no audio recording or
objective signal-quality measurement was taken.

There was one manual drive per mode, not identical deterministic route replay.
The windows contain shader/pipeline work and cannot be substituted for accepted
warm-cache repetitions of the stationary matrix. Two clean exits do not prove
the earlier GPU closing fault is fixed.

## Provisional profile recommendation

- **Preferred for current gameplay:** native 720p, MSAA Off.
- **Optional quality profile:** native 720p, MSAA 2x; playable with reduced fluency.
- **Fidelity reference:** the game's MSAA mode, normally 4x in the tested scene.
- **Pending:** stable 30 FPS, full races, long sessions and the GPU termination issue.

This is a user-validated initial gameplay recommendation, not a claim of complete
game compatibility or an experimentally established hardware ceiling. The CLI
default remains the game's MSAA mode; select Off/2x explicitly.

```sh
python3 -B tools/asahi-sdk-lab/play_probe.py --name manual-off \
  --seconds 180 --no-validation --scene-msaa off

python3 -B tools/asahi-sdk-lab/play_probe.py --name manual-2x \
  --seconds 180 --no-validation --scene-msaa 2x
```

These commands require existing local game data, sufficient memory and no active
GPU suspension marker. Profiles, generated game code, executables, caches, raw
logs, CSVs and captures remain ignored under the project workspace. The private
manual comparison is `forza-recomp/out/asahi-manual-driving/MANUAL-COMPARISON.md`.

## Safeguards and next decision

`resource_guard.py` checks physical memory/swap/PSI, bounds waits and cleans only
owned process trees using PID/start-time/boot checks. It cannot repair a kernel
or firmware hang. CPU-only simulated-process tests exercise those safeguards;
they do not replace a stability review before further GPU work.

The owner subsequently authorized a bounded stability review and transition to
profiling. Stage 1 is provisionally closed with the above incomplete matrix and
open stability issue; no missing repetitions were manufactured or restarted.
See the [stability review and Phase 2 entry](asahi-stability-and-profiling.md) for
the shutdown timeline, decision to defer reproduction and offline profiling
findings. Additional runs should answer a specific question rather than merely
fill the missing table rows.
