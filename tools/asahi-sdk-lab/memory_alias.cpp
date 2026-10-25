#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/system/xmemory.h>

#include <cstdio>
#include <cstring>

int main(int argc, char** argv) {
  rex::LogConfig logging;
  logging.log_to_console = true;
  rex::InitLogging(logging);
  rex::cvar::Init(argc, argv);
  int result = 0;
  {
    rex::memory::Memory memory;
    if (!memory.Initialize()) return 1;
    const uint32_t address = memory.SystemHeapAlloc(32, 32, rex::memory::kSystemHeapPhysical);
    if (!address) return 2;
    const uint32_t physical = memory.GetPhysicalAddress(address);
    auto* runtime = memory.TranslateVirtual(address);
    auto* guest = rex::memory::GuestPtr(memory.virtual_membase(), address);
    auto* gpu = memory.TranslatePhysical(physical);
    const uint32_t sentinel = 0x12345678;
    std::memcpy(gpu, &sentinel, sizeof(sentinel));
    if (guest != runtime || std::memcmp(guest, gpu, sizeof(sentinel))) {
      std::fprintf(stderr, "FAIL: guest=%08X physical=%08X generated/hooks=%p runtime=%p gpu=%p\n",
                   address, physical, static_cast<void*>(guest), static_cast<void*>(runtime),
                   static_cast<void*>(gpu));
      result = 3;
    } else {
      std::memset(guest, 0, sizeof(sentinel));
      uint32_t observed;
      std::memcpy(&observed, gpu, sizeof(observed));
      result = observed ? 4 : 0;
      std::printf("%s: physical alias guest=%08X physical=%08X host_page=%zu offset=%u\n",
                  result ? "FAIL" : "PASS", address, physical, rex::memory::page_size(),
                  rex::memory::detail::PhysicalHostOffset(address));
    }
    memory.SystemHeapFree(address);
  }
  rex::ShutdownLogging();
  return result;
}
