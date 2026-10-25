#!/usr/bin/env python3
"""Build the isolated Asahi experiment; fail explicitly when commercial files are absent."""
import argparse
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "forza-recomp/out/asahi-sdk-lab"
SDK = LAB / "sdk"
PREFIX = LAB / "install"
GAME = ROOT / "extracted/game"
RUNNER = ROOT / "tools/asahi_sdk_lab.py"


def run(name, command, seconds=600, cwd=ROOT, gpu=False):
    invocation = [sys.executable, "-B", str(RUNNER), "--name", name,
                  "--seconds", str(seconds), "--cwd", str(cwd)]
    if gpu:
        invocation += ["--gpu", "--validation"]
    subprocess.run(invocation + ["--", *map(str, command)], check=True, cwd=ROOT,
                   timeout=seconds + 40)


def check_game():
    needed = [GAME / name for name in ("default.xex", "XMediaFacade_default.xex",
                                       "SpeechFacade_default.xex", "media/db/gamedb.slt")]
    missing = [path for path in needed if not path.is_file()]
    print("Commercial base-game files (no downloads are performed):", flush=True)
    for path in needed:
        print(f"  {'OK' if path.is_file() else 'MISSING'} {path}", flush=True)
    print("A real scene additionally requires the complete media/ tree from the same disc 1 extraction.", flush=True)
    if missing:
        print("BLOCKED: provide your own complete disc 1 extraction, ISO, or complete Games on Demand package.", flush=True)
        return False
    sys.path.insert(0, str(ROOT / "launcher"))
    from forza_launcher.installer import check_game_folder
    compatible, message = check_game_folder(GAME)
    print(f"Executable compatibility: {message}", flush=True)
    return compatible


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "sdk", "host", "check-game", "game", "play"))
    parser.add_argument("--jobs", type=int, default=3)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in ("aarch64", "arm64"):
        parser.error("Native Linux ARM64 is required")
    if not 1 <= args.jobs <= 4:
        parser.error("Use 1..4 jobs for this diagnostic build")
    if args.mode in ("check-game", "game", "play") and not check_game():
        return 2
    if args.mode == "check-game":
        return 0
    if args.mode in ("prepare", "sdk"):
        run("asahi-sdk-prepare", [sys.executable, "-B",
            ROOT / "tools/asahi-sdk-lab/sdk.py", "prepare"], 1200)
        if args.mode == "prepare":
            return 0
    if args.mode == "sdk":
        # Never run the canonical preparer over the existing patched checkout:
        # it would erase this experiment. Reproduction is documented separately.
        if not (SDK / ".git").exists():
            parser.error("Missing isolated patched SDK; see docs/asahi-port-phase2.md")
        head = subprocess.check_output(["git", "-C", str(SDK), "rev-parse", "HEAD"], text=True).strip()
        if head != "bd833a2ab69d305f99ddf57418fc38ffec8af93d":
            parser.error("Unexpected SDK revision; refusing to rebuild a different checkout")
        run("asahi-sdk-configure", ["cmake", "--preset", "linux-arm64",
            "-DCMAKE_CONFIGURATION_TYPES=Release", "-DCMAKE_DEFAULT_BUILD_TYPE=Release",
            f"-DCMAKE_INSTALL_PREFIX={PREFIX}", "-DCMAKE_INSTALL_LIBDIR=lib",
            "-DREXGLUE_ENABLE_TRACY=OFF", f"-DREX_ASAHI_LAB_SOURCE={ROOT / 'tools/asahi-sdk-lab'}"],
            300, SDK)
        run("asahi-sdk-install", ["cmake", "--build", SDK / "out/build/linux-arm64",
            "--config", "Release", "--target", "install", "--parallel", args.jobs], 1800)
        return 0
    if not (PREFIX / "bin/rexglue").is_file():
        parser.error("Missing local SDK install; build mode 'sdk' first")
    build = LAB / ("host-base" if args.mode == "host" else "game-base")
    if args.mode in ("host", "game"):
        source = ROOT / ("tools/cmake/linux-arm64-host-check" if args.mode == "host" else "forza-recomp")
        command = ["cmake", "-S", source, "-B", build, "-G", "Ninja",
                   "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_CXX_COMPILER=clang++",
                   f"-DCMAKE_PREFIX_PATH={PREFIX}", f"-Drexglue_DIR={PREFIX / 'lib/cmake/rexglue'}"]
        if args.mode == "game":
            command += [f"-DFORZA_REXGLUE={PREFIX / 'bin/rexglue'}",
                        f"-DFORZA_XENOS_PLUGIN={PREFIX / 'lib/librexgpu-xenos.so'}",
                        "-DFORZA_CPU_TARGET=baseline", "-DFORZA_TITLE_UPDATE=0"]
        run(f"asahi-{args.mode}-configure", command, 600)
        target = "forza_host_check" if args.mode == "host" else "forza"
        run(f"asahi-{args.mode}-build", ["cmake", "--build", build, "--target", target,
                                       "--parallel", args.jobs], 1800)
        if args.mode == "game":
            # Codegen emits DLL sources after ninja has loaded its graph.
            run("asahi-game-build-second-pass", ["cmake", "--build", build, "--parallel", args.jobs], 1800)
            # SDK changes need staging even when the game did not relink (as on macOS,
            # and as tools/package_linux.sh does for releases): the binary finds the
            # runtime through its $ORIGIN runpath.
            for lib in ("librexruntime.so", "librexgpu-xenos.so"):
                run(f"asahi-game-stage-{lib}", ["cmake", "-E", "copy_if_different",
                    PREFIX / "lib" / lib, build / lib], 60)
        return 0
    if (LAB / "STOP-GPU.json").exists():
        parser.error("GPU tests are suspended. Inspect STOP-GPU.json and system stability before any launch")
    if not (build / "forza").is_file():
        parser.error("Missing diagnostic game executable; use mode 'game' first")
    profile = LAB / "profile-base"
    profile.mkdir(exist_ok=True)
    try:
        run("asahi-game-720p30", ["env", f"XDG_DATA_HOME={profile / 'host-data'}",
            "FORZA_SERVER=", "FORZA_LIVE_SIGNIN=0", "FORZA_FPSLOG=1", "VK_LAYER_VALIDATE_SYNC=1",
            "timeout", "--preserve-status", "--signal=INT", "--kill-after=10", "180",
            build / "forza", f"--game_data_root={GAME}", f"--user_data_root={profile}",
            f"--cache_root={profile / 'cache'}", f"--log_file={profile / 'forza.log'}",
            "--live_signin=false", "--resolution=720p", "--resolution_scale=1", "--frame_rate=30",
            "--vulkan_validation_enabled=true", "--vulkan_require_vertex_pipeline_stores_and_atomics=false",
            "--vulkan_memexport_compute=true", "--async_shader_compilation=false",
            "--forza_performance_overlay=fps"], 240, build, gpu=True)
    except subprocess.CalledProcessError as error:
        # The bounded run ends the game with SIGINT/SIGKILL; the supervisor only
        # suspends on its own 124 timeout or on a crash signal.
        if error.returncode not in (130, 137):
            raise
        print(f"Bounded play run ended by timeout (exit {error.returncode})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
