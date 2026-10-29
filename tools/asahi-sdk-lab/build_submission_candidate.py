#!/usr/bin/env python3
"""Recompile every GPU-plugin TU into an isolated candidate, using frozen baseline dependencies."""
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
import sys

import sdk as pinned
from prepare_submission_profile import FILES, SDK, syntax_command
from resource_guard import require_memory

ROOT = pinned.ROOT
HERE = Path(__file__).resolve().parent
LAB = ROOT / "forza-recomp/out/asahi-sdk-lab"
BUILD = SDK / "out/build/linux-arm64"
OUT = ROOT / "forza-recomp/out/asahi-profiling/candidates"
RUNNER = ROOT / "tools/asahi_sdk_lab.py"


def write(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def compile_command(line, overlay, lab, output, replacements, source_files=FILES):
    result = syntax_command(line, overlay)[:-1]  # remove -fsyntax-only
    result = [replacements.get(word, word) for word in result]
    source_mapping = {str(SDK / name): str(overlay / name) for name in source_files}
    result = [source_mapping.get(word, word) for word in result]
    result = ["-I" + str(lab) if word == "-I" + str(HERE) else word for word in result]
    return result + ["-c", "-o", str(output)]


def linker_words(line):
    words = shlex.split(line)
    if words[:2] == [":", "&&"]:
        words = words[2:]
    if "&&" in words:
        words = words[:words.index("&&")]  # never execute original post-link copies
    if not words or Path(words[0]).name not in ("clang++", "c++", "g++"):
        raise RuntimeError("Unexpected linker command")
    return words


def link_dependencies(line, build=BUILD):
    """Only link inputs: a shared-library output after -o is not a dependency."""
    words = linker_words(line)
    paths = []
    index = 1
    while index < len(words):
        word = words[index]
        if word in ("-o", "-Xlinker"):
            index += 2
            continue
        if not word.startswith("-") and word.endswith((".a", ".so")):
            paths.append((build / word).resolve())
        index += 1
    return paths


def link_command(line, objects, dependencies, output, build=BUILD):
    words = linker_words(line)
    result = [words[0]]
    consumed = set()
    index = 1
    while index < len(words):
        word = words[index]
        if word == "-o":
            result += ["-o", str(output)]
            index += 2
            continue
        if word == "-Xlinker" and words[index + 1].startswith("--dependency-file="):
            index += 2
            continue
        if word.startswith("-Wl,-rpath,"):
            word = "-Wl,-rpath,$ORIGIN"
        elif not word.startswith("-") and word.endswith((".o", ".a", ".so")):
            path = str((build / word).resolve())
            if word.endswith(".o"):
                if path not in objects:
                    raise RuntimeError(f"Refusing an unrecompiled GPU/probe object: {word}")
                consumed.add(path)
                word = str(objects[path])
            else:
                if path not in dependencies:
                    raise RuntimeError(f"Unfrozen link dependency: {word}")
                word = str(dependencies[path])
        result.append(word)
        index += 1
    if consumed != set(objects):
        raise RuntimeError("Compiled-object set and link-object set differ")
    return result + ["-Wl,-z,defs"]


def preserved_inputs():
    baseline = json.loads((ROOT / "forza-recomp/out/asahi-720p-msaa/baseline.json").read_text())
    paths = {LAB / "game-base" / name: digest for name, digest in baseline["binaries"].items()}
    h0 = LAB / "logs/20261025T072911.720979Z-local-720p-performance-demo"
    paths.update({h0 / name: digest for name, digest in baseline["h0"].items()})
    for path, expected in paths.items():
        if sha(path) != expected:
            raise RuntimeError(f"Preserved input differs; do not overwrite it: {path.relative_to(ROOT)}")
    return {str(path): digest for path, digest in paths.items()}


def copy_new(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha(destination) != sha(source):
            raise RuntimeError(f"Existing candidate input was modified: {destination}")
        return
    subprocess.run(["cp", "--reflink=auto", "--preserve=mode,timestamps", str(source), str(destination)],
                   check=True, timeout=30)


def run_step(name, command, seconds, cwd=ROOT, env=None):
    before = set((LAB / "logs").iterdir())
    invocation = [sys.executable, "-B", str(RUNNER), "--name", name, "--seconds", str(seconds),
                  "--cwd", str(cwd), "--", *map(str, command)]
    result = subprocess.run(invocation, cwd=ROOT, env=env, timeout=seconds + 30)
    directories = [p for p in set((LAB / "logs").iterdir()) - before if p.name.endswith("-" + name)]
    if len(directories) != 1 or not (directories[0] / "result.json").is_file():
        raise RuntimeError(f"Missing supervisor diagnostics for {name}")
    record = json.loads((directories[0] / "result.json").read_text())
    if result.returncode or record["returncode"] or record["processCleanup"]["remainingPids"]:
        raise RuntimeError(f"Step failed; no automatic retry: {directories[0].relative_to(ROOT)}")
    return directories[0]


def graph_commands(target):
    return subprocess.check_output(["ninja", "-f", "build-Release.ninja", "-t", "commands", target],
                                   cwd=BUILD, text=True, timeout=15).splitlines()


def probe_commands(lines, name):
    cpp = [line for line in lines if " -c " in line and f"/{name}.dir/" in line
           and " -x c++-header " not in line]
    link = [line for line in lines if " -c " not in line and " -o " in line
            and str(SDK / "out/linux-arm64/Release" / name) in line and "&&" in line]
    if len(cpp) != 1 or len(link) != 1:
        raise RuntimeError(f"Unexpected CPU-probe graph: {name}")
    return cpp, link[0]


def audit_plan(plan):
    """Fail closed on old objects, dependency writes and shell/post-link operations."""
    candidate = Path(plan["candidate"])
    if not candidate.is_relative_to(OUT):
        raise RuntimeError("Candidate must be inside the isolated output root")
    for unit in plan["units"]:
        command = unit["command"]
        output = Path(unit["output"])
        if not output.is_relative_to(candidate) or command.count("-o") != 1:
            raise RuntimeError("Unexpected candidate output")
        if command[command.index("-o") + 1] != str(output):
            raise RuntimeError("Output path differs from plan")
        for word in command:
            if word in ("&&", ";", "-MD", "-MMD", "-MF", "-MT", "-MQ", "-include-pch") or "--dependency-file=" in word:
                raise RuntimeError("Unexpected shell operation, binary PCH or dependency output")
            if not word.startswith("-") and word.endswith((".o", ".a", ".so")):
                if not Path(word).is_relative_to(candidate):
                    raise RuntimeError("Unfrozen object or link dependency")
            if word.startswith("-Wl,-rpath,") and word != "-Wl,-rpath,$ORIGIN":
                raise RuntimeError("Unexpected runtime search path")


def prepare(readback_profile=False):
    require_memory()  # use the conservative 6 GiB launch reserve for builds too
    if shutil.disk_usage(ROOT).free < 4 * 1024**3:
        raise RuntimeError("Need at least 4 GiB disk headroom")
    preserved = preserved_inputs()
    preparer = "prepare_readback_profile.py" if readback_profile else "prepare_submission_profile.py"
    prepare_command = [sys.executable, '-B', str(HERE / preparer)]
    overlay_output = subprocess.check_output(prepare_command,
                                             cwd=ROOT, text=True, timeout=60)
    overlay = ROOT / json.loads(overlay_output)["overlay"]
    source_manifest = json.loads((overlay / "manifest.json").read_text())
    plugin_lines = graph_commands("rexgpu-xenos:Release")
    compiles = [line for line in plugin_lines if " -c " in line and "-Drexgpu_xenos_EXPORTS" in line
                and " -x c++-header " not in line]
    links = [line for line in plugin_lines if " -shared " in line and "librexgpu-xenos.so" in line]
    if len(compiles) != 42 or len(links) != 1:
        raise RuntimeError("Unexpected pinned plugin graph; inspect before building")
    targets = {"plugin": (compiles, links[0])}
    for name in ("asahi_sdk_probe", "asahi_memory_alias"):
        lines = graph_commands(name + ":Release")
        targets[name] = probe_commands(lines, name)
    dependencies = {}
    for _, line in targets.values():
        for path in link_dependencies(line):
            actual = LAB / "game-base/librexruntime.so" if path.name == "librexruntime.so" else path
            dependencies[str(path)] = {"source": str(actual), "sha256": sha(actual)}
    lab_files = ("backend.cpp", "workloads.cpp", "workloads.h", "raster.cpp", "main.cpp",
                  "memory_alias.cpp", "candidate_check.cpp", "asahi_submission_profile.h")
    if readback_profile:
        lab_files += ("readback_gate.inc",)
    pch = BUILD / "src/graphics/CMakeFiles/rexgpu-xenos.dir/Release/cmake_pch.hxx"
    inputs = {"schema": 1, "builderSha256": sha(Path(__file__)), "overlay": source_manifest,
              "graph": targets, "dependencies": dependencies, "preserved": preserved,
              "labSources": {name: sha(HERE / name) for name in lab_files}, "pchTextSha256": sha(pch)}
    token = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:16]
    candidate = OUT / token
    candidate.mkdir(parents=True, exist_ok=True)
    overlay_files = tuple(source_manifest["overlayFiles"])
    for name in overlay_files:
        copy_new(overlay / name, candidate / "overlay" / name)
    for name in lab_files:
        copy_new(HERE / name, candidate / "lab" / name)
    copy_new(pch, candidate / "inputs/cmake_pch.hxx")
    mapping = {str(HERE / name): str(candidate / "lab" / name) for name in lab_files}
    mapping[str(pch)] = str(candidate / "inputs/cmake_pch.hxx")
    link_deps = {}
    for original, record in dependencies.items():
        destination = candidate / ("runtime" if Path(original).suffix == ".so" else "deps") / Path(original).name
        copy_new(Path(record["source"]), destination)
        link_deps[original] = destination
    for name in ("forza", "forza_XMediaFacade_default", "forza_SpeechFacade_default", "forza-telemetry.json"):
        copy_new(LAB / "game-base" / name, candidate / "runtime" / name)
    units = []
    probe_maps = {}
    for target, (source_commands, link) in targets.items():
        objects = {}
        for index, line in enumerate(source_commands):
            original = shlex.split(line)
            old_object = str((BUILD / original[original.index("-o") + 1]).resolve())
            source = Path(original[original.index("-c") + 1])
            output = candidate / "objects" / f"{target}-{index:02d}-{source.stem}.o"
            output.parent.mkdir(exist_ok=True)
            objects[old_object] = output
            units.append({"name": f"{target}-c{index:02d}", "output": str(output),
                           "command": compile_command(line, candidate / "overlay", candidate / "lab", output, mapping,
                                                      overlay_files)})
            if readback_profile:
                units[-1]["command"].insert(1, "-DREX_ASAHI_READBACK_GATE=1")
        output = candidate / "runtime" / ("librexgpu-xenos.so" if target == "plugin" else target)
        units.append({"name": target + "-link", "output": str(output),
                      "command": link_command(link, objects, link_deps, output)})
        probe_maps[target] = objects
    # Reuse the probe's public-ABI flags, but compile the candidate-local loader check anew.
    check_object = candidate / "objects/candidate-check.o"
    probe_compile = targets["asahi_sdk_probe"][0][0]
    check_mapping = mapping | {str(HERE / "main.cpp"): str(candidate / "lab/candidate_check.cpp")}
    units.append({"name": "candidate-check-c", "output": str(check_object),
                  "command": compile_command(probe_compile, candidate / "overlay", candidate / "lab", check_object, check_mapping)})
    original_probe_object = next(iter(probe_maps["asahi_sdk_probe"]))
    units.append({"name": "candidate-check-link", "output": str(candidate / "runtime/asahi_candidate_check"),
                  "command": link_command(targets["asahi_sdk_probe"][1], {original_probe_object: check_object}, link_deps,
                                           candidate / "runtime/asahi_candidate_check")})
    for name in ("SMAA-LICENSE.txt", "AMD-FidelityFX-LICENSE.txt"):
        source = SDK / "out/linux-arm64/Release" / name
        if source.is_file(): copy_new(source, candidate / "runtime" / name)
    (candidate / "tmp").mkdir(exist_ok=True)
    plan = {"schema": 1, "token": token, "candidate": str(candidate), "inputs": inputs, "units": units,
            "gpuObjectsRecompiled": 42, "runtimeRecompiled": False, "gameRecompiled": False,
            "baselineSdkOutRuntimeSha256": sha(SDK / "out/linux-arm64/Release/librexruntime.so")}
    # Round-trip before comparing an existing plan (graph tuples become JSON lists).
    plan = json.loads(json.dumps(plan))
    audit_plan(plan)
    plan_path = candidate / "plan.json"
    if plan_path.exists() and json.loads(plan_path.read_text()) != plan:
        raise RuntimeError("Candidate plan changed; do not overwrite it")
    if not plan_path.exists(): write(plan_path, plan)
    return plan_path, plan


def execute(plan_path, index):
    if not plan_path.resolve().is_relative_to(OUT): raise RuntimeError("Plan must be inside candidate output")
    plan = json.loads(plan_path.read_text())
    audit_plan(plan)
    command = plan["units"][index]["command"]
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.execv(command[0], command)


def build_candidate(plan_path, plan, resume):
    candidate = Path(plan["candidate"])
    state_path = candidate / "build.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"units": {}, "status": "prepared"}
    if state["status"] in ("building", "failed") and not resume:
        raise RuntimeError("Earlier attempt is incomplete; review diagnostics before explicit --resume")
    state["status"] = "building"
    write(state_path, state)
    env = os.environ | {"TMPDIR": str(candidate / "tmp"), "XDG_CACHE_HOME": str(candidate / "cache"), "CCACHE_DISABLE": "1"}
    for index, unit in enumerate(plan["units"]):
        output = Path(unit["output"])
        previous = state["units"].get(unit["name"])
        if previous and previous["status"] == "passed":
            if not output.is_file() or sha(output) != previous["sha256"]:
                raise RuntimeError("Completed candidate output changed; preserve it")
            continue
        if output.exists():
            if not previous or not resume:
                raise RuntimeError("Refusing to overwrite an unverified candidate output")
            output.rename(output.with_name(output.name + ".failed-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")))
        state["units"][unit["name"]] = {"status": "started"}
        write(state_path, state)
        print(f"[{index + 1}/{len(plan['units'])}] {unit['name']}", flush=True)
        try:
            directory = run_step("candidate-" + plan["token"][:8] + "-" + unit["name"],
                                 [sys.executable, "-B", str(Path(__file__).resolve()), "--execute", str(plan_path), str(index)],
                                 300, BUILD, env)
        except Exception:
            state["status"] = "failed"
            write(state_path, state)
            raise
        state["units"][unit["name"]] = {"status": "passed", "sha256": sha(output), "log": str(directory.relative_to(ROOT))}
        write(state_path, state)
    state["status"] = "linked"
    write(state_path, state)


def validate(plan):
    candidate = Path(plan["candidate"])
    runtime = candidate / "runtime"
    env = os.environ | {"LD_LIBRARY_PATH": str(runtime), "TMPDIR": str(candidate / "tmp"),
                        "XDG_DATA_HOME": str(candidate / "host-data"), "XDG_CACHE_HOME": str(candidate / "cache"),
                         "VK_DRIVER_FILES": "/dev/null"}
    env["ASAHI_EXPECT_READBACK_PROFILE"] = str(int(
        plan["inputs"]["overlay"].get("experiment") == "resolve-readback-reasons"))
    prefix = "candidate-" + plan["token"][:8]
    records = {}
    for name, command in (("loader", [runtime / "asahi_candidate_check"]),
                          ("alias", [runtime / "asahi_memory_alias"]),
                          ("offline", [runtime / "asahi_sdk_probe", "offline"])):
        records[name] = run_step(prefix + "-" + name, command, 60, runtime, env)
    if "PASS: physical alias" not in (records["alias"] / "stdout.log").read_text():
        raise RuntimeError("Alias invariant did not report PASS")
    if "LAB OFFLINE:" not in (records["offline"] / "stdout.log").read_text():
        raise RuntimeError("Offline probe did not confirm CPU-only mode")
    if env["ASAHI_EXPECT_READBACK_PROFILE"] == "1":
        records["readback-layout"] = run_step(prefix + "-readback-layout",
            [runtime / "asahi_sdk_probe", "readback-layout"], 60, runtime, env)
        if (records["readback-layout"] / "stdout.log").read_text().count("LAB RESOLVE LAYOUT PASS ") != 2:
            raise RuntimeError("Synthetic resolve rectangle layout did not pass CPU proof")
    records["spirv"] = run_step(prefix + "-spirv", [sys.executable, "-B", HERE / "validate_spirv.py",
                                                    records["offline"] / "artifacts"], 60, ROOT, env)
    records["relocations"] = run_step(prefix + "-relocations", ["ldd", "-r", runtime / "librexgpu-xenos.so"], 30, runtime, env)
    relocation_text = (records["relocations"] / "stdout.log").read_text()
    if "not found" in relocation_text or "undefined symbol" in relocation_text or str(runtime / "librexruntime.so") not in relocation_text:
        raise RuntimeError("Candidate has unresolved dependencies")
    preserved_inputs()
    pinned.verify(SDK, pinned.load_lock())
    report = {"cpuValidated": True, "gpuValidated": False, "gameLaunched": False,
              "checks": {name: str(path.relative_to(ROOT)) for name, path in records.items()},
              "runtimeFiles": {path.name: sha(path) for path in runtime.iterdir() if path.is_file()}}
    target = candidate / ("validation-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + ".json")
    write(target, report)
    print(json.dumps({"candidate": str(candidate.relative_to(ROOT)), "cpuValidated": True,
                      "gpuValidated": False, "gameLaunched": False}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--readback-profile", action="store_true", help="Independent default-off resolve-readback reasons recorder")
    parser.add_argument("--execute", nargs=2, metavar=("PLAN", "INDEX"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.execute:
        execute(Path(args.execute[0]), int(args.execute[1]))
        return
    plan_path, plan = prepare(args.readback_profile)
    if args.build:
        build_candidate(plan_path, plan, args.resume)
        validate(plan)
    else:
        print(json.dumps({"plan": str(plan_path.relative_to(ROOT)), "gpuCompileUnits": 42,
                          "commandCount": len(plan["units"]), "buildStarted": False}, indent=2))


if __name__ == "__main__":
    main()
