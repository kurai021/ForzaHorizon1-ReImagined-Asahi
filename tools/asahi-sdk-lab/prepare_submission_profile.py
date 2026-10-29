#!/usr/bin/env python3
"""Prepare/syntax-check an optional two-file overlay; never patch/build/install the live SDK."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import resource
import shlex
import shutil
import subprocess

import sdk as pinned

ROOT = pinned.ROOT
HERE = Path(__file__).resolve().parent
SDK = ROOT / "forza-recomp/out/asahi-sdk-lab/sdk"
OUT = ROOT / "forza-recomp/out/asahi-profiling/submission-overlays"
FILES = ("include/rex/graphics/vulkan/command_processor.h", "src/graphics/vulkan/command_processor.cpp")


def limit_compiler():
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))


def syntax_command(command, overlay, sdk=SDK):
    """Reuse real flags, not the old binary PCH or object/dependency output paths."""
    words = shlex.split(command)
    result = [words[0], "-I" + str(overlay / "include"), "-I" + str(HERE),
              "-I" + str(sdk / "src/graphics/vulkan"), "-DREX_ASAHI_SUBMISSION_PROFILE=1"]
    index = 1
    while index < len(words):
        word = words[index]
        if word == "-Xclang" and index + 1 < len(words) and words[index + 1] == "-include-pch":
            if words[index + 2] != "-Xclang":
                raise RuntimeError("Unexpected PCH command layout")
            index += 4
            continue
        if word in ("-o", "-MT", "-MF", "-MQ"):
            index += 2
            continue
        if word in ("-c", "-MD", "-MMD"):
            index += 1
            continue
        if word == str(sdk / FILES[1]):
            word = str(overlay / FILES[1])
        result.append(word)
        index += 1
    result.append("-fsyntax-only")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Also syntax-check the overlaid TU with the existing Release flags")
    args = parser.parse_args()
    # This verifies the original locked SDK. The optional overlay is not a fourth
    # baseline patch and must never be added to sdk.lock.json by this command.
    baseline = pinned.verify(SDK, pinned.load_lock())
    patch = HERE / "submission-profile-sdk.patch"
    hashes = {name: pinned.sha256(HERE / name) for name in
              ("submission-profile-sdk.patch", "asahi_submission_profile.h", "lab.cmake")}
    base_hashes = {name: pinned.sha256(SDK / name) for name in FILES}
    fingerprint = hashlib.sha256(json.dumps([hashes, base_hashes], sort_keys=True).encode()).hexdigest()[:16]
    overlay = OUT / fingerprint
    manifest_path = overlay / "manifest.json"
    if overlay.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["inputs"] != hashes or manifest["baseFiles"] != base_hashes:
            raise RuntimeError("Existing overlay inputs differ; do not overwrite it")
        if any(pinned.sha256(overlay / name) != digest for name, digest in manifest["overlayFiles"].items()):
            raise RuntimeError("Existing overlay was modified; preserve and inspect it")
    else:
        overlay.mkdir(parents=True)
        for name in FILES:
            (overlay / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SDK / name, overlay / name)
        command = ["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-d", str(overlay), "-i", str(patch)]
        subprocess.run(command + ["--dry-run"], check=True, capture_output=True, text=True, timeout=10)
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
        manifest = {"inputs": hashes, "baseFiles": base_hashes,
                    "overlayFiles": {name: pinned.sha256(overlay / name) for name in FILES},
                    "baselineSdk": baseline["sdk"], "gpuWork": False,
                    "liveSdkModified": False, "linkedPluginBuilt": False}
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    if args.check:
        build = SDK / "out/build/linux-arm64"
        target = "src/graphics/CMakeFiles/rexgpu-xenos.dir/Release/vulkan/command_processor.cpp.o"
        listing = subprocess.check_output(["ninja", "-f", "build-Release.ninja", "-t", "commands", target],
                                          cwd=build, text=True, timeout=10)
        matches = [line for line in listing.splitlines() if shlex.split(line)[-1] == str(SDK / FILES[1])]
        if len(matches) != 1:
            raise RuntimeError("Could not identify exactly one baseline compile command")
        command = syntax_command(matches[0], overlay)
        checks = overlay / "checks" / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        checks.mkdir(parents=True)
        (checks / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        try:
            result = subprocess.run(command, cwd=build, env=os.environ | {"TMPDIR": str(checks)},
                                    capture_output=True, text=True, timeout=90, preexec_fn=limit_compiler)
            (checks / "compiler.log").write_text(result.stdout + result.stderr)
        except subprocess.TimeoutExpired as error:
            (checks / "compiler.log").write_text(str(error))
            raise RuntimeError("Overlay syntax check exceeded 90 seconds") from error
        if result.returncode:
            raise RuntimeError(f"Overlay syntax check failed; inspect {checks.relative_to(ROOT)}/compiler.log")
        (checks / "result.json").write_text(json.dumps({"syntaxPassed": True, "addressSpaceLimitBytes": 2 * 1024**3,
            "linkedPluginBuilt": False, "gpuWork": False}, indent=2) + "\n")
    if any(pinned.sha256(SDK / name) != digest for name, digest in base_hashes.items()):
        raise RuntimeError("Original SDK input changed during overlay preparation")
    print(json.dumps({"overlay": str(overlay.relative_to(ROOT)), "syntaxChecked": args.check,
                      "liveSdkModified": False, "linkedPluginBuilt": False, "gpuWork": False}, indent=2))


if __name__ == "__main__":
    main()
