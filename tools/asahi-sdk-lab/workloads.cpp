#include "workloads.h"
#include <rex/graphics/format/ucode.h>
#include <rex/graphics/pipeline/shader/spirv_translator.h>
#include <rex/string/buffer.h>
#include <rex/ui/vulkan/util.h>
#include <array>
#include <bit>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <vector>

namespace {
using Device = rex::ui::vulkan::VulkanDevice;
using Translator = rex::graphics::SpirvShaderTranslator;
using Shader = rex::graphics::Shader;
namespace ucode = rex::graphics::ucode;
namespace xenos = rex::graphics::xenos;

void Check(VkResult result, const char* operation) {
  if (result != VK_SUCCESS) throw std::runtime_error(std::string(operation) + ": " + std::to_string(result));
}

std::filesystem::path Output() {
  const char* output = std::getenv("ASAHI_LAB_OUTPUT");
  if (!output) throw std::runtime_error("ASAHI_LAB_OUTPUT must be set by the bounded runner");
  return output;
}

std::vector<uint32_t> Program(bool memory_export, bool pixel = false) {
  // Hand-authored Xenos microcode, encoded from the SDK's documented ucode.h fields.
  // No extracted game shaders. alloc memory; exece {mad eA,c0,c1,c2; max eM0,c3,c3}.
  const uint64_t a = memory_export ? (uint64_t(ucode::ControlFlowOpcode::kAlloc) << 44) |
      (uint64_t(ucode::AllocType::kMemory) << 41) : 0;
  const uint64_t b = (uint64_t(ucode::ControlFlowOpcode::kExecEnd) << 44) |
                     (uint64_t(memory_export ? 2 : 1) << 12) | 1;
  std::vector<uint32_t> words{uint32_t(a), uint32_t(a >> 32) | (uint32_t(b) << 16), uint32_t(b >> 16)};
  const auto alu = [&](uint32_t destination, ucode::AluVectorOpcode opcode,
                       uint32_t src1, uint32_t src2, uint32_t src3) {
    words.push_back(destination | (1u << 15) | (15u << 16) |
                    (uint32_t(ucode::AluScalarOpcode::kRetainPrev) << 26));
    words.push_back(0); // identity swizzles, no predicates or negations
    words.push_back(src3 | (src2 << 8) | (src1 << 16) | (uint32_t(opcode) << 24));
  };
  if (memory_export) {
    alu(uint32_t(ucode::ExportRegister::kExportAddress), ucode::AluVectorOpcode::kMad, 0, 1, 2);
    alu(uint32_t(ucode::ExportRegister::kExportData0), ucode::AluVectorOpcode::kMax, 3, 3, 0);
  } else {
    alu(pixel ? 0 : uint32_t(ucode::ExportRegister::kVSPosition), ucode::AluVectorOpcode::kMax, 0, 0, 0);
  }
  return words;
}

std::vector<uint32_t> Translate(Device* device, const char* name, bool memexport,
                                 bool compute, bool reference = false, bool pixel = false,
                                 bool indexed_snapshot = false, bool raster_exports = false) {
  auto program = Program(memexport, pixel);
  if (indexed_snapshot) {
    program = IndexedMemexportFixtureBigEndian(raster_exports);
    for (auto& word : program) word = std::byteswap(word);
  }
  Shader shader(pixel ? xenos::ShaderType::kPixel : xenos::ShaderType::kVertex,
                0, program.data(), program.size(), std::endian::native);
  rex::string::StringBuffer disassembly;
  shader.AnalyzeUcode(disassembly);
  if (bool(shader.memexport_eM_written()) != memexport)
    throw std::runtime_error("fixture microcode analysis did not match its intent");
  std::ofstream(Output() / (std::string(name) + ".ucode.txt")) << shader.ucode_disassembly();
  Translator::Features features = device ? Translator::Features(device) : Translator::Features(false);
  if (!device) {
    // Offline regression profile, not a claim about a created Vulkan device.
    features.spirv_version = spv::Spv_1_5;
    features.max_storage_buffer_range = 2147483647;
    features.full_draw_index_uint32 = true;
    features.fragment_stores_and_atomics = true;
  }
  if (reference) features.vertex_pipeline_stores_and_atomics = true; // Offline reference ONLY.
  Translator translator(features, false, false, false);
  auto modification = pixel ? translator.GetDefaultPixelShaderModification(1) :
      translator.GetDefaultVertexShaderModification(1, compute ? Shader::HostVertexShaderType::kMemExportCompute :
                                                                Shader::HostVertexShaderType::kVertex);
  if (indexed_snapshot) {
    Translator::Modification snapshot(modification);
    snapshot.vertex.compute_index_snapshot = true;
    modification = snapshot.value;
  }
  auto* translation = shader.GetOrCreateTranslation(modification);
  if (!translator.TranslateAnalyzedShader(*translation)) throw std::runtime_error("fixture translation failed");
  const auto& binary = translation->translated_binary();
  std::ofstream file(Output() / (std::string(name) + ".spv"), std::ios::binary);
  file.write(reinterpret_cast<const char*>(binary.data()), binary.size());
  std::vector<uint32_t> words(binary.size() / 4);
  std::memcpy(words.data(), binary.data(), binary.size());
  unsigned atomics = 0;
  for (size_t offset = 5; offset < words.size();) {
    unsigned length = words[offset] >> 16, opcode = words[offset] & 0xffff;
    if (!length || offset + length > words.size()) throw std::runtime_error("malformed SPIR-V");
    atomics += opcode == spv::OpAtomicAnd || opcode == spv::OpAtomicOr;
    offset += length;
  }
  std::printf("LAB translate %s memexport_mask=%u bytes=%zu atomic_and_or=%u reference_only=%d\n",
              name, unsigned(shader.memexport_eM_written()), binary.size(), atomics, reference);
  if (memexport && (compute || reference) && !atomics)
    throw std::runtime_error("memexport fixture failed to exercise atomic subword paths");
  if (memexport && !compute && !reference && atomics)
    throw std::runtime_error("unsupported vertex stores unexpectedly emitted");
  return words;
}

// A small, coherent host-visible test allocation. Actual shader work is submitted
// through the SDK-created Honeykrisp device, never through an independent device.
struct Buffer {
  Device* gpu;
  VkBuffer buffer{};
  VkDeviceMemory memory{};
  void* mapped{};
  VkDeviceSize size;
  Buffer(Device* device, VkDeviceSize bytes, VkBufferUsageFlags usage) : gpu(device), size(bytes) {
    const auto& f = gpu->functions();
    VkBufferCreateInfo info{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    info.size = size; info.usage = usage; info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    Check(f.vkCreateBuffer(gpu->device(), &info, nullptr, &buffer), "buffer");
    VkMemoryRequirements requirements;
    f.vkGetBufferMemoryRequirements(gpu->device(), buffer, &requirements);
    uint32_t bits = requirements.memoryTypeBits & gpu->memory_types().host_visible &
                    gpu->memory_types().host_coherent;
    if (!bits) throw std::runtime_error("fixture needs coherent host-visible memory");
    VkMemoryAllocateInfo allocation{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    allocation.allocationSize = requirements.size;
    allocation.memoryTypeIndex = std::countr_zero(bits);
    Check(f.vkAllocateMemory(gpu->device(), &allocation, nullptr, &memory), "buffer allocation");
    Check(f.vkBindBufferMemory(gpu->device(), buffer, memory, 0), "bind buffer");
    Check(f.vkMapMemory(gpu->device(), memory, 0, VK_WHOLE_SIZE, 0, &mapped), "map buffer");
    std::memset(mapped, 0, size);
  }
  ~Buffer() {
    const auto& f = gpu->functions();
    f.vkUnmapMemory(gpu->device(), memory);
    f.vkDestroyBuffer(gpu->device(), buffer, nullptr);
    f.vkFreeMemory(gpu->device(), memory, nullptr);
  }
};

struct Fixture {
  Device* gpu;
  Buffer storage, uniforms;
  VkDescriptorSetLayout layouts[2]{};
  VkDescriptorPool descriptors{};
  VkDescriptorSet sets[2]{};
  VkPipelineLayout layout{};
  VkCommandPool pool{};
  VkCommandBuffer command{};
  VkFence fence{};
  VkQueue queue{};
  Fixture(Device* device) : gpu(device),
      storage(device, 65536, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT),
      uniforms(device, 16384, VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT) {
    const auto& f = gpu->functions();
    VkDescriptorSetLayoutBinding shared{0, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1,
      VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT | VK_SHADER_STAGE_COMPUTE_BIT, nullptr};
    VkDescriptorSetLayoutCreateInfo info{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    info.bindingCount = 1; info.pBindings = &shared;
    Check(f.vkCreateDescriptorSetLayout(gpu->device(), &info, nullptr, &layouts[0]), "shared layout");
    VkDescriptorSetLayoutBinding constants[5]{};
    for (unsigned i = 0; i < 5; ++i) constants[i] = {i, VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, 1, shared.stageFlags, nullptr};
    info.bindingCount = 5; info.pBindings = constants;
    Check(f.vkCreateDescriptorSetLayout(gpu->device(), &info, nullptr, &layouts[1]), "constant layout");
    VkDescriptorPoolSize sizes[]{{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1}, {VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, 5}};
    VkDescriptorPoolCreateInfo dp{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
    dp.maxSets = 2; dp.poolSizeCount = 2; dp.pPoolSizes = sizes;
    Check(f.vkCreateDescriptorPool(gpu->device(), &dp, nullptr, &descriptors), "descriptor pool");
    VkDescriptorSetAllocateInfo alloc{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
    alloc.descriptorPool = descriptors; alloc.descriptorSetCount = 2; alloc.pSetLayouts = layouts;
    Check(f.vkAllocateDescriptorSets(gpu->device(), &alloc, sets), "descriptors");
    VkDescriptorBufferInfo buffers[6]{{storage.buffer, 0, storage.size},
      {uniforms.buffer, 0, 4096}, {uniforms.buffer, 4096, 4096},
      {uniforms.buffer, 8192, 4096}, {uniforms.buffer, 12288, 1024}, {uniforms.buffer, 12288, 1024}};
    VkWriteDescriptorSet writes[6]{};
    for (unsigned i = 0; i < 6; ++i) {
      writes[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
      writes[i].dstSet = sets[i ? 1 : 0]; writes[i].dstBinding = i ? i - 1 : 0;
      writes[i].descriptorCount = 1;
      writes[i].descriptorType = i ? VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER : VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
      writes[i].pBufferInfo = &buffers[i];
    }
    f.vkUpdateDescriptorSets(gpu->device(), 6, writes, 0, nullptr);
    VkPipelineLayoutCreateInfo pl{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    pl.setLayoutCount = 2; pl.pSetLayouts = layouts;
    Check(f.vkCreatePipelineLayout(gpu->device(), &pl, nullptr, &layout), "pipeline layout");
    VkCommandPoolCreateInfo cp{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    cp.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    cp.queueFamilyIndex = gpu->queue_family_graphics_compute();
    Check(f.vkCreateCommandPool(gpu->device(), &cp, nullptr, &pool), "command pool");
    VkCommandBufferAllocateInfo cb{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    cb.commandPool = pool; cb.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY; cb.commandBufferCount = 1;
    Check(f.vkAllocateCommandBuffers(gpu->device(), &cb, &command), "command buffer");
    VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    Check(f.vkCreateFence(gpu->device(), &fi, nullptr, &fence), "fence");
    f.vkGetDeviceQueue(gpu->device(), cp.queueFamilyIndex, 0, &queue);
  }
  ~Fixture() {
    const auto& f = gpu->functions();
    f.vkDestroyFence(gpu->device(), fence, nullptr);
    f.vkDestroyCommandPool(gpu->device(), pool, nullptr);
    f.vkDestroyPipelineLayout(gpu->device(), layout, nullptr);
    f.vkDestroyDescriptorPool(gpu->device(), descriptors, nullptr);
    for (auto set : layouts) f.vkDestroyDescriptorSetLayout(gpu->device(), set, nullptr);
  }
  void Begin() {
    const auto& f = gpu->functions();
    Check(f.vkResetFences(gpu->device(), 1, &fence), "reset fence");
    Check(f.vkResetCommandPool(gpu->device(), pool, 0), "reset commands");
    VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    Check(f.vkBeginCommandBuffer(command, &begin), "begin");
    VkMemoryBarrier host{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
    host.srcAccessMask = VK_ACCESS_HOST_WRITE_BIT;
    host.dstAccessMask = VK_ACCESS_UNIFORM_READ_BIT | VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT;
    f.vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_HOST_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
                           0, 1, &host, 0, nullptr, 0, nullptr);
  }
  void Finish(VkPipelineStageFlags stage, VkAccessFlags access) {
    const auto& f = gpu->functions();
    VkMemoryBarrier host{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
    host.srcAccessMask = access; host.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
    f.vkCmdPipelineBarrier(command, stage, VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &host, 0, nullptr, 0, nullptr);
    Check(f.vkEndCommandBuffer(command), "end");
    VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    submit.commandBufferCount = 1; submit.pCommandBuffers = &command;
    Check(f.vkQueueSubmit(queue, 1, &submit, fence), "submit");
    const VkResult wait = f.vkWaitForFences(gpu->device(), 1, &fence, VK_TRUE, 5000000000ull);
    if (wait != VK_SUCCESS) {
      // Do not destroy resources still in flight after a timeout/device loss.
      std::fprintf(stderr, "LAB GPU STOP: fence wait returned %d\n", int(wait));
      std::fflush(nullptr);
      std::_Exit(124);
    }
  }
};

void ComputeMemexport(Fixture& test) {
  auto code = Translate(test.gpu, "compute-memexport-executed", true, true);
  VkShaderModule module = rex::ui::vulkan::util::CreateShaderModule(test.gpu, code.data(), code.size() * 4);
  if (!module) throw std::runtime_error("compute shader module");
  VkPipeline pipeline = rex::ui::vulkan::util::CreateComputePipeline(test.gpu, test.layout, module);
  if (!pipeline) throw std::runtime_error("compute memexport pipeline");
  const auto& f = test.gpu->functions();
  const auto run = [&](const char* name, xenos::ColorFormat format, uint32_t index,
                       uint32_t stream_count, uint32_t invocation_count,
                       std::array<float, 4> value, std::vector<uint8_t> expected_bytes, size_t offset) {
    std::memset(test.uniforms.mapped, 0, test.uniforms.size);
    std::memset(test.storage.mapped, 0xa5, test.storage.size);
    auto* system = static_cast<Translator::SystemConstants*>(test.uniforms.mapped);
    system->compute_memexport_vertex_count = invocation_count;
    system->vertex_index_reset = 0xffffffff;
    auto* constants = reinterpret_cast<float*>(static_cast<uint8_t*>(test.uniforms.mapped) + 4096);
    xenos::xe_gpu_memexport_stream_t stream{};
    stream.base_address = 64; stream.const_0x1 = 1;
    stream.const_0x4b000000 = 0x4b000000 | index;
    stream.format = format; stream.num_format = xenos::SurfaceNumberFormat::kUnsignedInteger;
    stream.const_0x4b0 = 0x4b0; stream.index_count = stream_count; stream.const_0x96 = 0x96;
    std::memcpy(constants + 8, &stream, sizeof(stream));
    std::memcpy(constants + 12, value.data(), sizeof(value));
    std::vector<uint8_t> expected(test.storage.size, 0xa5);
    std::copy(expected_bytes.begin(), expected_bytes.end(), expected.begin() + offset);
    test.Begin();
    f.vkCmdBindPipeline(test.command, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
    f.vkCmdBindDescriptorSets(test.command, VK_PIPELINE_BIND_POINT_COMPUTE, test.layout, 0, 2, test.sets, 0, nullptr);
    f.vkCmdDispatch(test.command, 2, 1, 1);
    test.Finish(VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_WRITE_BIT);
    if (std::memcmp(test.storage.mapped, expected.data(), expected.size())) {
      std::fprintf(stderr, "LAB READBACK FAIL %s (GPU fence completed)\n", name);
      std::ofstream dump(Output() / (std::string(name) + ".actual.bin"), std::ios::binary);
      dump.write(static_cast<const char*>(test.storage.mapped), test.storage.size);
      throw std::runtime_error(std::string("compute readback mismatch: ") + name);
    }
    std::printf("LAB EXEC PASS %s: full 65536-byte readback and neighboring sentinel bytes match\n", name);
  };
  run("memexport-byte-atomic", xenos::ColorFormat::k_8, 1, 2, 1,
      {123, 0, 0, 0}, {123}, 257);
  run("memexport-halfword-atomic", xenos::ColorFormat::k_16, 1, 2, 1,
      {4660, 0, 0, 0}, {0x34, 0x12}, 258);
  std::array<float, 4> floats{0.25f, 0.5f, 0.75f, 1.0f};
  std::vector<uint8_t> bytes(sizeof(floats)); std::memcpy(bytes.data(), floats.data(), bytes.size());
  run("memexport-float128", xenos::ColorFormat::k_32_32_32_32_FLOAT, 0, 1, 1, floats, bytes, 256);
  run("memexport-stream-out-of-range", xenos::ColorFormat::k_8, 1, 1, 1, {123, 0, 0, 0}, {}, 0);
  run("memexport-zero-invocations", xenos::ColorFormat::k_8, 0, 1, 0, {123, 0, 0, 0}, {}, 0);
  f.vkDestroyPipeline(test.gpu->device(), pipeline, nullptr);
  f.vkDestroyShaderModule(test.gpu->device(), module, nullptr);
}

void Render(Fixture& test, rex::graphics::vulkan::VulkanCommandProcessor& commands) {
  const auto& f = test.gpu->functions();
  VkImage image{}; VkDeviceMemory memory{};
  VkImageCreateInfo info{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
  info.imageType = VK_IMAGE_TYPE_2D; info.format = VK_FORMAT_R8G8B8A8_UNORM;
  info.extent = {16, 16, 1}; info.mipLevels = 1; info.arrayLayers = 1;
  info.samples = VK_SAMPLE_COUNT_1_BIT; info.tiling = VK_IMAGE_TILING_OPTIMAL;
  info.usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
  if (!rex::ui::vulkan::util::CreateDedicatedAllocationImage(test.gpu, info,
      rex::ui::vulkan::util::MemoryPurpose::kDeviceLocal, image, memory)) throw std::runtime_error("render image");
  VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
  vi.image = image; vi.viewType = VK_IMAGE_VIEW_TYPE_2D; vi.format = info.format;
  vi.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
  VkImageView view{}; Check(f.vkCreateImageView(test.gpu->device(), &vi, nullptr, &view), "image view");
  VkAttachmentDescription attachment{};
  attachment.format = info.format; attachment.samples = info.samples;
  attachment.loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR; attachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
  attachment.stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
  attachment.stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
  attachment.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED; attachment.finalLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
  VkAttachmentReference color{0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
  VkSubpassDescription sub{}; sub.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS;
  sub.colorAttachmentCount = 1; sub.pColorAttachments = &color;
  VkSubpassDependency dependency{0, VK_SUBPASS_EXTERNAL, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
      VK_PIPELINE_STAGE_TRANSFER_BIT, VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT, 0};
  VkRenderPassCreateInfo rp{VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO};
  rp.attachmentCount = 1; rp.pAttachments = &attachment; rp.subpassCount = 1; rp.pSubpasses = &sub;
  rp.dependencyCount = 1; rp.pDependencies = &dependency;
  VkRenderPass pass{}; Check(f.vkCreateRenderPass(test.gpu->device(), &rp, nullptr, &pass), "render pass");
  VkFramebufferCreateInfo fb{VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO};
  fb.renderPass = pass; fb.attachmentCount = 1; fb.pAttachments = &view; fb.width = fb.height = 16; fb.layers = 1;
  VkFramebuffer frame{}; Check(f.vkCreateFramebuffer(test.gpu->device(), &fb, nullptr, &frame), "framebuffer");
  std::vector<uint32_t> vertex; std::string error;
  if (!commands.CompileGlslToSpirv(VK_SHADER_STAGE_VERTEX_BIT, R"(#version 450
void main() {
  vec2 p[3] = vec2[3](vec2(-1,-1), vec2(3,-1), vec2(-1,3));
  gl_Position = vec4(p[gl_VertexIndex], 0, 1);
})", vertex, error)) throw std::runtime_error(error);
  auto fragment = Translate(test.gpu, "fragment-basic-executed", false, false, false, true);
  VkShaderModule modules[]{rex::ui::vulkan::util::CreateShaderModule(test.gpu, vertex.data(), vertex.size() * 4),
      rex::ui::vulkan::util::CreateShaderModule(test.gpu, fragment.data(), fragment.size() * 4)};
  VkPipelineShaderStageCreateInfo stages[2]{};
  for (unsigned i = 0; i < 2; ++i) {
    if (!modules[i]) throw std::runtime_error("render shader module");
    stages[i].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    stages[i].stage = i ? VK_SHADER_STAGE_FRAGMENT_BIT : VK_SHADER_STAGE_VERTEX_BIT;
    stages[i].module = modules[i]; stages[i].pName = "main";
  }
  VkPipelineVertexInputStateCreateInfo vertex_input{VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO};
  VkPipelineInputAssemblyStateCreateInfo assembly{VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO};
  assembly.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
  VkViewport viewport{0, 0, 16, 16, 0, 1}; VkRect2D scissor{{0, 0}, {16, 16}};
  VkPipelineViewportStateCreateInfo viewport_info{VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO};
  viewport_info.viewportCount = viewport_info.scissorCount = 1;
  viewport_info.pViewports = &viewport; viewport_info.pScissors = &scissor;
  VkPipelineRasterizationStateCreateInfo raster{VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO};
  raster.polygonMode = VK_POLYGON_MODE_FILL; raster.lineWidth = 1;
  VkPipelineMultisampleStateCreateInfo multisample{VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO};
  multisample.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;
  VkPipelineColorBlendAttachmentState blend{}; blend.colorWriteMask = 15;
  VkPipelineColorBlendStateCreateInfo blend_info{VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO};
  blend_info.attachmentCount = 1; blend_info.pAttachments = &blend;
  VkGraphicsPipelineCreateInfo pipeline_info{VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO};
  pipeline_info.stageCount = 2; pipeline_info.pStages = stages; pipeline_info.pVertexInputState = &vertex_input;
  pipeline_info.pInputAssemblyState = &assembly; pipeline_info.pViewportState = &viewport_info;
  pipeline_info.pRasterizationState = &raster; pipeline_info.pMultisampleState = &multisample;
  pipeline_info.pColorBlendState = &blend_info; pipeline_info.layout = test.layout; pipeline_info.renderPass = pass;
  VkPipeline pipeline{};
  Check(f.vkCreateGraphicsPipelines(test.gpu->device(), VK_NULL_HANDLE, 1, &pipeline_info, nullptr, &pipeline), "graphics pipeline");
  std::memset(test.uniforms.mapped, 0, test.uniforms.size);
  auto* system = static_cast<Translator::SystemConstants*>(test.uniforms.mapped);
  system->flags = Translator::kSysFlag_AlphaPassIfLess | Translator::kSysFlag_AlphaPassIfEqual |
                  Translator::kSysFlag_AlphaPassIfGreater;
  for (auto& bias : system->color_exp_bias) bias = 1.0f;
  float red[]{1, 0, 0, 1};
  std::memcpy(static_cast<uint8_t*>(test.uniforms.mapped) + 8192, red, sizeof(red));
  test.Begin();
  VkClearValue clear{};
  VkRenderPassBeginInfo begin{VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO};
  begin.renderPass = pass; begin.framebuffer = frame; begin.renderArea = scissor;
  begin.clearValueCount = 1; begin.pClearValues = &clear;
  f.vkCmdBeginRenderPass(test.command, &begin, VK_SUBPASS_CONTENTS_INLINE);
  f.vkCmdBindPipeline(test.command, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline);
  f.vkCmdBindDescriptorSets(test.command, VK_PIPELINE_BIND_POINT_GRAPHICS, test.layout, 0, 2, test.sets, 0, nullptr);
  f.vkCmdDraw(test.command, 3, 1, 0, 0);
  f.vkCmdEndRenderPass(test.command);
  VkBufferImageCopy copy{}; copy.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}; copy.imageExtent = {16, 16, 1};
  f.vkCmdCopyImageToBuffer(test.command, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, test.storage.buffer, 1, &copy);
  test.Finish(VK_PIPELINE_STAGE_TRANSFER_BIT, VK_ACCESS_TRANSFER_WRITE_BIT);
  auto* bytes = static_cast<uint8_t*>(test.storage.mapped);
  for (unsigned i = 0; i < 16 * 16; ++i) {
    if (bytes[i * 4] != 255 || bytes[i * 4 + 1] || bytes[i * 4 + 2] || bytes[i * 4 + 3] != 255)
      throw std::runtime_error("translated fragment shader image readback mismatch");
  }
  std::ofstream ppm(Output() / "translated-fragment.ppm", std::ios::binary);
  ppm << "P6\n16 16\n255\n";
  for (unsigned i = 0; i < 256; ++i) ppm.write(reinterpret_cast<char*>(bytes + i * 4), 3);
  std::puts("LAB EXEC PASS translated-fragment: graphics pipeline, draw, transfer, 256 red RGBA pixels verified");
  f.vkDestroyPipeline(test.gpu->device(), pipeline, nullptr);
  for (auto module : modules) f.vkDestroyShaderModule(test.gpu->device(), module, nullptr);
  f.vkDestroyFramebuffer(test.gpu->device(), frame, nullptr);
  f.vkDestroyRenderPass(test.gpu->device(), pass, nullptr);
  f.vkDestroyImageView(test.gpu->device(), view, nullptr);
  f.vkDestroyImage(test.gpu->device(), image, nullptr);
  f.vkFreeMemory(test.gpu->device(), memory, nullptr);
}
} // namespace

void TranslateFixtures(Device* device) {
  Translate(device, "vertex-basic", false, false);
  Translate(device, "fragment-basic", false, false, false, true);
  Translate(device, "vertex-memexport-suppressed", true, false);
  Translate(device, "vertex-memexport-reference-NOT-EXECUTED", true, false, true);
  Translate(device, "compute-memexport", true, true);
  Translate(device, "compute-memexport-index-snapshot", true, true, false, false, true);
  Translate(device, "compute-memexport-with-raster-exports", true, true, false, false, true, true);
}

std::vector<uint32_t> MemexportFixtureBigEndian() {
  auto words = Program(true);
  for (auto& word : words) word = std::byteswap(word);
  return words;
}

std::vector<uint32_t> IndexedMemexportFixtureBigEndian(bool raster) {
  auto words = Program(true);
  // eA = r0.xxxx * c1 + c2; eM0 = max(r0.xxxx, r0.xxxx).
  // r0.x is the guest vertex index, including endian, mask, clamp and base offset.
  words[4] = 0x6cu << 16;
  words[5] |= 1u << 31;
  words[7] = (0x6cu << 16) | (0x6cu << 8);
  words[8] = (uint32_t(ucode::AluVectorOpcode::kMax) << 24) | (1u << 31) | (1u << 30);
  if (raster) {
    words[1] += 1u << 28;
    words.push_back(uint32_t(ucode::ExportRegister::kVSPosition) | (1u << 15) | (15u << 16) |
                    (uint32_t(ucode::AluScalarOpcode::kRetainPrev) << 26));
    words.push_back(0);
    words.push_back((3u << 16) | (3u << 8) | (uint32_t(ucode::AluVectorOpcode::kMax) << 24));
  }
  for (auto& word : words) word = std::byteswap(word);
  return words;
}

std::vector<uint32_t> PixelFixtureBigEndian() {
  auto words = Program(false, true);
  for (auto& word : words) word = std::byteswap(word);
  return words;
}

void ExecuteFixtures(Device* device, rex::graphics::vulkan::VulkanCommandProcessor& commands) {
  Fixture test(device);
  ComputeMemexport(test);
  Render(test, commands);
}
