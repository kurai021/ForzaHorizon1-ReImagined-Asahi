#!/usr/bin/env python3
"""Audit the fork delta and optional source archive without reading private laboratory outputs."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
BASE = "43b64aeb63fb8553a906fca764e06614b8a6fe43"
PRIVATE_SUFFIXES = {".xex", ".iso", ".bik", ".wmv", ".pck", ".zip", ".rpm", ".exe", ".dll",
                    ".so", ".spv", ".bundle", ".core", ".log", ".csv", ".bin"}
SOURCE_SUFFIXES = {".py", ".cpp", ".h", ".cmake", ".patch", ".json", ".md", ".yml", ".yaml"}
LICENSES = ("LICENSE", "forza-recomp/LICENSE-Pinyon-Shift", "patches/rexglue-sdk/LICENSE-Pinyon-Shift")


def git(*args):
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(ROOT), *args])


def private_path(name):
    path = PurePosixPath(name)
    return (bool(set(path.parts) & {"extracted", "out", "__pycache__", "asahi-private"})
            or (name.startswith("forza-recomp/generated") and name != "forza-recomp/generated/rexglue.cmake")
            or path.suffix.lower() in PRIVATE_SUFFIXES
            or name.endswith((".tar.gz", ".tar.xz", ".tar")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--index", action="store_true", help="Audit the complete staged tree against the upstream baseline")
    parser.add_argument("--archive", type=Path, help="Verify a git archive of the audited revision")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.index and args.archive:
        parser.error("Archives are verified against a committed revision")
    if args.index:
        files = git("ls-files", "-z").decode().strip("\0").split("\0")
        changed = git("diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z", BASE).decode().strip("\0").split("\0")
        prefix = ":"
    else:
        files = git("ls-tree", "-r", "--name-only", "-z", args.revision).decode().strip("\0").split("\0")
        changed = git("diff", "--name-only", "--diff-filter=ACMRT", "-z", BASE, args.revision).decode().strip("\0").split("\0")
        prefix = args.revision + ":"
    files, changed = set(filter(None, files)), sorted(filter(None, changed))
    errors = [f"Private/generated/binary path in source tree: {name}" for name in sorted(files) if private_path(name)]
    hashes = {}
    for name in changed:
        path = PurePosixPath(name)
        if (path.suffix.lower() not in SOURCE_SUFFIXES and path.name not in
                {"CMakeLists.txt", "README", "LICENSE", ".gitignore", ".gitattributes"}):
            errors.append(f"Unexpected non-source addition/change: {name}")
        content = git("show", prefix + name)
        if b"\0" in content or content.startswith((b"\x7fELF", b"MZ", b"PK\x03\x04")):
            errors.append(f"Binary content in fork delta: {name}")
        diff_args = ["diff", "--no-ext-diff", "--no-color", "--unified=0"]
        diff_args += ["--cached", BASE] if args.index else [BASE, args.revision]
        added = b"\n".join(line[1:] for line in git(*diff_args, "--", name).splitlines()
                           if line.startswith(b"+") and not line.startswith(b"+++"))
        if re.search(rb"/home/[A-Za-z0-9_.-]+/|/Users/[A-Za-z0-9_.-]+/|/tmp/[A-Za-z0-9_.-]+/|github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{20,}", added):
            errors.append(f"Private machine path or credential-like content: {name}")
        hashes[name] = hashlib.sha256(content).hexdigest()
    for name in LICENSES:
        if git("show", BASE + ":" + name) != git("show", prefix + name):
            errors.append(f"Inherited license changed: {name}")
    active = sorted(name for name in files if name.startswith(".github/workflows/") and
                    PurePosixPath(name).suffix in {".yml", ".yaml"})
    for name in active:
        content = git("show", prefix + name).decode()
        if re.search(r"(?m)^\s*(?:contents|pages|id-token):\s*write|gh release (?:create|upload)|actions/deploy-pages|DISCORD_WEBHOOK|^\s*schedule:", content):
            errors.append(f"Publication or scheduled external-service workflow remains active: {name}")
    archive_checked = False
    if args.archive:
        seen = set()
        with tarfile.open(args.archive, "r:*") as archive:
            for member in archive:
                if member.isdir():
                    continue
                if member.name not in files:
                    errors.append(f"Unexpected archive member: {member.name}")
                    continue
                seen.add(member.name)
                if member.issym():
                    content = member.linkname.encode()
                elif member.isfile():
                    content = archive.extractfile(member).read()
                else:
                    errors.append(f"Unsupported archive member type: {member.name}")
                    continue
                if content != git("show", prefix + member.name):
                    errors.append(f"Archive differs from committed source: {member.name}")
        if seen != files:
            errors.append("Archive member set differs from committed source tree")
        archive_checked = True
    report = {"baseCommit": BASE, "revision": "index" if args.index else git("rev-parse", args.revision).decode().strip(),
              "trackedFiles": len(files), "forkDeltaFiles": len(changed),
              "inheritedImagesRetained": len([name for name in files if name.startswith("docs/images/")]),
              "activeWorkflows": active, "deltaSha256": hashes,
              "archiveVerified": archive_checked, "errors": errors, "passed": not errors}
    output = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output)
    print(output, end="")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
