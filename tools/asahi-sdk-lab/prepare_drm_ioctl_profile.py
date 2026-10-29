#!/usr/bin/env python3
"""Plan/build an isolated drmIoctl interposer; CPU loader check only, never GPU work."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys

from resource_guard import require_memory

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = ROOT / "forza-recomp/out/asahi-profiling/drm-ioctl-probes"
PRIVATE = ROOT / "forza-recomp/out"
EXPORTS = "{ global: drmIoctl; asahi_drm_ioctl_profile_abi; local: *; };\n"
HEADERS = tuple(Path("/usr/include/drm") / name for name in ("asahi_drm.h", "drm.h"))


def fingerprint(inputs):
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:16]


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def compiler_command(compiler, source, output, exports, private):
    return [compiler, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-fPIC", "-shared",
            "-fvisibility=hidden", "-fvisibility-inlines-hidden", "-fno-exceptions", "-fno-rtti",
            "-I/usr/include/drm", "-DASAHI_DRM_PROFILE_ROOT=" + json.dumps(str(private.resolve())),
            str(source), "-pthread", "-ldl", "-Wl,-z,defs", "-Wl,--version-script=" + str(exports),
            "-o", str(output)]


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def verified_library(directory, base=BASE, private=PRIVATE):
    directory = directory.resolve()
    if directory.parent != base.resolve():
        raise RuntimeError("Use a direct child of the private DRM probe directory")
    plan = json.loads((directory / "plan.json").read_text())
    result = json.loads((directory / "build.json").read_text())
    library = directory / "libasahi-drm-ioctl-profile.so"
    token = fingerprint(plan["inputs"])
    if (plan["schema"] != 1 or plan["token"] != token or directory.name != token or
            plan["gpuWork"] is not False or plan["sdkModified"] is not False or
            result["token"] != token or result["sourceSha256"] != plan["inputs"]["sourceSha256"]):
        raise RuntimeError("DRM probe manifest/build fingerprint differs")
    if (library.is_symlink() or Path(plan["library"]) != library or
            plan["inputs"]["privateRoot"] != str(private.resolve()) or
            result["cpuLoaderPassed"] is not True or result["abi"] != 1 or sha(library) != result["librarySha256"]):
        raise RuntimeError("DRM probe identity, ABI, root or output hash differs")
    if sha(directory / "drm_ioctl_profile.cpp") != plan["inputs"]["sourceSha256"]:
        raise RuntimeError("Frozen DRM probe source differs")
    if (plan["inputs"]["exports"] != EXPORTS or (directory / "exports.map").read_text() != EXPORTS or
            set(plan["inputs"]["headers"]) != {str(path) for path in HEADERS}):
        raise RuntimeError("DRM probe exports or required header manifest differs")
    if any(sha(Path(path)) != expected for path, expected in plan["inputs"]["headers"].items()):
        raise RuntimeError("Installed DRM UAPI headers changed since probe build")
    if any(char.isspace() or char == ':' for char in str(library)):
        raise RuntimeError("LD_PRELOAD cannot safely represent this library path")
    return library


def launch_environment(directory, executable, output, submission_start, submission_seconds):
    if not 5 <= submission_start <= 120 or not 1 <= submission_seconds <= 35:
        raise ValueError("DRM coverage requires 5 s margins and at most 35 s of SDK submission recording")
    library = verified_library(directory)
    if not output.resolve().is_relative_to(PRIVATE.resolve()) or output.exists() or output.is_symlink():
        raise ValueError("Use a new DRM trace under private out/")
    return {"LD_PRELOAD": str(library), "ASAHI_DRM_IOCTL_CSV": str(output.resolve()),
            "ASAHI_DRM_IOCTL_TARGET": str(executable.resolve()),
            "ASAHI_DRM_IOCTL_START": str(submission_start - 5),
            "ASAHI_DRM_IOCTL_SECONDS": str(submission_seconds + 10),
            "ASAHI_DRM_IOCTL_MAX_RECORDS": "262144"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    require_memory()
    compiler = shutil.which("clang++") or shutil.which("c++")
    if not compiler:
        parser.error("C++17 compiler required")
    source = HERE / "drm_ioctl_profile.cpp"
    inputs = {"sourceSha256": sha(source), "preparerSha256": sha(Path(__file__)),
              "headers": {str(p): sha(p) for p in HEADERS}, "exports": EXPORTS,
              "privateRoot": str(PRIVATE.resolve()), "compiler": subprocess.check_output(
                  [compiler, "--version"], text=True, timeout=5)}
    token = fingerprint(inputs)
    directory = BASE / token
    directory.mkdir(parents=True, exist_ok=True)
    frozen = directory / source.name
    exports = directory / "exports.map"
    for target, contents in ((frozen, source.read_text()), (exports, EXPORTS)):
        if target.exists():
            if target.read_text() != contents:
                raise RuntimeError("Existing probe input changed; preserve and inspect it")
        else:
            target.write_text(contents)
    library = directory / "libasahi-drm-ioctl-profile.so"
    command = compiler_command(compiler, frozen, library, exports, PRIVATE)
    plan = {"schema": 1, "token": token, "inputs": inputs, "command": command,
            "library": str(library), "gpuWork": False, "sdkModified": False}
    plan_path = directory / "plan.json"
    if plan_path.exists() and json.loads(plan_path.read_text()) != plan:
        raise RuntimeError("Existing probe plan differs")
    if not plan_path.exists():
        plan_path.write_text(json.dumps(plan, indent=2) + "\n")
    result_path = directory / "build.json"
    if args.build and result_path.exists():
        verified_library(directory)
    elif args.build:
        log = directory / "compiler.log"
        if library.exists() or log.exists():
            raise RuntimeError("Unverified earlier build exists; no automatic overwrite/retry")
        with log.open("x") as output:
            subprocess.run(command, cwd=directory, stdout=output, stderr=subprocess.STDOUT,
                           check=True, timeout=45, preexec_fn=limits,
                           env=os.environ | {"TMPDIR": str(directory)})
        symbols = subprocess.check_output(["nm", "-D", "--defined-only", str(library)], text=True, timeout=5)
        if {line.split()[-1] for line in symbols.splitlines()} != {"drmIoctl", "asahi_drm_ioctl_profile_abi"}:
            raise RuntimeError("Unexpected exported symbols; do not preload")
        env = {k: v for k, v in os.environ.items() if not k.startswith("ASAHI_DRM_IOCTL_") and k != "LD_PRELOAD"}
        check = [sys.executable, "-B", "-c",
                 "import ctypes,os,sys; m=ctypes.CDLL(sys.argv[1],mode=os.RTLD_NOW|os.RTLD_LOCAL); "
                 "m.asahi_drm_ioctl_profile_abi.restype=ctypes.c_uint32; "
                 "assert m.asahi_drm_ioctl_profile_abi()==1; print('PASS: CPU loader, ABI 1, recording disabled')",
                 str(library)]
        checked = subprocess.run(check, env=env, capture_output=True, text=True, check=True, timeout=5,
                                 preexec_fn=limits)
        (directory / "loader.log").write_text(checked.stdout + checked.stderr)
        result = {"token": token, "sourceSha256": inputs["sourceSha256"],
                  "librarySha256": sha(library), "cpuLoaderPassed": True, "abi": 1,
                  "forwardingTestsRequired": True, "gpuValidated": False, "gameLaunched": False}
        result_path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"probe": str(directory.relative_to(ROOT)), "built": result_path.exists(),
                      "gpuValidated": False, "gameLaunched": False}, indent=2))


if __name__ == "__main__":
    main()
