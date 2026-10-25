#!/usr/bin/env python3
"""Record provenance and export only lab changes relative to Forza's SDK patch series."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import argparse

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "forza-recomp/out/asahi-sdk-lab"
SDK = LAB / "sdk"


def git(*args, env=None):
    return subprocess.check_output(["git", "-C", str(SDK), *args], env=env, timeout=60)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--phase2", action="store_true", help="Preserve phase 1 and export an incremental integration patch")
parser.add_argument("--publish", action="store_true", help="Publish the generated phase-2 patch alongside the lab sources")
args = parser.parse_args()
if args.publish and not args.phase2:
    parser.error("--publish requires --phase2")
patches = sorted((ROOT / "patches/rexglue-sdk").glob("*.patch"))
head = git("rev-parse", "HEAD").decode().strip()
with tempfile.TemporaryDirectory(prefix="snapshot-index-", dir=LAB) as temporary:
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(Path(temporary) / "index")
    git("read-tree", head, env=env)
    for patch in patches:
        git("apply", "--cached", str(patch), env=env)
    # Do not commit, stage, checkout or rewrite the SDK working tree.
    gpu_paths = ["CMakeLists.txt",
               "src/ui/vulkan/vulkan_device.cpp", "src/graphics/vulkan/command_processor.cpp",
               "src/graphics/pipeline/shader/spirv_translator.cpp",
               "include/rex/graphics/pipeline/shader/spirv_translator.h",
               "include/rex/graphics/vulkan/command_processor.h",
                "include/rex/graphics/vulkan/render_target_cache.h"]
    memory_paths = ["include/rex/system/xmemory.h",
                    "resources/templates/codegen/pch_h.inja",
                    "resources/templates/test/ppc_config_h.inja"]
    paths = gpu_paths + memory_paths
    diff = git("diff", "--binary", "--src-prefix=a/", "--dst-prefix=b/", "--", *paths, env=env)
    exported = LAB / ("combined-sdk.patch" if args.phase2 else "experimental-sdk.patch")
    exported.write_bytes(diff)
    memory_patch = LAB / "linux-16k-sdk.patch"
    memory_patch.write_bytes(git("diff", "--binary", "--src-prefix=a/", "--dst-prefix=b/",
                                "--", *memory_paths, env=env))
    if args.phase2:
        git("apply", "--cached", str(ROOT / "tools/asahi-sdk-lab/experimental-sdk.patch"), env=env)
        incremental = git("diff", "--binary", "--src-prefix=a/", "--dst-prefix=b/", "--", *gpu_paths, env=env)
        (LAB / "memexport-compute-sdk.patch").write_bytes(incremental)
        if args.publish:
            (ROOT / "tools/asahi-sdk-lab/memexport-compute-sdk.patch").write_bytes(incremental)
            (ROOT / "tools/asahi-sdk-lab/linux-16k-sdk.patch").write_bytes(memory_patch.read_bytes())

source_paths = sorted((ROOT / "tools/asahi-sdk-lab").glob("*")) + [ROOT / "tools/asahi_sdk_lab.py",
    ROOT / "forza-recomp/CMakeLists.txt", ROOT / "forza-recomp/src/platform/guest_memory.h",
    ROOT / "docs/asahi-port-phase3.md", ROOT / "tools/cmake/linux-arm64-host-check/CMakeLists.txt"]
binary_paths = [SDK / "out/linux-arm64/Release" / name for name in
                 ("asahi_sdk_probe", "asahi_memory_alias", "librexruntime.so", "librexgpu-xenos.so", "rexglue")
                 if (SDK / "out/linux-arm64/Release" / name).is_file()]
binary_paths += [LAB / "game-base" / name for name in
                 ("forza", "forza_XMediaFacade_default", "forza_SpeechFacade_default",
                  "librexruntime.so", "librexgpu-xenos.so")
                 if (LAB / "game-base" / name).is_file()]
artifacts = [path for path in (LAB / "logs").glob("*/artifacts/*")
             if path.is_file() and path.suffix in (".spv", ".ppm", ".bin", ".png", ".csv")]
record = {
    "capturedUTC": dt.datetime.now(dt.timezone.utc).isoformat(),
    "forzaCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "sdkCommit": head,
    "projectSdkPatchCount": len(patches),
    "projectPatchSha256": {str(p.relative_to(ROOT)): digest(p) for p in
                          patches + sorted((ROOT / "patches/rexglue-sdk/sdl3").glob("*.patch"))},
    "experimentalPatchSha256": digest(exported),
    "physicalAliasPatchSha256": digest(memory_patch),
    "exportedPatch": exported.name,
    "phase2": args.phase2,
    "gpuSuspension": json.loads((LAB / "STOP-GPU.json").read_text()) if (LAB / "STOP-GPU.json").exists() else None,
    "sourceSha256": {str(p.relative_to(ROOT)): digest(p) for p in source_paths if p.is_file()},
    "binarySha256": {str(p.relative_to(ROOT)): digest(p) for p in binary_paths},
    "artifactSha256": {str(p.relative_to(LAB)): digest(p) for p in artifacts},
    "runs": [json.loads(p.read_text()) | {"logDirectory": str(p.parent.relative_to(LAB))}
             for p in sorted((LAB / "logs").glob("*/result.json"))],
}
(LAB / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
(LAB / "submodules.txt").write_bytes(git("submodule", "status", "--recursive"))
(LAB / "sdk-working-tree.diff").write_bytes(git("diff", "--binary"))
(LAB / "project-platform.patch").write_bytes(subprocess.check_output(
    ["git", "diff", "--binary", "--src-prefix=a/", "--dst-prefix=b/", "--", "forza-recomp/CMakeLists.txt"], cwd=ROOT))
print(f"Recorded {len(record['runs'])} runs, source/binary/artifact hashes and {len(patches)} project patches")
print("Exported", exported)
