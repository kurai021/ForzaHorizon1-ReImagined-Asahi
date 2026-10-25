#!/usr/bin/env python3
"""Prepare or verify the complete pinned SDK and submodule patch series."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
LOCK = HERE / "sdk.lock.json"
SDK_COMMIT = "bd833a2ab69d305f99ddf57418fc38ffec8af93d"
PROJECT_BASE = "43b64aeb63fb8553a906fca764e06614b8a6fe43"
SDK_URL = "https://github.com/rexglue/rexglue-sdk.git"


def git(repo, *args, env=None):
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(repo), *args],
                                   env=env, text=True).strip()


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def patch_records(paths):
    return [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in paths]


def record_lock(sdk):
    if git(sdk, "rev-parse", "HEAD") != SDK_COMMIT:
        raise RuntimeError("The preservation baseline must use the pinned SDK commit")
    inherited = sorted((ROOT / "patches/rexglue-sdk").glob("*.patch"))
    if len(inherited) != 116:
        raise RuntimeError("Expected the preserved series of 116 inherited SDK patches")
    submodules = []
    for line in git(sdk, "submodule", "status", "--recursive").splitlines():
        commit, path, *_ = line.split()
        if commit.startswith(("+", "-", "U")):
            raise RuntimeError(f"Unexpected submodule revision: {line}")
        submodules.append({"path": path, "commit": commit})
    record = {"schema": 1, "projectBaseCommit": PROJECT_BASE, "sdkRepository": SDK_URL,
              "sdkCommit": SDK_COMMIT, "inheritedPatches": patch_records(inherited),
              "asahiPatches": patch_records([HERE / name for name in (
                  "experimental-sdk.patch", "memexport-compute-sdk.patch", "linux-16k-sdk.patch")]),
              "sdlPatches": patch_records(sorted((ROOT / "patches/rexglue-sdk/sdl3").glob("*.patch"))),
              "submodules": submodules}
    LOCK.write_text(json.dumps(record, indent=2) + "\n")
    return record


def load_lock():
    lock = json.loads(LOCK.read_text())
    if lock["schema"] != 1 or len(lock["inheritedPatches"]) != 116:
        raise RuntimeError("Unexpected preservation lock schema or inherited patch count")
    for record in lock["inheritedPatches"] + lock["asahiPatches"] + lock["sdlPatches"]:
        if sha256(ROOT / record["path"]) != record["sha256"]:
            raise RuntimeError(f"Patch checksum mismatch: {record['path']}")
    actual = {str(p.relative_to(ROOT)) for p in (ROOT / "patches/rexglue-sdk").glob("*.patch")}
    if actual != {record["path"] for record in lock["inheritedPatches"]}:
        raise RuntimeError("The inherited patch directory differs from the locked series")
    return lock


def verify_tree(repo, commit, patches):
    if git(repo, "rev-parse", "HEAD") != commit:
        raise RuntimeError(f"Unexpected HEAD in {repo}")
    with tempfile.TemporaryDirectory(prefix="asahi-index-") as temporary:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(Path(temporary) / "index")
        git(repo, "read-tree", commit, env=env)
        for patch in patches:
            git(repo, "apply", "--cached", str(ROOT / patch["path"]), env=env)
        differences = git(repo, "diff", "--ignore-submodules=all", "--name-only", env=env)
        extras = git(repo, "ls-files", "--others", "--exclude-standard", env=env)
        if differences or extras:
            raise RuntimeError(f"SDK tree is not exactly reproduced in {repo}\n"
                               f"Changed/missing:\n{differences}\nUnexpected new files:\n{extras}")
        return {"commit": commit, "expectedTree": git(repo, "write-tree", env=env),
                "patchCount": len(patches),
                "verifiedFiles": len(git(repo, "ls-files", env=env).splitlines())}


def verify(sdk, lock):
    if Path(git(sdk, "rev-parse", "--show-toplevel")).resolve() != sdk.resolve():
        raise RuntimeError("SDK path must name its own Git checkout")
    report = {"sdk": verify_tree(sdk, lock["sdkCommit"],
                                  lock["inheritedPatches"] + lock["asahiPatches"]),
              "submodules": {}}
    actual_paths = {line.split()[1] for line in git(sdk, "submodule", "status", "--recursive").splitlines()}
    if actual_paths != {record["path"] for record in lock["submodules"]}:
        raise RuntimeError("Submodule set differs from the preservation lock")
    for record in lock["submodules"]:
        patches = lock["sdlPatches"] if record["path"] == "thirdparty/sdl3" else []
        report["submodules"][record["path"]] = verify_tree(
            sdk / record["path"], record["commit"], patches)
    report["verified"] = True
    return report


def prepare(sdk, lock):
    if sdk.exists():
        # Existing worktrees are read-only inputs to this operation.
        return verify(sdk, lock)
    sdk.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout",
                    lock["sdkRepository"], str(sdk)], check=True)
    git(sdk, "fetch", "--depth=1", "origin", lock["sdkCommit"])
    git(sdk, "checkout", "--detach", lock["sdkCommit"])
    git(sdk, "submodule", "update", "--init", "--recursive", "--depth=1")
    for patch in lock["inheritedPatches"] + lock["asahiPatches"]:
        git(sdk, "apply", str(ROOT / patch["path"]))
    for patch in lock["sdlPatches"]:
        git(sdk / "thirdparty/sdl3", "apply", str(ROOT / patch["path"]))
    return verify(sdk, lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("record-lock", "verify", "prepare"))
    parser.add_argument("--sdk", type=Path, default=ROOT / "forza-recomp/out/asahi-sdk-lab/sdk")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.mode == "record-lock":
        lock = record_lock(args.sdk.resolve())
        report = {"lock": str(LOCK.relative_to(ROOT)), "sdkCommit": lock["sdkCommit"],
                  "inheritedPatches": len(lock["inheritedPatches"]),
                  "asahiPatches": len(lock["asahiPatches"]),
                  "sdlPatches": len(lock["sdlPatches"]), "submodules": len(lock["submodules"])}
    else:
        lock = load_lock()
        report = (prepare if args.mode == "prepare" else verify)(args.sdk.resolve(), lock)
    output = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output)
    print(output, end="")


if __name__ == "__main__":
    main()
