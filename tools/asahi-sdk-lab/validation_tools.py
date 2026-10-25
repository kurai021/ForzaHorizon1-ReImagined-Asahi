#!/usr/bin/env python3
"""Download pinned Fedora validation RPMs and extract them locally, without installing."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "forza-recomp/out/asahi-sdk-lab"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--rpm-dir", type=Path, default=LAB / "packages")
    args = parser.parse_args()
    lock = json.loads(Path(__file__).with_name("validation-tools.lock.json").read_text())
    args.rpm_dir.mkdir(parents=True, exist_ok=True)
    destination = LAB / "local-tools"
    destination.mkdir(parents=True, exist_ok=True)
    for package in lock["packages"]:
        path = args.rpm_dir / (package["nevra"] + ".rpm")
        if not path.is_file():
            if not args.download:
                parser.error(f"Missing {path.name}; use --download or supply --rpm-dir")
            subprocess.run(["dnf", "download", f"--destdir={args.rpm_dir.resolve()}",
                            f"--setopt=cachedir={LAB / 'dnf-cache'}", package["nevra"]], check=True)
        if hashlib.sha256(path.read_bytes()).hexdigest() != package["sha256"]:
            raise RuntimeError(f"RPM checksum mismatch: {path.name}")
        producer = subprocess.Popen(["rpm2cpio", str(path.resolve())], stdout=subprocess.PIPE)
        try:
            subprocess.run(["cpio", "--extract", "--make-directories", "--no-absolute-filenames", "--quiet"],
                           stdin=producer.stdout, cwd=destination, check=True)
        finally:
            producer.stdout.close()
            code = producer.wait()
        if code:
            raise RuntimeError(f"rpm2cpio failed for {path.name}: {code}")
        print(f"Verified and extracted: {path.name}", flush=True)
    print(f"Local validation tools: {destination.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
