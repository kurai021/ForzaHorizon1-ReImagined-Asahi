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

For first-time dependencies, private game input and publication boundaries, see
[the Asahi guide](../../docs/asahi.md). `sdk.lock.json` fixes all SDK/submodule
revisions and patch hashes. The `asahi` branch contains the port; `main` remains
upstream-compatible. Remote publication still requires explicit owner authorization.
