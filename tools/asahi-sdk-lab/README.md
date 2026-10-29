# Asahi build, regression and profiling tools

Run commands from the project root. Tools derive their paths from this checkout;
SDK/game outputs and raw measurements belong under ignored `forza-recomp/out/`.
Do not commit commercial game files, translated game code, binaries or raw logs.

## Build and verify

```sh
python3 -B tools/asahi-sdk-lab/build.py sdk --jobs 3
python3 -B tools/asahi-sdk-lab/build.py check-game
python3 -B tools/asahi-sdk-lab/build.py game --jobs 3
python3 -B tools/asahi-sdk-lab/verify_artifacts.py
python3 -B -m unittest discover -s tools/asahi-sdk-lab -p 'test_*.py' -v
python3 -B tools/asahi-sdk-lab/regress.py --jobs 3 --require-16k --cpu-only
```

The functional SDK, installation and game are in `out/asahi-sdk-lab/sdk/`,
`install/` and `game-base/` respectively. The SDK lock and three baseline patches
retain ARM64 integration, compute memexport and mandatory 16 KiB physical aliases.
Existing SDK trees are verified rather than overwritten. GPU regression omits
`--cpu-only` and must be explicitly coordinated.

For source setup and licensing boundaries, see [the Asahi guide](../../docs/asahi.md)
and [source preservation](../../docs/asahi-preservation.md).

## Bounded gameplay

```sh
python3 -B tools/asahi-sdk-lab/play_probe.py --name local-play --seconds 180 \
  --no-validation --scene-msaa off
```

Use `--game-root` for an existing local extraction and `--profile` for an isolated
save/cache directory. `--autopress` supplies an explicit input schedule; there is
no automatic gameplay repetition. Candidate play requires a verified candidate,
`--physical-720p`, MSAA Off and a profile under `out/asahi-profiling/candidate-play/`.
`--plan-only` prints the command without starting gameplay or creating a profile.
Actual swapchain extent and candidate library mappings are checked live.

`resource_guard.py` retains the 6144 MiB launch reserve, 3072 MiB abort floor,
bounded sessions, identity-checked process cleanup and kernel checks. Keep 30 s
post-exit observation and 45 s cooldown between GPU trials. A stop marker requires
review; it is never automatically cleared. These checks cannot prevent every
driver or firmware failure.

The ignored checkout-local `swap-growth-policy.local.json` may select swap/PSI
telemetry only:

```json
{"schema":1,"enforceSwapGrowth":false,"enforceMemoryPressure":false}
```

Without it, both source-default cutoffs apply. Invalid policies fail closed and
the selected policy is latched at run start. Neither this file nor a profiling
option disables RAM, process, kernel or time safeguards.

## Retained diagnostic tooling

- `msaa_bench.py`: independent save/cache seeds and native-720p MSAA experiments.
- `profile_frames.py` / `analyse_frames.py`: offline, explicit-window CSV analysis.
- `profiling_tools.py`, `cpu_sampler.py`, `export_cpu_samples.py` and
  `analyse_cpu_samples.py`: bounded user-space CPU sampling and offline export.
  RPMs are checksum-verified and extracted locally, not installed globally.
- `prepare_submission_profile.py`, `asahi_submission_profile.h` and
  `analyse_submission_timeline.py`: optional bounded host submission attribution.
- `prepare_drm_ioctl_profile.py` and its analyzers: independently qualified native
  DRM call attribution, not an estimate of removable GPU work.
- `prepare_readback_profile.py`, `asahi_readback_profile.h` and
  `analyse_readback_profile.py`: default-off, bounded resolve-reason diagnostics.
  Schema v2 aggregates routine valid-copy skips but retains exceptional work;
  conservation/loss checks reject incomplete evidence. V1 remains readable.

The isolated diagnostic builder supports only maintained submission/readback
instrumentation:

```sh
python3 -B tools/asahi-sdk-lab/prepare_readback_profile.py --check
python3 -B tools/asahi-sdk-lab/build_submission_candidate.py --readback-profile --build
```

Builds recompile all plugin translation units, freeze dependencies and perform
candidate-local CPU checks. They never install over the functional game or run
GPU work implicitly. The readback fixture's `readback-layout` probe is CPU-only;
actual resolves require a separately authorized synthetic GPU run and explicit
`ASAHI_RUN_RESOLVE_GATE=1`.

## Closed experiments

[The final readback-history evaluation](../../docs/asahi-readback-history-final-review.md)
found no consistent practical benefit: 27.475 FPS off versus 27.382 FPS on in the
primary window. No optimization was promoted to the functional SDK/game.
History/coherency prototypes, render-pass reuse and split-offset experiment
scripts, fixtures and obsolete launch options were retired rather than maintained
as supported features. Useful generic diagnostics and the concise results remain.

## Publication checks

```sh
python3 -B tools/asahi-sdk-lab/audit_publication.py --index
```

Only redistributable source, locks, patches and project-specific aggregate
findings belong in the fork. The audit checks source-only publication boundaries,
private-path/credential patterns, inherited licenses and workflow permissions.
Raw machine inventories and unrelated-system investigations are not project
documentation or publication artifacts. No push or release is implicit.
