#include <rex/cvar.h>
#include <rex/logging.h>
#include <dlfcn.h>
#include <cstdio>
#include <filesystem>

int main(int argc, char** argv) {
  if (argc < 2) return 64;
  const char* mode = argv[1];
  std::setvbuf(stdout, nullptr, _IOLBF, 0);
  rex::LogConfig logging;
  logging.log_to_console = true;
  rex::InitLogging(logging);
  rex::cvar::Init(argc - 1, argv + 1);
  const auto library = std::filesystem::canonical("/proc/self/exe").parent_path() /
                       "librexgpu-xenos.so";
  void* plugin = dlopen(library.c_str(), RTLD_NOW | RTLD_LOCAL);
  if (!plugin) { std::fprintf(stderr, "%s\n", dlerror()); return 65; }
  auto run = reinterpret_cast<int (*)(const char*)>(dlsym(plugin, "AsahiSdkLabRun"));
  if (!run) { std::fprintf(stderr, "%s\n", dlerror()); return 66; }
  const int result = run(mode);
  // Leave the plugin mapped until process teardown: cvar registry holds its pointers.
  rex::ShutdownLogging();
  return result;
}
