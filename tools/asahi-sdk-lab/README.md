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
python3 -B tools/asahi-sdk-lab/play_probe.py --name local-play --seconds 180
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

For first-time dependencies, private game input and publication boundaries, see
[the Asahi guide](../../docs/asahi.md). `sdk.lock.json` fixes all SDK/submodule
revisions and patch hashes. The `asahi` branch contains the port; `main` remains
upstream-compatible. Remote publication still requires explicit owner authorization.
