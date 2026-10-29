#!/usr/bin/env python3
"""Bounded, logged local SDK experiments; no system graphics configuration changes."""
import argparse
import ctypes as c
import datetime as dt
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "forza-recomp/out/asahi-sdk-lab"
ICD = Path("/usr/share/vulkan/icd.d/asahi_icd.aarch64.json")
sys.path.insert(0, str(ROOT / "tools/asahi-sdk-lab"))
from resource_guard import OwnedProcesses, memory_snapshot, memory_problem, require_memory


def read_kernel(since):
    """Bounded journal reads; unreadable diagnostics fail closed without GPU work."""
    try:
        kernel = subprocess.run(["journalctl", "-k", "-b", "--since", since, "--no-pager", "-o", "short-iso-precise"],
                                capture_output=True, text=True, timeout=3)
        errors = subprocess.run(["journalctl", "-k", "-b", "--since", since, "-p", "err", "--no-pager"],
                                capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"kernelReadable": False, "kernelError": False, "kernelLog": repr(error), "errorLog": repr(error)}
    bad = re.search(r"(?im)^.*(?:\basahi\b|apple-dcp|\bdrm\b|\bgpu\b).*(?:fault|hang|timeout|timed out|error|crash)", kernel.stdout)
    return {"kernelReadable": kernel.returncode == errors.returncode == 0,
            "kernelError": bool(bad) or (errors.returncode == 0 and errors.stdout.strip() not in ("", "-- No entries --")),
            "kernelLog": kernel.stdout + kernel.stderr, "errorLog": errors.stdout + errors.stderr}


def apple_device():
    """Enumerate via the Asahi ICD only, without creating GPU workloads."""
    u, p = c.c_uint32, c.c_void_p

    class App(c.Structure):
        _fields_ = [("sType", u), ("pNext", p), ("name", c.c_char_p),
                    ("version", u), ("engine", c.c_char_p), ("engineVersion", u), ("api", u)]

    class Info(c.Structure):
        _fields_ = [("sType", u), ("pNext", p), ("flags", u), ("app", c.POINTER(App)),
                    ("layerCount", u), ("layers", p), ("extensionCount", u), ("extensions", p)]

    vk = c.CDLL("libvulkan.so.1")
    vk.vkCreateInstance.argtypes = [c.POINTER(Info), p, c.POINTER(p)]
    vk.vkCreateInstance.restype = c.c_int
    vk.vkEnumeratePhysicalDevices.argtypes = [p, c.POINTER(u), c.POINTER(p)]
    vk.vkEnumeratePhysicalDevices.restype = c.c_int
    vk.vkGetPhysicalDeviceProperties.argtypes = [p, p]
    vk.vkGetPhysicalDeviceFeatures.argtypes = [p, p]
    vk.vkDestroyInstance.argtypes = [p, p]
    app = App(0, None, b"asahi-sdk-lab-identity", 0, None, 0, (1 << 22) | (3 << 12))
    info = Info(1, None, 0, c.pointer(app), 0, None, 0, None)
    instance = p()
    result = vk.vkCreateInstance(c.byref(info), None, c.byref(instance))
    if result:
        raise RuntimeError(f"vkCreateInstance: {result}")
    try:
        n = u()
        assert vk.vkEnumeratePhysicalDevices(instance, c.byref(n), None) == 0
        devices = (p * n.value)()
        assert vk.vkEnumeratePhysicalDevices(instance, c.byref(n), devices) == 0
        records = []
        for device in devices:
            properties = c.create_string_buffer(4096)
            vk.vkGetPhysicalDeviceProperties(device, properties)
            words = c.cast(properties, c.POINTER(u))
            name = properties.raw[20:276].split(b"\0", 1)[0].decode()
            features = (u * 55)()
            vk.vkGetPhysicalDeviceFeatures(device, features)
            records.append({"name": name, "apiVersion": words[0], "vendorID": words[2],
                            "deviceID": words[3], "deviceType": words[4],
                            "pipelineCacheUUID": properties.raw[276:292].hex(),
                            "vertexPipelineStoresAndAtomics": bool(features[25]),
                            "fragmentStoresAndAtomics": bool(features[26])})
        if len(records) != 1 or "Apple M2 Pro" not in records[0]["name"] or records[0]["deviceType"] != 1:
            raise RuntimeError(f"Refusing non-Apple/inexact GPU selection: {records}")
        return records
    finally:
        vk.vkDestroyInstance(instance, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--validation", action="store_true", help="Use locally extracted validation layer")
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--name", required=True)
    parser.add_argument("--cwd", type=Path, default=ROOT)
    parser.add_argument("--stack-after", type=int, default=0, help="Capture native CPU thread stacks before timeout")
    parser.add_argument("--post-exit-seconds", type=int, default=0,
                        help="GPU-only kernel/resource observation after the command exits (0..60 seconds)")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not 1 <= args.seconds <= 3600:
        parser.error("command and a time limit of 1..3600 seconds required")
    if not 0 <= args.post_exit_seconds <= 60 or (args.post_exit_seconds and not args.gpu):
        parser.error("Post-exit observation requires --gpu and 0..60 seconds")
    LAB.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = LAB / "logs" / f"{stamp}-{args.name}"
    directory.mkdir(parents=True)
    env = os.environ.copy()
    env["ASAHI_LAB_OUTPUT"] = str(directory / "artifacts")
    (directory / "artifacts").mkdir()
    if args.validation:
        if not args.gpu:
            parser.error("--validation requires --gpu")
        local = LAB / "local-tools/usr"
        env["VK_LAYER_PATH"] = str(local / "share/vulkan/explicit_layer.d")
        env["LD_LIBRARY_PATH"] = str(local / "lib64") + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    identity = None
    initial_memory = memory_snapshot()
    if memory_problem(initial_memory, initial_memory):
        raise RuntimeError("Memory reserve or pressure disallows a new test")
    if args.gpu:
        if (LAB / "STOP-GPU.json").exists():
            raise RuntimeError("GPU experiments suspended by an earlier trial; inspect STOP-GPU.json")
        preflight_kernel = read_kernel("3 minutes ago")
        (directory / "preflight-kernel.log").write_text(preflight_kernel["kernelLog"])
        if not preflight_kernel["kernelReadable"] or preflight_kernel["kernelError"]:
            (LAB / "STOP-GPU.json").write_text(json.dumps({"reason": "Kernel preflight disallows GPU work",
                "kernel": preflight_kernel, "reviewedUTC": dt.datetime.now(dt.timezone.utc).isoformat()}, indent=2))
            raise RuntimeError("Recent kernel errors or unreadable journal; no GPU command started")
        if not ICD.is_file():
            raise RuntimeError(f"Missing Asahi ICD: {ICD}")
        initial_memory = require_memory()
        for key in ("VK_ICD_FILENAMES", "VK_ADD_DRIVER_FILES", "VK_LOADER_DRIVERS_SELECT",
                    "VK_LOADER_DRIVERS_DISABLE", "MESA_VK_DEVICE_SELECT", "DRI_PRIME"):
            env.pop(key, None)
            os.environ.pop(key, None)
        env["VK_DRIVER_FILES"] = str(ICD)
        os.environ["VK_DRIVER_FILES"] = str(ICD)
        env["XDG_CACHE_HOME"] = str(LAB / "cache")
        env["MESA_SHADER_CACHE_DIR"] = str(LAB / "cache/mesa")
        # Keep even the identity check bounded.
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("GPU enumeration timed out")))
        signal.alarm(15)
        try:
            identity = apple_device()
        finally:
            signal.alarm(0)
        print("GPU verified:", json.dumps(identity), flush=True)
    since = dt.datetime.now(dt.timezone.utc).isoformat()
    metadata = {"command": command, "cwd": str(args.cwd), "limitSeconds": args.seconds,
                 "gpu": identity, "since": since, "icd": str(ICD) if args.gpu else None,
                 "swapGrowthGuardEnabled": initial_memory.get("swapGrowthGuardEnabled", True),
                 "memoryPressureGuardEnabled": initial_memory.get("memoryPressureGuardEnabled", True),
                  "localValidation": args.validation, "artifacts": env["ASAHI_LAB_OUTPUT"]}
    guest_log = next((Path(arg.split("=", 1)[1]) for arg in command
                      if arg.startswith("--log_file=")), None)
    if guest_log is not None and not guest_log.is_absolute():
        guest_log = args.cwd / guest_log
    guest_log_offset = guest_log.stat().st_size if guest_log and guest_log.is_file() else 0
    print("Log:", directory, flush=True)
    started = time.monotonic()
    resource_samples = []
    resource_abort = None
    runner_error = None
    code = 125
    cleanup = None
    with (directory / "stdout.log").open("w") as log:
        process = subprocess.Popen(command, cwd=args.cwd, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        owned = OwnedProcesses(process)
        try:
            stack_done = False
            kernel_checked_at = started
            while process.poll() is None:
                owned.capture()
                elapsed = time.monotonic() - started
                sample = memory_snapshot()
                resource_samples.append(sample)
                resource_abort = memory_problem(sample, initial_memory)
                if resource_abort:
                    owned.stop()
                    break
                if elapsed >= args.seconds:
                    raise subprocess.TimeoutExpired(command, args.seconds)
                if args.gpu and time.monotonic() - kernel_checked_at >= 5:
                    health = read_kernel(since)
                    kernel_checked_at = time.monotonic()
                    if not health["kernelReadable"] or health["kernelError"]:
                        runner_error = "Live kernel health check failed"
                        (directory / "live-kernel.log").write_text(health["kernelLog"])
                        (LAB / "STOP-GPU.json").write_text(json.dumps(metadata | {"reason": runner_error,
                            "kernel": health}, indent=2))
                        owned.stop()
                        break
                if args.stack_after and not stack_done and elapsed >= args.stack_after:
                    stacks = subprocess.run(["eu-stack", "-p", str(process.pid)],
                        capture_output=True, text=True, timeout=10)
                    (directory / "cpu-stacks.log").write_text(stacks.stdout + stacks.stderr)
                    stack_done = True
                time.sleep(.5)
            code = 125 if resource_abort or runner_error else process.wait(timeout=5)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            owned.stop()
            code = 124
        except Exception as error:
            runner_error = repr(error)
            owned.stop()
            code = 125
        finally:
            cleanup = owned.finish()
            process.poll()
    metadata.update(returncode=code, elapsedSeconds=time.monotonic() - started)
    if cleanup["remainingPids"]:
        code = 125
        metadata["returncode"] = code
    metadata["processCleanup"] = cleanup
    metadata["runnerError"] = runner_error
    metadata["memoryBefore"] = initial_memory
    metadata["memoryAfter"] = memory_snapshot()
    metadata["resourceAbort"] = resource_abort
    metadata["minimumAvailableMiB"] = min(s["availableMiB"] for s in
                                         [initial_memory, *resource_samples, metadata["memoryAfter"]])
    (directory / "resources.json").write_text(json.dumps(resource_samples, indent=2) + "\n")
    output = (directory / "stdout.log").read_text(errors="replace")
    if guest_log and guest_log.is_file():
        with guest_log.open("rb") as log:
            if guest_log.stat().st_size >= guest_log_offset:
                log.seek(guest_log_offset)
            guest_output = log.read().decode(errors="replace")
        (directory / "guest.log").write_text(guest_output)
        output += "\n" + guest_output
    metadata["validationErrorLines"] = [line for line in output.splitlines()
        if "Validation Error:" in line or "LAB_VALIDATION 4096" in line or "Vulkan Error (" in line]
    metadata["validationWarningLines"] = [line for line in output.splitlines()
        if "Validation Warning:" in line or "LAB_VALIDATION 256" in line or "Vulkan Warning (" in line]
    if args.gpu:
        observation_start = time.monotonic()
        try:
            while time.monotonic() - observation_start < args.post_exit_seconds:
                sample = memory_snapshot()
                resource_samples.append(sample)
                problem = memory_problem(sample, initial_memory)
                if problem:
                    metadata["postExitResourceProblem"] = problem
                time.sleep(min(.5, max(0, args.post_exit_seconds - (time.monotonic() - observation_start))))
        except KeyboardInterrupt:
            runner_error = metadata["runnerError"] = "Post-exit observation interrupted"
        metadata["postExitObservationSeconds"] = time.monotonic() - observation_start
        metadata["postExitMemory"] = memory_snapshot()
        (directory / "resources.json").write_text(json.dumps(resource_samples, indent=2) + "\n")
        health = read_kernel(since)
        (directory / "kernel.log").write_text(health["kernelLog"])
        (directory / "kernel-errors.log").write_text(health["errorLog"])
        metadata.update({key: health[key] for key in ("kernelReadable", "kernelError")})
        if metadata["kernelError"] or not metadata["kernelReadable"] or resource_abort or runner_error or metadata.get("postExitResourceProblem") or cleanup["remainingPids"] or code in (124, -signal.SIGSEGV, -signal.SIGABRT):
            (LAB / "STOP-GPU.json").write_text(json.dumps(metadata, indent=2))
            print("STOP: GPU tests suspended; inspect diagnostics", flush=True)
    (directory / "result.json").write_text(json.dumps(metadata, indent=2))
    print(f"Exit {code}; elapsed {metadata['elapsedSeconds']:.1f}s", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
