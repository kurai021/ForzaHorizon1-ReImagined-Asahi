// Deterministic CPU-only tests; no SDK, Vulkan, game data or GPU initialization.
#include "asahi_submission_profile.h"
#include <cassert>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <thread>
#include <vector>

namespace p = rex::graphics::vulkan::submission_profile;
static std::atomic<uint64_t> wall{100}, clock_calls{0};
static uint64_t TestWall() { ++clock_calls; return wall.fetch_add(1000); }
static p::Stamp TestSnapshot() {
  static thread_local uint64_t cpu = 0;
  cpu += 100;
  return {TestWall(), cpu * 3, cpu, true};
}
static p::Clock TestClock() { return {TestWall, TestSnapshot}; }
static p::Stamp UnknownCpu() { return {TestWall(), 0, 0, false}; }
static std::string Read(const std::filesystem::path& path) {
  std::ifstream stream(path);
  return {std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()};
}
static p::Config Config(uint32_t jobs = 4, uint32_t waits = 4) { return {0, 1, jobs, waits}; }

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::filesystem::path directory(argv[1]);
  const auto calls = clock_calls.load();
  assert(!p::Recorder::Create("", Config(), TestClock()));
  int invoked = 0;
  assert(p::Measure(nullptr, p::Stage::kReplay, [&] { ++invoked; return -4; }) == -4);
  p::MeasureWait(nullptr, p::WaitKind::kHostSubmission, 1, 1, [&] { ++invoked; });
  assert(invoked == 2 && clock_calls.load() == calls);
  auto token = p::Measure(nullptr, p::Stage::kQueueAcquire, [] { return std::make_unique<int>(7); });
  assert(*token == 7);  // move-only returns and lifetimes are preserved
  p::Timing unknown_cpu;
  { p::Scope scope(&unknown_cpu, {TestWall, UnknownCpu}); }
  assert(unknown_cpu.called && !unknown_cpu.cpu_known && unknown_cpu.user == 0);

  const auto path = directory / "stages.csv";
  auto recorder = p::Recorder::Create(path.c_str(), Config(), TestClock());
  assert(recorder);
  const auto ticket = recorder->Admit(42, 9);
  assert(ticket != p::kNoTicket);
  auto job = recorder->Start(ticket, 100, 20, false);
  int result = p::Measure(&job, p::Stage::kResetPool, [] { return 0; });
  assert(result == 0);
  result = p::Measure(&job, p::Stage::kReplay, [] { return -4; });
  job.result = result;
  // A failed operation must not invent a later end/submit phase.
  recorder->Wait(p::WaitKind::kHostSubmission, 42, 9, [] {});
  recorder->Wait(p::WaitKind::kFenceCompletion, 42, 9, [] {});
  wall += 2000000000;
  assert(!recorder->FinalizeIfReady());  // outstanding worker: nonblocking, incomplete
  assert(Read(path).find("#status=complete") == std::string::npos);
  recorder->Publish(ticket, std::move(job));
  assert(recorder->FinalizeIfReady());
  auto text = Read(path);
  assert(text.find("job,42,9,reset_pool,") != std::string::npos);
  assert(text.find("job,42,9,replay,") != std::string::npos);
  assert(text.find("job,42,9,queue_submit,") == std::string::npos);
  assert(text.find("wait,42,9,host_submission,") != std::string::npos);
  assert(text.find("wait,42,9,fence_completion,") != std::string::npos);
  assert(text.find("#status=complete") != std::string::npos);
  assert(recorder->FinalizeIfReady() && Read(path) == text);  // no duplicate export
  const auto finalized_calls = clock_calls.load();
  assert(recorder->Admit(43, 9) == p::kNoTicket);
  recorder->Wait(p::WaitKind::kHostSubmission, 43, 9, [] {});
  assert(clock_calls.load() == finalized_calls);
  assert(!p::Recorder::Create(path.c_str(), Config(), TestClock()));
  assert(Read(path) == text);  // existing evidence is never overwritten

  const auto caps_path = directory / "caps.csv";
  auto capped = p::Recorder::Create(caps_path.c_str(), Config(1, 1), TestClock());
  const auto first = capped->Admit(1, 1);
  assert(capped->Admit(2, 1) == p::kNoTicket);
  auto native = capped->Start(first, 1, 0, true);
  assert(!native.supported);  // partial native/split path is marked, never skipped
  p::Measure(&native, p::Stage::kTotal, [&] { ++invoked; });
  capped->Publish(first, std::move(native));
  capped->Wait(p::WaitKind::kHostSubmission, 1, 1, [&] { ++invoked; });
  capped->Wait(p::WaitKind::kFenceCompletion, 1, 1, [&] { ++invoked; });
  assert(invoked == 5);  // capped telemetry never suppresses work or waits
  wall += 2000000000;
  assert(capped->FinalizeIfReady());
  text = Read(caps_path);
  assert(text.find("#dropped_jobs=1") != std::string::npos);
  assert(text.find("#dropped_waits=1") != std::string::npos);

  auto invalid = Config();
  invalid.duration_seconds = 46;
  const auto invalid_path = directory / "invalid.csv";
  assert(!p::Recorder::Create(invalid_path.c_str(), invalid, TestClock()));
  assert(!std::filesystem::exists(invalid_path));
  invalid = Config();
  invalid.duration_seconds = std::numeric_limits<double>::quiet_NaN();
  assert(!p::Recorder::Create(invalid_path.c_str(), invalid, TestClock()));
  assert(!p::Recorder::Create(invalid_path.c_str(), Config(), {nullptr, TestSnapshot}));
  assert(!std::filesystem::exists(invalid_path));

  const auto ordinary_path = directory / "ordinary.csv";
  auto ordinary = p::Recorder::Create(ordinary_path.c_str(), Config(), TestClock());
  const auto ordinary_ticket = ordinary->Admit(100, 5);
  auto ordinary_job = ordinary->Start(ordinary_ticket, 100, 20, false);
  p::Measure(&ordinary_job, p::Stage::kTotal, [&] {
    for (size_t stage = 0; stage < size_t(p::Stage::kTotal); ++stage)
      p::Measure(&ordinary_job, p::Stage(stage), [] {});
  });
  ordinary_job.result = 0;
  ordinary->Publish(ordinary_ticket, std::move(ordinary_job));
  ordinary->Wait(p::WaitKind::kHostSubmission, 100, 5, [] {});
  ordinary->Wait(p::WaitKind::kFenceCompletion, 100, 5, [] {});
  wall += 2000000000;
  assert(ordinary->FinalizeIfReady());

  // Publication may race with nonblocking checks; slots are never reused.
  const auto threaded_path = directory / "threaded.csv";
  auto threaded = p::Recorder::Create(threaded_path.c_str(), Config(128, 1), TestClock());
  std::vector<uint32_t> tickets;
  for (uint64_t id = 1; id <= 128; ++id) tickets.push_back(threaded->Admit(id, 1));
  wall += 2000000000;
  std::thread worker([&] {
    for (auto id : tickets) {
      auto record = threaded->Start(id, 10, 0, false);
      p::Measure(&record, p::Stage::kReplay, [] {});
      threaded->Publish(id, std::move(record));
    }
  });
  for (unsigned i = 0; i < 10000 && !threaded->FinalizeIfReady(); ++i) std::this_thread::yield();
  worker.join();
  assert(threaded->FinalizeIfReady());
  assert(Read(threaded_path).find("#jobs=128") != std::string::npos);
  std::puts("PASS: bounded submission stages, disabled mode, unchanged callbacks, publication and overflow");
}
