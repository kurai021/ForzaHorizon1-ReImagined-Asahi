# Asahi profiling resume checkpoint — 2026-10-28

This source-only checkpoint was explicitly authorized by the owner. It does not
install an optimization, validate sustained 30 FPS or close the historical
termination-associated GPU issue. No push is authorized by the checkpoint.

## Current state

- Working functional SDK/game, mandatory 16 KiB physical-alias correction, pinned
  baseline patches, generated game code and H0 remain unchanged.
- Native physical 1280×720 / scale 1 / MSAA Off / target 30 FPS and ordinary
  synchronization remain the gameplay reference. Recent stationary captures were
  approximately 27.6 FPS, not a sustained 30-FPS lock.
- Render-pass cache reuse showed no demonstrated benefit and is parked,
  default-off. The split-getter physical-offset variant was screened out CPU-side.
- Original readback diagnostics passed a narrow synthetic GPU gate and one clean
  game session, but its live trace dropped 92087 events. The frame CSV is complete;
  the detailed trace is not usable for whole-window reason attribution.
- Sparse observer **v2** now aggregates only ordinary short valid-copy skips,
  retaining exceptional work in detail. It preserves the 8192-detail / 4096-key
  capacities and stays below the 2 MiB fixed-array ceiling.
- Latest isolated diagnostic candidate: **`dc68ecaa53000c6c`**. All 49 build steps,
  six candidate-local CPU checks and **155 CPU tests** passed. **This exact binary
  is not GPU-validated and has not run the game.**
- Two independent options have CPU-side evidence: mutation-aware same-frame
  history cleanup memoization (A), and conditional host invalidation using the
  actual allocation's coherence property (B). Neither is applied to the SDK/game
  or included as an optimization in the diagnostic build. Neither has a measured
  game benefit; A has stronger exact-state evidence, B is a lower-priority model.

## Resume here

1. Read [the sparse observer/two-option review](asahi-readback-next-options.md).
2. Recheck the exact candidate hashes, local policy, ≥6144 MiB available RAM,
   absence of active game/probe/stop marker, cooldown and clean readable kernel.
3. Obtain/confirm authorization for a **renewed synthetic GPU gate on v2** before
   gameplay. Use the actual `IssueCopy` fixture with validation and 30 s post-exit
   observation. Do not transfer the older binary's GPU acceptance.
4. Qualify v2 event conservation: four actual resolves / three CPU copies,
   normally three detailed rows plus the aggregated valid skip. Do not rely on
   old v1 row ordering. Verify bytes, pixels, sentinels, export and cleanup.
5. Only after that gate, coordinate one fresh-seed stationary game capture with
   the owner. Confirm visual correctness and no manual camera/control movement.
   Reject overflow, incomplete export or kernel/GPU faults; never auto-repeat.
6. Use the complete reason evidence before attributing large stutters or building
   an optional optimization. Test A and B independently; any eventual A+B needs
   its own correctness/stability checks. No skipped readbacks, weaker fences,
   unsafe Mesa batching or resolution/physics reduction is permitted.

## Local artifacts and safeguards

Raw logs, binaries, save/cache seeds, profiles and traces remain ignored under
`forza-recomp/out/`. The commit does **not** archive those private bytes: they are
preserved in this checkout, with locations/hashes documented in the reviews.
Do not delete or overwrite them when resuming. The functional baseline/H0 can
be checked with `msaa_bench.verify_baseline()`; candidate runtimes with
`candidate_play.verified_runtime()`.

The ignored `out/asahi-sdk-lab/swap-growth-policy.local.json` currently contains:

```json
{"schema":1,"enforceSwapGrowth":false,"enforceMemoryPressure":false}
```

The owner explicitly chose swap/PSI **telemetry only**. Keep this local policy;
missing policy restores source-default enforcement of both metrics. RAM launch
reserve 6144 MiB / abort floor 3072 MiB, time bounds, cooldowns, PID-safe cleanup,
kernel/GPU checks and post-exit observation remain required. Do not clear a future
GPU-fault marker automatically. These protections cannot guarantee prevention of
firmware hangs or physical power-cycle requirements.

Latest CPU-suite evidence:
`out/asahi-sdk-lab/logs/20261028T220308.665190Z-sparse-readback-final-cpu-regressions/`.
Candidate build:
`out/asahi-sdk-lab/logs/20261028T220343.546012Z-sparse-readback-final-isolated-build/`.
Frozen sources, CPU stress traces and preservation review:
`out/asahi-profiling/candidates/dc68ecaa53000c6c/sparse-review/20261028T220718.865018Z/`.

For prior gate/game qualifications and their limits, see
[the readback history](asahi-readback-instrumentation.md). No driver/global
configuration changes, upstream integration, release or push are part of this
checkpoint.
