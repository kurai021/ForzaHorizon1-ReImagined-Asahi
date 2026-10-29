#!/usr/bin/env python3
"""Extract locked ARM64 perf tools locally; never install packages or change sysctls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "forza-recomp/out/asahi-profiling"
PERF_ROOT = BASE / "perf-root"


def perf_environment(root=PERF_ROOT):
    env = os.environ.copy()
    # Only perf receives these libraries. Never inject them into the game.
    env["LD_LIBRARY_PATH"] = str(root.resolve() / "usr/lib64")
    env["PERF_CONFIG"] = "/dev/null"
    env["DEBUGINFOD_URLS"] = ""
    env["PERF_PAGER"] = "cat"
    env["LC_ALL"] = "C"
    return env


def verify_rpm(path, expected):
    with path.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != expected:
        raise RuntimeError(f"RPM checksum mismatch: {path.name}")
    subprocess.run(["rpm", "--checksig", str(path)], check=True, timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpm-dir", type=Path, default=BASE / "packages")
    args = parser.parse_args()
    lock = json.loads(Path(__file__).with_name("profiling-tools.lock.json").read_text())
    paths = [(args.rpm_dir / (p["nevra"] + ".rpm"), p) for p in lock["packages"]]
    # Verify every package before extracting any of them.
    for path, package in paths:
        if not path.is_file():
            parser.error(f"Missing {path.name}; download the locked RPM into --rpm-dir, without installing")
        verify_rpm(path, package["sha256"])
    PERF_ROOT.mkdir(parents=True, exist_ok=True)
    for path, _ in paths:
        producer = subprocess.Popen(["rpm2cpio", str(path.resolve())], stdout=subprocess.PIPE)
        try:
            extracted = subprocess.run(["cpio", "--extract", "--make-directories", "--no-absolute-filenames", "--quiet"],
                           stdin=producer.stdout, cwd=PERF_ROOT, capture_output=True, timeout=30)
            if extracted.returncode:
                raise RuntimeError(extracted.stderr.decode(errors="replace"))
            producer.stdout.close()
            if producer.wait(timeout=5):
                raise RuntimeError(f"rpm2cpio failed: {path.name}")
        finally:
            producer.stdout.close()
            if producer.poll() is None:
                producer.kill()
                producer.wait(timeout=5)
    result = subprocess.run([str(PERF_ROOT / "usr/bin/perf"), "--version"],
                            env=perf_environment(), capture_output=True, text=True, check=True, timeout=10)
    print(result.stdout.strip())


if __name__ == "__main__":
    main()
