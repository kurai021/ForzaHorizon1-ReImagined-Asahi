#include "asahi_readback_profile.h"
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

namespace p = rex::graphics::vulkan::readback_profile;
uint64_t now = 100;
uint64_t clock_now() { return now; }
void require(bool b) { if (!b) throw std::runtime_error("readback recorder test failed"); }
int main(int argc, char** argv) {
  if (argc != 2) return 2;
  try {
    const std::string root = argv[1];
    require(!p::Recorder::Create("", {}, nullptr));
    require(!p::Recorder::Create((root + "/invalid.csv").c_str(), {0, 46, 1}, clock_now));
    int calls = 0;
    {
      p::Trace disabled(nullptr, 0, false, false, 2, 1);
      require(disabled.WaitFor(p::Wait::kMiss, [&] { ++calls; return false; }) == false);
      require(disabled.Allocate([&] { ++calls; return true; }));
      disabled.Copy(4, [&] { ++calls; });
      require(calls == 3);
    }
    const auto path = root + "/ordinary.csv";
    auto recorder = p::Recorder::Create(path.c_str(), {0, 1, 4}, clock_now);
    require(bool(recorder));
    require(!p::Recorder::Create(path.c_str(), {0, 1, 4}, clock_now));
    {
      p::Trace trace(recorder.get(), 0, false, false, 2, 1);
      auto* row = trace.row(); require(row && row->key_id == 1 && row->generation == 1);
      row->mode = 2; row->length = 256;
      trace.Flag(p::kSlotMiss);
      require(trace.Allocate([&] { now += 10; return true; })); row->allocated_bytes += 256;
      require(trace.WaitFor(p::Wait::kMiss, [&] { now += 20; return true; }));
      trace.Copy(256, [&] { now += 5; });
      now = 1000000200;
      require(!recorder->FinalizeIfReady());  // a still-open admitted scope cannot be exported
    }
    require(recorder->FinalizeIfReady());
    require(recorder->FinalizeIfReady());
    // Events beginning outside the window do not consume capacity.
    { p::Trace closed(recorder.get(), 1, false, false, 3, 2); require(!closed.row()); }
    now = 100;
    auto generations = p::Recorder::Create((root + "/generations.csv").c_str(), {1, 1, 8}, clock_now);
    { p::Trace prior(generations.get(), 42, false, false, 2, 1); require(!prior.row()); }
    now = 1000000200;
    { p::Trace hit(generations.get(), 42, true, false, 3, 2); require(hit.row()->generation == 1); hit.row()->mode = 2; }
    { p::Trace evict(generations.get(), 42, true, true, 3, 2); evict.Flag(p::kEvictAge); }
    { p::Trace recreate(generations.get(), 42, false, false, 4, 3); require(recreate.row()->generation == 2); recreate.row()->mode = 3; }
    now = 2000000300; require(generations->FinalizeIfReady());
    now = 100;
    auto capacity = p::Recorder::Create((root + "/capacity.csv").c_str(), {0, 1, 1}, clock_now);
    for (int i = 0; i < 2; ++i) { p::Trace t(capacity.get(), 1, false, false, 2, 1); if (t.row()) t.row()->mode = 2; }
    now = 1000000200; require(capacity->FinalizeIfReady());
    now = 100;
    auto keys = p::Recorder::Create((root + "/keys.csv").c_str(), {1, 1, 1}, clock_now);
    for (uint64_t key = 0; key < 4097; ++key) { p::Trace t(keys.get(), key, false, false, 2, 1); }
    now = 1000000200;
    { p::Trace t(keys.get(), 0, true, false, 2, 1); t.row()->mode = 2; }
    now = 2000000300; require(keys->FinalizeIfReady());
    now = 100;
    auto failure = p::Recorder::Create((root + "/failure.csv").c_str(), {0, 1, 2}, clock_now);
    { p::Trace t(failure.get(), 2, false, false, 2, 1); t.row()->mode = 3;
      require(!t.WaitFor(p::Wait::kFull, [] { now += 1; return false; })); }
    now = 1000000200; require(failure->FinalizeIfReady());
    now = 100;
    auto backwards = p::Recorder::Create((root + "/backwards.csv").c_str(), {0, 1, 2}, clock_now);
    { p::Trace t(backwards.get(), 3, false, false, 2, 1); t.row()->mode = 2;
      t.WaitFor(p::Wait::kMiss, [] { --now; return true; }); now += 2; }
    now = 1000000200; require(backwards->FinalizeIfReady());
    now = 100;
    auto flood = p::Recorder::Create((root + "/routine-flood.csv").c_str(), {0, 1, 4}, clock_now);
    for (unsigned i = 0; i < 100279; ++i) {
      p::Trace t(flood.get(), 42, true, false, 2, 1);
      require(t.row());
      t.row()->mode = 2;
      t.Flag(p::kCpuValid | p::kSkipValid);
      ++now;
    }
    { p::Trace late(flood.get(), 43, false, false, 3, 2);
      require(late.row()); late.row()->mode = 3;
      require(late.WaitFor(p::Wait::kFull, [] { now += 100; return true; }));
      late.Copy(256, [] { now += 10; }); }
    // A slow apparent skip and a hot-history skip must not disappear in summaries.
    { p::Trace slow(flood.get(), 42, true, false, 4, 3);
      slow.row()->mode = 2; slow.Flag(p::kCpuValid | p::kSkipValid); now += 1000001; }
    { p::Trace hot(flood.get(), 42, true, false, 5, 4);
      hot.row()->mode = 2; hot.Flag(p::kHistoryHot | p::kSkipValid); ++now; }
    now = 1000000200; require(flood->FinalizeIfReady());
    now = 100;
    auto crossing = p::Recorder::Create((root + "/routine-crossing.csv").c_str(), {0, 1, 2}, clock_now);
    now = 1000000050;
    { p::Trace t(crossing.get(), 1, true, false, 2, 1);
      t.row()->mode = 2; t.Flag(p::kCpuValid | p::kSkipValid); now += 100; }
    require(crossing->FinalizeIfReady());
    std::cout << "PASS: bounded resolve telemetry, forwarding, identities and failure qualification\n";
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
