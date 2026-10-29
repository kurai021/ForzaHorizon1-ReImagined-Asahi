# Phase 2: bounded process-local DRM measurement preparation

This follows the [offline submission timeline](asahi-submission-timeline.md).
The implementation and validation below describe the **initial CPU-only
preparation**, not a renderer optimization or measured FPS gain. Subsequent
owner-authorized [live validation and findings](asahi-drm-ioctl-findings.md) now
include a qualified short game trace and a separately retained overflowed one.
No batching, readback, synchronization, physics, driver installation or global
kernel/graphics/perf setting was changed.

## Measurement contract

`tools/asahi-sdk-lab/drm_ioctl_profile.cpp` interposes the fixed libdrm ABI:

```c
int drmIoctl(int fd, unsigned long request, void *argument);
```

It forwards each invocation once with the original arguments and preserves its
return value and `errno`, including stale `errno` on success. It neither inspects
the payload nor implements retries. The original function is resolved through
`RTLD_NEXT`, or an already-loaded `RTLD_LOCAL` libdrm using `RTLD_NOLOAD`; it does
not load a new driver or replace libdrm with a raw syscall. Invalid requested
configuration or unavailable forwarding fails with exit 125, never fabricated
GPU success or skipped work.

**Counts are `drmIoctl` invocations, not raw kernel syscall attempts.** Libdrm may
retry internally. Each event stores the numeric request, TID, monotonic interval,
thread user/system CPU deltas, result and returned `errno`. The numeric native
submit request comes from the frozen installed Asahi UAPI header. No fd, device
path, payload pointer or buffer contents are recorded. Request matching alone
does not independently identify a DRM device, count control streams or name the
dominant kernel function.

Wall and CPU intervals measure host-side work with observer cost, **not GPU
execution**. `getrusage(RUSAGE_THREAD)` has microsecond resolution; snapshots are
not simultaneous with the wall stamps. Do not subtract nested CPU measurements
to infer blocked/GPU time, or add other-thread wall totals into a frame budget.

## Scope, bounds and completion

- No recording is requested without a nonempty `ASAHI_DRM_IOCTL_CSV`. Disabled
  loading allocates no recorder buffers and starts no exporter, but interposing
  the function still has forwarding overhead. An unpreloaded run is a distinct
  reference, not an equivalent disabled-recorder measurement.
- Recording requires an exact `/proc/self/exe` match. The recorder latches its
  PID in memory and in the environment. Forked children cannot record into the
  parent's state; exec children inherit the owner latch and cannot overwrite it.
- Output must be new, below the compiled private `forza-recomp/out/` root, and is
  created exclusively with mode 0600 and close-on-exec. Existing files are never
  truncated. This is a local experiment boundary, not a sandbox against hostile
  concurrent filesystem changes.
- At most 262144 events and 64 thread identities are retained. Event storage is
  statically bounded below 20 MiB. Overflow is explicit and invalidates complete
  attribution, without suppressing any original call.
- The default admission window is 75–115 s after recorder initialization. The
  exporter sleeps to an absolute monotonic deadline, without periodic wakeups
  during collection. It then closes admissions atomically and waits at most 5 s
  for telemetry writers, not for GPU completion, before deferred CSV export.
- No destructor/exit join is used: the game calls `_Exit`. Early termination,
  pending calls, missing records or a missing complete footer are incomplete
  evidence. Empty captures cannot be interpreted as zero native-submit cost.
  Calls crossing the window end are counted but excluded from duration totals.

## Isolated build and launch plan

The preparer fingerprints source, preparer, installed UAPI headers, compiler
identity, export map and approved private root. Frozen inputs and the library
stay under `out/asahi-profiling/drm-ioctl-probes/TOKEN/`; it never rebuilds or
installs the SDK/plugin. Failed or existing unverified outputs are preserved.
Only `drmIoctl` and `asahi_drm_ioctl_profile_abi` are exported. ABI 1 is checked by
an eager CPU loader with recording disabled and no device access.

CPU-only build command:

```sh
python3 -B tools/asahi_sdk_lab.py --name drm-build --seconds 60 -- \
  env TMPDIR=/tmp/opencode python3 -B \
  tools/asahi-sdk-lab/prepare_drm_ioctl_profile.py --build
```

`play_probe.py --drm-ioctl-probe DIR` additionally requires the existing isolated
candidate, explicit submission recording, no simultaneous perf/validation, no
ambient `LD_PRELOAD`, and the existing physical-720p/MSAA-Off/resource checks.
It verifies the probe's manifest/build fingerprint, frozen source/export map,
library hash, ABI, private root and current UAPI hashes before using it.

The command inserts `env LD_PRELOAD=...` **after `timeout`, immediately before
the game executable**. The supervisor, inhibitor, timeout and launch helpers do
not receive the injected loader environment. Descendant execs can inherit it,
but the exact-executable/PID checks prevent their recording. A stale owner latch
is removed at the game launch only. The launch environment is not installed in
the shell or Python supervisor.

For an SDK window of 80–110 s, the launcher requests DRM recording at 75–115 s.
SDK starts must be 5–120 s and durations 1–35 s, preserving 5 s nominal margins
and the DRM 45 s maximum. The two recorders initialize at different times, so
actual full coverage must still be checked from their monotonic timestamps.
`--plan-only` verifies and prints the command without creating a game profile or
launching a game; printing `--gpu` inside that plan is not executing a GPU test.
The launcher retains an available `drm-ioctl.csv` in the run artifacts. Its
success status is **not** automatic validation of DRM trace completeness or
reachability: run the analyser separately after exit and kernel observation.

## Offline validation and submission joining

```sh
python3 -B tools/asahi-sdk-lab/analyse_drm_ioctl_profile.py PRIVATE/drm-ioctl.csv
python3 -B tools/asahi-sdk-lab/analyse_drm_ioctl_profile.py PRIVATE/drm-ioctl.csv \
  --submissions PRIVATE/submission.csv
```

The bounded reader rejects malformed intervals/schema and reports missing
completion, loss, unknown CPU, missing thread identities or nonzero native
returns as unqualified evidence. It reports each request separately.

Joining additionally requires a qualified ordinary-job SDK trace, completed
post-window export, consistent monotonic/realtime bridges within 10 ms, and DRM
coverage of the entire SDK window and all queue-submit intervals. It selects
exactly one retained thread comm beginning `Vulkan Submiss`, or requires an
explicit inspected `--tid`. Overlapping DRM calls or calls intersecting an SDK
scope boundary are refused rather than guessed. Native calls outside queue
scopes remain an explicit finding. The report retains both input hashes.

This is interval containment on the pinned Linux monotonic clocks, not
nanosecond clock calibration. **SDK trace v1 has no PID**: the operator must supply
a retained same-run pair; matching clocks/names alone do not prove process
identity. Thread names and a complete CSV do not prove Mesa actually used the
hook on the Vulkan submission thread.

## Completed CPU checks and remaining live gates

The complete bounded CPU suite passed **97 tests**, including fake-libdrm exact
forwarding, positive/error returns, stale `errno`, retry-count preservation,
global/local resolution, concurrent publication, overflow, fork/exec exclusion,
window exclusion, exclusive/private output, early exit, manifest/hash/header
rejection, game-only preload placement and synthetic SDK joins with coverage,
bridge, TID and boundary rejection. The fake provider never calls an ioctl or
opens a GPU/device; this is not live driver validation.

Private records:

- Suite: `out/asahi-sdk-lab/logs/20261028T013543.410841Z-phase2-drm-full-cpu-suite/`.
- Isolated build/ABI check: `20261028T013603.650700Z-phase2-drm-final-isolated-build/`
  under that logs root; probe token `41a24a167d171350`.
- Actual-path CPU launch plan:
  `20261028T013635.159285Z-phase2-drm-real-path-plan-only/`.
- Preservation/hash binding:
  `20261028T013828.893405Z-phase2-drm-preservation-and-hash-binding/`.

The probe's ignored `cpu-validation-and-preservation.json` binds the current
source/library hashes to the suite and launch-plan log hashes. The library SHA-256
is `83b064864c115fcd4f4f3a69aa8e4f90fdeb6b6e590d6c0ac76e05465678acb7`.
The audit verified the functional/frozen runtime and seeds, complete H0 manifest,
SDK lock/three patches, installed libraries, candidate runtime, earlier live
trace/frame hashes and the prior DRM build. The actual-path plan created no
profile, the audit found no active suspension marker, and private outputs remain
Git-ignored. This confirms preservation, not live instrumentation safety.

The preserved functional runtime, SDK install, baseline lock/three patches, H0,
candidate and earlier live trace are not instrumentation build targets. Raw
outputs and hash-bound validation records remain ignored under `out/`; only
source/tests and qualified aggregate documentation are intended for publication.
No commit or push is authorized by this preparation step.

Before using the measurements, separately coordinate one bounded GPU
reachability/correctness gate, inspect its clean post-exit kernel observation,
then assess observer overhead and arrange any game capture. Retain
`STOP-GPU.json`, cooldowns, memory/PSI/swap guards, owned-process cleanup, physical
1280×720 scale 1, ordinary synchronization and no batching/debug options. Do not
automatically chain sessions. Actual calls per Vulkan submission, live overhead,
sustained 30 FPS and the historical termination-associated failure remain open.

The later [live follow-up](asahi-drm-ioctl-findings.md) completed the separately
coordinated synthetic gate and game captures. It establishes hundreds of native
invocations per SDK queue scope, but not isolated overhead or an optimization.
