#!/usr/bin/env python3
"""Bounded Asahi game probe with memory monitoring, frame costs and optional screenshots."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "forza-recomp/out/asahi-sdk-lab"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--game-root", type=Path, default=ROOT / "extracted/game")
    parser.add_argument("--profile", type=Path, default=LAB / "profile-base")
    parser.add_argument("--validation", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--autopress", default="")
    parser.add_argument("--min-free-mib", type=int, default=400)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 180 or args.min_free_mib < 400:
        parser.error("Use 1..180 seconds and at least 400 MiB for the emergency memory floor")
    if not args.name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in args.name):
        parser.error("Use letters, digits, hyphens and underscores in the probe name")
    build = LAB / "game-base"
    if not (build / "forza").is_file() or not (args.game_root / "default.xex").is_file():
        parser.error("Build the game first and provide an existing local game extraction")
    profile = args.profile.resolve()
    profile.mkdir(parents=True, exist_ok=True)
    log = profile / (args.name + ".log")
    csv = profile / (args.name + ".csv")
    command = [sys.executable, "-B", str(ROOT / "tools/asahi_sdk_lab.py"),
               "--name", args.name, "--seconds", str(args.seconds + 35), "--gpu"]
    if args.validation:
        command += ["--validation"]
    command += ["--cwd", str(build), "--", "env", f"XDG_DATA_HOME={profile / 'host-data'}",
                "FORZA_SERVER=", "FORZA_LIVE_SIGNIN=0", "FORZA_FPSLOG=1",
                f"FORZA_AUTOPRESS={args.autopress}", f"VK_LAYER_VALIDATE_SYNC={int(args.validation)}",
                "timeout", "--preserve-status", "--signal=INT", "--kill-after=10", str(args.seconds),
                str(build / "forza"), f"--game_data_root={args.game_root.resolve()}",
                f"--user_data_root={profile}", f"--cache_root={profile / 'cache'}", f"--log_file={log}",
                "--live_signin=false", "--resolution=720p", "--resolution_scale=1", "--frame_rate=30",
                f"--vulkan_validation_enabled={str(args.validation).lower()}",
                "--vulkan_require_vertex_pipeline_stores_and_atomics=false",
                "--vulkan_memexport_compute=true", "--async_shader_compilation=false",
                f"--fh1_frame_cost_csv={csv}", "--forza_performance_overlay=fps"]
    before = set((LAB / "logs").iterdir())
    process = subprocess.Popen(command, cwd=ROOT)
    start = time.monotonic()
    captured = set()
    minimum = None
    directory = None
    aborted = False
    while process.poll() is None:
        available = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                             if line.startswith("MemAvailable:")))
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
            except FileNotFoundError:
                pass
        if available < args.min_free_mib * 1024:
            for pid in games:
                try:
                    os.kill(int(pid), signal.SIGINT)
                    aborted = True
                except ProcessLookupError:
                    pass
        elapsed = time.monotonic() - start
        if games and directory:
            for second in (12, 30, 60, 85, 115, 145, 175):
                if elapsed > second and second not in captured:
                    if shutil.which("grim"):
                        subprocess.run(["grim", str(directory / f"artifacts/screen-{second}s.png")], timeout=5, check=False)
                    if second == 12 and shutil.which("eu-stack"):
                        with (directory / "cpu-stacks.log").open("w") as output:
                            try:
                                subprocess.run(["eu-stack", "-p", games[0]], stdout=output,
                                               stderr=subprocess.STDOUT, timeout=8, check=False)
                            except subprocess.TimeoutExpired:
                                pass
                    captured.add(second)
        time.sleep(1)
    if directory:
        if csv.is_file():
            shutil.copyfile(csv, directory / "artifacts/frame-cost.csv")
        (directory / "memory-monitor.json").write_text(json.dumps({
            "minimumAvailableKiB": minimum, "abortFloorMiB": args.min_free_mib,
            "memoryAborted": aborted}, indent=2) + "\n")
    print(f"Minimum MemAvailable: {minimum / 1024**2:.2f} GiB; memory abort: {aborted}")
    return process.returncode or (2 if aborted else 0)


if __name__ == "__main__":
    sys.exit(main())
