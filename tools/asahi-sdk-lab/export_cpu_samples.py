#!/usr/bin/env python3
"""Export one retained CPU recording with bounded RAM/time; never launch the game."""
import argparse
import json
from pathlib import Path
import resource
import subprocess
import time

from profiling_tools import PERF_ROOT, ROOT, perf_environment
from resource_guard import OwnedProcesses, memory_snapshot, memory_problem, require_memory


def limit_address_space():
    # Symbol/DWARF expansion is an experiment too, not an unbounded offline step.
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New .script path under project out/")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "forza-recomp/out") or not args.recording.is_file():
        parser.error("Use an existing recording and a new output path inside project out/")
    stderr_path = output.with_suffix(output.suffix + ".stderr")
    metadata_path = output.with_suffix(output.suffix + ".json")
    if any(path.exists() for path in (output, stderr_path, metadata_path)):
        parser.error("Export outputs already exist; preserve them instead of overwriting")
    initial = require_memory()
    command = [str(PERF_ROOT / "usr/bin/perf"), "--buildid-dir", str(output.parent / "perf-buildid"),
               "script", "--no-inline", "--input", str(args.recording.resolve()), "--ns", "-F",
               "comm,pid,tid,time,event,ip,sym,dso"]
    started = time.monotonic()
    samples = []
    abort = None
    with output.open("x") as stream, stderr_path.open("x") as errors:
        process = subprocess.Popen(command, env=perf_environment(), stdout=stream, stderr=errors,
                                   start_new_session=True, preexec_fn=limit_address_space)
        owned = OwnedProcesses(process)
        try:
            while process.poll() is None:
                owned.capture()
                sample = memory_snapshot()
                samples.append(sample)
                abort = memory_problem(sample, initial)
                if not abort and time.monotonic() - started > 60:
                    abort = "CPU export exceeded 60 seconds"
                if not abort and output.stat().st_size > 64 * 1024**2:
                    abort = "CPU script export exceeded 64 MiB"
                if abort:
                    break
                time.sleep(.1)
        except BaseException as error:
            abort = repr(error)
        finally:
            cleanup = owned.finish()
            process.poll()
    metadata = {"command": command, "returncode": process.returncode, "abort": abort,
                "elapsedSeconds": time.monotonic() - started, "processCleanup": cleanup,
                "memoryBefore": initial, "memoryAfter": memory_snapshot(),
                "minimumAvailableMiB": min(row["availableMiB"] for row in [initial, *samples]),
                "maxChildRssKiB": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                "addressSpaceLimitBytes": 2 * 1024**3, "inlineExpansion": False}
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))
    return 0 if not abort and process.returncode == 0 and not cleanup["remainingPids"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
