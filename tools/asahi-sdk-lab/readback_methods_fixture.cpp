// Extracted unscaled resolve/slot/eviction fragments, CPU mocks only. No Vulkan linkage.
#include "asahi_readback_profile.h"
#include <array>
#include <cstring>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <unordered_map>
#include <vector>

namespace readback_profile = rex::graphics::vulkan::readback_profile;
using VkBuffer = uint64_t;
using VkDeviceMemory = uint64_t;
using VkDevice = uint64_t;
constexpr uint64_t VK_NULL_HANDLE = 0, VK_WHOLE_SIZE = UINT64_MAX;
constexpr int VK_SUCCESS = 0, VK_BUFFER_USAGE_TRANSFER_DST_BIT = 1;
constexpr int VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE = 1, VK_PIPELINE_STAGE_TRANSFER_BIT = 2;
constexpr int VK_PIPELINE_STAGE_HOST_BIT = 4, VK_ACCESS_TRANSFER_WRITE_BIT = 8, VK_ACCESS_HOST_READ_BIT = 16;
struct VkMappedMemoryRange { int sType{}; VkDeviceMemory memory{}; uint64_t offset{}, size{}; };
struct VkBufferCopy { uint64_t srcOffset{}, dstOffset{}, size{}; };
enum class ReadbackResolveMode { kDisabled, kFast, kSome, kFull };
namespace xenos { enum class ColorFormat { k_8_8_8_8, kOther }; }
namespace reg {
struct RB_COPY_DEST_PITCH { uint32_t copy_dest_pitch = 1280, copy_dest_height = 720; };
struct RB_COPY_DEST_INFO { xenos::ColorFormat copy_dest_format = xenos::ColorFormat::k_8_8_8_8; };
struct RB_COPY_CONTROL { uint32_t copy_src_select = 0; };
}
struct Registers {
  reg::RB_COPY_DEST_PITCH pitch;
  reg::RB_COPY_DEST_INFO info;
  reg::RB_COPY_CONTROL control;
  template <typename T> T Get() const {
    if constexpr (std::is_same_v<T, reg::RB_COPY_DEST_PITCH>) return pitch;
    else if constexpr (std::is_same_v<T, reg::RB_COPY_DEST_INFO>) return info;
    else return control;
  }
};
namespace fh1_frame_cost {
struct Frame { double readback_ms = 0, readback_wait_ms = 0; uint64_t readbacks = 0, readback_bytes = 0, readbacks_skipped = 0; };
inline Frame current;
Frame& Current() { return current; }
struct Timer { explicit Timer(double&) {} };
}
namespace fh1_readback_history {
struct History {
  bool hot = false;
  bool IsHot(uint64_t) const { return hot; }
  void Resolved(uint64_t, uint64_t) {}
  void Forget(uint64_t) {}
};
}
uint64_t fake = 100;
uint64_t fake_clock() { return fake; }
struct Device {
  using Functions = Device;
  mutable uint64_t next = 10;
  mutable std::map<uint64_t, std::vector<uint8_t>> data;
  mutable std::vector<std::string> operations;
  bool allocation_fail = false, map_fail = false;
  VkDevice device() const { return 1; }
  const Functions& functions() const { return *this; }
  int vkMapMemory(VkDevice, VkDeviceMemory m, uint64_t, uint64_t, uint32_t, void** pointer) const {
    operations.push_back("map");
    if (map_fail) return -1;
    *pointer = data.at(m).data(); return VK_SUCCESS;
  }
  void vkUnmapMemory(VkDevice, VkDeviceMemory) const { operations.push_back("unmap"); }
  void vkDestroyBuffer(VkDevice, VkBuffer, void*) const { operations.push_back("destroy"); }
  void vkFreeMemory(VkDevice, VkDeviceMemory, void*) const { operations.push_back("free"); }
  void vkInvalidateMappedMemoryRanges(VkDevice, unsigned, const VkMappedMemoryRange*) const { operations.push_back("invalidate"); }
};
namespace ui::vulkan {
using VulkanDevice = Device;
namespace util {
enum class MemoryPurpose { kReadback };
bool CreateDedicatedAllocationBuffer(const Device* d, uint32_t size, int, MemoryPurpose, VkBuffer& b, VkDeviceMemory& m) {
  d->operations.push_back("allocate:" + std::to_string(size));
  if (d->allocation_fail) return false;
  b = m = d->next++; d->data[m] = std::vector<uint8_t>(size, 0x5a); return true;
}
}
}
struct Shared {
  enum class Usage { kRead };
  void Use(Usage) {}
  VkBuffer buffer() const { return 1; }
};
using VulkanSharedMemory = Shared;
struct Texture { bool IsDrawResolutionScaled() const { return false; } };
struct Memory {
  std::array<uint8_t, 1024> bytes{};
  bool missing = false;
  uint8_t* TranslatePhysical(uint32_t) { return missing ? nullptr : bytes.data(); }
};
struct Deferred {
  Device* d;
  void CmdVkCopyBuffer(VkBuffer, VkBuffer, unsigned, const VkBufferCopy*) { d->operations.push_back("gpu-copy"); }
};
struct Base {
  struct ReadbackBuffer {
    VkBuffer buffers[2]{}; VkDeviceMemory memories[2]{}; void* mapped_data[2]{};
    uint32_t sizes[2]{}; uint64_t submission_written[2]{}; uint32_t written_size[2]{};
    uint32_t current_index = 0; uint64_t last_used_frame = 0; bool resolve_cpu_copy_valid = false;
  };
  static constexpr size_t kMaxReadbackBuffers = 256;
  static constexpr uint64_t kReadbackBufferEvictionAgeFrames = 60;
  Device d;
  Registers regs;
  Shared shared;
  Texture textures;
  Memory memory;
  Registers* register_file_ = &regs;
  Shared* shared_memory_ = &shared;
  Texture* texture_cache_ = &textures;
  Memory* memory_ = &memory;
  Deferred deferred_command_buffer_{&d};
  fh1_readback_history::History history;
  std::unordered_map<uint64_t, ReadbackBuffer> readback_buffers_, memexport_readback_buffers_;
  std::unique_ptr<readback_profile::Recorder> readback_profile_;
  uint64_t frame_current_ = 100;
  bool skip_hot = true, wait_ok = true;
  ReadbackResolveMode mode = ReadbackResolveMode::kSome;
  const Device* GetVulkanDevice() const { return &d; }
  uint64_t GetCurrentSubmission() const { return 10; }
  uint64_t GetReadbackResolveKey(uint32_t, uint32_t) const { return 42; }
  static uint32_t AlignReadbackBufferSize(uint32_t size) { return (size + 255) & ~255u; }
  bool AwaitAllQueueOperationsCompletion() { d.operations.push_back("wait"); fake += 1000; return wait_ok; }
  void SubmitBarriers(bool) { d.operations.push_back("submit-barriers"); }
  void PushBufferMemoryBarrier(VkBuffer, uint64_t, uint32_t, int, int, int, int) { d.operations.push_back("host-barrier"); }
  fh1_readback_history::History& GetReadbackHistory() { return history; }
  void Seed(uint32_t size) {
    auto& r = readback_buffers_[42];
    r.buffers[0] = r.memories[0] = d.next++;
    d.data[r.memories[0]] = std::vector<uint8_t>(size, 0x5a);
    r.mapped_data[0] = d.data[r.memories[0]].data(); r.sizes[0] = size;
  }
  std::vector<uint64_t> State() const {
    std::vector<uint64_t> result;
    std::map<uint64_t, ReadbackBuffer> ordered(readback_buffers_.begin(), readback_buffers_.end());
    for (auto& [key, r] : ordered) result.insert(result.end(), {key, r.current_index, r.sizes[0], r.sizes[1],
        r.buffers[0], r.buffers[1], r.memories[0], r.memories[1], r.last_used_frame, uint64_t(r.resolve_cpu_copy_valid)});
    return result;
  }
};
#define REXCVAR_GET(name) skip_hot
#define REXGPU_ERROR(...) ((void)0)
struct Reference : Base {
  bool Run(uint32_t written_address, uint32_t written_length);
  void EvictOldReadbackBuffers(std::unordered_map<uint64_t, ReadbackBuffer>& buffer_map);
};
struct Instrumented : Base {
  bool Run(uint32_t written_address, uint32_t written_length);
  void EvictOldReadbackBuffers(std::unordered_map<uint64_t, ReadbackBuffer>& buffer_map);
};
#include "readback_methods.inc"
void require(bool b) { if (!b) throw std::runtime_error("readback renderer forwarding differs"); }
template <typename T> void configure(T& h, const std::string& name) {
  if (name == "thumbnail") h.regs.pitch = {768, 288};
  if (name == "full" || name == "growth" || name == "resize-failure" || name == "full-failure") h.mode = ReadbackResolveMode::kFull;
  if (name == "hot") h.history.hot = true;
  if (name == "growth" || name == "resize-failure") h.Seed(128);
  if (name == "allocation-failure") h.d.allocation_fail = true;
  if (name == "map-failure") h.d.map_fail = true;
  if (name == "resize-failure" || name == "full-failure" || name == "miss-failure") h.wait_ok = false;
  if (name == "missing-destination") h.memory.missing = true;
  if (name == "fast-ready") {
    h.mode = ReadbackResolveMode::kFast;
    h.Seed(256);
    auto& r = h.readback_buffers_[42];
    r.buffers[1] = r.memories[1] = h.d.next++;
    h.d.data[r.memories[1]] = std::vector<uint8_t>(256, 0x5a);
    r.mapped_data[1] = h.d.data[r.memories[1]].data(); r.sizes[1] = 256;
  }
  if (name == "evict-count") {
    for (uint64_t key = 0; key < 257; ++key) h.readback_buffers_[key].last_used_frame = 100;
  }
}
int main(int argc, char** argv) {
  if (argc != 2) return 2;
  try {
    const std::string root = argv[1];
    for (bool enabled : {false, true}) {
      for (const std::string name : {"some", "hot", "full", "thumbnail", "growth", "allocation-failure",
                                     "map-failure", "resize-failure", "full-failure", "miss-failure", "missing-destination",
                                     "fast-ready", "evict-count"}) {
        Reference reference; Instrumented instrumented;
        configure(reference, name); configure(instrumented, name);
        fake = 100;
        if (enabled) instrumented.readback_profile_ = readback_profile::Recorder::Create(
            (root + "/" + name + ".csv").c_str(), {0, 1, 512}, fake_clock);
        for (int i = 0; i < 2; ++i) {
          fh1_frame_cost::current = {};
          const bool a = reference.Run(0, 256); const auto counters = fh1_frame_cost::current;
          fh1_frame_cost::current = {};
          const bool b = instrumented.Run(0, 256);
          require(a == b && reference.d.operations == instrumented.d.operations);
          require(reference.State() == instrumented.State() && reference.memory.bytes == instrumented.memory.bytes);
          require(counters.readbacks == fh1_frame_cost::current.readbacks &&
                  counters.readbacks_skipped == fh1_frame_cost::current.readbacks_skipped &&
                  counters.readback_bytes == fh1_frame_cost::current.readback_bytes);
        }
        reference.frame_current_ = instrumented.frame_current_ = 200;
        reference.EvictOldReadbackBuffers(reference.readback_buffers_);
        instrumented.EvictOldReadbackBuffers(instrumented.readback_buffers_);
        require(reference.d.operations == instrumented.d.operations && reference.State() == instrumented.State());
        if (enabled) { fake = 1000001000; require(instrumented.readback_profile_->FinalizeIfReady()); }
      }
    }
    std::cout << "PASS: extracted unscaled readback fragments preserve operations, bytes, indices, counters and failures\n";
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
