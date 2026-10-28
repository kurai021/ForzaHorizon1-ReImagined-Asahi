# Optional render-pass reuse candidate: completed pair, no demonstrated benefit

This implements the narrow candidate from the
[offline review](asahi-render-pass-reuse-review.md), after owner authorization.
It is **not installed into the functional SDK/game**, not enabled by default,
and not a demonstrated performance or stability improvement.

## Exact patch scope

`tools/asahi-sdk-lab/render-pass-reuse-sdk.patch` and
`asahi_render_pass_reuse.h` add the init-only, default-false
`fh1_asahi_render_pass_reuse` option. Each render-target cache latches the option
at construction, avoiding registry lookups in the cache hot path and mixed
on/off identities within one cache lifetime.

The helper accepts exactly the transfer key for:

- One color attachment in slot 0, no depth/stencil or other color attachments.
- Guest 1x samples and `k_8_8_8_8` color format.
- Zero unused/reserved key fields.

Only the local render-pass/framebuffer **cache lookup identity** is normalized.
Shader and transfer-pipeline keys, attachments, load/store operations, copies,
draws, barriers, access/layout tracking and guest data are unchanged. A pending
barrier still ends a pass. All other keys keep their original path. This does
not implement general transfer merging or Mesa batching.

`prepare_render_pass_reuse.py` composes a new five-file frozen overlay with the
existing optional submission recorder. It verifies inputs, applies the patch
with zero fuzz and preserves failed/existing attempts. It never edits the live
SDK or baseline lock. `build_submission_candidate.py --render-pass-reuse`
prepares this variant; adding `--build` serially compiles all 42 GPU-plugin
translation units coherently with the adapted headers. It reuses the preserved
functional runtime, not the different SDK-out runtime.

## CPU tests and isolated build

The final bounded suite passed **110 tests**. New coverage includes:

- Exact-key whitelist, every single-bit mutation including reserved fields,
  and 100000 deterministic generated keys; disabled mode is identity-preserving.
- Actual SDK key types and extracted SDK format/cache/rendering-info/reuse
  method bodies, compiled against mock allocation and command sinks.
- Equal Vulkan attachment descriptions and framebuffer views/extents for the
  accepted case, and two cache entries becoming one.
- Dynamic and legacy scope reuse, on and off; a required barrier still causes
  a scope end. Different resource bases/pitches, gamma, float/integer transfer
  views and MSAA remain distinct.
- Cache clearing and explicit pass-end state reset in the fixture.
- The optional patch is not in the baseline lock; its variable defaults off;
  render-target compilation uses the overlay, not the original TU.
- Explicit supported-candidate on/off launch flags and resource-abort observation
  behavior described below.

**Test boundary:** these CPU regressions adapt only owner class/device type
names in the extracted methods and use mocked resource allocation and barrier
submission. They do not execute Vulkan or prove arbitrary GPU memory ordering.
The standalone fixture uses a deterministic height stub, not a validation of
the guest's full geometry/tiling calculations. The patch leaves those calculations
unchanged. No claim of broad compatibility follows from the CPU tests.

Candidate: `out/asahi-profiling/candidates/4a7a466e5157f0db/`.
All 49 build steps and five candidate-local CPU checks passed: eager loader and
default-off registry, actual 16 KiB aliasing, offline SPIR-V translation and
validation, and relocation/local-runtime checks. Full build elapsed about 124 s.
The original candidate `063119c3d4972f3a` remains unchanged.
Candidate plugin SHA-256:
`7b30aa2898e56865f5bb2d38bd01f5ffeac123c094542ddd360235ca6d3fa8c0`.

Private records under `out/asahi-sdk-lab/logs/`:

- `20261028T190213.274908Z-render-pass-reuse-isolated-candidate-build/`.
- `20261028T191041.435373Z-render-pass-reuse-post-abort-cpu-regressions/`
  (110 tests).

Initial overlay dry-run and fixture-header attempts failed before renderer
execution; their private records were preserved. They were corrected before
the passing build/tests, not reused as successful validation evidence.

## Enabled synthetic GPU gate

Run: `20261028T190446.214800Z-render-pass-reuse-4a7a466e-gpu-on/` under that logs
root. It explicitly enabled the option and passed:

- **9/9 existing integration cases**, including CPU mirrors and red raster
  pixel readback.
- A real render-pass/framebuffer cache identity check with reuse enabled, using
  the existing raster target and normal barrier path.
- Zero Vulkan validation errors/warnings, clean readable 30 s post-exit kernel
  observation and no remaining processes/resource abort.

The additional check in `raster.cpp` is compiled only for this isolated variant;
it does not modify the game workload. The real GPU gate exercised the default
dynamic-rendering configuration; legacy reuse has CPU coverage, not a separate
new live GPU gate. The candidate's `render-pass-reuse-gpu-review.json` retains
the result and plugin hash. This is synthetic correctness, not game benefit.

## First game reference: resource abort, not an A/B result

The owner approved the first stationary session. Separate profiles/cache seeds
were prepared for the **same binary** with explicit `--render-pass-reuse off`
and `on`. Both planned identical 5 s SDK / 15 s DRM windows, physical 1280×720,
scale 1, MSAA Off, ordinary synchronization and the same menu/handbrake inputs.

Only the **off** reference was launched:
`20261028T190816.197687Z-reuse-20261028T190806Z-off/`.
At approximately **21.3 s**, global swap use grew from 752.23 MiB to 1391.80 MiB
(about **639.56 MiB**), exceeding the unchanged 512 MiB growth limit. Available
RAM remained above approximately 7342 MiB. The supervisor stopped the owned
game/timeout with SIGINT and left no remaining processes.

This was a **resource-guard abort before the capture window**, with the option
explicitly false. It is not a measured patch regression, proof of an unrelated
process being responsible, or evidence of a GPU crash. The source of the global
swap growth has not been attributed. No on session or performance comparison was
performed. The initial swapchain reached 1280×720, but the trial ended before
the launcher's 30 s ongoing surface/library qualification could complete.

The outer launcher's resource-stop path also interrupted the supervisor's
post-exit observation after about 0.36 s. A subsequent **read-only retrospective
kernel review plus a separate 30 s passive observation** found no kernel errors
and no active game. It does not replace or relabel the original interrupted
observation as uninterrupted.

The launcher was corrected to signal only an identity-checked owned game and
wait up to 45 s for its supervisor to finish observation, including when that
supervisor has already stopped the game itself. Two CPU regressions verify that
path. The original result and `STOP-GPU.json` remained unchanged and active at
that checkpoint. The subsequent owner-authorized unpatched retry is below.

Private profiles, launch plans, passive review and authorization are under
`out/asahi-profiling/candidate-play/reuse-20261028T190806Z/`. The on profile remains
unlaunched. No limit was relaxed and no unrelated process was killed to make
room for another attempt.

The ignored `final-preservation-and-candidate-state.json` in that session binds
the source/plugin hashes, final 110-test log, pinned SDK tree and retained marker
hash. It verifies the baseline/install/H0, prior candidate, source seeds and
unlaunched on profile. The final audit is in
`out/asahi-sdk-lab/logs/20261028T191409.973014Z-render-pass-reuse-final-preservation-and-binding/`.

## Subsequently authorized retry without the patch

The owner requested a relaunch without the patch and explicitly authorized
archiving the swap-abort marker and one new 3 minute stationary session. This
used the **previous candidate `063119c3d4972f3a`**, not merely the new binary with
reuse disabled. Its plugin hash was verified against the preserved version;
the render-pass reuse experiment is absent from its manifest. Resource limits,
native physical-720p/scale-1/MSAA-Off policy and the 5 s SDK / 15 s DRM measurement
windows were retained, with a fresh copy of the same initial profile/cache seeds.

Run: `out/asahi-sdk-lab/logs/20261028T194944.306200Z-unpatched-20261028T194934Z/`.
It completed **180.84 s plus 30.00 s of clean readable post-exit kernel
observation**, verified physical 1280×720 and old-candidate library mappings,
with no remaining processes or resource abort. Available memory stayed above
about 5623 MiB; swap started at 1371.97 MiB and peak sampled growth was 55.20 MiB.
It was not swap-free. No new suspension marker was created.

The short traces are qualified: **140 SDK jobs**, **210382 DRM events**, zero
loss and a same-run join averaging **462.94 native-submit invocations/job**.
Frame-CSV t=80–85 s averaged 27.89 FPS; t=80–170 s averaged **27.66 FPS**, median
33.347 ms and P95 50.382 ms. These are command-processor cadence figures from a
stationary protocol, not displayed-frame timing, a driving benchmark or an
optimization gain. Owner visual/stationary feedback for this retry is pending.

The marker is preserved byte-for-byte as `archived-STOP-GPU.json` under
`out/asahi-profiling/candidate-play/unpatched-retry-20261028T194934Z/`, alongside
the authorization, plans and qualified review. Its SHA-256 remains
`689fa59b90ff8e4be5c2280fd4a7a3a59b4297a01c9f9568900c4ba15a70f3ec`.
The initial interrupted response had launched no game or changed the marker;
that state was checked before the authorized retry proceeded.

The successful old-candidate run **does not establish why global swap grew in
the earlier new-candidate patch-off attempt**. The patch-enabled game session
had not run at that point, and no A/B benefit or regression was established. The launcher
correction was used, but its resource-abort observation branch was not exercised
because this retry had no resource abort.

## Subsequently authorized enabled attempt: resource abort before measurement

The owner then explicitly requested `vamos ahora con el parche`. The previously
unlaunched on profile was verified against the same frozen seeds used by the
successful unpatched retry; candidate/library hashes, resource reserve, cooldown,
kernel health and the explicit `--fh1_asahi_render_pass_reuse=true` launch plan
were checked. No second session was chained.

Run: `out/asahi-sdk-lab/logs/20261028T195717.308960Z-reuse-20261028T190806Z-on/`.
At **20.79 s**, global swap use grew from 1426.20 to 2368.38 MiB, a peak sampled
increase of **942.17 MiB**, above the unchanged 512 MiB limit. Available RAM
remained above approximately 7012 MiB. The owned game/timeout were stopped with
SIGINT, and no processes remained. The measurement window had not started:
the DRM trace is incomplete/unqualified and no FPS or native-call benefit is
inferred from this attempt.

This time the corrected launcher allowed **30.00 s of uninterrupted post-exit
observation**. The kernel journal was readable and clean, with no runner error;
the outer observation record confirms waiting for the supervisor. The separate
resource warning remains valid even though kernel health was clean. The
swapchain reached 1280×720 initially, but ongoing surface/library qualification
again could not finish before the early abort.

**A new `STOP-GPU.json` is active and retained unchanged.** Its SHA-256 is
`3031567b3dbffb94b8fe69fb576cdd55048b58b33edd017aa7264fd8e958abef`.
The earlier owner-authorized archive remains intact. The on authorization,
incomplete trace analysis and `on-resource-abort-qualified-review.json` are
retained under the original `reuse-20261028T190806Z/` session directory.

The two new-candidate game attempts, off and on, both stopped for global swap
growth before measurement; the intervening old-candidate run completed. This
pattern warrants an **offline candidate/resource comparison**, not an attribution
of the growth specifically to enabled cache reuse. The executable and runtime
bytes match between candidates, while the plugin files differ by only 456 bytes
in size; those facts do not measure resident/allocated memory or identify the
source of system-wide swap growth. The equal executable/runtime copies also
have distinct inodes; any page-cache or residency explanation is unproven.
Do not relax limits, clear the new marker or start further GPU sessions merely
to obtain an A/B. Baseline/H0, source seeds, both candidates and the successful
unpatched traces were verified unchanged after the abort.

## Later owner-authorized swap-policy change (historical first override)

The owner subsequently explicitly requested disabling the swap-associated block
temporarily. At that stage only the **global swap-growth cutoff** was disabled for future
runs in this checkout, through ignored
`out/asahi-sdk-lab/swap-growth-policy.local.json` (`enforceSwapGrowth: false`).
The source default without that file is still the 512 MiB cutoff; swap use is
still recorded and the initial snapshot latches the policy for each run.

The 6 GiB launch reserve, 3 GiB abort floor, memory PSI checks, timeouts/cooldowns,
kernel/GPU fault suspension, post-exit observation and owned-process cleanup
remained enabled. No system swap/kernel setting was changed. **117 CPU tests**
passed, including refusal to disable other guards through the policy and
continued RAM/PSI enforcement with swap growth allowed.

The active marker from the enabled attempt was verified to contain only the
swap resource stop, a clean kernel/complete observation and no remaining
processes. It was archived byte-for-byte at
`out/asahi-profiling/resource-policy/20261028T200943.036461Z/archived-STOP-GPU.json`;
its hash remains `3031567b3dbffb94b8fe69fb576cdd55048b58b33edd017aa7264fd8e958abef`.
That directory also retains the owner's authorization and policy-change record.
No game/GPU run was launched by this policy change. The prior aborts remain
historical aborts, not measurements newly accepted under the new policy.

To restore the swap cutoff, set the local policy boolean to true or remove the
file; it applies to subsequent runs. Further game testing still needs owner
readiness and the unchanged remaining preflights.

## Explicit PSI authorization and completed fresh pair

The owner requested `relanza dos pruebas, sin el parche y con el parche`. The
first swap-allowed unpatched retry stopped at **21.82 s** for sustained memory
reclaim/stall pressure: full avg10 reached **1.81%** against the 1% guard, with
minimum available RAM about **7111 MiB**. Its kernel and complete 30 s post-exit
observation were clean. The patched session of that preparation was not launched.
The owner then explicitly selected **`Desactivar también PSI`**.

The ignored local policy now has both `enforceSwapGrowth: false` and
`enforceMemoryPressure: false`. Both metrics remain recorded; policy selection
is latched from each run's initial snapshot. Missing policy restores both guards,
and legacy two-field policy keeps PSI enabled. Malformed/unknown fields fail
closed. The 6144 MiB launch reserve, 3072 MiB abort floor, cooldowns, time bounds,
kernel/GPU checks, owned cleanup and 30 s post-exit observation remain unchanged.
**122 CPU tests passed** in
`out/asahi-sdk-lab/logs/20261028T201704.421642Z-local-swap-psi-policy-cpu-regressions/`.
The PSI-only marker was archived unchanged under
`out/asahi-profiling/resource-policy/20261028T201742.165774Z/`, with explicit
owner authorization. Historical aborted captures were not overwritten.

A **fresh pair** with identical immutable save/cache/Mesa seeds is retained at
`out/asahi-profiling/candidate-play/reuse-pair-20261028T202023Z/`. The literal
unpatched candidate is `063119c3d4972f3a`; the patched candidate is
`4a7a466e5157f0db` with explicit `--fh1_asahi_render_pass_reuse=true`. This is a
cross-build comparison, not same-binary off/on A/B. Both used physical 1280×720,
scale 1, MSAA Off, target 30 FPS, the same scripted stationary inputs, 5 s SDK
recording starting at 80 s and 15 s DRM recording starting at 75 s.

The unpatched run was reviewed before the second; cooldown, unchanged fresh
patched seeds and owner readiness were rechecked. The owner reported normal
images and no manual controls/camera movement for **both sessions**.

| Measure | Unpatched | Patch enabled |
| --- | ---: | ---: |
| Execution | 180.96 s | 181.11 s |
| Post-exit observation | 30.00 s | 30.00 s |
| Minimum available RAM | 5987.73 MiB | 5434.59 MiB |
| Peak global swap growth, including initial sample | 549.22 MiB | 0 MiB |
| Peak PSI full avg10 | 0.72% | 0% |
| SDK jobs | 137 | 138 |
| DRM events / dropped | 208043 / 0 | 209050 / 0 |
| Native invocations per joined job | 461.72 | 462.62 |
| FPS, t=80–170 s | 27.63 | 27.67 |
| P95 frame time, same long window | 50.395 ms | 49.983 ms |
| Frames >50 ms | 6.44% | 4.98% |
| Frames >66.667 ms | 1 / 2486 | 9 / 2490 |
| Maximum frame time | 66.828 ms | 116.074 ms |
| Mean draws/frame, same long window | 2144.62 | 2127.26 |

Both supervisor results returned zero with clean readable kernel journals, no
resource abort/runner error or remaining process, verified swapchain extent and
candidate library mappings, and complete lossless SDK/DRM captures. No active
`STOP-GPU.json` was produced. Validation layers were disabled for these timing
runs; clean logs are not a live validation-layer correctness proof.

Native invocations contained **97.20% / 97.27% of queue-submit host wall** and
**98.83% / 98.39% of its system CPU** (unpatched/patched). There were also **65 / 50**
native invocations on the selected thread outside queue scopes inside the SDK
window; these are explicitly retained, not reassigned. Nested costs include
observer overhead and are not GPU execution measurements.

Workload comparison is not exact. The fully contained single-job/no-creation/
no-readback cohorts have 136 frames each but mean draws **2129.35 / 2112.34** and
different deferred-word counts. Matching exact draw counts yields only **13
jobs per side** across five shared counts: **464.31 / 465.15 native calls/job**.
Matching draws plus both deferred-word counts yields **no shared keys**. These
proxies do not establish identical attachments/state or equivalent images; neither
raw averages nor tiny matched subsets isolate the patch effect.

The patched long window also contains 10 readbacks across nine frames and one new
shader/pipeline, while the unpatched window contains none. Its >66.667 ms frames
all occur in the creation/readback-bearing subset. Do not delete those frames
from the headline result or claim that the differing tail proves a patch
regression. Likewise the smaller >50 ms fraction is not a demonstrated general
slow-frame improvement. Global swap/PSI differences do not identify process
allocations or an algorithm-specific cause.

Per-mode qualified reviews, full joins, timelines, hashes and
`pair-comparison.json` remain private in the pair directory. Visual feedback was
recorded after the initial comparison; the final preservation record supersedes
the earlier pending-feedback fields. No third session was automatically launched.

## Decision and next gate

**Park this route: keep the patch default-off and experimental, not selected for
further performance trials without new evidence.** The owner requested moving
to the [independent next candidate](asahi-physical-offset-candidate.md).
The completed pair supports this narrow scene running correctly as observed,
but shows **no demonstrated reduction in native calls or meaningful FPS gain**.
Frame-time tails are mixed and workloads differ. The older resource-aborted
attempts remain aborts, not performance results. Neither sustained driving at
30 FPS, broad gameplay stability nor a fix for historical termination issues
is established.

Before another candidate session, review host resource conditions and the latest
completed run with the owner. Any future active suspension marker still requires
explicit authorization before removal/archival. Do not overwrite the aborted
reference or automatically repeat the pair. Retain the remaining safeguards and
the explicitly authorized local policy; review each completed session before the next.

For multiple optimization routes, test A and B independently against the same
control before considering A+B. Passing changes are not necessarily additive;
their combination needs its own correctness/stability gate. Do not combine
unmeasured changes to compensate for this missing A/B or infer that cache reuse
fixes the historical termination-associated issue.

The functional SDK/game, lock/three baseline patches, H0 and earlier evidence
remain preservation inputs. Raw artifacts stay ignored under `out/`; no commit,
push, release, driver install or global configuration change was made.
