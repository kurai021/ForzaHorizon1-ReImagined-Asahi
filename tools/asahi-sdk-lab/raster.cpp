// Read actual IssueDraw render targets through the SDK's submission machinery.
// Access is enabled only for the isolated lab plugin, never for normal builds.
#include "workloads.h"
#include <rex/ui/vulkan/util.h>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>

namespace rex::graphics::vulkan {
struct AsahiSdkLabAccess {
  static void ReadColor(VulkanCommandProcessor& commands) {
    auto& cache = *commands.render_target_cache_;
    auto* color = static_cast<VulkanRenderTargetCache::VulkanRenderTarget*>(
        cache.last_update_accumulated_render_targets()[1]);
    if (!color) throw std::runtime_error("IssueDraw produced no color target");
    auto format = cache.GetColorVulkanFormat(color->key().GetColorFormat());
    if (format != VK_FORMAT_R8G8B8A8_UNORM && format != VK_FORMAT_B8G8R8A8_UNORM)
      throw std::runtime_error("unexpected diagnostic render target format");
    const auto& target_key = color->key();
    const uint32_t target_width = target_key.GetWidth();
    const uint32_t target_height =
        cache.GetRenderTargetHeight(target_key.pitch_tiles_at_32bpp, target_key.msaa_samples);
    std::printf("LAB raster target %ux%u\n", target_width, target_height);
    auto* device = commands.GetVulkanDevice();
    const auto& f = device->functions();
    const VkDeviceSize readback_size =
        VkDeviceSize(target_width) * target_height * sizeof(uint32_t);
    VkBuffer buffer{}; VkDeviceMemory memory{}; uint32_t memory_type{};
    if (!ui::vulkan::util::CreateDedicatedAllocationBuffer(device, readback_size,
        VK_BUFFER_USAGE_TRANSFER_DST_BIT, ui::vulkan::util::MemoryPurpose::kReadback,
        buffer, memory, &memory_type)) throw std::runtime_error("raster readback allocation");
    if (!commands.BeginSubmission(true)) throw std::runtime_error("raster readback begin");
    commands.EndRenderPass();
    auto range = ui::vulkan::util::InitializeSubresourceRange(VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1);
    auto stage = color->current_stage_mask(); auto access = color->current_access_mask();
    auto layout = color->current_layout();
    commands.PushImageMemoryBarrier(color->image(), range, stage, VK_PIPELINE_STAGE_TRANSFER_BIT,
        access, VK_ACCESS_TRANSFER_READ_BIT, layout, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);
    commands.SubmitBarriers(true);
    VkBufferImageCopy copy{};
    copy.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    copy.imageExtent = {target_width, target_height, 1};
    if (!commands.AwaitAllQueueOperationsCompletion()) throw std::runtime_error("raster transition submit");
    // SDK descriptors/state are left intact. Read pixels with a bounded native
    // copy only after all SDK work completes, acquiring its queue's mutex.
    VkCommandPoolCreateInfo pool_info{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    pool_info.queueFamilyIndex = device->queue_family_graphics_compute();
    VkCommandPool pool{};
    if (f.vkCreateCommandPool(device->device(), &pool_info, nullptr, &pool) != VK_SUCCESS)
      throw std::runtime_error("raster copy command pool");
    VkCommandBufferAllocateInfo alloc{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    alloc.commandPool = pool; alloc.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY; alloc.commandBufferCount = 1;
    VkCommandBuffer cmd{};
    if (f.vkAllocateCommandBuffers(device->device(), &alloc, &cmd) != VK_SUCCESS)
      throw std::runtime_error("raster copy command buffer");
    VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    if (f.vkBeginCommandBuffer(cmd, &begin) != VK_SUCCESS) throw std::runtime_error("raster copy begin");
    f.vkCmdCopyImageToBuffer(cmd, color->image(), VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buffer, 1,
                             &copy);
    VkMemoryBarrier host{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
    host.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT; host.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
    f.vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
        0, 1, &host, 0, nullptr, 0, nullptr);
    if (f.vkEndCommandBuffer(cmd) != VK_SUCCESS) throw std::runtime_error("raster copy end");
    VkFenceCreateInfo fence_info{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO}; VkFence fence{};
    if (f.vkCreateFence(device->device(), &fence_info, nullptr, &fence) != VK_SUCCESS)
      throw std::runtime_error("raster copy fence");
    VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    submit.commandBufferCount = 1; submit.pCommandBuffers = &cmd;
    {
      auto queue = device->AcquireQueue(pool_info.queueFamilyIndex, 0);
      if (f.vkQueueSubmit(queue.queue(), 1, &submit, fence) != VK_SUCCESS)
        throw std::runtime_error("raster copy submission");
    }
    if (f.vkWaitForFences(device->device(), 1, &fence, VK_TRUE, 5000000000ull) != VK_SUCCESS) {
      std::fprintf(stderr, "LAB GPU STOP: raster copy fence timeout or device loss\n");
      std::fflush(nullptr); std::_Exit(124);
    }
    f.vkDestroyFence(device->device(), fence, nullptr);
    f.vkDestroyCommandPool(device->device(), pool, nullptr);
    if (!commands.BeginSubmission(true)) throw std::runtime_error("raster restore begin");
    commands.PushImageMemoryBarrier(color->image(), range, VK_PIPELINE_STAGE_TRANSFER_BIT, stage,
        VK_ACCESS_TRANSFER_READ_BIT, access, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, layout);
    if (!commands.AwaitAllQueueOperationsCompletion()) throw std::runtime_error("raster readback submit");
    void* mapping{};
    if (f.vkMapMemory(device->device(), memory, 0, VK_WHOLE_SIZE, 0, &mapping) != VK_SUCCESS)
      throw std::runtime_error("raster readback map");
    if (!(device->memory_types().host_coherent & (1u << memory_type))) {
      VkMappedMemoryRange invalidate{VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE};
      invalidate.memory = memory; invalidate.size = VK_WHOLE_SIZE;
      if (f.vkInvalidateMappedMemoryRanges(device->device(), 1, &invalidate) != VK_SUCCESS)
        throw std::runtime_error("raster readback invalidate");
    }
    auto* bytes = static_cast<const uint8_t*>(mapping);
    const unsigned red = format == VK_FORMAT_R8G8B8A8_UNORM ? 0 : 2;
    unsigned red_pixels = 0;
    uint32_t first_red_x = 0, first_red_y = 0;
    for (uint32_t y = 0; y < target_height; ++y) {
      for (uint32_t x = 0; x < target_width; ++x) {
        const uint8_t* pixel = bytes + (size_t(y) * target_width + x) * 4;
        if (pixel[red] == 255 && pixel[1] == 0 && pixel[2 - red] == 0 && pixel[3] == 255) {
          if (!red_pixels) { first_red_x = x; first_red_y = y; }
          ++red_pixels;
        }
      }
    }
    const auto output = std::filesystem::path(std::getenv("ASAHI_LAB_OUTPUT"));
    constexpr uint32_t kCrop = 64;
    const uint32_t crop_width = target_width < kCrop ? target_width : kCrop;
    const uint32_t crop_height = target_height < kCrop ? target_height : kCrop;
    const uint32_t crop_x = red_pixels ? std::min(first_red_x, target_width - crop_width) : 0;
    const uint32_t crop_y = red_pixels ? std::min(first_red_y, target_height - crop_height) : 0;
    std::ofstream ppm(output / "issue-draw-raster.ppm", std::ios::binary);
    ppm << "P6\n" << crop_width << ' ' << crop_height << "\n255\n";
    for (uint32_t y = 0; y < crop_height; ++y) {
      for (uint32_t x = 0; x < crop_width; ++x) {
        const uint8_t* pixel = bytes + (size_t(crop_y + y) * target_width + crop_x + x) * 4;
        const char rgb[]{char(pixel[red]), char(pixel[1]), char(pixel[2 - red])};
        ppm.write(rgb, 3);
      }
    }
    f.vkUnmapMemory(device->device(), memory);
    f.vkDestroyBuffer(device->device(), buffer, nullptr);
    f.vkFreeMemory(device->device(), memory, nullptr);
    if (!red_pixels)
      throw std::runtime_error("IssueDraw color readback has no expected red point (target=" +
                               std::to_string(target_width) + "x" +
                               std::to_string(target_height) + ")");
    std::printf("LAB INTEGRATION PASS raster-and-memexport: %u red pixels, first at %u,%u "
                "(target %ux%u)\n", red_pixels, first_red_x, first_red_y, target_width,
                target_height);
  }
};
}

void VerifyIntegratedRaster(rex::graphics::vulkan::VulkanCommandProcessor& commands) {
  rex::graphics::vulkan::AsahiSdkLabAccess::ReadColor(commands);
}
