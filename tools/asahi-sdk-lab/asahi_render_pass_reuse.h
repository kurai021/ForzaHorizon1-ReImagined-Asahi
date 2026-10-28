#pragma once
// Optional experiment: cache identity only. Never change guest/pipeline keys or barriers.
#include <rex/graphics/xenos.h>

namespace rex::graphics::vulkan::render_pass_reuse {
template <typename Key>
Key CacheKey(Key key, bool enabled) {
  if (!enabled) return key;
  Key eligible{};
  eligible.msaa_samples = xenos::MsaaSamples::k1X;
  eligible.depth_and_color_used = 0b10;
  eligible.color_0_view_format = xenos::ColorRenderTargetFormat::k_8_8_8_8;
  eligible.color_rts_use_transfer_formats = 1;
  // Exact-key whitelist also excludes unused/reserved bits, gamma, depth, MRT
  // and all other sample/format variants. RGBA8 uses the same transfer view.
  if (key == eligible) key.color_rts_use_transfer_formats = 0;
  return key;
}
}  // namespace rex::graphics::vulkan::render_pass_reuse
