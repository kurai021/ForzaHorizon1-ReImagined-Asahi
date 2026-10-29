// Separate probe executable only: preserve the candidate and game unchanged.
// main.cpp is frozen beside this file from the validated candidate's lab inputs.
#define main AsahiSdkProbeMain
#include "main.cpp"
#undef main

#include <cerrno>
#include <cstring>
#include <ctime>

int main(int argc, char** argv) {
  if (argc < 2 || (std::strcmp(argv[1], "offline") && std::strcmp(argv[1], "integration"))) return 64;
  const int result = AsahiSdkProbeMain(argc, argv);
  if (result) return result;
  // All existing correctness checks and GPU teardown have finished. Keep only
  // this process alive for the bounded deferred exporter; no extra GPU workload.
  timespec remaining{3, 0};
  while (nanosleep(&remaining, &remaining)) if (errno != EINTR) return 70;
  std::puts("LAB DRM gate: original probe passed; 3 s exporter grace completed");
  return 0;
}
