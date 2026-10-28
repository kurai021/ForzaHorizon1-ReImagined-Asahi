// CPU-only extracted-helper equivalence and synthetic code-generation probe.
#include "physical_offset_methods.h"

#include <array>
#include <barrier>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string_view>
#include <thread>
#include <vector>

extern "C" u32 baseline_other(u32);
extern "C" u32 split_other(u32);

// Stable names for assembly inspection; no forced inlining of the SDK methods.
extern "C" [[gnu::noinline]] u32 baseline_entry(u32 a) {
  return baseline::PhysicalHostOffset(a);
}
extern "C" [[gnu::noinline]] u32 split_entry(u32 a) {
  return split::PhysicalHostOffset(a);
}

void require(bool condition) {
  if (!condition) throw std::runtime_error("physical-offset CPU equivalence failed");
}

u32 expected(u32 address) {
#if REX_PLATFORM_WIN32 || (REX_PLATFORM_MAC && defined(__aarch64__))
  return address >= 0xE0000000u ? 0x1000u : 0;
#elif REX_PLATFORM_LINUX
  return address >= 0xE0000000u && configured_granularity > 4096 ? 4096 : 0;
#else
  return 0;
#endif
}

void check_address(u32 a) {
  const u32 offset = expected(a);
  require(baseline::PhysicalHostOffset(a) == offset);
  require(split::PhysicalHostOffset(a) == offset);
  require(baseline_entry(a) == offset && split_entry(a) == offset);
  require(baseline_other(a) == offset && split_other(a) == offset);
  // Integer arithmetic only; do not dereference imaginary guest pointers.
  require(uint64_t(a) + baseline_other(a) == uint64_t(a) + split_other(a));
}

void correctness() {
  for (u32 a : {0u, 1u, 0x7FFFFFFFu, 0xDFFFFFFEu, 0xDFFFFFFFu}) check_address(a);
  require(baseline::queries == 0 && split::queries == 0);
  // The first high-address access races in different translation units.
  std::barrier start(8);
  std::atomic<unsigned> errors{0};
  std::vector<std::thread> workers;
  for (unsigned i = 0; i < 8; ++i) {
    workers.emplace_back([&, i] {
      start.arrive_and_wait();
      const auto b = i & 1 ? baseline_other(0xE0000000u) : baseline_entry(0xE0000000u);
      const auto s = i & 1 ? split_entry(0xE0000000u) : split_other(0xE0000000u);
      if (b != expected(0xE0000000u) || s != b) ++errors;
    });
  }
  for (auto& worker : workers) worker.join();
  require(errors == 0);
  for (u32 a : {0xE0000000u, 0xE0000001u, 0xEFFFFFFFu, 0xF0000000u, 0xFFFFFFFFu}) check_address(a);
  // Sweep every upper 16-bit prefix and threshold neighbors, then deterministic random addresses.
  for (uint64_t prefix = 0; prefix < 65536; ++prefix) {
    check_address(u32(prefix << 16));
    check_address(u32((prefix << 16) | 0xFFFFu));
  }
  u32 random = 0x4D5309C9u;
  for (unsigned i = 0; i < 100000; ++i) {
    random = random * 1664525u + 1013904223u;
    check_address(random);
  }
#if REX_PLATFORM_LINUX && !REX_PLATFORM_WIN32 && !REX_PLATFORM_MAC
  require(baseline::queries == 1 && split::queries == 1);
#else
  require(baseline::queries == 0 && split::queries == 0);
#endif
}

template <bool Split, bool Entry>
[[gnu::noinline]] uint64_t loop(const std::vector<u32>& addresses) {
  uint64_t sum = 0;
  for (unsigned repeat = 0; repeat < 16; ++repeat) {
    for (u32 a : addresses) {
      if constexpr (Entry) sum += Split ? split_entry(a) : baseline_entry(a);
      else if constexpr (Split) sum += split::PhysicalHostOffset(a);
      else sum += baseline::PhysicalHostOffset(a);
    }
  }
  return sum;
}

void benchmark() {
  // Warm both statics before measuring; initialization has its separate correctness test.
  check_address(0xE0000000u);
  std::cout << "{\"pageSize\":" << configured_granularity << ",\"rounds\":5,\"samples\":[";
  bool first = true;
  for (unsigned percent : {0u, 1u, 50u, 100u}) {
    std::vector<u32> addresses(65536);
    u32 random = 0x4D5309C9u;
    uint64_t expected_sum = 0;
    for (auto& address : addresses) {
      random = random * 1664525u + 1013904223u;
      const bool high = random % 100 < percent;
      address = high ? 0xE0000000u | (random & 0x1FFFFFFFu) : random & 0x7FFFFFFFu;
      expected_sum += expected(address) * uint64_t(16);
    }
    for (bool entry : {false, true}) {
      for (unsigned round = 0; round < 5; ++round) {
        for (unsigned side = 0; side < 2; ++side) {
          const bool split = bool(side ^ (round & 1));
          const auto start = std::chrono::steady_clock::now();
          uint64_t sum;
          if (entry) sum = split ? loop<true, true>(addresses) : loop<false, true>(addresses);
          else sum = split ? loop<true, false>(addresses) : loop<false, false>(addresses);
          const auto end = std::chrono::steady_clock::now();
          require(sum == expected_sum);
          const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count();
          if (!first) std::cout << ',';
          first = false;
          std::cout << "{\"highAddressPercentRequested\":" << percent
                    << ",\"entryWrapper\":" << (entry ? "true" : "false")
                    << ",\"round\":" << round << ",\"split\":" << (split ? "true" : "false")
                    << ",\"accesses\":" << addresses.size() * 16
                    << ",\"wallNs\":" << ns << ",\"checksum\":" << sum << '}';
        }
      }
    }
  }
  std::cout << "]}\n";
}

int main(int argc, char** argv) {
  try {
    if (argc != 3) return 2;
    const unsigned long page = std::strtoul(argv[2], nullptr, 10);
    if (page != 4096 && page != 16384 && page != 65536) return 2;
    configured_granularity = u32(page);
    const std::string_view mode(argv[1]);
    if (mode == "check") {
      correctness();
      std::cout << "{\"equivalent\":true,\"concurrentFirstUse\":true,\"crossTranslationUnit\":true,"
                   "\"baselineQueries\":" << baseline::queries << ",\"splitQueries\":" << split::queries << "}\n";
    } else if (mode == "bench") benchmark();
    else return 2;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
