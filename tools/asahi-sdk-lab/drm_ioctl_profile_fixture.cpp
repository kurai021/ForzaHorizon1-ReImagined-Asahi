// CPU-only ABI fixtures. The fake library never calls ioctl or opens a device.
#include <drm/asahi_drm.h>
#include <atomic>
#include <cassert>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <pthread.h>
#include <sys/wait.h>
#include <unistd.h>
#include <thread>
#include <vector>

struct Payload { unsigned magic = 0x1234, calls = 0; int positive = 0; };
constexpr unsigned long kOtherRequest = 0x12345678;
#ifdef ASAHI_FAKE_LIBDRM
static std::atomic<unsigned> calls{0};
extern "C" int drmIoctl(int fd, unsigned long request, void* argument) {
  ++calls;
  if (!argument) { errno = EFAULT; return -1; }
  auto* payload = static_cast<Payload*>(argument);
  assert(fd == 77 && payload->magic == 0x1234);
  ++payload->calls;
  if (request == kOtherRequest) { errno = EAGAIN; return -1; }
  assert(request == DRM_IOCTL_ASAHI_SUBMIT);
  return payload->positive ? 7 : 0;  // Success preserves incoming errno, including stale errno.
}
extern "C" unsigned fake_calls() { return calls.load(); }
#else
using Function = int (*)(int, unsigned long, void*);
void Verify(Function function, unsigned count, bool positive = false) {
  pthread_setname_np(pthread_self(), "Vulkan Submissi");
  Payload payload;
  payload.positive = positive;
  for (unsigned index = 0; index < count; ++index) {
    errno = EDOM;
    assert(function(77, DRM_IOCTL_ASAHI_SUBMIT, &payload) == (positive ? 7 : 0));
    assert(errno == EDOM);
    errno = ERANGE;
    assert(function(77, kOtherRequest, &payload) == -1 && errno == EAGAIN);
  }
  assert(payload.calls == 2 * count);  // No retry on the deliberately returned EAGAIN.
  errno = E2BIG;
  assert(function(77, kOtherRequest, nullptr) == -1 && errno == EFAULT);
}
int main(int argc, char** argv) {
  assert(argc == 3);
  const char* mode = argv[1];
  const bool local = !std::strcmp(mode, "local");
  void* provider = nullptr;
  if (std::strcmp(mode, "missing")) {
    provider = dlopen(argv[2], RTLD_NOW | (local ? RTLD_LOCAL : RTLD_GLOBAL));
    assert(provider);
  }
  auto function = reinterpret_cast<Function>(dlsym(RTLD_DEFAULT, "drmIoctl"));
  assert(function);
  if (!std::strcmp(mode, "missing")) { function(77, 0, nullptr); return 99; }
  if (!std::strcmp(mode, "late")) usleep(150000);
  if (!std::strcmp(mode, "threads")) {
    std::vector<std::thread> workers;
    for (unsigned i = 0; i < 4; ++i) workers.emplace_back([&] { Verify(function, 64); });
    for (auto& worker : workers) worker.join();
  } else {
    Verify(function, 3, !std::strcmp(mode, "positive"));
  }
  auto count = reinterpret_cast<unsigned (*)()>(dlsym(provider, "fake_calls"));
  assert(count() == (!std::strcmp(mode, "threads") ? 4 * 129 : 7));
  if (!std::strcmp(mode, "fork") || !std::strcmp(mode, "exec")) {
    const pid_t child = fork();
    assert(child >= 0);
    if (!child) {
      if (!std::strcmp(mode, "exec")) {
        execl(argv[0], argv[0], "normal", argv[2], nullptr);
        _exit(99);
      }
      Verify(function, 2);
      _exit(0);
    }
    int status = 0;
    assert(waitpid(child, &status, 0) == child && status == 0);
  }
  if (std::strcmp(mode, "early")) usleep(250000);
  std::puts("PASS: exact forwarding, arguments, errno and retry count; no GPU/device access");
}
#endif
