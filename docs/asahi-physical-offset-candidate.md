# Next candidate: physical-address helper, split-getter variant screened out

After the [render-pass comparison](asahi-render-pass-reuse-candidate.md) showed
no demonstrated benefit, the owner requested moving to the next candidate.
The independent CPU-helper route was still pending from the
[source/assembly inspection](asahi-phase2-inspection.md). This checkpoint adds
only an extracted-method CPU proof and bounded synthetic timings. **No game,
GPU workload, functional SDK edit or game/runtime rebuild was performed.**

## Hypothesis and invariants

The preserved user CPU recording attributed 420 leaves (13.8% of samples) to
`PhysicalHostOffset` and inline instances. That is not a recoverable-time or FPS
estimate. Earlier inspection of the preserved executable found an unconditional
prologue in its standalone helper, but very few samples on static-guard loads.

The tested variant separates the Linux cached-granularity getter into a
`noinline` inline function, leaving the guest-address threshold in the small
entry function. It preserves runtime page-size detection, thread-safe local
static initialization, the low-address zero offset, and the mandatory 4096-byte
compensation for high physical aliases on hosts with pages larger than 4096.
It does not hardcode a 16 KiB host or disable thread-safe statics. Windows/macOS
and fallback branches remain unchanged. This is **not combined with cache reuse**.

`prepare_physical_offset_probe.py` extracts the actual pinned helper from
`sdk/include/rex/system/xmemory.h`, rejects unexpected/duplicate definitions,
and transforms only the recognized Linux body. The proof replaces the page-size
provider with a controlled, counted provider; it does not link a graphics runtime.
Sources, compile command, compiler identity/hash, binary, assembly and raw timings
are frozen under ignored `out/asahi-profiling/physical-offset-probes/`.

## CPU gates and result

**Nine new tests passed**, including Linux page sizes 4096/16384/65536,
Windows/macOS branch models (not tests on those operating systems), fallback,
threshold neighbors, every upper-16-bit prefix with both low-bit endpoints,
100000 deterministic random addresses, and concurrent first high-address use
across two translation units. Each Linux helper queries granularity exactly once;
low-address accesses do not initialize it. There is no fabricated guest-memory
dereference or claim of a real mapping test.

The complete CPU suite passed **131 tests**:
`out/asahi-sdk-lab/logs/20261028T204658.086131Z-physical-offset-full-cpu-regressions/`.
The final frozen-input proof is retained at:
`out/asahi-profiling/physical-offset-probes/21dd89ec2a0f5be7/20261028T204805.682548Z/`.
Its supervisor is
`out/asahi-sdk-lab/logs/20261028T204801.319612Z-physical-offset-frozen-input-proof/`.
The earlier exploratory proof remains intact, not overwritten.

The microbenchmark uses 65536 deterministic addresses, 16 passes per sample,
five alternating-order rounds, and requested high-address mixes of 0/1/50/100%.
It tests direct calls and an explicitly out-of-line entry wrapper. Relevant
Release flags include `-O3 -march=armv8-a`, without PCH or game linkage.
There is no governor, affinity, thermal, swap or system configuration change.

Selected final-run median host ns/access (synthetic only):

| Address mix / call context | Original | Split getter |
| --- | ---: | ---: |
| All low, direct | 0.941 | 0.968 |
| Half high, direct | 3.583 | 3.949 |
| All high, direct | 0.460 | 0.941 |
| All high, entry wrapper | 1.231 | 1.537 |

The current compiler already places the original wrapper's low-address return
**before its stack prologue** in this small harness. The split wrapper tail-calls
the cached getter for high addresses, but the additional boundary penalizes the
direct high-address loop. The smaller differences vary with mix/context and
host conditions; the earlier proof has different low-path timing magnitudes.
Do not infer universal regression percentages or game gains from these numbers.
The harness does not reproduce the preserved executable's complete compilation
context, generated-function register pressure or instruction-cache behavior.

**Decision: screen out this split-getter variant before a full rebuild or GPU
session.** It preserves the extracted arithmetic but has no consistent synthetic
benefit. This does not reject every possible helper/code-generation optimization.
Real physical-alias mappings, coherent recompilation of affected game/runtime/
plugin translation units and gameplay validation would still be required for
any future variant that earns promotion. Do not substitute a GPU-plugin-only
rebuild for a helper used throughout generated game code.

## Next selected investigation: readback stalls, not omitted readbacks

The retained timeline and latest pair connect the largest spikes with readback
waits and occasional compilation. In the patched pair's long window, eight of
the nine >66.667 ms frames have readbacks and one creates a shader/pipeline;
this is an association, not an isolated effect of cache reuse.

The next narrow target is **readback staging-buffer/cache-miss churn**, starting
with source inspection and distinguishing first allocation, slot growth,
eviction/recreation, and explicitly full thumbnail reads. Source references below
are to the preserved unpatched submission overlay:

- `command_processor.cpp:6481–6526`: mapped-slot reuse and allocation/growth;
  replacing an existing slot waits before destroying it.
- `:6529–6569`: thumbnail resolves must refresh immediately; `some` already skips
  an existing valid CPU copy. Extending this skipping is not the new candidate.
- `:6780–6801`: full reads and missing delayed-read slots wait before copying.
  `command_processor.h:493–495` waits through the current submission, not simply
  an independently removable `vkQueueWaitIdle` call.
- `:6828–6866` and header `:537–539`: eviction after inactivity / entry-count
  pressure and allocation size rounding. Increasing retention without a bounded
  memory/lifetime proof is not justified.

Existing telemetry has no per-readback reason, cache-key history or allocation
identity. It cannot yet select a particular eviction, allocation or wait as
redundant. A targeted measurement must distinguish those reasons before an
optimization patch; no new readback instrumentation or live session is implied
by this inspection. Preserve copies, thumbnail correctness, fences, barriers,
guest visibility and all device-loss handling. Mesa batching remains excluded.

The owner subsequently authorized continuing. The
[readback-reason preparation](asahi-readback-instrumentation.md) implements an
independent default-off observer and CPU-validates/builds its isolated diagnostic
candidate; it has not yet started a GPU/game session or demonstrated a benefit.

## Resource policy and preservation

The owner reaffirmed swap and memory PSI as **telemetry, not stop conditions**
for this checkout. The ignored local policy retains both enforcement booleans
as false. RAM reserve/floor, runtime bounds, owned-process cleanup, kernel/GPU
health checks and post-exit observation remain required. These measures reduce
risk; they cannot guarantee prevention of firmware failure or a hard lockup.
The suite/preparation ran under the CPU supervisor, with bounded compiler/runtime
address spaces and no intentional memory pressure.

Functional baseline/H0, SDK lock/three baseline patches, both previous candidates
and local policy were verified unchanged. No active stop marker or game process
remained; recent kernel logs were readable and clean. No commit/push was made.
