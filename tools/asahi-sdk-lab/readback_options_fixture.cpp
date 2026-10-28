#include "baseline_history.h"
#include "memo_history.h"
#include <bit>
#include <array>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

void require(bool b) { if (!b) throw std::runtime_error("readback option equivalence failed"); }
namespace rex {
bool bit_scan_forward(uint32_t bits, uint32_t* out) {
  if (!bits) return false;
  *out = std::countr_zero(bits); return true;
}
}
namespace rex::ui::vulkan {
struct VulkanDevice {
  struct MemoryTypes { uint32_t host_visible = 0, host_cached = 0, host_coherent = 0, device_local = 0; };
};
}
#define assert_unhandled_case(x) throw std::runtime_error("unexpected memory purpose")
#include "memory_type_methods.inc"
using Baseline = rex::graphics::vulkan::baseline_history::History;
using Memo = rex::graphics::vulkan::memo_history::History;

void equivalent(const Baseline& a, const Memo& b) {
  require(a.size() == b.size());
  for (const auto& [key, entry] : a.entries_) {
    auto it = b.entries_.find(key); require(it != b.entries_.end());
    require(entry.last_frame == it->second.last_frame && entry.run == it->second.run &&
            entry.longest_run == it->second.longest_run && a.IsHot(key) == b.IsHot(key));
  }
}

void state_proof() {
  Baseline a; Memo b;
  // An insertion after a same-frame Forget MUST invalidate memoization, including the cap.
  a.Forget(0); b.Forget(0);
  for (uint64_t key = 0; key < 2500; ++key) {
    a.Resolved(key, 0); b.Resolved(key, 0);
    a.Forget(0); b.Forget(0); equivalent(a, b);
  }
  a.Forget(54001); b.Forget(54001); equivalent(a, b); require(a.size() == 0);
  for (uint64_t frame = 1; frame <= 170; ++frame) {
    for (uint64_t key = 0; key < 16; ++key) for (int repeat = 0; repeat < 6; ++repeat) {
      a.Resolved(key, frame); b.Resolved(key, frame);
      a.Forget(frame); b.Forget(frame); equivalent(a, b);
    }
  }
  require(a.IsHot(0) && b.IsHot(0));
  uint32_t random = 0x4D5309C9;
  uint64_t frame = 171;
  for (unsigned i = 0; i < 15000; ++i) {
    random = random * 1664525u + 1013904223u;
    frame += (random >> 24) % 4;
    const uint64_t key = random % 256;
    a.Resolved(key, frame); b.Resolved(key, frame);
    if (i % 7 == 0) { a.Forget(frame); b.Forget(frame); }
    equivalent(a, b);
  }
  // Frame changes, long gaps, zero and wrap must not bypass the original arithmetic.
  for (uint64_t frame2 : {UINT64_MAX, uint64_t(0), uint64_t(2), uint64_t(1000000)}) {
    a.Resolved(1, frame2); b.Resolved(1, frame2);
    a.Forget(frame2); b.Forget(frame2); equivalent(a, b);
  }
}

bool needs_invalidate(uint32_t actual_type, uint32_t coherent_types) {
  return actual_type >= 32 || !(coherent_types & (uint32_t(1) << actual_type));
}

void coherency_proof() {
  using namespace rex::ui::vulkan;
  using namespace rex::ui::vulkan::util;
  VulkanDevice::MemoryTypes types{3, 1, 2, 3};
  // Readback preference is cached, NOT necessarily coherent. Do not infer coherence from purpose.
  require(ChooseMemoryType(types, 3, MemoryPurpose::kReadback) == 0);
  require(needs_invalidate(0, types.host_coherent));
  types.host_coherent = 1;
  require(!needs_invalidate(ChooseMemoryType(types, 3, MemoryPurpose::kReadback), types.host_coherent));
  for (uint32_t type = 0; type < 32; ++type) {
    require(!needs_invalidate(type, uint32_t(1) << type));
    require(needs_invalidate(type, ~(uint32_t(1) << type)));
  }
  require(needs_invalidate(32, UINT32_MAX) && needs_invalidate(UINT32_MAX, UINT32_MAX));
  for (bool coherent : {false, true}) for (bool wait_ok : {false, true}) {
    std::array<uint8_t, 256> gpu, original{}, guarded{};
    for (size_t i = 0; i < gpu.size(); ++i) gpu[i] = uint8_t(i ^ 0x5a);
    unsigned original_waits = 1, guarded_waits = 1, original_copies = 0, guarded_copies = 0;
    unsigned invalidations = 0;
    if (wait_ok) {
      std::memcpy(original.data(), gpu.data(), gpu.size()); ++original_copies;
      if (needs_invalidate(0, coherent ? 1 : 0)) ++invalidations;
      std::memcpy(guarded.data(), gpu.data(), gpu.size()); ++guarded_copies;
    }
    require(original == guarded && original_waits == guarded_waits && original_copies == guarded_copies);
    require(invalidations == unsigned(wait_ok && !coherent));
  }
}

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  try {
    state_proof(); coherency_proof();
    Baseline stress_a; Memo stress_b;
    for (uint64_t key = 0; key < 16; ++key) {
      stress_a.Resolved(key, 599); stress_b.Resolved(key, 599);
    }
    for (unsigned repeat = 0; repeat < 6; ++repeat) for (uint64_t key = 0; key < 16; ++key) {
      stress_a.Resolved(key, 600); stress_b.Resolved(key, 600);
      stress_a.Forget(600); stress_b.Forget(600); equivalent(stress_a, stress_b);
    }
    require(stress_a.inspection_visits == 1536 && stress_b.inspection_visits == 256);
    Baseline a; Memo b;
    std::ifstream input(argv[1]); require(bool(input));
    uint64_t frame, key, resolves = 0, forgets = 0; char comma;
    while (input >> frame >> comma >> key) {
      require(comma == ',');
      a.Resolved(key, frame); b.Resolved(key, frame); ++resolves;
      if (frame % 600 == 0) { a.Forget(frame); b.Forget(frame); ++forgets; }
      equivalent(a, b);
    }
    require(input.eof() && resolves > 0);
    std::cout << "{\"historyExactStateEquivalent\":true,\"coherencyTypeSelectionModelPassed\":true,"
                 "\"prefixReplayResolves\":" << resolves << ",\"prefixReplayForgetCalls\":" << forgets
              << ",\"baselineEntryVisits\":" << a.inspection_visits
              << ",\"memoizedEntryVisits\":" << b.inspection_visits
              << ",\"syntheticDuplicateFrameBaselineVisits\":" << stress_a.inspection_visits
              << ",\"syntheticDuplicateFrameMemoVisits\":" << stress_b.inspection_visits
              << ",\"gamePerformanceMeasured\":false}\n";
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
