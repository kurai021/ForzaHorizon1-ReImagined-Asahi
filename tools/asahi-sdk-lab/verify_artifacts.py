#!/usr/bin/env python3
"""Static verification of private ARM64 game outputs and matching SDK libraries; never launches the game."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "forza-recomp/out/asahi-sdk-lab"
NAMES = ("forza", "forza_XMediaFacade_default", "forza_SpeechFacade_default",
         "librexruntime.so", "librexgpu-xenos.so")


def inspect(path):
    with path.open("rb") as stream:
        header = stream.read(64)
        if header[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", header, 18)[0] != 183:
            raise RuntimeError(f"Expected little-endian ELF64 AArch64: {path.name}")
        offset = struct.unpack_from("<Q", header, 32)[0]
        entry_size, count = struct.unpack_from("<HH", header, 54)
        alignments = []
        relro = False
        safe_stack = False
        for index in range(count):
            stream.seek(offset + index * entry_size)
            kind, flags, _, _, _, _, _, alignment = struct.unpack("<IIQQQQQQ", stream.read(56))
            if kind == 1:
                alignments.append(alignment)
                if alignment < 16384:
                    raise RuntimeError(f"LOAD alignment below 16 KiB: {path.name}")
            elif kind == 0x6474E551:
                safe_stack = not flags & 1
            elif kind == 0x6474E552:
                relro = True
        if not alignments or not relro or not safe_stack:
            raise RuntimeError(f"Missing LOAD/RELRO/non-executable-stack metadata: {path.name}")
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"bytes": path.stat().st_size, "sha256": digest, "machine": "AArch64",
            "loadAlignments": alignments, "relro": relro, "nonExecutableStack": safe_stack}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    build = LAB / "game-base"
    artifacts = {name: inspect(build / name) for name in NAMES}
    for name in ("librexruntime.so", "librexgpu-xenos.so"):
        with (LAB / "install/lib" / name).open("rb") as stream:
            expected = hashlib.file_digest(stream, "sha256").hexdigest()
        if expected != artifacts[name]["sha256"]:
            raise RuntimeError(f"Staged library differs from this clone's SDK install: {name}")
    dynamic = subprocess.check_output(["readelf", "-d", str(build / "forza")], text=True)
    if not re.search(r"\(RUNPATH\).*\[\$ORIGIN\]", dynamic):
        raise RuntimeError("The game must load its runtime via the expected $ORIGIN RUNPATH")
    dependencies = subprocess.check_output(["ldd", str(build / "forza")], text=True)
    runtime = re.search(r"librexruntime\.so\s+=>\s+(\S+)", dependencies)
    if "not found" in dependencies or not runtime or Path(runtime.group(1)).resolve() != (build / "librexruntime.so").resolve():
        raise RuntimeError("Missing dependency or runtime resolved outside this clone's game directory")
    report = {"verified": True, "artifacts": artifacts,
              "librariesMatchLocalInstall": True, "runtimeResolvedLocally": True}
    output = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output)
    print(output, end="")


if __name__ == "__main__":
    main()
