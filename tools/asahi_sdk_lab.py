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
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not 1 <= args.seconds <= 3600:
        parser.error("command and a time limit of 1..3600 seconds required")
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
    if args.gpu:
        if (LAB / "STOP-GPU.json").exists():
            raise RuntimeError("GPU experiments stopped by earlier kernel error; inspect STOP-GPU.json")
        if not ICD.is_file():
            raise RuntimeError(f"Missing Asahi ICD: {ICD}")
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
                 "localValidation": args.validation, "artifacts": env["ASAHI_LAB_OUTPUT"]}
    guest_log = next((Path(arg.split("=", 1)[1]) for arg in command
                      if arg.startswith("--log_file=")), None)
    if guest_log is not None and not guest_log.is_absolute():
        guest_log = args.cwd / guest_log
    guest_log_offset = guest_log.stat().st_size if guest_log and guest_log.is_file() else 0
    print("Log:", directory, flush=True)
    started = time.monotonic()
    with (directory / "stdout.log").open("w") as log:
        process = subprocess.Popen(command, cwd=args.cwd, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            if args.stack_after:
                try:
                    code = process.wait(timeout=args.stack_after)
                except subprocess.TimeoutExpired:
                    stacks = subprocess.run(["eu-stack", "-p", str(process.pid)],
                        capture_output=True, text=True, timeout=10)
                    (directory / "cpu-stacks.log").write_text(stacks.stdout + stacks.stderr)
                    code = process.wait(timeout=max(1, args.seconds - (time.monotonic() - started)))
            else:
                code = process.wait(timeout=args.seconds)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code = 124
    metadata.update(returncode=code, elapsedSeconds=time.monotonic() - started)
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
        kernel = subprocess.run(["journalctl", "-k", "-b", "--since", since, "--no-pager", "-o", "short-iso"],
                                capture_output=True, text=True, timeout=15)
        errors = subprocess.run(["journalctl", "-k", "-b", "--since", since, "-p", "err", "--no-pager"],
                                capture_output=True, text=True, timeout=15)
        (directory / "kernel.log").write_text(kernel.stdout + kernel.stderr)
        (directory / "kernel-errors.log").write_text(errors.stdout + errors.stderr)
        bad = re.search(r"(?im)^.*(?:\basahi\b|apple-dcp|\bdrm\b|\bgpu\b).*(?:fault|hang|timeout|timed out|error|crash)", kernel.stdout)
        metadata["kernelReadable"] = kernel.returncode == errors.returncode == 0
        metadata["kernelError"] = bool(bad) or (errors.returncode == 0 and errors.stdout.strip() not in ("", "-- No entries --"))
        if metadata["kernelError"] or not metadata["kernelReadable"] or code in (124, -signal.SIGSEGV, -signal.SIGABRT):
            (LAB / "STOP-GPU.json").write_text(json.dumps(metadata, indent=2))
            print("STOP: GPU tests suspended; inspect diagnostics", flush=True)
    (directory / "result.json").write_text(json.dumps(metadata, indent=2))
    print(f"Exit {code}; elapsed {metadata['elapsedSeconds']:.1f}s", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
