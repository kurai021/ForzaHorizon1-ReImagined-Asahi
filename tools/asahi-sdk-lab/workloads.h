#pragma once
#include <rex/graphics/vulkan/command_processor.h>
void TranslateFixtures(rex::ui::vulkan::VulkanDevice* device);
std::vector<uint32_t> MemexportFixtureBigEndian();
std::vector<uint32_t> IndexedMemexportFixtureBigEndian(bool raster = false);
std::vector<uint32_t> PixelFixtureBigEndian();
void VerifyIntegratedRaster(rex::graphics::vulkan::VulkanCommandProcessor& commands);
#ifdef REX_ASAHI_READBACK_GATE
void VerifyReadbackLayoutCpu();
#endif
void ExecuteFixtures(rex::ui::vulkan::VulkanDevice* device,
                     rex::graphics::vulkan::VulkanCommandProcessor& commands);
