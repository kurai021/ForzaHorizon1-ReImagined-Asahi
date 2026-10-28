# Phase 1 of the optimization plan: offline render-pass reuse review

This follows the [live DRM findings](asahi-drm-ioctl-findings.md). The owner asked
for findings before recalculating the next actions. **No renderer patch, SDK
rebuild, game/GPU run or configuration change is part of this review.**

## Result: a narrow cache-identity candidate

The pinned Vulkan SDK can use separate cache entries for a normal draw pass and
an ownership-transfer pass even when the selected Vulkan attachment format and
image view are identical. This is a concrete source-level opportunity to test,
not evidence that all ~467 native invocations per SDK submission are redundant.

The first candidate should be restricted to **one color attachment in slot 0,
no depth/stencil, guest 1x samples and `k_8_8_8_8` color format**. These are guard
conditions for a proposed experiment, not a change to the game's attachments or
MSAA policy. All other configurations must retain their existing paths.

### Source chain

Paths below are relative to the pinned SDK under
`forza-recomp/out/asahi-sdk-lab/sdk/`. The command-processor line references use
the frozen, live-tested candidate overlay under
`out/asahi-profiling/candidates/063119c3d4972f3a/overlay/`.

1. `include/rex/graphics/vulkan/render_target_cache.h:39–80`:
   `RenderPassKey` includes `color_rts_use_transfer_formats`; equality and hashing
   include that bit. `FramebufferKey` also includes the render-pass key.
2. `src/graphics/vulkan/render_target_cache.cpp:4953–4980`:
   color ownership transfers unconditionally set that bit before requesting the
   transfer render pass/framebuffer. The adjacent TODO explicitly identifies
   possible reuse of guest passes where formats agree; the broader TODO is not
   itself proof that every transfer can be combined.
3. `render_target_cache.cpp:1757–1854`:
   `k_8_8_8_8` drawing selects `VK_FORMAT_R8G8B8A8_UNORM`; ownership transfer for
   that format falls through to the same format selection. The same does **not**
   hold generally for float/SNORM formats with integer transfer views.
4. `render_target_cache.cpp:2057–2060` and header `:365–367`:
   a separate transfer view is created only when the format differs. Otherwise
   `view_color_transfer()` returns the normal `view_depth_color()` handle.
5. `render_target_cache.cpp:1596–1724,2271–2365`:
   render-pass/framebuffer lookup uses the unnormalized key. In the restricted
   case, toggling only the transfer-format bit selects the same attachment
   descriptions/views but creates a distinct cache identity.
6. Candidate `src/graphics/vulkan/command_processor.cpp:4311–4327,4393–4409`:
   after submitting required barriers, reuse depends on framebuffer identity
   and, for legacy render passes, the render-pass handle too. Distinct cache
   entries prevent that existing reuse fast path, including when dynamic
   rendering is enabled.

### Proposed mechanism, not implemented

For the restricted case only, normalize the transfer-format bit **locally in
both render-pass and framebuffer caches** when it does not change the selected
format or view. Leave shader/transfer-pipeline keys, guest formats, copies,
draws, access/layout tracking and `SubmitBarriers` behavior unchanged. Reuse
would happen through the existing compatible-pass checks, not through a new
blanket permission to merge passes.

The required proof must cover attachment identity, sample count, extents,
load/store behavior, pipeline compatibility, cache lifetime/invalidation and
memory ordering. Equal formats alone are not enough to declare a renderer
change safe. A pending barrier still ends the pass; different attachments still
require a different pass.

**Expected opportunity:** fewer duplicate cached objects and, on eligible
adjacent transfer/draw transitions with no required intervening barrier, fewer
pass closures/restarts. It is not established how often this narrow case occurs
in the captured game. The existing trace has no attachment/key history, so no
FPS gain or number of removable native calls can be predicted from it.

## Paths not selected for a quick change

- Candidate `command_processor.cpp:4274–4308` already avoids a pass end for an
  empty barrier list unless explicitly forced. Removing `force_end_render_pass`
  globally would not preserve the requirements of transfer/compute operations.
- Repeated equal masks are not necessarily redundant barriers. For example,
  `shared_memory.cpp:204–231` commits prior writes even without a usage change;
  `texture_cache.cpp:1966–1978` explicitly preserves write-after-write ordering.
- Shared-memory uploads already support safe hoisting before a pass when page
  hazards allow it (`shared_memory.cpp:278–371`, candidate processor
  `:4486–4540`). Widening that permission needs its own hazard/alias proof.
- Compute memexport, texture loading and ownership transfers can legitimately
  switch between graphics, compute and transfer work. No trace attributes the
  hundreds of native calls uniquely to one of those causes.
- The retained reference Mesa `hk_queue.c:248–256,293–374` splits native submits
  conservatively and documents correctness failures with batching. That remains
  excluded. Reference-tag source is not an exact installed-driver reconstruction.

## Recalculated next actions

1. **Recommended next scope: CPU-only proof for this one restricted candidate.**
   Test the cache-key equivalence and its negative cases, plus rendering-info
   equivalence and the existing reuse/invalidation behavior. Do not rebuild or
   alter the functional SDK just to investigate the proposal.
2. If that proof fails or requires broader render-target changes, stop this
   candidate and choose another; do not compensate by removing synchronization.
3. If it passes, separately agree on an optional isolated overlay/candidate and
   its synthetic GPU gate. A future bounded capture must determine whether the
   eligible transitions actually occur and whether pass/native-call counts fall.
4. If eligible transitions are absent or costs do not improve, reject this
   candidate rather than expanding it into general transfer merging.
5. If a gain is reproducible with equivalent effective work and correct visuals,
   then consider driving validation. No such run is started by this review.

## Frame-rate and user-experience objective

The owner reports that the earlier 25–26 FPS averages are enjoyable. Preserve
that functioning experience: prioritize correctness, stability and reduced
slow-frame frequency over an invasive change chasing a headline average.

`forza-recomp/src/main.cpp:31–43` defaults to 30 and supports other explicitly
selected rates; the retained commands explicitly request 30. Thus **30 is the
configured target/cap for these sessions**, not a demonstrated universal engine
ceiling or a sustained lock. A capped near-30 stationary scene can hide an
optimization's FPS benefit while still revealing CPU/headroom savings.

Window/swapchain dimensions, internal render scale and native submission work
are different variables. Similar perceived behavior at different window sizes
is consistent with substantial CPU/driver submission cost, but is not a
controlled demonstration that resolution never matters. Retain the physical
1280×720, scale-1 objective; no resolution or frame-rate experiment is proposed.

This review changes only documentation and private review records. Any new
renderer implementation, GPU session, commit or push remains a later decision.

The owner subsequently authorized the
[optional patch and tests](asahi-render-pass-reuse-candidate.md). CPU and enabled
synthetic GPU checks passed, but the first patch-off game reference stopped for
swap growth before its measurement window. A later unpatched retry completed,
but the subsequently authorized patched-on trial also stopped for swap growth
before measurement. A new `STOP-GPU.json` was left active; no performance benefit
or algorithm-specific cause of the resource growth is established. The owner
later explicitly authorized a checkout-local swap-growth override and archival
of that swap-only marker, then explicitly disabled PSI after an unpatched PSI
abort. A fresh unpatched/enabled pair completed cleanly with normal owner-reported
images, but no demonstrated native-call/FPS benefit (27.63/27.67 FPS; 461.72/462.62
native calls/job). Workloads and long-tail events differ; see the candidate
checkpoint for the current policy, evidence and limitations.
