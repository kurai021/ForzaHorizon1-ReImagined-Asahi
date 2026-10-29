#!/usr/bin/env python3
"""Controlled 720p MSAA trials using frozen existing binaries, not renderer changes."""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import statistics
import struct
import subprocess
import sys
import time
import zlib
from resource_guard import OwnedProcesses, memory_snapshot, memory_problem, START_FREE_MIB, ABORT_FREE_MIB

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "forza-recomp/out/asahi-sdk-lab"
WORK = ROOT / "forza-recomp/out/asahi-720p-msaa"
H0 = LAB / "logs/20261025T072911.720979Z-local-720p-performance-demo"
MODES = ("game", "2x", "off")
ORDER = (("game", "2x", "off"), ("2x", "off", "game"), ("off", "game", "2x"))
BINARIES = ("forza", "forza_XMediaFacade_default", "forza_SpeechFacade_default",
            "librexruntime.so", "librexgpu-xenos.so", "forza-telemetry.json")
BUTTONS = "3:A,7:START,11:A,14:A,18:A,22:A,40:A,60:A"
PROTOCOL = {"resolution": [1280, 720], "resolutionScale": 1, "frameRate": 30,
            "vsync": True, "validation": False, "asyncShaders": False,
            "measureAtSeconds": 80, "measureSeconds": 30, "boundaryGuardSeconds": .25,
            "capturesAtSeconds": [75, 115], "durationSeconds": 120,
            "minimumFreeMiB": START_FREE_MIB, "abortFreeMiB": ABORT_FREE_MIB, "cooldownSeconds": 45,
            "maxAvailableSensorCelsius": 75, "buttons": BUTTONS,
            "presentMetric": "existing presenter handoff callback, not physical display timing",
            "order": ORDER, "warmups": "one full identical no-validation session per mode"}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def copy_tree(source, destination):
    if destination.exists():
        raise RuntimeError(f"Refusing to replace {destination}")
    subprocess.run(["cp", "--reflink=auto", "-a", str(source), str(destination)], check=True)


def tree_hashes(directory):
    return {str(p.relative_to(directory)): sha(p) for p in sorted(directory.rglob("*")) if p.is_file()}


def snapshot():
    memory = memory_snapshot()
    sensors = []
    for device in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        try:
            name = (device / "name").read_text().strip()
            for path in sorted(device.glob("temp*_input")):
                label = path.with_name(path.name.replace("_input", "_label"))
                sensors.append({"device": name, "sensor": path.name,
                                "label": label.read_text().strip() if label.exists() else "",
                                "celsius": int(path.read_text()) / 1000})
        except (OSError, ValueError):
            continue
    cpu_gpu = [s for s in sensors if re.search(r"cpu|gpu|soc|package", s["label"], re.I)]
    return {**memory, "loadAverage": list(os.getloadavg()),
            "sensors": sensors, "cpuGpuTemperatureAvailable": bool(cpu_gpu),
            "cpuGpuTemperatures": cpu_gpu}


def preflight():
    if (LAB / "STOP-GPU.json").exists():
        raise RuntimeError("STOP-GPU.json is active; no GPU run permitted")
    if subprocess.run(["pgrep", "-x", "forza"], capture_output=True).returncode == 0:
        raise RuntimeError("Another forza process is active")
    previous = WORK / "last-run.json"
    if previous.exists():
        elapsed = time.time() - json.loads(previous.read_text())["finishedEpoch"]
        if elapsed < PROTOCOL["cooldownSeconds"]:
            time.sleep(PROTOCOL["cooldownSeconds"] - elapsed)
    sample = snapshot()
    recent = subprocess.run(["journalctl", "-k", "-b", "--since", "3 minutes ago", "-p", "err",
                             "--no-pager", "-o", "short-iso"], capture_output=True, text=True, timeout=15)
    sample["recentKernelErrors"] = recent.stdout.strip()
    if recent.returncode != 0 or sample["recentKernelErrors"] not in ("", "-- No entries --"):
        raise RuntimeError("Recent kernel errors or unreadable stability log; postpone GPU trial")
    if sample["availableMiB"] < PROTOCOL["minimumFreeMiB"]:
        raise RuntimeError(f"Insufficient free memory for comparable run: {sample['availableMiB']:.0f} MiB")
    if memory_problem(sample, sample):
        raise RuntimeError("Memory pressure before trial; postpone GPU work")
    if any(s["celsius"] >= PROTOCOL["maxAvailableSensorCelsius"] for s in sample["sensors"]):
        raise RuntimeError("Available temperature sensor reached the trial limit")
    return sample


def freeze():
    if WORK.exists():
        raise RuntimeError("An experiment already exists; preserve it instead of reinitializing")
    WORK.mkdir(parents=True)
    seed = WORK / "seed"
    seed.mkdir()
    copy_tree(H0, seed / "H0")
    (seed / "bin").mkdir()
    for name in BINARIES:
        subprocess.run(["cp", "--reflink=auto", "-a", str(LAB / "game-base" / name),
                        str(seed / "bin" / name)], check=True)
    profile = LAB / "profile-hsio"
    shutil.copytree(profile, seed / "profile", ignore=shutil.ignore_patterns("cache", "*.log", "*.csv"))
    copy_tree(profile / "cache", seed / "cache")
    if (LAB / "cache/mesa").exists():
        copy_tree(LAB / "cache/mesa", seed / "mesa")
    else:
        (seed / "mesa").mkdir()
    binaries = {name: sha(LAB / "game-base" / name) for name in BINARIES}
    manifest = {"baseCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "protocol": PROTOCOL, "binaries": binaries,
                "h0": tree_hashes(H0), "profileSeed": tree_hashes(seed / "profile"),
                "cacheSeed": tree_hashes(seed / "cache"),
                "sdkLockSha256": sha(Path(__file__).with_name("sdk.lock.json")),
                "sdkPatches": {p.name: sha(p) for p in Path(__file__).parent.glob("*.patch")},
                "gameData": "extracted/linux-test-game; same content as H0, shortened splash movies",
                "cpuGpuTemperature": "Unavailable unless explicitly exposed by existing hwmon labels"}
    write(WORK / "baseline.json", manifest)
    write(WORK / "protocol.json", PROTOCOL)
    print(f"Frozen H0, {len(binaries)} runtime files and independent profile/cache seeds in {WORK.relative_to(ROOT)}")


def verify_baseline():
    manifest = json.loads((WORK / "baseline.json").read_text())
    for name, expected in manifest["binaries"].items():
        for path in (LAB / "game-base" / name, WORK / "seed/bin" / name):
            if sha(path) != expected:
                raise RuntimeError(f"Frozen or functional runtime changed: {name}")
    if tree_hashes(H0) != manifest["h0"]:
        raise RuntimeError("Historical H0 changed")
    if tree_hashes(WORK / "seed/profile") != manifest["profileSeed"]:
        raise RuntimeError("Profile seed changed")
    if tree_hashes(WORK / "seed/cache") != manifest["cacheSeed"]:
        raise RuntimeError("Cache seed changed")
    if sha(Path(__file__).with_name("sdk.lock.json")) != manifest["sdkLockSha256"]:
        raise RuntimeError("SDK lock changed")
    for name, expected in manifest["sdkPatches"].items():
        if sha(Path(__file__).parent / name) != expected:
            raise RuntimeError(f"SDK patch changed: {name}")


def read_csv(path):
    if not path.exists():
        return [], 0
    with path.open() as stream:
        raw = list(csv.DictReader(stream))
    complete = [r for r in raw if None not in r and all(v is not None and v != "" for v in r.values())]
    return complete, len(raw) - len(complete)


def statistics_ms(values):
    if not values:
        return {"frames": 0}
    values = sorted(values)
    percentile = lambda p: values[max(0, math.ceil(p * len(values)) - 1)]
    return {"frames": len(values), "fps": 1000 * len(values) / sum(values),
            "meanMs": statistics.mean(values), "p50Ms": percentile(.5),
            "p95Ms": percentile(.95), "p99Ms": percentile(.99), "maxMs": values[-1],
            "above40MsPercent": 100 * sum(v > 40 for v in values) / len(values),
            "atMost35MsPercent": 100 * sum(v <= 35 for v in values) / len(values)}


def png_from_ppm(path):
    header, dimensions, maximum, pixels = path.read_bytes().split(b"\n", 3)
    if header != b"P6" or maximum != b"255":
        raise RuntimeError("Unexpected host capture format")
    width, height = map(int, dimensions.split())
    if len(pixels) != width * height * 3:
        raise RuntimeError("Incomplete host capture")
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    scanlines = b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    path.with_suffix(".png").write_bytes(b"\x89PNG\r\n\x1a\n" +
        chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
        chunk(b"IDAT", zlib.compress(scanlines, 3)) + chunk(b"IEND", b""))
    return [width, height]


def summarize_run(directory):
    events = json.loads((directory / "events.json").read_text())
    protocol = events.get("protocol", PROTOCOL)
    log = (directory / "guest.log").read_text(errors="replace")
    rows, truncated = read_csv(directory / "frame-cost.csv")
    guard = protocol["boundaryGuardSeconds"]
    begin, end = events["measureStartEpoch"] + guard, events["measureEndEpoch"] - guard
    selected = [r for r in rows if begin <= float(r["epoch"]) < end]
    summary = {"mode": events["mode"], "kind": events["kind"], "round": events["round"],
               "frameCost": statistics_ms([float(r["ms"]) for r in selected]),
               "completeCostRows": len(rows), "incompleteCostRows": truncated,
               "counters": {key: statistics.mean(float(r[key]) for r in selected) for key in
                            ("cpu_ms", "gpu_wait_ms", "guest_wait_ms", "draws", "pipeline_ms", "shaders", "pipelines")}
               if selected else {}, "perfStreams": {}, "captures": {}}
    perf, incomplete = read_csv(directory / "perf.csv")
    summary["incompletePerfRows"] = incomplete
    for source in ("guest", "present"):
        stream = [r for r in perf if r["scene"] == "msaa-measure" and r["src"] == source]
        if stream:
            first, last = float(stream[0]["t"]), float(stream[-1]["t"])
            summary["perfStreams"][source] = statistics_ms([float(r["ms"]) for r in stream
                                                          if first + guard <= float(r["t"]) < last - guard])
    for path in sorted((directory / "shots").glob("*.ppm")):
        summary["captures"][path.name] = png_from_ppm(path)
    summary["sceneOverride"] = re.findall(r"scene_msaa: render scenario tiling (\d+) instead of (\d+)", log)
    summary["warmupCache"] = re.findall(r"Vulkan driver pipeline cache: .*", log)
    summary["swapchains"] = re.findall(r"VulkanPresenter: Created (\d+x\d+) swapchain", log)
    reasons = []
    if not selected:
        reasons.append("no measured cost frames")
    elif sum(int(r["pipelines"]) + int(r["shaders"]) for r in selected):
        reasons.append("shader/pipeline creation inside measured window")
    if not summary["perfStreams"].get("guest", {}).get("frames"):
        reasons.append("guest stream missing")
    if not summary["captures"] or any(size != [1280, 720] for size in summary["captures"].values()):
        reasons.append("native 1280x720 host output not established")
    expected = {"2x": ("1", "0"), "off": ("2", "0")}.get(events["mode"])
    if expected and list(expected) not in [list(item) for item in summary["sceneOverride"]]:
        reasons.append("requested MSAA scenario override not observed")
    if events["aborted"]:
        reasons.append("run resource/readiness abort")
    if events.get("processCleanup", {}).get("remainingPids"):
        reasons.append("test process could not be terminated")
    result = json.loads((Path(events["supervisorDirectory"]) / "result.json").read_text())
    if result["returncode"] != 0 or result.get("kernelError") or not result.get("kernelReadable"):
        reasons.append("supervisor or kernel stability failure")
    if result["validationErrorLines"]:
        reasons.append("Vulkan error")
    summary["invalidReasons"] = reasons
    summary["valid"] = not reasons
    write(directory / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    return summary


def promote_cache(directory):
    events = json.loads((directory / "events.json").read_text())
    result = json.loads((Path(events["supervisorDirectory"]) / "result.json").read_text())
    if events["aborted"] or result["returncode"] != 0 or result.get("kernelError"):
        raise RuntimeError("An unsuccessful trial cannot seed a cache")
    mode = events["mode"]
    generation = WORK / "cache-generations" / mode / directory.name
    generation.mkdir(parents=True)
    copy_tree(directory / "cache", generation / "cache")
    copy_tree(directory / "mesa", generation / "mesa")
    write(WORK / "ready" / mode / "current.json", {"source": str(generation.relative_to(WORK)),
          "fromRun": directory.name, "cacheHashes": tree_hashes(generation / "cache")})
    print(f"Cache warmed from retained trial: {directory.name}")


def run_trial(mode, kind, round_number, attempt=1, stationary=False):
    verify_baseline()
    before = preflight()
    name = f"{kind}-{mode}-r{round_number}"
    if attempt > 1:
        name += f"-a{attempt}"
    series = WORK / "stationary-v3" if stationary else WORK
    protocol = dict(PROTOCOL)
    if stationary:
        protocol.update(scenario="stationary initial world, handbrake A held from 55s; no accelerator or reverse trigger",
                        buttons=BUTTONS + ",55:A:75", sleepInhibition="session-scoped systemd-inhibit sleep:idle and SDL idle inhibition",
                        version=3, reference="H0, driving v1 and rejected reverse-trigger v2 retained separately; not a driving benchmark")
        if not (series / "protocol.json").exists():
            write(series / "protocol.json", protocol)
    directory = series / "runs" / name
    if directory.exists():
        raise RuntimeError(f"Existing trial is immutable: {name}")
    directory.mkdir(parents=True)
    seed = WORK / "seed"
    copy_tree(seed / "profile", directory / "profile")
    source = seed if kind == "warm" else WORK / "ready" / mode
    if kind == "measure" and (source / "current.json").exists():
        source = WORK / json.loads((source / "current.json").read_text())["source"]
    copy_tree(source / "cache", directory / "cache")
    copy_tree(source / "mesa", directory / "mesa")
    (directory / "shots").mkdir()
    fifo = directory / "input.fifo"
    os.mkfifo(fifo)
    fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    write(directory / "preflight.json", before)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FORZA_", "RECOMP_PERF"))}
    env["SDL_VIDEO_ALLOW_SCREENSAVER"] = "0"
    command = [sys.executable, "-B", str(ROOT / "tools/asahi_sdk_lab.py"),
               "--name", "msaa-" + name, "--seconds", "155", "--gpu", "--cwd", str(seed / "bin"), "--",
               "env", f"XDG_DATA_HOME={directory / 'profile/host-data'}",
               f"XDG_CACHE_HOME={directory / 'host-cache'}", f"MESA_SHADER_CACHE_DIR={directory / 'mesa'}",
               "FORZA_SERVER=", "FORZA_LIVE_SIGNIN=0", "FORZA_FPSLOG=1", f"FORZA_AUTOPRESS={protocol['buttons']}",
               f"FORZA_AUTOPRESS_FIFO={fifo}", f"FORZA_FRAMEDUMP_DIR={directory / 'shots'}",
               f"FORZA_SCREENSHOT_TRIGGER={directory / 'shot.trigger'}", "RECOMP_PERF=1",
               f"RECOMP_PERF_CSV={directory / 'perf.csv'}", "RECOMP_PERF_SCENE=startup",
               "VK_LAYER_VALIDATE_SYNC=0", "timeout", "--preserve-status", "--signal=INT", "--kill-after=10", "120",
               str(seed / "bin/forza"), f"--game_data_root={ROOT / 'extracted/linux-test-game'}",
               f"--user_data_root={directory / 'profile'}", f"--cache_root={directory / 'cache'}",
               f"--log_file={directory / 'guest.log'}", "--live_signin=false", "--resolution=720p",
               "--resolution_scale=1", "--frame_rate=30", f"--scene_msaa={mode}",
               "--vulkan_validation_enabled=false", "--vulkan_require_vertex_pipeline_stores_and_atomics=false",
               "--vulkan_memexport_compute=true", "--async_shader_compilation=false",
               f"--fh1_frame_cost_csv={directory / 'frame-cost.csv'}", "--forza_performance_overlay=fps"]
    if stationary:
        command = ["systemd-inhibit", "--what=sleep:idle", "--mode=block", "--who=Asahi-MSAA",
                   "--why=Controlled 720p MSAA trial", *command]
    events = {"mode": mode, "kind": kind, "round": round_number, "command": command, "protocol": protocol,
              "attempt": attempt, "cacheSource": str(source.relative_to(WORK)),
              "harnessSha256": sha(Path(__file__)), "aborted": False, "captures": []}
    write(directory / "command.json", command)
    old_logs = set((LAB / "logs").iterdir())
    with (directory / "supervisor.log").open("w") as output:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    owned = OwnedProcesses(process)
    start = time.monotonic()
    suspend_offset = time.clock_gettime(time.CLOCK_BOOTTIME) - start
    samples, owned_mappings, game_pid = [], {}, None
    sent = set()
    try:
        while process.poll() is None:
            owned.capture()
            elapsed = time.monotonic() - start
            sample = snapshot()
            sample["suspendOffsetSeconds"] = time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic() - suspend_offset
            samples.append(sample)
            matches = set((LAB / "logs").iterdir()) - old_logs
            matching = [p for p in matches if p.name.endswith("-msaa-" + name)]
            if matching:
                events["supervisorDirectory"] = str(matching[0])
            pids = subprocess.run(["pgrep", "-x", "forza"], capture_output=True, text=True).stdout.split()
            for candidate in pids:
                try:
                    arguments = Path(f"/proc/{candidate}/cmdline").read_bytes().split(b"\0")
                    if f"--log_file={directory / 'guest.log'}".encode() in arguments:
                        game_pid = int(candidate)
                        owned.adopt(game_pid)
                        mappings = Path(f"/proc/{candidate}/maps").read_text()
                        for name_match in re.findall(r"/dev/shm/xenia_memory_\d+", mappings):
                            path = Path(name_match)
                            if path.exists():
                                owned_mappings[str(path)] = path.stat().st_ino
                except (FileNotFoundError, ProcessLookupError):
                    pass
            memory_error = memory_problem(sample, before, PROTOCOL["abortFreeMiB"])
            bad_memory = bool(memory_error)
            bad_temperature = any(s["celsius"] >= PROTOCOL["maxAvailableSensorCelsius"] for s in sample["sensors"])
            if bad_memory or bad_temperature or sample["suspendOffsetSeconds"] > .5:
                events["aborted"] = True
                events["resourceAbortReason"] = memory_error or "temperature/suspend limit"
                owned.stop()
                break
            if elapsed >= 75 and "before" not in sent:
                (directory / "shot.trigger").touch()
                events["captures"].append({"phase": "before", "epoch": time.time()})
                sent.add("before")
            if elapsed >= 80 and "measure" not in sent:
                text = (directory / "guest.log").read_text(errors="replace") if (directory / "guest.log").exists() else ""
                if "Wheel: driving" not in text or not list((directory / "shots").glob("*.ppm")):
                    events["aborted"] = True
                    if game_pid:
                        os.kill(game_pid, signal.SIGINT)
                events["measureStartEpoch"] = time.time()
                os.write(fd, b"SCENE:msaa-measure\n" if stationary else b"SCENE:msaa-measure\nRT:30\n")
                sent.add("measure")
            if elapsed >= 110 and "end" not in sent:
                events["measureEndEpoch"] = time.time()
                os.write(fd, b"SCENE:after-measure\n")
                sent.add("end")
            if elapsed >= 115 and "after" not in sent:
                (directory / "shot.trigger").touch()
                events["captures"].append({"phase": "after", "epoch": time.time()})
                sent.add("after")
            time.sleep(1)
    except BaseException as error:
        events["harnessError"] = repr(error)
        events["aborted"] = True
        raise
    finally:
        events["processCleanup"] = owned.finish()
        if events["processCleanup"]["remainingPids"]:
            events["aborted"] = True
        if events["aborted"] or events["processCleanup"]["remainingPids"]:
            marker = LAB / "STOP-GPU.json"
            if not marker.exists():
                write(marker, {"reason": events.get("resourceAbortReason", events.get("harnessError", "trial abort")),
                               "trial": str(directory), "processCleanup": events["processCleanup"]})
        write(directory / "process-cleanup.json", events["processCleanup"])
        write(directory / "events.json", events)
        os.close(fd)
    events["finishedEpoch"] = time.time()
    events["exitCode"] = process.poll()
    events["memoryAfter"] = memory_snapshot()
    events["minimumAvailableMiB"] = min(s["availableMiB"] for s in [before, *samples, events["memoryAfter"]])
    events["ownedMappingsRemoved"] = []
    if game_pid and not Path(f"/proc/{game_pid}").exists():
        for name_match, inode in owned_mappings.items():
            path = Path(name_match)
            if path.exists() and path.stat().st_ino == inode and path.stat().st_uid == os.getuid():
                path.unlink()
                events["ownedMappingsRemoved"].append(name_match)
    write(directory / "events.json", events)
    write(directory / "resources.json", samples)
    write(WORK / "last-run.json", {"finishedEpoch": events["finishedEpoch"]})
    if "measureStartEpoch" not in events or "measureEndEpoch" not in events:
        raise RuntimeError(f"Trial ended before the measurement window; inspect {directory}")
    summary = summarize_run(directory)
    if kind == "warm" and not events["aborted"] and events["exitCode"] == 0 and not (LAB / "STOP-GPU.json").exists():
        ready = WORK / "ready" / mode
        ready.mkdir(parents=True)
        copy_tree(directory / "cache", ready / "cache")
        copy_tree(directory / "mesa", ready / "mesa")
        write(ready / "from-run.json", {"run": str(directory.relative_to(ROOT)), "cache": tree_hashes(ready / "cache")})
    return summary


def make_report():
    verify_baseline()
    observations = []
    for directory in sorted((WORK / "runs").glob("measure-*")):
        summary = json.loads((directory / "summary.json").read_text())
        events = json.loads((directory / "events.json").read_text())
        result = json.loads((Path(events["supervisorDirectory"]) / "result.json").read_text())
        samples = json.loads((directory / "resources.json").read_text())
        observations.append({"run": directory.name, "mode": summary["mode"], "round": summary["round"],
            "attempt": events.get("attempt", 1), "frameCost": summary["frameCost"],
            "presentProxy": summary["perfStreams"].get("present"), "counters": summary["counters"],
            "technicalValid": summary["valid"], "invalidReasons": summary["invalidReasons"],
            "kernelError": result.get("kernelError"), "returncode": result["returncode"],
            "availableMiBBefore": json.loads((directory / "preflight.json").read_text())["availableMiB"],
            "minimumAvailableMiB": events["minimumAvailableMiB"],
            "cpuGpuTemperatureAvailable": any(s["cpuGpuTemperatureAvailable"] for s in samples),
            "cacheSource": events.get("cacheSource", "initial mode warmup cache"),
            "harnessSha256": events["harnessSha256"]})
    planned = []
    for round_number, modes in enumerate(ORDER, 1):
        for mode in modes:
            run = next((o for o in observations if o["mode"] == mode and o["round"] == round_number and o["attempt"] == 1), None)
            planned.append({"sequence": len(planned) + 1, "mode": mode, "round": round_number,
                            "observation": run, "status": "executed" if run else "blocked by STOP-GPU"})
    dispersion = {}
    for mode in MODES:
        values = [o["frameCost"]["fps"] for o in observations if o["mode"] == mode and not o["kernelError"]]
        dispersion[mode] = {"observationsIncludingRejectedRetries": len(values),
            "meanFPS": statistics.mean(values) if values else None,
            "minFPS": min(values) if values else None, "maxFPS": max(values) if values else None,
            "sampleStdDevFPS": statistics.stdev(values) if len(values) > 1 else None,
            "note": "Descriptive only: includes pipeline-contaminated retries, not three controlled repetitions"}
    report = {"scope": "Historical driving-v1 series only; stationary-v3 and manual sessions are separate", "protocol": json.loads((WORK / "protocol.json").read_text()),
        "h0Preserved": True, "functionalBinariesPreserved": True,
        "planned": planned, "observations": observations, "descriptiveDispersion": dispersion,
        "gpuSuspended": (LAB / "STOP-GPU.json").exists(),
        "causalMsaaComparisonValid": False,
        "limitations": ["Driving-v1 stopped after timeout/unmapped reads in measure-2x-r2; later sessions require separate review",
            "Five of nine planned trials not executed",
            "Residual shader/pipeline creation after warmup; rejected trials retained",
            "Identical input did not reproduce identical trajectory: collisions and final positions differ",
            "CPU/GPU temperatures not exposed by available hwmon sensors",
            "Present callback measures presenter handoff, not physical display timing",
            "RECOMP_PERF collection differs from historical H0; tail overhead not calibrated"]}
    write(WORK / "report.json", report)
    lines = ["# Historical driving-v1 MSAA report — incomplete", "",
        "This report covers driving-v1 only. Stationary-v3 and owner-driven sessions are separate. The functional executable and H0 hashes match the frozen reference.", "",
        "## Planned nine trials", "", "| # | Round | MSAA | FPS | p50 ms | p95 ms | p99 ms | State |", "|---|---|---|---:|---:|---:|---:|---|"]
    for entry in planned:
        o = entry["observation"]
        if o:
            s = o["frameCost"]
            state = "; ".join(o["invalidReasons"]) or "technical gates passed; route comparison not established"
            lines.append(f"| {entry['sequence']} | {entry['round']} | {entry['mode']} | {s['fps']:.2f} | {s['p50Ms']:.2f} | {s['p95Ms']:.2f} | {s['p99Ms']:.2f} | {state} |")
        else:
            lines.append(f"| {entry['sequence']} | {entry['round']} | {entry['mode']} | — | — | — | — | STOP-GPU: not executed |")
    lines += ["", "## Additional retained attempts", "", "| Run | FPS | p95 ms | p99 ms | Reason not accepted |", "|---|---:|---:|---:|---|"]
    for o in observations:
        if o["attempt"] > 1:
            s = o["frameCost"]
            lines.append(f"| {o['run']} | {s['fps']:.2f} | {s['p95Ms']:.2f} | {s['p99Ms']:.2f} | {'; '.join(o['invalidReasons'])} |")
    lines += ["", "## Descriptive dispersion (not a valid paired A/B result)", "",
              "| MSAA | n, including rejected retries | Mean FPS | Min–max FPS | Sample SD |", "|---|---:|---:|---:|---:|"]
    for mode, s in dispersion.items():
        if not s["observationsIncludingRejectedRetries"]:
            lines.append(f"| {mode} | 0 | — | — | not estimable |")
            continue
        sd = f"{s['sampleStdDevFPS']:.3f}" if s["sampleStdDevFPS"] is not None else "not estimable"
        lines.append(f"| {mode} | {s['observationsIncludingRejectedRetries']} | {s['meanFPS']:.2f} | {s['minFPS']:.2f}–{s['maxFPS']:.2f} | {sd} |")
    lines += ["", "## Interpretation", "",
        "Off produced the highest observed rates (~25 FPS), but none established stable 30 FPS. No definitive profile recommendation or causal speedup is justified by this incomplete, trajectory-divergent set.", "",
        "The pre-window 1280x720 captures show the same starting scenery with no obvious missing road, vegetation, barriers, car or HUD. Off has more edge aliasing. Post-window captures show different final locations and collisions: identical accelerator input was not deterministic route replay.", "",
        "CPU/GPU temperature is unavailable; the available board/storage sensors are recorded without claiming chip-temperature control. Resource floors were not crossed.", "",
        "## Stop condition", "",
        "The project supervisor recorded native Asahi GPU timeouts and unmapped reads around session termination, despite process exit 0. Cause and attribution are unproven. The gpuSuspended JSON field reflects the current marker; retain the separate review/archive history when resuming.", "",
        "## Limitations", "", *["- " + item for item in report["limitations"]], "",
        "## Next decision", "",
        "Review the GPU failure and authorize any restart separately. Before resuming a causal MSAA comparison, use a reproducible fixed-camera/static scenario or verified route replay; keep these failed/rejected attempts and H0 as history. Do not proceed to renderer optimization on these results."]
    (WORK / "REPORT.md").write_text("\n".join(lines) + "\n")
    visual = WORK / "visual"
    visual.mkdir(exist_ok=True)
    captures = []
    for mode in MODES:
        path = sorted((WORK / "runs" / f"warm-{mode}-r1/shots").glob("*.ppm"))[0]
        header, dims, maximum, pixels = path.read_bytes().split(b"\n", 3)
        width, height = map(int, dims.split())
        captures.append((width, height, pixels))
    width, height = captures[0][:2]
    combined = b"".join(b"".join(pixels[y * width * 3:(y + 1) * width * 3]
                                  for _, _, pixels in captures) for y in range(height))
    target = visual / "starting-scene-4x-2x-off.ppm"
    target.write_bytes(f"P6\n{width * 3} {height}\n255\n".encode() + combined)
    png_from_ppm(target)
    print(f"Retained {len(observations)} attempts; {sum(e['observation'] is not None for e in planned)}/9 planned trials executed; GPU suspended={report['gpuSuspended']}")
    print(f"Report: {WORK.relative_to(ROOT) / 'REPORT.md'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "preflight", "run", "summarize", "verify-baseline", "promote-cache", "report"))
    parser.add_argument("--mode", choices=MODES, default="game")
    parser.add_argument("--kind", choices=("warm", "measure"), default="measure")
    parser.add_argument("--round", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--stationary", action="store_true", help="Separate fixed-camera v3 series using the handbrake")
    parser.add_argument("--run", type=Path)
    args = parser.parse_args()
    if args.attempt < 1:
        parser.error("Attempt must be positive")
    if args.action in ("summarize", "promote-cache") and args.run is None:
        parser.error("This action requires --run")
    if args.action == "freeze":
        freeze()
    elif args.action == "preflight":
        print(json.dumps(preflight(), indent=2))
    elif args.action == "verify-baseline":
        verify_baseline()
        print("PASS: functional runtime and historical H0 remain byte-identical")
    elif args.action == "summarize":
        summarize_run(args.run)
    elif args.action == "promote-cache":
        promote_cache(args.run)
    elif args.action == "report":
        make_report()
    else:
        result = run_trial(args.mode, args.kind, args.round, args.attempt, args.stationary)
        return 0 if args.kind == "warm" or result["valid"] else 2


if __name__ == "__main__":
    sys.exit(main())
