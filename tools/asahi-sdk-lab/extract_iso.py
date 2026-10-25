#!/usr/bin/env python3
"""Extract a local disc-1 ISO once, using the project's existing XEX validators."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "forza-recomp/out/asahi-sdk-lab"
DEST = ROOT / "extracted/game"


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iso", type=Path)
    args = parser.parse_args()
    source = args.iso.expanduser().resolve(strict=True)
    if not source.is_file():
        parser.error("The source must be an ISO file")
    part = DEST.with_name(DEST.name + ".part")
    if any(path.exists() or path.is_symlink() for path in (DEST, part)):
        parser.error("Extraction destination or .part already exists; refusing to replace existing work")
    if DEST.parent.is_symlink():
        parser.error("The extraction parent must not be a symlink")
    (LAB / "tmp").mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(LAB / "tmp")
    os.environ["XDG_CACHE_HOME"] = str(LAB / "cache")
    sys.path.insert(0, str(ROOT / "launcher"))
    from forza_launcher import installer
    import xiso_extract
    image = xiso_extract.open_iso(source)
    try:
        entries = image.entries()
        count = sum(not entry.is_dir and entry.path.split("/")[0].lower() not in installer.DISC_SKIP
                    for entry in entries)
        format_name = image.format
    finally:
        image.close()
    print(f"Source: {source}\nFormat: {format_name}\nDestination: {DEST}", flush=True)
    before = sha256(source)
    print(f"Source SHA256 before: {before}", flush=True)
    checkpoint = [-1, time.monotonic()]

    def progress(done, total, name):
        block = done // (256 << 20)
        if block != checkpoint[0] or time.monotonic() - checkpoint[1] >= 5 or done == total:
            print(f"Extracted {done}/{total} bytes: {name}", flush=True)
            checkpoint[:] = [block, time.monotonic()]

    extracted = installer.extract_game(str(source), DEST, progress,
        note=lambda message: print(f"Executable check: {message}", flush=True))
    compatible, message = installer.check_game_folder(DEST)
    if not compatible:
        raise RuntimeError(message)
    after = sha256(source)
    if before != after:
        raise RuntimeError("The ISO changed while extracting; stop and inspect the source")
    record = {"source": str(source), "sourceBytes": source.stat().st_size,
              "sourceSha256Before": before, "sourceSha256After": after,
              "format": format_name, "destination": str(DEST), "extractedBytes": extracted,
              "expectedFilesExcludingSystemUpdate": count,
              "extractedFiles": sum(path.is_file() for path in DEST.rglob("*")),
              "compatible": compatible, "compatibilityMessage": message,
              "executableSha256": {name: sha256(DEST / name) for name in installer.REQUIRED_FILES}}
    output = Path(os.environ["ASAHI_LAB_OUTPUT"])
    (output / "extraction.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
