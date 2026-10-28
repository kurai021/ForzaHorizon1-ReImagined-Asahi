#!/usr/bin/env python3
"""Bounded Asahi game probe with memory monitoring, frame costs and optional screenshots."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from resource_guard import OwnedProcesses, require_memory, memory_snapshot, memory_problem, ABORT_FREE_MIB
from cpu_sampler import CpuSampler, thread_snapshot
from profiling_tools import PERF_ROOT, perf_environment
from candidate_play import verified_runtime, session_flags, SurfaceLog, configure_owned_window, require_cooldown
from candidate_play import loaded_candidate_libraries
from candidate_play import window_api_preflight, abort_game_with_observation
from candidate_play import render_pass_reuse_flags
from candidate_play import readback_profile_flags
from prepare_drm_ioctl_profile import launch_environment
from resource_guard import process_identity

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "forza-recomp/out/asahi-sdk-lab"
PLAY_ROOT = ROOT / "forza-recomp/out/asahi-profiling/candidate-play"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--game-root", type=Path, default=ROOT / "extracted/game")
    parser.add_argument("--profile", type=Path, default=LAB / "profile-base")
    parser.add_argument("--validation", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--autopress", default="")
    parser.add_argument("--scene-msaa", choices=("game", "2x", "off"), default="game")
    parser.add_argument("--min-free-mib", type=int, default=ABORT_FREE_MIB)
    parser.add_argument("--cpu-profile", action="store_true", help="49 Hz user-space perf samples; no renderer changes")
    parser.add_argument("--profile-start", type=int, default=80)
    parser.add_argument("--profile-seconds", type=int, default=30)
    parser.add_argument("--post-exit-seconds", type=int, default=30)
    parser.add_argument("--candidate", type=Path, help="Hash-verified isolated candidate directory, never an install target")
    parser.add_argument("--physical-720p", action="store_true", help="Owned Hyprland window only; require actual 1280x720 swapchain")
    parser.add_argument("--submission-profile", action="store_true", help="Enable the candidate's bounded submission recorder")
    parser.add_argument("--drm-ioctl-probe", type=Path, help="Explicitly preload a hash-verified private probe into the game only")
    parser.add_argument("--render-pass-reuse", choices=("off", "on"), help="Explicit A/B mode for a candidate containing the optional cache-identity patch")
    parser.add_argument("--readback-profile", action="store_true", help="Optional resolve-reason recording, independent of DRM/perf and render-pass reuse")
    parser.add_argument("--plan-only", action="store_true", help="Print command without creating a profile or launching the game")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 180 or args.min_free_mib < ABORT_FREE_MIB:
        parser.error(f"Use 1..180 seconds and at least {ABORT_FREE_MIB} MiB for the physical-memory reserve")
    if not 30 <= args.post_exit_seconds <= 60:
        parser.error("Keep 30..60 seconds of post-exit kernel observation")
    if args.cpu_profile:
        if args.validation or not (1 <= args.profile_seconds <= 45 and
                                   25 <= args.profile_start and args.profile_start + args.profile_seconds + 10 <= args.seconds):
            parser.error("CPU profiling requires --no-validation and a 1..45 s window after 25 s, ending at least 10 s before close")
        if not (PERF_ROOT / "usr/bin/perf").is_file():
            parser.error("Prepare local perf with profiling_tools.py before starting a profiling session")
    if args.candidate and (not args.physical_720p or args.scene_msaa != "off"):
        parser.error("Candidate sessions require --physical-720p and --scene-msaa off")
    if args.submission_profile and (not args.candidate or args.cpu_profile or args.validation or
                                   args.profile_start + args.profile_seconds + 20 > args.seconds):
        parser.error("Submission capture requires a candidate, --no-validation, no simultaneous perf and at least 20 s after the window")
    if args.drm_ioctl_probe and (not args.submission_profile or os.environ.get("LD_PRELOAD")):
        parser.error("DRM probing requires --submission-profile and no ambient LD_PRELOAD; never mix interposers")
    if args.readback_profile and (args.validation or args.cpu_profile or args.drm_ioctl_probe or
                                 args.render_pass_reuse is not None or os.environ.get('LD_PRELOAD')):
        parser.error('Keep readback profiling independent: no validation/perf/DRM/reuse or ambient preload')
    if args.physical_720p and not shutil.which("hyprctl"):
        parser.error("Physical surface control currently requires Hyprland; do not change global display configuration")
    if args.physical_720p and args.seconds < 45:
        parser.error("Physical surface validation requires at least 45 s including setup")
    if not args.name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in args.name):
        parser.error("Use letters, digits, hyphens and underscores in the probe name")
    build = verified_runtime(args.candidate) if args.candidate else LAB / "game-base"
    try:
        reuse_flags = render_pass_reuse_flags(args.candidate, args.render_pass_reuse)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    if not (build / "forza").is_file() or not (args.game_root / "default.xex").is_file():
        parser.error("Build the game first and provide an existing local game extraction")
    if (LAB / "STOP-GPU.json").exists():
        parser.error("GPU trials remain suspended by STOP-GPU.json")
    if args.candidate or args.physical_720p:
        if subprocess.run(["pgrep", "-x", "forza"], capture_output=True, timeout=3).returncode == 0:
            parser.error("Another game is active; do not overlap GPU trials")
        require_cooldown(LAB / "logs")
    initial_memory = require_memory()
    if not shutil.which("systemd-inhibit"):
        parser.error("systemd-inhibit is required for a protected interactive session")
    if args.physical_720p and not args.plan_only:
        window_api_preflight()
    profile = args.profile.resolve()
    if args.candidate and not profile.is_relative_to(PLAY_ROOT):
        parser.error("Keep candidate session profiles under out/asahi-profiling/candidate-play/")
    if (args.candidate or args.physical_720p) and profile.exists() and any(profile.glob(args.name + ".*")):
        parser.error("Session outputs already exist; preserve them and use a fresh profile/name")
    if args.submission_profile and (profile / "submission.csv").exists():
        parser.error("A submission trace already exists; use a fresh profile")
    try:
        readback_flags = readback_profile_flags(args.candidate, profile, args.readback_profile,
                                                args.profile_start, args.profile_seconds, args.seconds)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    if args.readback_profile and (profile / 'readback.csv').exists():
        parser.error('A readback trace already exists; use a fresh profile')
    if not args.plan_only:
        profile.mkdir(parents=True, exist_ok=True)
    if args.cpu_profile and not args.plan_only:
        preflight = subprocess.run([str(PERF_ROOT / "usr/bin/perf"), "stat", "--event", "task-clock:u", "--",
            sys.executable, "-B", "-c", "sum(i*i for i in range(100000))"],
            env=perf_environment(), capture_output=True, text=True, timeout=10)
        (profile / (args.name + "-perf-preflight.json")).write_text(json.dumps({
            "returncode": preflight.returncode, "stdout": preflight.stdout, "stderr": preflight.stderr}, indent=2) + "\n")
        if preflight.returncode:
            parser.error("Local user-space perf preflight failed; no game was started")
    log = profile / (args.name + ".log")
    csv = profile / (args.name + ".csv")
    command = [sys.executable, "-B", str(ROOT / "tools/asahi_sdk_lab.py"),
               "--name", args.name, "--seconds", str(args.seconds + 35), "--gpu",
               "--post-exit-seconds", str(args.post_exit_seconds)]
    if args.validation:
        command += ["--validation"]
    command += ["--cwd", str(build), "--", "env", "-u", "FORZA_AUTOPRESS_FIFO",
                "-u", "FORZA_FRAMEDUMP_DIR", "-u", "FORZA_SCREENSHOT_TRIGGER",
                "-u", "RECOMP_PERF", "-u", "RECOMP_PERF_CSV", "-u", "FORZA_FRAMEDUMP_TRIGGER",
                f"XDG_DATA_HOME={profile / 'host-data'}",
                f"XDG_CACHE_HOME={profile / 'host-cache'}", f"MESA_SHADER_CACHE_DIR={profile / 'mesa'}",
                "SDL_VIDEO_ALLOW_SCREENSAVER=0",
                "FORZA_SERVER=", "FORZA_LIVE_SIGNIN=0", "FORZA_FPSLOG=1",
                f"FORZA_AUTOPRESS={args.autopress}", f"VK_LAYER_VALIDATE_SYNC={int(args.validation)}",
                "timeout", "--preserve-status", "--signal=INT", "--kill-after=10", str(args.seconds),
                str(build / "forza"), f"--game_data_root={args.game_root.resolve()}",
                f"--user_data_root={profile}", f"--cache_root={profile / 'cache'}", f"--log_file={log}",
                "--live_signin=false", "--resolution=720p", "--resolution_scale=1", "--frame_rate=30",
                f"--scene_msaa={args.scene_msaa}",
                f"--vulkan_validation_enabled={str(args.validation).lower()}",
                "--vulkan_require_vertex_pipeline_stores_and_atomics=false",
                "--vulkan_memexport_compute=true", "--async_shader_compilation=false",
                f"--fh1_frame_cost_csv={csv}", "--forza_performance_overlay=fps"]
    if args.candidate or args.physical_720p:
        command += session_flags(profile, args.physical_720p, args.submission_profile,
                                  args.profile_start, args.profile_seconds, instrumented=bool(args.candidate))
    command += reuse_flags
    command += readback_flags
    if args.candidate:
        libraries = str(build)
        if args.validation:
            libraries = str(LAB / "local-tools/usr/lib64") + ":" + libraries
        command.insert(command.index("SDL_VIDEO_ALLOW_SCREENSAVER=0"), "LD_LIBRARY_PATH=" + libraries)
    if args.drm_ioctl_probe:
        try:
            drm_env = launch_environment(args.drm_ioctl_probe, build / "forza", profile / "drm-ioctl.csv",
                                         args.profile_start, args.profile_seconds)
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            parser.error(str(error))
        # Scope the loader environment after timeout, to the game—not its supervisor/helpers.
        target = command.index(str(build / "forza"))
        command[target:target] = ["env", "-u", "ASAHI_DRM_IOCTL_OWNER_PID",
                                  *[key + "=" + value for key, value in drm_env.items()]]
    if args.plan_only:
        print(json.dumps({"command": list(map(str, command)), "gameLaunched": False,
                          "runtime": str(build), "physical720pRequired": args.physical_720p,
                          "submissionRecording": args.submission_profile,
                          "renderPassReuseRequested": args.render_pass_reuse,
                          "readbackRecording": args.readback_profile,
                          "swapGrowthGuardEnabled": bool(initial_memory.get("swapGrowthGuardEnabled", True)),
                          "memoryPressureGuardEnabled": bool(initial_memory.get("memoryPressureGuardEnabled", True)),
                          "drmIoctlProbeRequested": bool(args.drm_ioctl_probe)}, indent=2))
        return 0
    before = set((LAB / "logs").iterdir())
    process = subprocess.Popen(["systemd-inhibit", "--what=sleep:idle", "--mode=block",
                                "--who=Asahi-manual-session", "--why=Bounded manual driving trial", *command],
                               cwd=ROOT, start_new_session=True)
    owned = OwnedProcesses(process)
    start = time.monotonic()
    captured = set()
    minimum = None
    directory = None
    aborted = False
    reason = None
    sampler = None
    thread_log = None
    profile_complete = False
    surface = SurfaceLog(log) if args.physical_720p else None
    window_control = None
    surface_verified = False
    runtime_verified = False
    games = []
    abort_observation = None
    quiet_observers = args.cpu_profile or bool(args.candidate) or args.physical_720p or args.submission_profile
    try:
        while process.poll() is None:
            owned.capture()
            sample = memory_snapshot()
            available = sample["availableMiB"] * 1024
            minimum = available if minimum is None else min(minimum, available)
            directories = [d for d in set((LAB / "logs").iterdir()) - before if d.name.endswith("-" + args.name)]
            if directories:
                directory = directories[0]
            candidates = subprocess.run(["pgrep", "-x", "forza"], capture_output=True, text=True).stdout.split()
            games = []
            for pid in candidates:
                try:
                    if f"--log_file={log}".encode() in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0"):
                        games.append(pid)
                        owned.adopt(int(pid))
                except FileNotFoundError:
                    pass
            reason = memory_problem(sample, initial_memory, args.min_free_mib)
            if reason:
                aborted = True
                abort_observation = abort_game_with_observation(process, owned, games)
                break
            elapsed = time.monotonic() - start
            if surface is not None:
                if window_control is None and len(games) == 1 and elapsed < 25:
                    game_pid = int(games[0])
                    window_control = configure_owned_window(game_pid, process_identity(game_pid))
                surface.tick()
                if elapsed >= 30:
                    if window_control is None or surface.extent != (1280, 720) or any(e != (1280, 720) for e in surface.new_extents):
                        raise RuntimeError(f"Physical 1280x720 surface not verified: {surface.extent}")
                    surface_verified = True
                    if args.candidate and not runtime_verified:
                        if len(games) != 1:
                            raise RuntimeError("Expected one owned game for candidate mapping verification")
                        runtime_verified = loaded_candidate_libraries(Path(f"/proc/{games[0]}/maps").read_text(), build)
                        if not runtime_verified:
                            raise RuntimeError("Candidate runtime and plugin are not both mapped in the game")
            if elapsed > args.seconds + args.post_exit_seconds + 75:
                raise TimeoutError("Game probe exceeded command, observation and cleanup limits")
            if args.cpu_profile and elapsed >= args.profile_start and sampler is None:
                if len(games) != 1 or directory is None:
                    raise RuntimeError("Exactly one owned game must be present at the sampling boundary")
                sampler = CpuSampler(int(games[0]), directory, seconds=args.profile_seconds)
                thread_log = (directory / "thread-cpu.jsonl").open("x")
                print("CPU sampling started for the bounded driving window", flush=True)
            if sampler is not None and not sampler.finished:
                sampler.tick()
                thread_log.write(json.dumps({"epochNs": time.time_ns(), "monoNs": time.monotonic_ns(),
                    "clockTicksPerSecond": os.sysconf("SC_CLK_TCK"),
                    "threads": thread_snapshot(sampler.pid, sampler.identity)}) + "\n")
                if sampler.completed:
                    profile_complete = sampler.finish()["completed"]
                    thread_log.close()
                    print("CPU sampling finished; normal driving may continue", flush=True)
            if games and directory:
                for second in (12, 30, 60, 85, 115, 145, 175):
                    if elapsed > second and second not in captured:
                        # Keep screenshots/ptrace stack stops out of profiling runs.
                        if not quiet_observers and shutil.which("grim"):
                            subprocess.run(["grim", str(directory / f"artifacts/screen-{second}s.png")], timeout=5, check=False)
                        if not quiet_observers and second == 12 and shutil.which("eu-stack"):
                            with (directory / "cpu-stacks.log").open("w") as output:
                                try:
                                    subprocess.run(["eu-stack", "-p", games[0]], stdout=output,
                                                   stderr=subprocess.STDOUT, timeout=8, check=False)
                                except subprocess.TimeoutExpired:
                                    pass
                        captured.add(second)
            time.sleep(1)
    except BaseException as error:
        aborted = True
        reason = repr(error)
        if not isinstance(error, KeyboardInterrupt):
            try:
                abort_observation = abort_game_with_observation(process, owned, games)
            except Exception as observation_error:
                abort_observation = {"supervisorCompleted": False, "error": repr(observation_error)}
        raise
    finally:
        if sampler is not None:
            try:
                profile_complete = sampler.finish()["completed"]
            except Exception as error:
                aborted = True
                reason = reason or f"CPU sampler finalization failed: {error!r}"
            finally:
                if thread_log is not None:
                    thread_log.close()
        cleanup = owned.finish()
        if args.cpu_profile and not profile_complete:
            aborted = True
            reason = reason or "CPU recording did not complete"
        if cleanup["remainingPids"]:
            aborted = True
            reason = reason or "test processes remain active after cleanup"
        if surface is not None:
            try:
                surface.tick()
                if not surface_verified or surface.extent != (1280, 720) or any(e != (1280, 720) for e in surface.new_extents):
                    aborted = True
                    reason = reason or "Physical 1280x720 swapchain was not verified through the session"
            except Exception as error:
                aborted = True
                reason = reason or f"Surface monitor failed: {error!r}"
            surface_record = {"windowControl": window_control, "lastExtent": surface.extent,
                              "swapchainEvents": surface.events, "verifiedDuringSession": surface_verified,
                              "candidateLibrariesVerified": runtime_verified,
                              "abortObservation": abort_observation,
                              "finalExtentAccepted": surface.extent == (1280, 720), "sessionAborted": aborted}
            (profile / (args.name + ".surface.json")).write_text(json.dumps(surface_record, indent=2) + "\n")
            if directory:
                (directory / "physical-surface.json").write_text(json.dumps(surface_record, indent=2) + "\n")
        if aborted or cleanup["remainingPids"]:
            marker = LAB / "STOP-GPU.json"
            if not marker.exists():
                marker.write_text(json.dumps({"reason": reason or "test descendants remain active",
                                             "processCleanup": cleanup}, indent=2) + "\n")
        if directory:
            (directory / "process-cleanup.json").write_text(json.dumps(cleanup, indent=2) + "\n")
    if directory:
        if csv.is_file():
            shutil.copyfile(csv, directory / "artifacts/frame-cost.csv")
        if args.drm_ioctl_probe and (profile / "drm-ioctl.csv").is_file():
            shutil.copyfile(profile / "drm-ioctl.csv", directory / "artifacts/drm-ioctl.csv")
        (directory / "memory-monitor.json").write_text(json.dumps({
            "minimumAvailableKiB": minimum, "abortFloorMiB": args.min_free_mib,
            "memoryAborted": aborted, "abortReason": reason, "memoryBefore": initial_memory,
            "cpuProfileRequested": args.cpu_profile, "cpuProfileComplete": profile_complete,
            "drmIoctlProbeRequested": bool(args.drm_ioctl_probe),
            "renderPassReuseRequested": args.render_pass_reuse,
            "memoryAfter": memory_snapshot(), "processCleanup": cleanup}, indent=2) + "\n")
    print(f"Minimum MemAvailable: {(minimum or 0) / 1024**2:.2f} GiB; memory abort: {aborted}")
    return process.returncode or (2 if aborted else 0)


if __name__ == "__main__":
    sys.exit(main())
