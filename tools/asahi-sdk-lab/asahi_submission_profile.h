#pragma once
// Optional Phase 2 telemetry. No Vulkan dependencies, GPU timestamps or policy changes.
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <memory>
#include <new>
#include <utility>
#if defined(__linux__)
#include <sys/resource.h>
#endif

namespace rex::graphics::vulkan::submission_profile {

constexpr uint32_t kNoTicket = UINT32_MAX;
enum class Stage { kResetPool, kBegin, kReplay, kEnd, kQueueAcquire, kQueueSubmit, kTotal, kCount };
constexpr const char* kStageNames[] = {
    "reset_pool", "begin", "replay", "end", "queue_acquire", "queue_submit", "total"};
enum class WaitKind { kHostSubmission, kFenceCompletion };

inline uint64_t WallNow() {
  return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count());
}
struct Stamp {
  uint64_t wall = 0, user = 0, system = 0;
  bool cpu_known = false;
};
inline Stamp Snapshot() {
  Stamp stamp;
#if defined(__linux__)
  rusage usage{};
  if (getrusage(RUSAGE_THREAD, &usage) == 0) {
    stamp.user = uint64_t(usage.ru_utime.tv_sec) * 1000000000 + uint64_t(usage.ru_utime.tv_usec) * 1000;
    stamp.system = uint64_t(usage.ru_stime.tv_sec) * 1000000000 + uint64_t(usage.ru_stime.tv_usec) * 1000;
    stamp.cpu_known = true;
  }
#endif
  stamp.wall = WallNow();
  return stamp;
}
struct Clock {
  uint64_t (*wall)() = WallNow;
  Stamp (*snapshot)() = Snapshot;
};
struct Timing {
  uint64_t start = 0, end = 0, user = 0, system = 0;
  bool called = false, cpu_known = false;
};

class Scope {
 public:
  Scope(Timing* timing, Clock clock) : timing_(timing), clock_(clock) {
    if (timing_) start_ = clock_.snapshot();
  }
  ~Scope() {
    if (!timing_) return;
    const Stamp end = clock_.snapshot();
    timing_->start = start_.wall;
    timing_->end = end.wall;
    timing_->called = true;
    timing_->cpu_known = start_.cpu_known && end.cpu_known &&
        end.user >= start_.user && end.system >= start_.system;
    if (timing_->cpu_known) {
      timing_->user = end.user - start_.user;
      timing_->system = end.system - start_.system;
    }
  }
  Scope(const Scope&) = delete;
  Scope& operator=(const Scope&) = delete;
 private:
  Timing* timing_;
  Clock clock_;
  Stamp start_{};
};

struct Job {
  uint64_t submission = 0, frame = 0, enqueued = 0, dequeued = 0;
  uint64_t main_words = 0, hoisted_words = 0;
  int32_t result = INT32_MIN;  // not recorded (for example, generic wait callbacks)
  bool supported = true, skipped = false;
  Clock clock;
  std::array<Timing, size_t(Stage::kCount)> stages{};
};
template <typename Function>
decltype(auto) Measure(Job* job, Stage stage, Function&& function) {
  Scope scope(job ? &job->stages[size_t(stage)] : nullptr, job ? job->clock : Clock{});
  return std::forward<Function>(function)();
}

struct Config {
  double start_seconds = 80, duration_seconds = 30;
  uint32_t max_jobs = 16384, max_waits = 8192;
};

// Admit/Wait/Finalize: command-processor thread only. Start/Publish: submission
// worker only. Slots are single-assignment, never reused. Acquire/release protects
// telemetry; it does not replace or weaken the renderer's synchronization.
class Recorder {
 public:
  static std::unique_ptr<Recorder> Create(const char* path, Config config = {}, Clock clock = {}) {
    if (!path || !*path) return nullptr;  // no allocation, clocks or I/O when disabled
    if (!clock.wall || !clock.snapshot || !std::isfinite(config.start_seconds) ||
        !std::isfinite(config.duration_seconds) || config.start_seconds < 0 || config.start_seconds > 600 ||
        config.duration_seconds < 1 || config.duration_seconds > 45 ||
        !config.max_jobs || config.max_jobs > 16384 || !config.max_waits || config.max_waits > 8192) return nullptr;
    auto recorder = std::unique_ptr<Recorder>(new (std::nothrow) Recorder(config, clock));
    if (!recorder || !recorder->slots_ || !recorder->waits_) return nullptr;
    recorder->origin_ = clock.wall();
    const uint64_t start_delta = uint64_t(config.start_seconds * 1000000000);
    const uint64_t end_delta = start_delta + uint64_t(config.duration_seconds * 1000000000);
    if (recorder->origin_ > std::numeric_limits<uint64_t>::max() - end_delta) return nullptr;
    recorder->start_ = recorder->origin_ + start_delta;
    recorder->end_ = recorder->origin_ + end_delta;
    // Refuse an existing output, including an old partial capture.
    recorder->file_ = std::fopen(path, "wx");
    if (!recorder->file_) return nullptr;
    const auto epoch = std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    const uint64_t bridge_mono = clock.wall();
    std::fprintf(recorder->file_,
        "#schema=asahi-submission-v1\n#clock=steady_ns\n#cpu_clock=getrusage_thread\n"
        "#origin_mono_ns=%llu\n#origin_epoch_ns=%llu\n#capture_origin_mono_ns=%llu\n"
        "#cpu_resolution=us\n#window_start_ns=%llu\n#window_end_ns=%llu\n"
        "#gpu_execution_timestamps=0\n#result_unknown=-2147483648\n#finalization=required\n"
        "kind,submission,frame,stage,start_ns,end_ns,wall_ns,user_ns,system_ns,cpu_known,"
        "main_words,hoisted_words,result,supported,skipped,crossed_end\n",
        U(bridge_mono), U(uint64_t(epoch)), U(recorder->origin_), U(recorder->start_), U(recorder->end_));
    recorder->io_error_ = std::fflush(recorder->file_) != 0;
    return recorder;
  }
  ~Recorder() { if (file_) std::fclose(file_); }

  uint32_t Admit(uint64_t submission, uint64_t frame) {
    if (finalized_) return kNoTicket;
    const uint64_t now = clock_.wall();
    if (now < start_ || now >= end_) return kNoTicket;
    if (jobs_ == config_.max_jobs) { ++dropped_jobs_; return kNoTicket; }
    const uint32_t ticket = jobs_++;
    Job& job = slots_[ticket].job;
    job.submission = submission;
    job.frame = frame;
    job.enqueued = now;
    job.clock = clock_;
    return ticket;
  }
  Job Start(uint32_t ticket, uint64_t main_words, uint64_t hoisted_words, bool native_upscale) const {
    Job job = slots_[ticket].job;  // published to this worker by the existing job queue
    job.dequeued = clock_.wall();
    job.main_words = main_words;
    job.hoisted_words = hoisted_words;
    job.supported = !native_upscale;  // split/native path is not fully stage-instrumented
    return job;
  }
  void Publish(uint32_t ticket, Job job) {
    slots_[ticket].job = std::move(job);
    slots_[ticket].ready.store(true, std::memory_order_release);
  }
  template <typename Function>
  decltype(auto) Wait(WaitKind kind, uint64_t submission, uint64_t frame, Function&& function) {
    if (finalized_) return std::forward<Function>(function)();
    const uint64_t now = clock_.wall();
    if (now < start_ || now >= end_) return std::forward<Function>(function)();
    if (wait_count_ == config_.max_waits) {
      ++dropped_waits_;
      return std::forward<Function>(function)();
    }
    WaitRecord& record = waits_[wait_count_++];
    record.kind = kind;
    record.submission = submission;
    record.frame = frame;
    Scope scope(&record.timing, clock_);
    return std::forward<Function>(function)();
  }
  // Checks pending jobs without waiting. Export is synchronous, but only after
  // the capture window; there is no polling loop, GPU call or shutdown hook.
  bool FinalizeIfReady() {
    if (finalized_) return !io_error_;
    if (clock_.wall() < end_) return false;
    for (uint32_t i = 0; i < jobs_; ++i) {
      if (!slots_[i].ready.load(std::memory_order_acquire)) return false;
    }
    const uint64_t export_start = clock_.wall();
    for (uint32_t i = 0; i < jobs_; ++i) {
      const Job& job = slots_[i].job;
      Timing delay;
      delay.start = job.enqueued;
      delay.end = job.dequeued;
      delay.called = true;
      Write("job", job, "queue_delay", delay);
      for (size_t stage = 0; stage < size_t(Stage::kCount); ++stage) {
        if (job.stages[stage].called) Write("job", job, kStageNames[stage], job.stages[stage]);
      }
    }
    for (uint32_t i = 0; i < wait_count_; ++i) {
      const WaitRecord& wait = waits_[i];
      Job job;
      job.submission = wait.submission;
      job.frame = wait.frame;
      Write("wait", job, wait.kind == WaitKind::kHostSubmission ? "host_submission" : "fence_completion", wait.timing);
    }
    io_error_ = std::fflush(file_) != 0 || std::ferror(file_) || io_error_;
    const uint64_t export_end = clock_.wall();
    std::fprintf(file_, "#jobs=%u\n#waits=%u\n#dropped_jobs=%u\n#dropped_waits=%u\n"
                         "#export_start_ns=%llu\n#export_end_ns=%llu\n",
                 jobs_, wait_count_, dropped_jobs_, dropped_waits_, U(export_start), U(export_end));
    io_error_ = io_error_ || std::ferror(file_);
    if (!io_error_) std::fprintf(file_, "#status=complete\n");
    io_error_ = std::fflush(file_) != 0 || std::ferror(file_) || io_error_;
    finalized_ = true;
    return !io_error_;
  }
 private:
  struct Slot { Job job; std::atomic<bool> ready{false}; };
  struct WaitRecord { WaitKind kind{}; uint64_t submission = 0, frame = 0; Timing timing; };
  static_assert(sizeof(Slot) * 16384 + sizeof(WaitRecord) * 8192 <= 8 * 1024 * 1024,
                "Telemetry buffers must stay below 8 MiB");
  Recorder(Config config, Clock clock) : config_(config), clock_(clock),
      slots_(new (std::nothrow) Slot[config.max_jobs]), waits_(new (std::nothrow) WaitRecord[config.max_waits]) {}
  static unsigned long long U(uint64_t value) { return static_cast<unsigned long long>(value); }
  void Write(const char* kind, const Job& job, const char* stage, const Timing& time) {
    std::fprintf(file_, "%s,%llu,%llu,%s,%llu,%llu,%llu,%llu,%llu,%u,%llu,%llu,%d,%u,%u,%u\n",
        kind, U(job.submission), U(job.frame), stage, U(time.start), U(time.end),
        U(time.end >= time.start ? time.end - time.start : 0), U(time.user), U(time.system),
        unsigned(time.cpu_known), U(job.main_words), U(job.hoisted_words), job.result,
        unsigned(job.supported), unsigned(job.skipped), unsigned(time.end > end_));
  }
  Config config_;
  Clock clock_;
  std::unique_ptr<Slot[]> slots_;
  std::unique_ptr<WaitRecord[]> waits_;
  std::FILE* file_ = nullptr;
  uint64_t origin_ = 0, start_ = 0, end_ = 0;
  uint32_t jobs_ = 0, wait_count_ = 0, dropped_jobs_ = 0, dropped_waits_ = 0;
  bool finalized_ = false, io_error_ = false;
};

template <typename Function>
decltype(auto) MeasureWait(Recorder* recorder, WaitKind kind, uint64_t submission,
                          uint64_t frame, Function&& function) {
  if (!recorder) return std::forward<Function>(function)();
  return recorder->Wait(kind, submission, frame, std::forward<Function>(function));
}
}  // namespace rex::graphics::vulkan::submission_profile
