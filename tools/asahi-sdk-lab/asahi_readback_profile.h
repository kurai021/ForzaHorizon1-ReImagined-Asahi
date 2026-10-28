#pragma once
// Command-processor-thread-only telemetry. Never changes readback policy or GPU work.
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <memory>
#include <new>
#include <optional>
#include <utility>

namespace rex::graphics::vulkan::readback_profile {
inline uint64_t WallNow() {
  return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count());
}
enum class Wait { kResize, kFull, kMiss };
struct Record {
  uint64_t frame = 0, submission = 0, key_id = 0, generation = 0;
  uint64_t start = 0, end = 0;
  uint64_t resize_wait = 0, full_wait = 0, miss_wait = 0, allocation_wall = 0, copy_wall = 0;
  uint64_t allocated_bytes = 0, copied_bytes = 0, retained_bytes = 0;
  uint32_t kind = 0, mode = 0, length = 0, index = 0, size0 = 0, size1 = 0;
  uint32_t flags = 0, allocations = 0, copies = 0, failed_waits = 0;
  uint64_t events = 1;
  uint32_t identity_slot = UINT32_MAX;  // internal only, never exported
};
enum Flag : uint32_t {
  kThumbnail = 1, kScaled = 2, kEntryPresent = 4, kCpuValid = 8,
  kHistoryHot = 16, kSkipValid = 32, kSlotMiss = 64,
  kEvictAge = 128, kEvictCount = 256, kAllocationFailed = 512, kMapFailed = 1024
};
struct Config { double start_seconds = 80, duration_seconds = 30; uint32_t max_events = 8192; };

class Recorder {
 public:
  static std::unique_ptr<Recorder> Create(const char* path, Config config = {},
                                         uint64_t (*wall)() = WallNow) {
    if (!path || !*path) return nullptr;
    if (!wall || !std::isfinite(config.start_seconds) || !std::isfinite(config.duration_seconds) ||
        config.start_seconds < 0 || config.start_seconds > 600 ||
        config.duration_seconds < 1 || config.duration_seconds > 45 ||
        !config.max_events || config.max_events > 8192) return nullptr;
    auto out = std::unique_ptr<Recorder>(new (std::nothrow) Recorder(config, wall));
    if (!out || !out->records_) return nullptr;
    const uint64_t origin = wall();
    const uint64_t delta = uint64_t((config.start_seconds + config.duration_seconds) * 1e9);
    if (origin > UINT64_MAX - delta) return nullptr;
    out->start_ = origin + uint64_t(config.start_seconds * 1e9);
    out->end_ = origin + delta;
    out->file_ = std::fopen(path, "wx");
    if (!out->file_) return nullptr;
    const uint64_t epoch = uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count());
    const uint64_t bridge = wall();
    std::fprintf(out->file_,
        "#schema=asahi-readback-v2\n#clock=steady_ns\n#scope=resolve_only\n"
        "#origin_mono_ns=%llu\n#origin_epoch_ns=%llu\n#window_start_ns=%llu\n#window_end_ns=%llu\n"
        "#raw_keys_recorded=0\n#gpu_execution_timestamps=0\n#key_table_capacity=4096\n"
        "#routine_policy=some_valid_unscaled_no_work_le_1ms\n#routine_aggregation=per_key_across_generations\n"
        "kind,frame,submission,key_id,generation,start_ns,end_ns,wall_ns,mode,length,index,size0,size1,flags,"
        "allocations,allocated_bytes,allocation_wall_ns,copies,copied_bytes,copy_wall_ns,resize_wait_ns,"
        "full_wait_ns,miss_wait_ns,failed_waits,retained_bytes,events\n",
        U(bridge), U(epoch), U(out->start_), U(out->end_));
    out->io_error_ = std::fflush(out->file_) != 0;
    return out;
  }
  ~Recorder() { if (file_) std::fclose(file_); }
  uint64_t Now() const { return wall_(); }
  uint64_t Elapsed(uint64_t before, uint64_t after) {
    if (after >= before) return after - before;
    ++clock_errors_;
    return 0;
  }
  // Stable opaque identity across eviction, including observations before admission.
  // Raw keys stay only in this bounded table, never in the output.
  bool Begin(uint64_t key, bool entry_present, bool eviction, uint64_t frame, uint64_t submission,
             std::optional<Record>& storage) {
    if (finalized_) return false;
    Key* identity = Find(key);
    if (identity && !entry_present && !eviction) ++identity->generation;
    const uint64_t now = Now();
    if (now < start_ || now >= end_ || !identity) return false;
    Record& row = storage.emplace();
    row.kind = eviction ? 1 : 0;
    row.frame = frame; row.submission = submission;
    row.key_id = identity->id; row.generation = identity->generation;
    row.start = now;
    row.identity_slot = uint32_t(identity - keys_.data());
    if (entry_present) row.flags |= kEntryPresent;
    ++pending_;
    ++admitted_;
    return true;
  }
  void Finish(Record* row) {
    if (!row) return;
    row->end = Now();
    if (row->end < row->start) ++clock_errors_;
    --pending_;
    ++completed_;
    // Only an ordinary existing valid CPU-copy skip with no work, short host
    // scope and no boundary crossing may be summarized. Hot-history refreshes,
    // scaled paths, slow skips, failures, allocations, waits and copies keep detail.
    const bool routine = row->kind == 0 && row->mode == 2 &&
        row->flags == (kEntryPresent | kCpuValid | kSkipValid) &&
        !row->allocations && !row->allocated_bytes && !row->allocation_wall &&
        !row->copies && !row->copied_bytes && !row->copy_wall && !row->failed_waits &&
        !row->resize_wait && !row->full_wait && !row->miss_wait &&
        row->end >= row->start && row->end - row->start <= 1000000 && row->end <= end_;
    if (routine) {
      Key& key = keys_[row->identity_slot];
      if (!key.routine_events) key.first = row->start;
      else if (row->start < key.first) key.first = row->start;
      if (row->end > key.last) key.last = row->end;
      ++key.routine_events;
      key.routine_wall += row->end - row->start;
      ++routine_events_;
    } else if (count_ == config_.max_events) {
      ++dropped_;
    } else {
      records_[count_++] = *row;
    }
  }
  bool FinalizeIfReady() {
    if (finalized_) return !io_error_;
    if (Now() < end_ || pending_) return false;
    const uint64_t export_start = Now();
    for (uint32_t i = 0; i < count_; ++i) {
      const Record& r = records_[i];
      Write(r, r.end >= r.start ? r.end - r.start : 0);
    }
    uint32_t summaries = 0;
    for (const Key& key : keys_) {
      if (!key.routine_events) continue;
      Record summary;
      summary.kind = 2; summary.key_id = key.id;
      summary.start = key.first; summary.end = key.last;
      summary.mode = 2; summary.flags = kEntryPresent | kCpuValid | kSkipValid;
      summary.events = key.routine_events;
      Write(summary, key.routine_wall);
      ++summaries;
    }
    io_error_ = std::fflush(file_) != 0 || std::ferror(file_) || io_error_;
    const uint64_t export_end = Now();
    std::fprintf(file_, "#records=%u\n#detailed_records=%u\n#routine_events=%llu\n#admitted_events=%llu\n#completed_events=%llu\n"
                        "#dropped=%llu\n#key_overflow=%llu\n#clock_errors=%llu\n"
                        "#export_start_ns=%llu\n#export_end_ns=%llu\n",
                 count_ + summaries, count_, U(routine_events_), U(admitted_), U(completed_),
                 U(dropped_), U(key_overflow_), U(clock_errors_), U(export_start), U(export_end));
    io_error_ = std::ferror(file_) || io_error_;
    if (!io_error_) std::fprintf(file_, "#status=complete\n");
    io_error_ = std::fflush(file_) != 0 || std::ferror(file_) || io_error_;
    finalized_ = true;
    return !io_error_;
  }
 private:
  struct Key {
    uint64_t key = 0, id = 0, generation = 0;
    uint64_t routine_events = 0, routine_wall = 0, first = 0, last = 0;
  };
  Key* Find(uint64_t key) {
    // Open addressing, at most 50% full; lookup is always bounded.
    uint64_t mixed = key;
    mixed ^= mixed >> 33; mixed *= UINT64_C(0xff51afd7ed558ccd); mixed ^= mixed >> 33;
    size_t slot = size_t(mixed) & (keys_.size() - 1);
    for (size_t probe = 0; probe < keys_.size(); ++probe, slot = (slot + 1) & (keys_.size() - 1)) {
      Key& value = keys_[slot];
      if (value.id && value.key == key) return &value;
      if (!value.id) {
        if (key_count_ == 4096) break;
        value.key = key; value.id = ++key_count_;
        return &value;
      }
    }
    ++key_overflow_;
    return nullptr;
  }
  static unsigned long long U(uint64_t v) { return static_cast<unsigned long long>(v); }
  void Write(const Record& r, uint64_t wall) {
    std::fprintf(file_, "%u,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%u,%u,%u,%u,%u,%u,%u,%llu,%llu,%u,%llu,%llu,%llu,%llu,%llu,%u,%llu,%llu\n",
        r.kind, U(r.frame), U(r.submission), U(r.key_id), U(r.generation), U(r.start), U(r.end), U(wall),
        r.mode, r.length, r.index, r.size0, r.size1, r.flags, r.allocations, U(r.allocated_bytes),
        U(r.allocation_wall), r.copies, U(r.copied_bytes), U(r.copy_wall), U(r.resize_wait),
        U(r.full_wait), U(r.miss_wait), r.failed_waits, U(r.retained_bytes), U(r.events));
  }
  Recorder(Config config, uint64_t (*wall)()) : config_(config), wall_(wall),
      records_(new (std::nothrow) Record[config.max_events]) {}
  static_assert(sizeof(Record) * 8192 + sizeof(Key) * 8192 < 2 * 1024 * 1024);
  Config config_;
  uint64_t (*wall_)();
  std::unique_ptr<Record[]> records_;
  std::array<Key, 8192> keys_{};
  std::FILE* file_ = nullptr;
  uint64_t start_ = 0, end_ = 0, dropped_ = 0, key_overflow_ = 0, clock_errors_ = 0;
  uint64_t admitted_ = 0, completed_ = 0, routine_events_ = 0;
  uint32_t key_count_ = 0, count_ = 0, pending_ = 0;
  bool finalized_ = false, io_error_ = false;
};

class Trace {
 public:
  Trace(Recorder* recorder, uint64_t key, bool present, bool eviction, uint64_t frame, uint64_t submission)
      : recorder_(recorder) {
    if (recorder && recorder->Begin(key, present, eviction, frame, submission, local_)) row_ = &*local_;
  }
  ~Trace() { if (row_) recorder_->Finish(row_); }
  Trace(const Trace&) = delete;
  Trace& operator=(const Trace&) = delete;
  Record* row() { return row_; }
  void Flag(uint32_t flag) { if (row_) row_->flags |= flag; }
  template <typename Function> bool WaitFor(Wait kind, Function&& function) {
    if (!row_) return std::forward<Function>(function)();
    const uint64_t before = recorder_->Now();
    const bool result = std::forward<Function>(function)();
    const uint64_t after = recorder_->Now();
    const uint64_t elapsed = recorder_->Elapsed(before, after);
    if (kind == Wait::kResize) row_->resize_wait += elapsed;
    else if (kind == Wait::kFull) row_->full_wait += elapsed;
    else row_->miss_wait += elapsed;
    if (!result) ++row_->failed_waits;
    return result;
  }
  template <typename Function> bool Allocate(Function&& function) {
    if (!row_) return std::forward<Function>(function)();
    const uint64_t before = recorder_->Now();
    const bool result = std::forward<Function>(function)();
    const uint64_t after = recorder_->Now();
    row_->allocation_wall += recorder_->Elapsed(before, after);
    ++row_->allocations;
    return result;
  }
  template <typename Function> void Copy(uint32_t bytes, Function&& function) {
    if (!row_) { std::forward<Function>(function)(); return; }
    const uint64_t before = recorder_->Now();
    std::forward<Function>(function)();
    const uint64_t after = recorder_->Now();
    row_->copy_wall += recorder_->Elapsed(before, after);
    ++row_->copies; row_->copied_bytes += bytes;
  }
 private:
  Recorder* recorder_;
  std::optional<Record> local_;  // no record initialization when disabled/outside admission
  Record* row_ = nullptr;
};
}  // namespace rex::graphics::vulkan::readback_profile
