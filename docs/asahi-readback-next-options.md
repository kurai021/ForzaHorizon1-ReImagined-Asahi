# Readback follow-up: sparse observer and two independent CPU-screened options

The owner requested continuing without intermediate approval until more than one
option exists, new authorization is needed, or gameplay observation is required.
This checkpoint reaches **two distinct candidate options**, without applying
either to the working SDK/game. It also fixes the diagnostic saturation in a new
CPU-validated isolated build. **No new GPU/game session was launched.**

These are opportunities with different strengths, not two demonstrated game
speedups. The prior 44–68 ms readback-associated waits remain unexplained at the
per-key/reason level because the [first live reason trace overflowed](asahi-readback-instrumentation.md#first-authorized-game-capture-clean-session-rejected-reason-trace).

## Observer v2: bound storage, aggregate only the ordinary no-work case

The old observer admitted every valid-copy skip and lost 92087 events. The new
`asahi-readback-v2` observer classifies a scope **after it finishes**, using a
scope-local record rather than consuming detailed-event capacity at entry.

Only an event matching all of these conditions is aggregated per opaque key:

- resolve mode `some`, entry already present and CPU copy already valid;
- exact ordinary valid-skip flags, not thumbnail, scaled or hot-history cases;
- no allocation attempt/bytes, memcpy, timed wait or renderer failure;
- host duration ≤1 ms, ending within the admission window.

Everything else retains detail, including unusual slow skips and boundary
crossings. **All original renderer callbacks and policies remain unchanged.**
Aggregation of a skipped CPU copy is not permission to skip the GPU resolve.
Scope record initialization is avoided when disabled or outside admission.

Detailed capacity stays **8192**, distinct-key capacity **4096**, and fixed record
and key arrays remain below the existing **2 MiB ceiling** (compile-time check).
The enlarged per-key counters use more of that existing ceiling; this is not a
claim of identical resident bytes. No buffer capacity or global memory policy was
increased. Per-key summaries are additional bounded output rows, not additional
live detailed-event slots; parsing is capped at 12288 physical rows and 16 MiB.

Schema v2 adds `events`. Detailed rows have one event. Kind 2 rows summarize
routine skips across generations of one opaque SDK key: their wall time is a
**sum**, while start/end merely bound observations. Their frame/submission/
generation tags are zero; individual routine frame timestamps are not retained.
Slow or exceptional events still have their real frame/submission/generation.
The parser requires exact admission/completion/detail/aggregate/loss conservation
and rejects duplicate/malformed summaries, loss, clock errors and failed work.
The legacy v1 reader remains supported; the old overflowed file stays rejected,
not retroactively repaired or reclassified.

A persisted CPU stress trace admits **100279 ordinary skips**, followed by a
late full wait/copy, a >1 ms apparent skip, and a hot-history skip. It retains all
three exceptional events plus one summary, with **zero drops** and a configured
detail capacity of only four. The extracted-method forwarding/bytes/failure tests
also pass with recording off/on. This establishes bounded CPU behavior, not
live reachability or observer overhead.

**155 CPU tests passed**:
`out/asahi-sdk-lab/logs/20261028T220308.665190Z-sparse-readback-final-cpu-regressions/`.
The actual SDK command-processor TU passed syntax checking; the final isolated
candidate **`dc68ecaa53000c6c`** completed all **49 build steps**, coherently
recompiling 42 plugin TUs, and **six candidate-local CPU checks** including real
16 KiB aliases and resolve geometry. It is diagnostics-only and default-off.

Build: `out/asahi-sdk-lab/logs/20261028T220343.546012Z-sparse-readback-final-isolated-build/`.
Frozen sources, CPU stress traces and preservation review:
`out/asahi-profiling/candidates/dc68ecaa53000c6c/sparse-review/20261028T220718.865018Z/`.
Earlier intermediate builds/reviews remain intact.

**This new binary has not passed a GPU gate.** Acceptance of `4313b69e10fd4644`
must not be transferred. A renewed actual-resolve synthetic gate is required
before another owner-coordinated gameplay capture. No automatic repetition of the
prior game session was performed.

The unchanged synthetic fixture should still exercise four actual resolves and
three CPU copies. The v2 qualification must count the aggregated skip correctly
(three detailed rows plus one summary), not assume the old per-event row order.

## Option A — avoid provably redundant history cleanup scans

Pinned source: `sdk/src/graphics/vulkan/fh1_readback_history.h:38–75` and
`sdk/src/graphics/vulkan/command_processor.cpp:6501–6511`.

For every `some` resolve in a frame divisible by 600, the current command
processor calls `History::Forget(frame)`. This scans entries for age and, if
needed, prunes the oldest over the cap. Many same-frame calls may not change the
history. Simply running once per frame would **not** be correct: later inserts
or updates in the same frame can affect pruning and retention.

The CPU prototype instead memoizes only if **both the frame and history state
are unchanged**. `Resolved` invalidates the clean flag on insertion, last-frame/
run changes, or longest-run changes. `Forget` with a new frame always executes
the original expiration logic; a dirty same-frame history also executes it.
All thresholds, ties, erase order, keys, cached-copy rules and actual readbacks
remain unchanged. No extra retention or resource-lifetime change is proposed.

`prepare_readback_options.py` extracts the real pinned History class and builds
original/prototype versions. Test-only state visibility and visit counters allow
comparison of **every entry's fields, membership and IsHot result** after each
operation. Tests cover same-frame mutation after cleanup, >2048 keys, expiration,
150-frame hot classification, duplicate resolves, 15000 pseudo-random operations,
gaps/backwards/wrapping frame values, and the retained live prefix.

In a **synthetic** duplicate-key frame (16 keys, six repeats, frame 600), scanned
entry visits fall from **1536 to 256**, with equal resulting state after every
call. This is an iteration-count result, not CPU nanoseconds or an FPS estimate.
The retained 8191-resolve prefix happens to contain **zero** eligible Forget
calls, so it provides no live savings estimate. This route may reduce occasional
CPU housekeeping but is not demonstrated to explain the large readback waits.

Status: tested CPU prototype under ignored output, **not a renderer overlay,
linked optimization candidate, GPU test or game patch**.

## Option B — avoid unnecessary host invalidation only for coherent allocations

Pinned source: resolve readback unconditionally invalidates its mapped range at
`command_processor.cpp:6760–6765`. Other existing SDK paths (for example full
memexport readback and the lab raster readback) already guard cache management
using the actual allocation memory type.

Vulkan defines [`HOST_COHERENT`](https://docs.vulkan.org/refpages/latest/refpages/source/VkMemoryPropertyFlagBits.html)
to mean explicit host flush/invalidate commands are not needed for host cache
availability/visibility. It does **not** remove GPU completion, pipeline barriers
or host/device ordering requirements. All waits, copies and barriers must remain.

A possible narrow patch would retain each slot's **actual successful allocation
type** from `CreateDedicatedAllocationBuffer(..., &memory_type)`, and omit only
`vkInvalidateMappedMemoryRanges` when that type is host-coherent. Unknown types
must conservatively retain invalidation; failed replacements must retain the
previous slot's metadata. Newly allocated/reused slots cannot inherit an old
coherence flag accidentally.

The CPU proof compiles the actual SDK `ChooseHostMemoryType` / `ChooseMemoryType`
functions with mock memory properties and checks selection/bit boundaries,
unchanged modeled completion/copy behavior, and conservative unknown-type
handling. **Readback-purpose memory is not necessarily coherent:** the real SDK
prefers host-cached memory, and the model explicitly covers a cached non-coherent
type winning over a coherent alternative. Do not assume unified Asahi memory or
`HOST_VISIBLE` establishes coherence.

Status: specification/source opportunity plus **mock model**, not an implemented
SDK patch or proof of the live readback allocation type. Its benefit could be
negligible, especially with only a few actual CPU copies. It is lower priority
and is not a proposed remedy for 44–68 ms GPU-related host waits.

## Choice and preservation

The two proofs are retained under
`out/asahi-profiling/readback-options/74667d80bba510ab/` with frozen source hashes,
compiler command, proposed history text and CPU results. Neither optimization
is included in the new observer binary. The normative coherence rule is separate
from what the mock tests establish.

**Recommendation:** among optimization prototypes, A has the stronger exact-state
CPU evidence and is the first to consider. Before attributing improvements in
large stutters to either route, requalify the sparse observer and obtain a
complete relevant game trace. Do not promise a gain, combine A+B before separate
correctness/stability gates, replace the functional SDK, or relax synchronization
to compensate for missing evidence.

The owner's stopping condition is now met by two independent options. No new
GPU/game session, commit or push was started. Functional baseline/H0, SDK lock/
three baseline patches, immutable evidence and all earlier candidates remain
unchanged. Swap/PSI remain telemetry only; RAM reserve/floor, bounds, cooldowns,
owned cleanup and kernel/GPU safeguards remain required. No active game or stop
marker remained at the final review.
