"""Bounded, identity-checked perf attachment to one owned process (no GPU calls)."""
import json
from pathlib import Path
import subprocess
import time

from profiling_tools import PERF_ROOT, perf_environment
from resource_guard import OwnedProcesses, process_identity

MAX_DATA_BYTES = 256 * 1024 * 1024


def record_command(perf_root, pid, output, seconds=30, frequency=49):
    if not 1 <= seconds <= 45 or not 19 <= frequency <= 99 or pid <= 0:
        raise ValueError("Use a positive PID, 1..45 seconds and 19..99 Hz")
    return [str(perf_root / "usr/bin/perf"), "--buildid-dir", str(output.parent / "perf-buildid"),
            "record", "--no-buildid-cache", "--output", str(output),
            "--event", "cpu-clock:u", "--freq", str(frequency), "--call-graph", "fp",
            "--clockid", "mono", "--mmap-pages", "64", "--pid", str(pid),
            "--", "sleep", str(seconds)]


def thread_snapshot(pid, identity):
    if identity is None or process_identity(pid) != identity:
        return []
    rows = []
    for path in Path(f"/proc/{pid}/task").glob("*/stat"):
        try:
            text = path.read_text()
            fields = text[text.rfind(")") + 2:].split()
            rows.append({"tid": int(path.parent.name), "name": text[text.find("(") + 1:text.rfind(")")],
                         "state": fields[0], "userTicks": int(fields[11]),
                         "systemTicks": int(fields[12]), "startTicks": int(fields[19])})
        except (FileNotFoundError, ProcessLookupError):
            pass
    return rows if process_identity(pid) == identity else []


class CpuSampler:
    def __init__(self, pid, directory, *, perf_root=PERF_ROOT, seconds=30, frequency=49):
        self.identity = process_identity(pid)
        if self.identity is None:
            raise RuntimeError("Sampling target is no longer an owned live process")
        self.pid = pid
        self.directory = directory
        self.data = directory / "cpu-perf.data"
        if self.data.exists():
            raise RuntimeError("Refusing to overwrite an existing perf recording")
        self.command = record_command(perf_root, pid, self.data, seconds, frequency)
        self.log = (directory / "cpu-perf.log").open("x")
        self.metadata = {"pid": pid, "startTicks": self.identity, "command": self.command,
                         "clock": "CLOCK_MONOTONIC", "requestedEpochNs": time.time_ns(),
                         "requestedMonoNs": time.monotonic_ns(), "frequencyHz": frequency,
                         "durationLimitSeconds": seconds, "callGraph": "fp",
                         "maxDataBytes": MAX_DATA_BYTES,
                         "limits": ["User-space CPU samples, not blocked-time or GPU samples.",
                                    "Frame-pointer stacks may be incomplete in optimized modules.",
                                    "Use leaf samples when caller unwinding is incomplete."]}
        try:
            if process_identity(pid) != self.identity:
                raise RuntimeError("Sampling target identity changed before attachment")
            self.process = subprocess.Popen(self.command, env=perf_environment(perf_root),
                                            stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
            self.owned = OwnedProcesses(self.process)
        except BaseException:
            self.log.close()
            raise
        self.completed = False
        self.failure = None
        self.finished = False

    def tick(self):
        self.owned.capture()
        if self.data.exists() and self.data.stat().st_size > MAX_DATA_BYTES:
            self.failure = "perf recording exceeded 256 MiB"
        elif self.process.poll() is not None:
            if self.process.returncode != 0:
                self.failure = f"perf exited {self.process.returncode}"
            else:
                self.completed = True
                self.metadata.setdefault("collectionEndedEpochNs", time.time_ns())
                self.metadata.setdefault("collectionEndedMonoNs", time.monotonic_ns())
        elif time.monotonic_ns() - self.metadata["requestedMonoNs"] > (self.metadata["durationLimitSeconds"] + 10) * 10**9:
            self.failure = "perf exceeded its bounded collection window"
        if self.failure:
            raise RuntimeError(self.failure)

    def finish(self):
        if not self.finished:
            self.completed = self.completed or self.process.poll() == 0
            cleanup = self.owned.finish()
            self.process.poll()
            self.log.close()
            if cleanup["remainingPids"]:
                self.completed = False
                self.failure = self.failure or "perf processes remain alive"
            if not self.data.is_file() or self.data.stat().st_size == 0:
                self.completed = False
                self.failure = self.failure or "perf data is missing or empty"
            if self.failure:
                self.completed = False
            self.metadata.update(endedEpochNs=time.time_ns(), endedMonoNs=time.monotonic_ns(),
                returncode=self.process.returncode, completed=self.completed, failure=self.failure,
                dataBytes=self.data.stat().st_size if self.data.exists() else 0, processCleanup=cleanup)
            (self.directory / "cpu-sampler.json").write_text(json.dumps(self.metadata, indent=2) + "\n")
            self.finished = True
        return self.metadata
