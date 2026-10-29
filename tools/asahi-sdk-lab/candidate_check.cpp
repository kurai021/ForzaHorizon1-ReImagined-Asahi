// CPU-only loader/registry check. Never constructs a graphics provider or device.
#include <rex/cvar.h>
#include <rex/logging.h>
#include <dlfcn.h>
#include <link.h>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <string>
#include <vector>

namespace {
struct Libraries { std::vector<std::string> runtime; bool driver = false; };
int Inspect(dl_phdr_info* info, size_t, void* data) {
  auto& libraries = *static_cast<Libraries*>(data);
  const std::filesystem::path path(info->dlpi_name);
  if (path.filename() == "librexruntime.so") libraries.runtime.push_back(path.string());
  if (path.filename() == "libvulkan_asahi.so") libraries.driver = true;
  return 0;
}
}  // namespace

int main(int argc, char** argv) {
  rex::LogConfig logging;
  logging.log_to_console = true;
  rex::InitLogging(logging);
  rex::cvar::Init(argc, argv);
  const auto directory = std::filesystem::canonical("/proc/self/exe").parent_path();
  void* plugin = dlopen((directory / "librexgpu-xenos.so").c_str(), RTLD_NOW | RTLD_LOCAL);
  if (!plugin) { std::fprintf(stderr, "%s\n", dlerror()); return 65; }
  if (!dlsym(plugin, "AsahiSdkLabRun")) return 66;
  unsigned found = 0;
  unsigned readback_found = 0;
  for (const auto& flag : rex::cvar::GetRegistry()) {
    if (flag.name == "fh1_submission_profile_csv" || flag.name == "fh1_submission_profile_start" ||
         flag.name == "fh1_submission_profile_seconds") ++found;
    if (flag.name == "fh1_readback_profile_csv" || flag.name == "fh1_readback_profile_start" ||
        flag.name == "fh1_readback_profile_seconds") ++readback_found;
  }
  if (found != 3 || !rex::cvar::GetFlagByName("fh1_submission_profile_csv").empty() ||
      std::stod(rex::cvar::GetFlagByName("fh1_submission_profile_start")) != 80 ||
       std::stod(rex::cvar::GetFlagByName("fh1_submission_profile_seconds")) != 30) return 67;
  const char* expect_readback = std::getenv("ASAHI_EXPECT_READBACK_PROFILE");
  if (expect_readback && readback_found != (std::string(expect_readback) == "1" ? 3u : 0u)) return 71;
  if (readback_found) {
    if (readback_found != 3 || !rex::cvar::GetFlagByName("fh1_readback_profile_csv").empty() ||
        std::stod(rex::cvar::GetFlagByName("fh1_readback_profile_start")) != 80 ||
        std::stod(rex::cvar::GetFlagByName("fh1_readback_profile_seconds")) != 30) return 72;
    std::puts("PASS: three optional readback variables registered; recording disabled by default");
  }
  Libraries libraries;
  dl_iterate_phdr(Inspect, &libraries);
  if (libraries.driver || libraries.runtime.size() != 1 ||
      std::filesystem::canonical(libraries.runtime.front()) != directory / "librexruntime.so") return 68;
  std::puts("PASS: candidate plugin RTLD_NOW, three profiling variables, recording disabled by default");
  std::puts("PASS: candidate-local runtime; no Asahi ICD loaded; no graphics provider constructed");
  // The cvar registry retains plugin-owned pointers, so leave the DSO mapped.
  rex::ShutdownLogging();
  return 0;
}
