// Synthetic SDK audit only. No game code, assets, or shaders.
#include <rex/graphics/vulkan/command_processor.h>
#include <rex/graphics/vulkan/graphics_system.h>
#include <rex/graphics/pipeline/shader/spirv_translator.h>
#include <rex/logging.h>
#include <rex/system/xmemory.h>
#include <rex/ui/vulkan/provider.h>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>
#include <bit>
#include <vector>
#include "workloads.h"

namespace {
using Device = rex::ui::vulkan::VulkanDevice;
std::atomic<unsigned> errors{0};
std::atomic<unsigned> warnings{0};

VKAPI_ATTR VkBool32 VKAPI_CALL Validation(VkDebugUtilsMessageSeverityFlagBitsEXT severity,
    VkDebugUtilsMessageTypeFlagsEXT, const VkDebugUtilsMessengerCallbackDataEXT* message, void*) {
  if (severity & VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT) ++errors;
  if (severity & VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT) ++warnings;
  std::fprintf(stderr, "LAB_VALIDATION %u %s: %s\n", severity,
               message->pMessageIdName ? message->pMessageIdName : "", message->pMessage);
  return VK_FALSE;
}

void Check(VkResult result, const char* operation) {
  if (result != VK_SUCCESS) throw std::runtime_error(std::string(operation) + ": " + std::to_string(result));
}

class Graphics : public rex::graphics::vulkan::VulkanGraphicsSystem {
 public:
  void Adopt(std::unique_ptr<rex::ui::vulkan::VulkanProvider> provider, rex::memory::Memory* memory) {
    provider_ = std::move(provider);
    memory_ = memory;
  }
};

class Commands : public rex::graphics::vulkan::VulkanCommandProcessor {
 public:
  explicit Commands(Graphics* graphics) : VulkanCommandProcessor(graphics, nullptr) {}
  using VulkanCommandProcessor::SetupContext;
  using VulkanCommandProcessor::ShutdownContext;
  void VerifyMemexportRejected() {
    const auto words = MemexportFixtureBigEndian();
    active_vertex_shader_ = LoadShader(rex::graphics::xenos::ShaderType::kVertex, 0,
                                       words.data(), uint32_t(words.size()));
    if (!active_vertex_shader_) throw std::runtime_error("memexport fixture load failed");
    if (IssueDraw(rex::graphics::xenos::PrimitiveType::kPointList, 1, nullptr, false))
      throw std::runtime_error("backend unexpectedly accepted unsupported vertex memexport");
    active_vertex_shader_ = nullptr;
    std::puts("LAB EXPECTED REJECTION: real IssueDraw refuses vertex memexport; no silent omission");
  }
  void VerifyIntegratedMemexport(bool raster_only = false) {
    namespace x = rex::graphics::xenos;
    namespace r = rex::graphics::reg;
    const auto words = IndexedMemexportFixtureBigEndian();
    active_vertex_shader_ = LoadShader(x::ShaderType::kVertex, 0, words.data(), uint32_t(words.size()));
    if (!active_vertex_shader_) throw std::runtime_error("indexed fixture load failed");
    constexpr uint32_t output = 0x01000000, indices = 0x02000000;
    const auto run = [&](const char* name, bool dma, bool wide, bool big,
                         std::vector<uint32_t> source, uint32_t base_offset,
                         bool restart, bool alias) {
      InvalidateGpuMemory();
      std::memset(memory_->TranslatePhysical(output), 0xa5, 4096);
      const uint32_t index_base = alias ? output : indices + (wide ? 0 : 2);
      const uint32_t bytes = uint32_t(source.size()) * (wide ? 4u : 2u);
      auto* index_memory = memory_->TranslatePhysical<uint8_t*>(index_base);
      for (size_t i = 0; i < source.size(); ++i) {
        if (wide) {
          uint32_t value = big ? std::byteswap(source[i]) : source[i];
          std::memcpy(index_memory + 4 * i, &value, 4);
        } else {
          uint16_t value = uint16_t(source[i]); if (big) value = std::byteswap(value);
          std::memcpy(index_memory + 2 * i, &value, 2);
        }
      }
      std::vector<uint8_t> expected(4096);
      std::memcpy(expected.data(), memory_->TranslatePhysical(output), expected.size());
      for (size_t i = 0; i < source.size(); ++i) {
        if (restart && source[i] == (wide ? 0xffffffu : 0xffffu)) continue;
        const uint32_t vertex = ((dma ? source[i] : uint32_t(i)) & 0xffffffu) + base_offset;
        if (vertex >= 64) throw std::runtime_error("fixture stream overflow");
        float value = float(vertex);
        std::memcpy(expected.data() + vertex * 4, &value, 4);
      }
      x::xe_gpu_memexport_stream_t stream{};
      stream.base_address = output >> 2; stream.const_0x1 = 1;
      stream.const_0x4b000000 = 0x4b000000;
      stream.format = x::ColorFormat::k_32_FLOAT;
      stream.num_format = x::SurfaceNumberFormat::kFloat;
      stream.const_0x4b0 = 0x4b0; stream.index_count = 64; stream.const_0x96 = 0x96;
      uint32_t raw[4]; std::memcpy(raw, &stream, sizeof(raw));
      for (unsigned i = 0; i < 4; ++i) {
        WriteRegister(rex::graphics::XE_GPU_REG_SHADER_CONSTANT_000_X + 4 + i,
                      std::bit_cast<uint32_t>(i == 1 ? 1.0f : 0.0f));
        WriteRegister(rex::graphics::XE_GPU_REG_SHADER_CONSTANT_000_X + 8 + i, raw[i]);
      }
      WriteRegister(rex::graphics::XE_GPU_REG_RB_SURFACE_INFO, 1280);
      WriteRegister(rex::graphics::XE_GPU_REG_VGT_MIN_VTX_INDX, 0);
      WriteRegister(rex::graphics::XE_GPU_REG_VGT_MAX_VTX_INDX, 0xffffff);
      WriteRegister(rex::graphics::XE_GPU_REG_VGT_INDX_OFFSET, base_offset);
      WriteRegister(rex::graphics::XE_GPU_REG_VGT_DMA_BASE, index_base);
      r::VGT_DMA_SIZE size{};
      size.num_words = uint32_t(source.size());
      size.swap_mode = !big ? x::Endian::kNone : wide ? x::Endian::k8in32 : x::Endian::k8in16;
      WriteRegister(r::VGT_DMA_SIZE::register_index, size.value);
      r::PA_SU_SC_MODE_CNTL control{}; control.multi_prim_ib_ena = restart;
      WriteRegister(r::PA_SU_SC_MODE_CNTL::register_index, control.value);
      WriteRegister(rex::graphics::XE_GPU_REG_VGT_MULTI_PRIM_IB_RESET_INDX, wide ? 0xffffffu : 0xffffu);
      r::VGT_DRAW_INITIATOR draw{};
      draw.prim_type = restart ? x::PrimitiveType::kTriangleStrip : x::PrimitiveType::kPointList;
      draw.num_indices = uint32_t(source.size()); draw.index_size = wide ? x::IndexFormat::kInt32 : x::IndexFormat::kInt16;
      draw.source_select = dma ? x::SourceSelect::kDMA : x::SourceSelect::kAutoIndex;
      WriteRegister(r::VGT_DRAW_INITIATOR::register_index, draw.value);
      if (!IssueDraw(draw.prim_type, uint32_t(source.size()), nullptr, false))
        throw std::runtime_error(std::string("IssueDraw integration failed: ") + name);
      if (std::memcmp(memory_->TranslatePhysical(output), expected.data(), expected.size()))
        throw std::runtime_error(std::string("IssueDraw readback mismatch: ") + name);
      std::printf("LAB INTEGRATION PASS %s: real IssueDraw, %u indices, %u source bytes, 4096-byte CPU mirror verified\n",
                  name, draw.num_indices, bytes);
    };
    if (!raster_only) {
    run("auto", false, false, false, {0, 1, 2}, 0, false, false);
    run("dma16-be-offset2", true, false, true, {3, 1, 2}, 0, false, false);
    run("dma16-le", true, false, false, {2, 0, 1}, 0, false, false);
    run("dma32-be-mask24", true, true, true, {0xab000003, 1, 2}, 0, false, false);
    run("base-offset", true, true, false, {2, 0, 1}, 4, false, false);
    run("primitive-reset16", true, false, true, {0, 1, 0xffff, 2, 3}, 0, true, false);
    run("index-output-alias", true, true, false, {2, 0, 1}, 0, false, true);
    }
    const auto raster_words = IndexedMemexportFixtureBigEndian(true);
    const auto pixel_words = PixelFixtureBigEndian();
    active_vertex_shader_ = LoadShader(x::ShaderType::kVertex, 0, raster_words.data(), uint32_t(raster_words.size()));
    active_pixel_shader_ = LoadShader(x::ShaderType::kPixel, 0, pixel_words.data(), uint32_t(pixel_words.size()));
    for (unsigned i = 0; i < 4; ++i) {
      WriteRegister(rex::graphics::XE_GPU_REG_SHADER_CONSTANT_000_X + 12 + i,
                    std::bit_cast<uint32_t>(i == 3 ? 1.0f : 0.0f));
      WriteRegister(rex::graphics::XE_GPU_REG_SHADER_CONSTANT_256_X + i,
                    std::bit_cast<uint32_t>(i == 0 || i == 3 ? 1.0f : 0.0f));
    }
    WriteRegister(rex::graphics::XE_GPU_REG_RB_MODECONTROL, uint32_t(x::EdramMode::kColorDepth));
    WriteRegister(rex::graphics::XE_GPU_REG_RB_BLENDCONTROL0, 1u | (1u << 16));
    WriteRegister(rex::graphics::XE_GPU_REG_RB_COLOR_MASK, 15);
    WriteRegister(rex::graphics::XE_GPU_REG_PA_CL_VTE_CNTL, 0x43f);
    WriteRegister(rex::graphics::XE_GPU_REG_PA_SC_SCREEN_SCISSOR_BR, 1280 | (2048u << 16));
    WriteRegister(rex::graphics::XE_GPU_REG_PA_SC_WINDOW_SCISSOR_BR, 1280 | (2048u << 16));
    WriteRegister(rex::graphics::XE_GPU_REG_PA_SU_POINT_SIZE, 64 | (64u << 16));
    WriteRegister(rex::graphics::XE_GPU_REG_PA_SU_POINT_MINMAX, 64 | (64u << 16));
    for (auto reg : {rex::graphics::XE_GPU_REG_PA_CL_VPORT_XSCALE, rex::graphics::XE_GPU_REG_PA_CL_VPORT_XOFFSET,
                    rex::graphics::XE_GPU_REG_PA_CL_VPORT_YSCALE, rex::graphics::XE_GPU_REG_PA_CL_VPORT_YOFFSET})
      WriteRegister(reg, std::bit_cast<uint32_t>(8.0f));
    WriteRegister(rex::graphics::XE_GPU_REG_PA_CL_VPORT_ZSCALE, std::bit_cast<uint32_t>(1.0f));
    run("raster-and-memexport-cpu", false, false, false, {0, 1, 2}, 0, false, false);
    VerifyIntegratedRaster(*this);
    active_pixel_shader_ = nullptr;
    active_vertex_shader_ = nullptr;
  }
};

void Backend(Graphics& graphics, bool workloads, bool integration = false, bool raster_only = false) {
  Commands commands(&graphics);
  const bool initialized = commands.SetupContext();
  std::printf("LAB backend SetupContext=%d\n", initialized);
  try {
    if (initialized && integration) commands.VerifyIntegratedMemexport(raster_only);
    if (initialized && workloads) {
      commands.VerifyMemexportRejected();
      ExecuteFixtures(commands.GetVulkanDevice(), commands);
    }
  } catch (const std::exception& error) {
    std::fprintf(stderr, "LAB fixture failure before cleanup: %s\n", error.what());
    commands.ShutdownContext();
    throw;
  }
  commands.ShutdownContext();
  if (!initialized) throw std::runtime_error("Backend context failed; see SDK diagnostics");
}
}  // namespace

extern "C" __attribute__((visibility("default"))) int AsahiSdkLabRun(const char* mode) {
  try {
#ifdef REX_ASAHI_READBACK_GATE
    if (std::strcmp(mode, "readback-layout") == 0) {
      std::puts("LAB OFFLINE: resolve layout only; no Vulkan instance, device, or submission");
      VerifyReadbackLayoutCpu();
      return 0;
    }
#endif
    if (std::strcmp(mode, "offline") == 0) {
      std::puts("LAB OFFLINE: SPIR-V translation only; no Vulkan instance, device, or submission");
      TranslateFixtures(nullptr);
      return 0;
    }
    auto provider = rex::ui::vulkan::VulkanProvider::Create(true, false);
    if (!provider) { std::puts("LAB provider rejected device"); return 2; }
    auto* device = provider->vulkan_device();
    auto* instance = device->vulkan_instance();
    const auto& ifn = instance->functions();
    VkPhysicalDeviceProperties properties{};
    ifn.vkGetPhysicalDeviceProperties(device->physical_device(), &properties);
    VkPhysicalDeviceDriverProperties driver{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES};
    VkPhysicalDeviceProperties2 properties2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, &driver};
    ifn.vkGetPhysicalDeviceProperties2(device->physical_device(), &properties2);
    std::printf("LAB GPU name=%s type=%u vendor=%x driver=%s info=%s driverID=%u\n",
                properties.deviceName, properties.deviceType, properties.vendorID,
                driver.driverName, driver.driverInfo, unsigned(driver.driverID));
    if (!std::strstr(properties.deviceName, "Apple M2 Pro") ||
        properties.deviceType != VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU ||
        std::string(driver.driverName) != "Honeykrisp")
      throw std::runtime_error("Refusing unexpected physical device or driver");
    if (!ifn.vkCreateDebugUtilsMessengerEXT) throw std::runtime_error("debug utils unavailable");
    VkDebugUtilsMessengerCreateInfoEXT debug{VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT};
    debug.messageSeverity = VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT |
                            VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT;
    debug.messageType = VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT |
                        VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT |
                        VK_DEBUG_UTILS_MESSAGE_TYPE_PERFORMANCE_BIT_EXT;
    debug.pfnUserCallback = Validation;
    VkDebugUtilsMessengerEXT messenger{};
    Check(ifn.vkCreateDebugUtilsMessengerEXT(instance->instance(), &debug, nullptr, &messenger), "debug messenger");
    const auto& caps = device->properties();
    std::printf("LAB enabled vertexStores=%d fragmentStores=%d geometry=%d tessellation=%d interlock=%d/%d\n",
                caps.vertexPipelineStoresAndAtomics, caps.fragmentStoresAndAtomics,
                caps.geometryShader, caps.tessellationShader,
                caps.fragmentShaderPixelInterlock, caps.fragmentShaderSampleInterlock);
    if (std::strcmp(mode, "backend") == 0 || std::strcmp(mode, "workloads") == 0 || std::strcmp(mode, "integration") == 0 || std::strcmp(mode, "raster") == 0) {
      rex::memory::Memory memory;
      if (!memory.Initialize()) throw std::runtime_error("guest memory initialization failed");
      Graphics graphics;
      graphics.Adopt(std::move(provider), &memory);
      Backend(graphics, std::strcmp(mode, "workloads") == 0,
              std::strcmp(mode, "integration") == 0 || std::strcmp(mode, "raster") == 0,
              std::strcmp(mode, "raster") == 0);
      ifn.vkDestroyDebugUtilsMessengerEXT(instance->instance(), messenger, nullptr);
      graphics.Shutdown();
    } else if (std::strcmp(mode, "translate") == 0) {
      TranslateFixtures(device);
      ifn.vkDestroyDebugUtilsMessengerEXT(instance->instance(), messenger, nullptr);
      provider.reset();
    } else if (std::strcmp(mode, "provider") == 0) {
      ifn.vkDestroyDebugUtilsMessengerEXT(instance->instance(), messenger, nullptr);
      provider.reset();
    } else {
      throw std::runtime_error("unknown probe mode");
    }
    std::printf("LAB validation errors=%u warnings=%u\n", errors.load(), warnings.load());
    return errors ? 3 : 0;
  } catch (const std::exception& error) {
    std::fprintf(stderr, "LAB FAIL: %s\n", error.what());
    return 1;
  }
}
