#!/usr/bin/env python3
"""Offline validation/disassembly of the synthetic translator fixtures."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
tools = root / "forza-recomp/out/asahi-sdk-lab/local-tools/usr/bin"
failures = 0
validated = 0
for directory in sys.argv[1:]:
    for shader in sorted(Path(directory).glob("*.spv")):
        validated += 1
        result = subprocess.run([str(tools / "spirv-val"), "--target-env", "vulkan1.3",
                                 "--scalar-block-layout", str(shader)], capture_output=True, text=True, timeout=15)
        print(f"{shader.name}: {'PASS' if result.returncode == 0 else 'FAIL'}", flush=True)
        print(result.stdout + result.stderr, end="", flush=True)
        failures += result.returncode != 0
        subprocess.run([str(tools / "spirv-dis"), str(shader), "-o", str(shader.with_suffix(".spvasm"))],
                       check=True, timeout=15)
if not validated:
    print("No SPIR-V fixtures found; refusing an empty validation run", file=sys.stderr)
    sys.exit(2)
sys.exit(bool(failures))
