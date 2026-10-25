#!/usr/bin/env python3
"""ARM64 host, physical-memory, offline SPIR-V and synthetic GPU regressions; no game files."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "forza-recomp/out/asahi-sdk-lab"
HERE = Path(__file__).resolve().parent


def run(name, command, gpu=False, seconds=90):
    before = set((LAB / "logs").iterdir())
    runner = [sys.executable, "-B", str(ROOT / "tools/asahi_sdk_lab.py"),
              "--name", name, "--seconds", str(seconds)]
    if gpu:
        runner += ["--gpu", "--validation"]
    subprocess.run(runner + ["--", *map(str, command)], cwd=ROOT, check=True, timeout=seconds + 40)
    directory = next(path for path in set((LAB / "logs").iterdir()) - before
                     if path.name.endswith("-" + name))
    result = json.loads((directory / "result.json").read_text())
    if result["validationErrorLines"] or (gpu and result["validationWarningLines"]):
        raise RuntimeError(f"Unexpected Vulkan validation messages in {directory}")
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--cpu-only", action="store_true")
    parser.add_argument("--require-16k", action="store_true")
    args = parser.parse_args()
    if args.require_16k and os.sysconf("SC_PAGESIZE") != 16384:
        parser.error("This validation requires a host using actual 16 KiB pages")
    subprocess.run([sys.executable, "-B", str(HERE / "sdk.py"), "verify"], check=True, cwd=ROOT)
    subprocess.run([sys.executable, "-B", str(HERE / "build.py"), "host", "--jobs", str(args.jobs)],
                   check=True, cwd=ROOT)
    binary = LAB / "sdk/out/linux-arm64/Release"
    alias = run("regression-physical-alias", [binary / "asahi_memory_alias"])
    if "PASS: physical alias" not in (alias / "stdout.log").read_text():
        raise RuntimeError("Physical alias regression did not report PASS")
    offline = run("regression-offline-spirv", [binary / "asahi_sdk_probe", "offline"])
    run("regression-spirv-val", [sys.executable, "-B", HERE / "validate_spirv.py", offline / "artifacts"])
    if not args.cpu_only:
        integration = run("regression-gpu-integration", ["env", "VK_LAYER_VALIDATE_SYNC=1",
            binary / "asahi_sdk_probe", "integration", "--vulkan_validation_enabled=true",
            "--vulkan_require_vertex_pipeline_stores_and_atomics=false", "--vulkan_memexport_compute=true",
            "--async_shader_compilation=false", "--vulkan_render_target_initial_fill=1"], gpu=True)
        output = (integration / "stdout.log").read_text()
        if output.count("LAB INTEGRATION PASS ") != 9 or "LAB validation errors=0 warnings=0" not in output:
            raise RuntimeError("Expected all 9 integration cases and zero validation messages")
    print(f"PASS: ARM64 regressions; host page size {os.sysconf('SC_PAGESIZE')}; GPU={not args.cpu_only}")


if __name__ == "__main__":
    main()
