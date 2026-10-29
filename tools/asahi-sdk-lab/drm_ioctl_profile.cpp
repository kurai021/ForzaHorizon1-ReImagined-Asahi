// Optional process-local libdrm interposer. Never reads ioctl payloads or changes retries.
#define _GNU_SOURCE 1
#include <drm/asahi_drm.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>

#ifndef ASAHI_DRM_PROFILE_ROOT
#error "Build only through the isolated preparer with an approved output root"
#endif

extern "C" __attribute__((visibility("default"))) int drmIoctl(int, unsigned long, void*);
namespace {
using Function = int (*)(int, unsigned long, void*);
Function real_ioctl = nullptr;
pthread_once_t resolver_once = PTHREAD_ONCE_INIT;
thread_local bool resolving = false;
constexpr uint32_t kClosed = 0x80000000u, kMaxRecords = 262144, kMaxThreads = 64;

[[noreturn]] void Fail(const char* message) {
  const int ignored = int(write(STDERR_FILENO, message, std::strlen(message)));
  (void)ignored;
  _exit(125);  // No safe forwarding target/configuration: never substitute a raw syscall.
}
void Resolve() {
  resolving = true;
  real_ioctl = reinterpret_cast<Function>(dlsym(RTLD_NEXT, "drmIoctl"));
  if (!real_ioctl) {
    // Mesa may load libdrm in an RTLD_LOCAL dependency group. Do not load a new library.
    void* handle = dlopen("libdrm.so.2", RTLD_NOW | RTLD_NOLOAD);
    if (handle) real_ioctl = reinterpret_cast<Function>(dlsym(handle, "drmIoctl"));
    // Keep that existing-library reference alive; the function pointer must not dangle.
  }
  resolving = false;
  if (!real_ioctl || real_ioctl == &drmIoctl) Fail("DRM profile: original drmIoctl unavailable\n");
}
uint64_t Wall() {
  timespec time{};
  if (clock_gettime(CLOCK_MONOTONIC, &time)) return 0;
  return uint64_t(time.tv_sec) * 1000000000 + uint64_t(time.tv_nsec);
}
struct Stamp { uint64_t wall = 0, user = 0, system = 0; bool cpu_known = false; };
Stamp Snapshot() {
  Stamp stamp;
  rusage usage{};
  if (!getrusage(RUSAGE_THREAD, &usage)) {
    stamp.user = uint64_t(usage.ru_utime.tv_sec) * 1000000000 + uint64_t(usage.ru_utime.tv_usec) * 1000;
    stamp.system = uint64_t(usage.ru_stime.tv_sec) * 1000000000 + uint64_t(usage.ru_stime.tv_usec) * 1000;
    stamp.cpu_known = true;
  }
  stamp.wall = Wall();
  return stamp;
}
struct Event {
  uint64_t request = 0, start = 0, end = 0, user = 0, system = 0;
  uint32_t tid = 0;
  int32_t result = 0, error = 0;
  bool cpu_known = false;
};
struct Slot { Event event; std::atomic<bool> ready{false}; };
struct Thread { uint32_t tid = 0; char name[16]{}; };
struct State {
  pid_t pid = 0;
  uint64_t origin = 0, start = 0, end = 0;
  uint32_t capacity = 0;
  Slot* slots = nullptr;
  FILE* file = nullptr;
  std::atomic<uint32_t> gate{0}, next{0}, thread_count{0};
  std::atomic<uint64_t> dropped{0}, clock_errors{0}, thread_overflow{0};
  Thread threads[kMaxThreads]{};
};
static_assert(sizeof(Slot) * kMaxRecords < 20 * 1024 * 1024, "Bound telemetry storage below 20 MiB");
std::atomic<State*> state{nullptr};  // Process-lifetime storage; no destructor or exit wait.

bool Enter(State* current) {
  uint32_t gate = current->gate.load(std::memory_order_relaxed);
  do {
    if (gate & kClosed) return false;
  } while (!current->gate.compare_exchange_weak(gate, gate + 1, std::memory_order_acquire));
  return true;
}
struct Active {
  State* current;
  ~Active() { current->gate.fetch_sub(1, std::memory_order_release); }
};
uint32_t Reserve(State* current) {
  uint32_t next = current->next.load(std::memory_order_relaxed);
  do {
    if (next == current->capacity) {
      current->dropped.fetch_add(1, std::memory_order_relaxed);
      return UINT32_MAX;
    }
  } while (!current->next.compare_exchange_weak(next, next + 1, std::memory_order_relaxed));
  return next;
}
uint32_t RegisterThread(State* current) {
  static thread_local uint32_t tid = 0;
  if (tid) return tid;
  tid = uint32_t(syscall(SYS_gettid));
  const uint32_t index = current->thread_count.fetch_add(1, std::memory_order_relaxed);
  if (index < kMaxThreads) {
    current->threads[index].tid = tid;
    if (prctl(PR_GET_NAME, current->threads[index].name)) current->threads[index].name[0] = 0;
  } else {
    current->thread_overflow.fetch_add(1, std::memory_order_relaxed);
  }
  return tid;
}
unsigned long long U(uint64_t value) { return static_cast<unsigned long long>(value); }

void* Export(void* pointer) {
  auto* current = static_cast<State*>(pointer);
  timespec interval{0, 5000000};
  const timespec deadline{time_t(current->end / 1000000000ULL), long(current->end % 1000000000ULL)};
  int sleep_error;
  do { sleep_error = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &deadline, nullptr); }
  while (sleep_error == EINTR);
  // No periodic exporter wakeups during collection; only pending writers are polled below.
  uint64_t now = sleep_error ? 0 : Wall();
  // Close admissions atomically, then observe telemetry writers, not GPU completion.
  current->gate.fetch_or(kClosed, std::memory_order_acq_rel);
  const uint64_t closed = now;
  bool complete = now != 0;
  while (complete && (current->gate.load(std::memory_order_acquire) & ~kClosed)) {
    nanosleep(&interval, nullptr);
    now = Wall();
    if (!now || now - closed > 5000000000ULL) complete = false;
  }
  if (!complete) {
    std::fprintf(current->file, "#status=incomplete\n#reason=pending_calls_or_clock_failure\n");
    std::fflush(current->file);
    std::fclose(current->file);
    return nullptr;
  }
  const uint64_t export_start = Wall();
  const uint32_t count = current->next.load(std::memory_order_relaxed);
  for (uint32_t index = 0; index < count; ++index) {
    if (!current->slots[index].ready.load(std::memory_order_acquire)) {
      complete = false;
      break;
    }
    const Event& event = current->slots[index].event;
    std::fprintf(current->file, "%u,%u,%llu,%llu,%llu,%llu,%llu,%llu,%u,%d,%d\n",
                 index, event.tid, U(event.request), U(event.start), U(event.end),
                 U(event.end - event.start), U(event.user), U(event.system),
                 unsigned(event.cpu_known), event.result, event.error);
  }
  const uint32_t threads = current->thread_count.load(std::memory_order_relaxed);
  for (uint32_t index = 0; index < threads && index < kMaxThreads; ++index) {
    const Thread& thread = current->threads[index];
    std::fprintf(current->file, "#thread_%u=", thread.tid);
    // Encode comm bytes; no newline or metadata injection from a thread name.
    for (unsigned i = 0; i < sizeof(thread.name) && thread.name[i]; ++i)
      std::fprintf(current->file, "%02x", unsigned(static_cast<unsigned char>(thread.name[i])));
    std::fputc('\n', current->file);
  }
  bool io_error = std::fflush(current->file) != 0 || std::ferror(current->file);
  const uint64_t export_end = Wall();
  std::fprintf(current->file,
               "#records=%u\n#dropped=%llu\n#clock_errors=%llu\n#thread_overflow=%llu\n"
               "#admission_closed_ns=%llu\n#export_start_ns=%llu\n#export_end_ns=%llu\n",
               count, U(current->dropped.load()), U(current->clock_errors.load()),
               U(current->thread_overflow.load()), U(closed), U(export_start), U(export_end));
  if (complete && !io_error && export_start && export_end) std::fputs("#status=complete\n", current->file);
  else std::fputs("#status=incomplete\n", current->file);
  std::fflush(current->file);
  std::fclose(current->file);
  return nullptr;
}

double Setting(const char* name, double fallback, double low, double high) {
  const char* text = std::getenv(name);
  if (!text) return fallback;
  errno = 0;
  char* end = nullptr;
  const double value = std::strtod(text, &end);
  if (errno || end == text || *end || !std::isfinite(value) || value < low || value > high)
    Fail("DRM profile: invalid bounded setting\n");
  return value;
}
bool AllowedPath(const char* path) {
  if (!path || path[0] != '/' || std::strlen(path) >= PATH_MAX) return false;
  char parent[PATH_MAX];
  std::strcpy(parent, path);
  char* slash = std::strrchr(parent, '/');
  if (!slash || !slash[1]) return false;
  *slash = 0;
  char resolved[PATH_MAX];
  if (!realpath(parent, resolved)) return false;
  const char* root = ASAHI_DRM_PROFILE_ROOT;
  const size_t size = std::strlen(root);
  return !std::strncmp(resolved, root, size) && (resolved[size] == 0 || resolved[size] == '/');
}
__attribute__((constructor)) void Initialize() {
  const char* path = std::getenv("ASAHI_DRM_IOCTL_CSV");
  if (!path || !*path) return;  // No recorder clocks, buffers, thread or file when disabled.
  const char* target = std::getenv("ASAHI_DRM_IOCTL_TARGET");
  if (!target || !*target) Fail("DRM profile: an exact executable target is required\n");
  char exe[PATH_MAX];
  const ssize_t size = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
  if (size < 0) Fail("DRM profile: cannot identify executable\n");
  exe[size] = 0;
  if (std::strcmp(exe, target)) return;  // Never record a helper executable.
  const pid_t pid = getpid();
  const char* inherited = std::getenv("ASAHI_DRM_IOCTL_OWNER_PID");
  if (inherited && Setting("ASAHI_DRM_IOCTL_OWNER_PID", 0, 1, INT_MAX) != pid) return;
  if (!AllowedPath(path)) Fail("DRM profile: output must be new and inside the approved private root\n");
  const double start = Setting("ASAHI_DRM_IOCTL_START", 75, 0, 600);
  const double duration = Setting("ASAHI_DRM_IOCTL_SECONDS", 40, .05, 45);
  const double records = Setting("ASAHI_DRM_IOCTL_MAX_RECORDS", kMaxRecords, 1, kMaxRecords);
  if (records != std::floor(records)) Fail("DRM profile: record capacity must be integral\n");
  auto* current = new (std::nothrow) State;
  if (!current) Fail("DRM profile: state allocation failed\n");
  current->capacity = uint32_t(records);
  current->slots = new (std::nothrow) Slot[current->capacity];
  if (!current->slots) Fail("DRM profile: bounded slot allocation failed\n");
  current->pid = pid;
  current->origin = Wall();
  current->start = current->origin + uint64_t(start * 1e9);
  current->end = current->start + uint64_t(duration * 1e9);
  timespec epoch{};
  if (!current->origin || clock_gettime(CLOCK_REALTIME, &epoch)) Fail("DRM profile: clock unavailable\n");
  const uint64_t bridge = Wall();
  const int fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
  if (fd < 0) Fail("DRM profile: refusing existing/unwritable output\n");
  current->file = fdopen(fd, "w");
  if (!current->file) Fail("DRM profile: output stream failed\n");
  std::fprintf(current->file,
               "#schema=asahi-drm-ioctl-v1\n#clock=monotonic_ns\n#cpu_clock=getrusage_thread\n"
               "#origin_mono_ns=%llu\n#origin_epoch_ns=%llu\n#window_start_ns=%llu\n#window_end_ns=%llu\n"
               "#pid=%u\n#submit_request=%llu\n#capacity=%u\n#boundary=drmIoctl\n"
               "#raw_syscall_attempts=unknown\n#payload_recorded=0\n"
               "ticket,tid,request,start_ns,end_ns,wall_ns,user_ns,system_ns,cpu_known,result,errno_value\n",
               U(bridge), U(uint64_t(epoch.tv_sec) * 1000000000 + uint64_t(epoch.tv_nsec)),
               U(current->start), U(current->end), unsigned(pid), U(DRM_IOCTL_ASAHI_SUBMIT), current->capacity);
  if (std::fflush(current->file)) Fail("DRM profile: output header failed\n");
  char owner[32];
  std::snprintf(owner, sizeof(owner), "%u", unsigned(pid));
  if (setenv("ASAHI_DRM_IOCTL_OWNER_PID", owner, 1)) Fail("DRM profile: owner latch failed\n");
  state.store(current, std::memory_order_release);
  pthread_t worker;
  if (pthread_create(&worker, nullptr, Export, current)) Fail("DRM profile: export thread failed\n");
  pthread_detach(worker);
}
}  // namespace

extern "C" __attribute__((visibility("default"))) uint32_t asahi_drm_ioctl_profile_abi() { return 1; }

extern "C" __attribute__((visibility("default"))) int drmIoctl(int fd, unsigned long request, void* argument) {
  const int incoming_errno = errno;
  if (resolving) Fail("DRM profile: recursive original-symbol resolution\n");
  pthread_once(&resolver_once, Resolve);
  State* current = state.load(std::memory_order_acquire);
  if (!current || current->pid != getpid() || !Enter(current)) {
    errno = incoming_errno;
    return real_ioctl(fd, request, argument);
  }
  Active active{current};
  const uint64_t now = Wall();
  if (!now) current->clock_errors.fetch_add(1, std::memory_order_relaxed);
  if (!now || now < current->start || now >= current->end) {
    errno = incoming_errno;
    return real_ioctl(fd, request, argument);
  }
  const uint32_t tid = RegisterThread(current);
  const Stamp begin = Snapshot();
  if (!begin.wall || begin.wall >= current->end) {
    if (!begin.wall) current->clock_errors.fetch_add(1, std::memory_order_relaxed);
    errno = incoming_errno;
    return real_ioctl(fd, request, argument);
  }
  const uint32_t ticket = Reserve(current);
  errno = incoming_errno;
  const int result = real_ioctl(fd, request, argument);  // Exactly once, unchanged arguments/retries.
  const int returned_errno = errno;
  if (ticket != UINT32_MAX) {
    const Stamp end = Snapshot();
    Event event;
    event.request = request; event.tid = tid; event.start = begin.wall;
    event.end = end.wall >= begin.wall ? end.wall : begin.wall;
    event.cpu_known = begin.cpu_known && end.cpu_known && end.user >= begin.user && end.system >= begin.system;
    if (!end.wall || end.wall < begin.wall) current->clock_errors.fetch_add(1, std::memory_order_relaxed);
    if (event.cpu_known) { event.user = end.user - begin.user; event.system = end.system - begin.system; }
    event.result = result; event.error = returned_errno;
    current->slots[ticket].event = event;
    current->slots[ticket].ready.store(true, std::memory_order_release);
  }
  errno = returned_errno;
  return result;
}
