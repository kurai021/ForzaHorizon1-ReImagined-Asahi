// CPU-only: exact extracted SDK method bodies, real keys/Vulkan structs, mocked
// allocation and command sink. No Vulkan entry point or GPU device is called.
#include <rex/graphics/vulkan/render_target_cache.h>
#include <rex/graphics/vulkan/asahi_render_pass_reuse.h>
#include <rex/logging.h>
#include "fh1_frame_cost.h"
#include <atomic>
#include <cassert>
#include <cstdio>
#include <string>
#include <vector>

namespace rex::graphics::vulkan {
struct AsahiSdkLabAccess {
  using FramebufferKey = VulkanRenderTargetCache::FramebufferKey;
  using RenderTargetKey = VulkanRenderTargetCache::RenderTargetKey;
};
using Key = VulkanRenderTargetCache::RenderPassKey;
using Framebuffer = VulkanRenderTargetCache::Framebuffer;
namespace fixture_flags { inline bool vulkan_dynamic_rendering = true;
inline bool vulkan_hoist_shared_memory_uploads = false; }
#undef REXCVAR_GET
#define REXCVAR_GET(name) fixture_flags::name

template <typename T> T Handle(uintptr_t value) { return reinterpret_cast<T>(value); }
struct PassInfo {
  std::vector<VkAttachmentDescription> attachments;
  std::vector<VkAttachmentReference> colors;
  VkAttachmentReference depth{};
  std::vector<VkSubpassDependency> dependencies;
  bool has_depth = false;
  bool operator==(const PassInfo& other) const {
    return has_depth == other.has_depth && attachments.size() == other.attachments.size() &&
      colors.size() == other.colors.size() && dependencies.size() == other.dependencies.size() &&
      !std::memcmp(attachments.data(), other.attachments.data(), attachments.size()*sizeof(attachments[0])) &&
      !std::memcmp(colors.data(), other.colors.data(), colors.size()*sizeof(colors[0])) &&
      (!has_depth || !std::memcmp(&depth, &other.depth, sizeof(depth))) &&
      !std::memcmp(dependencies.data(), other.dependencies.data(), dependencies.size()*sizeof(dependencies[0]));
  }
};
struct MockDevice {
  struct Properties { uint32_t framebufferColorSampleCounts = 7, sampledImageIntegerSampleCounts = 7;
    uint32_t maxFramebufferWidth = 16384, maxFramebufferHeight = 16384; bool dynamicRendering = true; } props;
  const Properties& properties() const { return props; }
  VkDevice device() const { return Handle<VkDevice>(1); }
  std::vector<PassInfo> passes;
  struct FB { std::vector<VkImageView> views; uint32_t width, height; };
  std::vector<FB> framebuffers;
  static inline MockDevice* current = nullptr;
  struct Functions {
    static VkResult vkCreateRenderPass(VkDevice, const VkRenderPassCreateInfo* info,
                                      const VkAllocationCallbacks*, VkRenderPass* out) {
      PassInfo saved;
      saved.attachments.assign(info->pAttachments, info->pAttachments + info->attachmentCount);
      const auto& sub = info->pSubpasses[0];
      saved.colors.assign(sub.pColorAttachments, sub.pColorAttachments + sub.colorAttachmentCount);
      saved.has_depth = sub.pDepthStencilAttachment != nullptr;
      if (saved.has_depth) saved.depth = *sub.pDepthStencilAttachment;
      saved.dependencies.assign(info->pDependencies, info->pDependencies + info->dependencyCount);
      current->passes.push_back(saved);
      *out = Handle<VkRenderPass>(current->passes.size()+10);
      return VK_SUCCESS;
    }
    static VkResult vkCreateFramebuffer(VkDevice, const VkFramebufferCreateInfo* info,
                                       const VkAllocationCallbacks*, VkFramebuffer* out) {
      current->framebuffers.push_back({{info->pAttachments, info->pAttachments+info->attachmentCount},
                                      info->width, info->height});
      *out = Handle<VkFramebuffer>(current->framebuffers.size()+100);
      return VK_SUCCESS;
    }
  } funcs;
  const Functions& functions() const { current = const_cast<MockDevice*>(this); return funcs; }
};
struct MockTarget {
  using RenderTargetKey = AsahiSdkLabAccess::RenderTargetKey;
  RenderTargetKey target_key{};
  VkImageView view = Handle<VkImageView>(500);
  const RenderTargetKey& key() const { return target_key; }
};
struct MockVulkanTarget : MockTarget {
  static constexpr auto kColorDrawLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
  static constexpr auto kDepthDrawLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
  static constexpr VkPipelineStageFlags kColorDrawStageMask = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;
  static constexpr VkAccessFlags kColorDrawAccessMask = VK_ACCESS_COLOR_ATTACHMENT_READ_BIT | VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
  static constexpr VkPipelineStageFlags kDepthDrawStageMask = VK_PIPELINE_STAGE_EARLY_FRAGMENT_TESTS_BIT | VK_PIPELINE_STAGE_LATE_FRAGMENT_TESTS_BIT;
  static constexpr VkAccessFlags kDepthDrawAccessMask = VK_ACCESS_DEPTH_STENCIL_ATTACHMENT_READ_BIT | VK_ACCESS_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT;
  VkImageView view_depth_color() const { return view; }
  VkImageView view_depth_stencil() const { return view; }
  VkImageView view_color_transfer() const { return view; }
};
struct CacheHarness {
  using RenderPassKey = Key;
  using FramebufferKey = AsahiSdkLabAccess::FramebufferKey;
  using RenderTargetKey = AsahiSdkLabAccess::RenderTargetKey;
  using RenderTarget = MockTarget;
  using VulkanRenderTarget = MockVulkanTarget;
  using Framebuffer = vulkan::Framebuffer;
  enum class Path { kHostRenderTargets };
  Path GetPath() const { return Path::kHostRenderTargets; }
  struct Processor { MockDevice device; const MockDevice* GetVulkanDevice() const { return &device; } } command_processor_;
  const bool asahi_render_pass_reuse_;
  explicit CacheHarness(bool enabled) : asahi_render_pass_reuse_(enabled) {}
  std::unordered_map<Key, VkRenderPass, Key::Hasher> render_passes_;
  struct Hasher { size_t operator()(const FramebufferKey& key) const {
    const auto* bytes = reinterpret_cast<const unsigned char*>(&key); size_t result = 0;
    for (size_t i=0;i<sizeof(key);++i) result = result*131 + bytes[i]; return result; } };
  std::unordered_map<FramebufferKey, Framebuffer, Hasher> framebuffers_;
  bool msaa_2x_attachments_supported_ = true, gamma_render_target_as_unorm16_ = false;
  bool color_rg16_draw_format_fallback_to_float_ = false, color_rgba16_draw_format_fallback_to_float_ = false;
  bool color_16bit_transfer_uint_formats_supported_ = true, color_32bit_transfer_uint_formats_supported_ = true;
  Key last_update_render_pass_key_{};
  MockTarget* targets[5]{};
  MockTarget* const* last_update_accumulated_render_targets() const { return targets; }
  bool IsMsaa2xSupported(bool) const { return true; }
  uint32_t draw_resolution_scale_x() const { return 1; }
  uint32_t draw_resolution_scale_y() const { return 1; }
  uint32_t GetRenderTargetHeight(uint32_t, xenos::MsaaSamples) const { return 2048; }
  VkFormat GetDepthVulkanFormat(xenos::DepthRenderTargetFormat) const { return VK_FORMAT_D24_UNORM_S8_UINT; }
  VkFormat GetColorVulkanFormat(xenos::ColorRenderTargetFormat) const;
  VkFormat GetColorOwnershipTransferVulkanFormat(xenos::ColorRenderTargetFormat,
                  xenos::MsaaSamples = xenos::MsaaSamples::k1X, bool* = nullptr) const;
  VkRenderPass GetHostRenderTargetsRenderPass(Key);
  const Framebuffer* GetHostRenderTargetsFramebuffer(Key, uint32_t, const MockTarget* const*);
  void GetLastUpdateRenderingAttachments(VkRenderingAttachmentInfo*, uint32_t*,
                                        VkRenderingAttachmentInfo*, VkRenderingAttachmentInfo*) const;
};
struct CommandSink {
  unsigned begins = 0, ends = 0;
  std::vector<VkRenderingAttachmentInfo> colors;
  void CmdVkBeginRenderPass(const VkRenderPassBeginInfo*, VkSubpassContents) { ++begins; }
  void CmdVkEndRenderPass() { ++ends; }
  void CmdVkEndRendering() { ++ends; }
  void CmdVkBeginRendering(const VkRenderingInfo* info) {
    ++begins; colors.clear();
    if (info->colorAttachmentCount) colors.assign(info->pColorAttachments, info->pColorAttachments+info->colorAttachmentCount);
  }
  uint32_t CmdHoistPoint() { return 0; }
};
struct ProcessorHarness {
  bool submission_open_ = true, in_render_pass_ = false, hoist_render_pass_ = false;
  bool pending = false;
  unsigned submitted_barriers = 0;
  VkRenderPass current_render_pass_ = VK_NULL_HANDLE;
  const Framebuffer* current_framebuffer_ = nullptr;
  uint32_t hoist_point_ = 0; uint64_t hoist_render_pass_serial_ = 0;
  std::vector<uint64_t> hoist_read_pages_;
  CommandSink deferred_command_buffer_;
  CacheHarness* render_target_cache_;
  explicit ProcessorHarness(CacheHarness& cache) : render_target_cache_(&cache) {}
  const MockDevice* GetVulkanDevice() const { return &render_target_cache_->command_processor_.device; }
  void EndRenderPass();
  bool SubmitBarriers(bool force) { if (pending || force) EndRenderPass();
    if (!pending) return false; pending=false; ++submitted_barriers; return true; }
  void SubmitBarriersAndEnterRenderTargetCacheRenderPass(VkRenderPass, const Framebuffer*);
  void SubmitBarriersAndEnterRenderTargetCacheRenderPass(VkRenderPass, const Framebuffer*, VkImageView, bool);
};
// Generated from the composed SDK files. Only owner class/device type names are
// adapted for the mocks above; cache/descriptor/reuse statements are unchanged.
#include "extracted_reuse_methods.inc"

Key DrawKey() { Key key{}; key.msaa_samples=xenos::MsaaSamples::k1X;
  key.depth_and_color_used=2; key.color_0_view_format=xenos::ColorRenderTargetFormat::k_8_8_8_8; return key; }
Key TransferKey() { auto key=DrawKey();key.color_rts_use_transfer_formats=1;return key; }

void Whitelist() {
  const auto transfer=TransferKey(),draw=DrawKey();
  assert(render_pass_reuse::CacheKey(transfer,true)==draw);
  assert(render_pass_reuse::CacheKey(transfer,false)==transfer);
  assert(render_pass_reuse::CacheKey(draw,true)==draw);
  for (unsigned bit=0;bit<32;++bit) { auto key=transfer;key.key^=1u<<bit;
    assert(render_pass_reuse::CacheKey(key,true)==key); }
  uint32_t random=12345;
  for (unsigned i=0;i<100000;++i) { random=random*1664525u+1013904223u;Key key{};key.key=random;
    assert(render_pass_reuse::CacheKey(key,false)==key);
    assert(render_pass_reuse::CacheKey(key,true)==(key==transfer?draw:key)); }
}

void Exercise(bool enabled, bool dynamic, bool barrier) {
  fixture_flags::vulkan_dynamic_rendering=dynamic;
  CacheHarness cache(enabled);MockVulkanTarget target;target.target_key.base_tiles=10;
  cache.targets[1]=&target;cache.last_update_render_pass_key_=DrawKey();
  const MockTarget* attachments[5]{nullptr,&target,nullptr,nullptr,nullptr};
  auto pass=cache.GetHostRenderTargetsRenderPass(DrawKey());
  auto* draw=cache.GetHostRenderTargetsFramebuffer(DrawKey(),16,attachments);
  auto transfer_pass=cache.GetHostRenderTargetsRenderPass(TransferKey());
  auto* transfer=cache.GetHostRenderTargetsFramebuffer(TransferKey(),16,attachments);
  assert((pass==transfer_pass)==enabled);assert((draw==transfer)==enabled);
  auto& device=cache.command_processor_.device;
  assert(device.passes.size()==(enabled?1:2));
  if (!enabled) assert(device.passes[0]==device.passes[1]);
  assert(device.framebuffers.size()==(enabled?1:2));
  if (!enabled) {const auto& a=device.framebuffers[0];const auto& b=device.framebuffers[1];
    assert(a.views==b.views && a.width==b.width && a.height==b.height);}
  ProcessorHarness commands(cache);
  commands.SubmitBarriersAndEnterRenderTargetCacheRenderPass(pass,draw);
  auto original_colors=commands.deferred_command_buffer_.colors;
  commands.pending=barrier;
  commands.SubmitBarriersAndEnterRenderTargetCacheRenderPass(transfer_pass,transfer,target.view,false);
  commands.SubmitBarriersAndEnterRenderTargetCacheRenderPass(pass,draw);
  assert(commands.deferred_command_buffer_.begins==(enabled?(barrier?2u:1u):3u));
  assert(commands.submitted_barriers==unsigned(barrier));
  if (dynamic) { const auto& colors=commands.deferred_command_buffer_.colors;
    assert(original_colors.size()==1 && colors.size()==1);
    assert(!std::memcmp(original_colors.data(),colors.data(),sizeof(colors[0]))); }
  commands.EndRenderPass();assert(!commands.in_render_pass_ && !commands.current_framebuffer_);
  cache.framebuffers_.clear();cache.render_passes_.clear();
  auto fresh=cache.GetHostRenderTargetsRenderPass(DrawKey());assert(fresh!=pass);
}

void IdentityGuards() {
  CacheHarness cache(true);MockVulkanTarget target;target.target_key.base_tiles=10;
  const MockTarget* attachments[5]{nullptr,&target,nullptr,nullptr,nullptr};
  auto* first=cache.GetHostRenderTargetsFramebuffer(DrawKey(),16,attachments);
  target.target_key.base_tiles=11;
  auto* other=cache.GetHostRenderTargetsFramebuffer(TransferKey(),16,attachments);assert(first!=other);
  auto* different_pitch=cache.GetHostRenderTargetsFramebuffer(TransferKey(),17,attachments);assert(other!=different_pitch);
  auto gamma=TransferKey();gamma.color_0_view_format=xenos::ColorRenderTargetFormat::k_8_8_8_8_GAMMA;
  auto normal=gamma;normal.color_rts_use_transfer_formats=0;
  assert(cache.GetHostRenderTargetsRenderPass(gamma)!=cache.GetHostRenderTargetsRenderPass(normal));
  auto msaa=TransferKey();msaa.msaa_samples=xenos::MsaaSamples::k2X;normal=msaa;normal.color_rts_use_transfer_formats=0;
  assert(cache.GetHostRenderTargetsRenderPass(msaa)!=cache.GetHostRenderTargetsRenderPass(normal));
  auto floating=TransferKey();floating.color_0_view_format=xenos::ColorRenderTargetFormat::k_16_16_16_16_FLOAT;
  assert(cache.GetColorVulkanFormat(floating.color_0_view_format)!=cache.GetColorOwnershipTransferVulkanFormat(floating.color_0_view_format));
  normal=floating;normal.color_rts_use_transfer_formats=0;
  assert(cache.GetHostRenderTargetsRenderPass(floating)!=cache.GetHostRenderTargetsRenderPass(normal));
}
}  // namespace rex::graphics::vulkan

int main(int argc,char** argv) {
  using namespace rex::graphics::vulkan;
  assert(argc==2);std::string mode=argv[1];
  if(mode=="whitelist") Whitelist();
  else if(mode=="identity") IdentityGuards();
  else {
    bool enabled=mode.find("enabled")!=std::string::npos;
    bool dynamic=mode.find("dynamic")!=std::string::npos;
    bool barrier=mode.find("barrier")!=std::string::npos;
    Exercise(enabled,dynamic,barrier);
  }
  std::printf("PASS: CPU extracted-method regression %s; no Vulkan/device calls\n",argv[1]);
}
