"""Memory limits, optional checkout-local swap/PSI policy, and owned-process cleanup."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

START_FREE_MIB = 6144
ABORT_FREE_MIB = 3072
MAX_SWAP_GROWTH_MIB = 512
SWAP_POLICY = Path(__file__).resolve().parents[2] / "forza-recomp/out/asahi-sdk-lab/swap-growth-policy.local.json"


def local_resource_policy(path=None):
    """Only the two explicitly authorized global memory metrics are overridable."""
    path = SWAP_POLICY if path is None else path
    try:
        with path.open("rb") as source:
            contents = source.read(1025)
    except FileNotFoundError:
        return {"enforceSwapGrowth": True, "enforceMemoryPressure": True}
    if len(contents) > 1024:
        raise RuntimeError("Local swap policy exceeds 1024 bytes")
    try:
        policy = json.loads(contents)
    except (ValueError, UnicodeDecodeError) as error:
        raise RuntimeError("Invalid local swap policy JSON") from error
    if (not isinstance(policy, dict) or not {"schema", "enforceSwapGrowth"}.issubset(policy) or
            not set(policy).issubset({"schema", "enforceSwapGrowth", "enforceMemoryPressure"}) or
            type(policy["schema"]) is not int or policy["schema"] != 1 or
            type(policy["enforceSwapGrowth"]) is not bool or
            type(policy.get("enforceMemoryPressure", True)) is not bool):
        raise RuntimeError("Use schema 1 and boolean swap/PSI options; no other guard overrides")
    return {"enforceSwapGrowth": policy["enforceSwapGrowth"],
            "enforceMemoryPressure": policy.get("enforceMemoryPressure", True)}


def swap_growth_guard_enabled(path=None):
    return local_resource_policy(path)["enforceSwapGrowth"]


def parse_memory(meminfo, pressure=""):
    fields = {line.split(":", 1)[0]: int(line.split()[1]) for line in meminfo.splitlines()
              if ":" in line and len(line.split()) >= 2}
    values = {"availableMiB": fields["MemAvailable"] / 1024,
              "freeMiB": fields["MemFree"] / 1024,
              "totalMiB": fields["MemTotal"] / 1024,
              "swapUsedMiB": (fields["SwapTotal"] - fields["SwapFree"]) / 1024,
              "pressureSomeAvg10": 0.0, "pressureFullAvg10": 0.0}
    for line in pressure.splitlines():
        words = line.split()
        if words and words[0] in ("some", "full"):
            entries = dict(word.split("=", 1) for word in words[1:])
            values["pressure" + words[0].title() + "Avg10"] = float(entries["avg10"])
    return values


def memory_snapshot():
    pressure = Path("/proc/pressure/memory")
    values = parse_memory(Path("/proc/meminfo").read_text(), pressure.read_text() if pressure.exists() else "")
    values["pressureAvailable"] = pressure.exists()
    values["epoch"] = time.time()
    policy = local_resource_policy()
    values["swapGrowthGuardEnabled"] = policy["enforceSwapGrowth"]
    values["swapGrowthLimitMiB"] = MAX_SWAP_GROWTH_MIB if values["swapGrowthGuardEnabled"] else None
    values["memoryPressureGuardEnabled"] = policy["enforceMemoryPressure"]
    return values


def memory_problem(sample, initial, floor=ABORT_FREE_MIB):
    if sample["availableMiB"] < floor:
        return f"physical memory reserve below {floor} MiB"
    # Latch the policy from the initial snapshot for this run. Later policy-file
    # changes cannot silently loosen a running test's selected safeguards.
    if (initial.get("swapGrowthGuardEnabled", True) and
            sample["swapUsedMiB"] - initial["swapUsedMiB"] > MAX_SWAP_GROWTH_MIB):
        return "swap grew by more than 512 MiB during the test"
    if (initial.get("memoryPressureGuardEnabled", True) and
            (sample["pressureFullAvg10"] >= 1 or sample["pressureSomeAvg10"] >= 5)):
        return "sustained memory reclaim/stall pressure"
    return None


def require_memory():
    sample = memory_snapshot()
    problem = memory_problem(sample, sample)
    if problem or sample["availableMiB"] < START_FREE_MIB:
        raise RuntimeError(problem or f"Need at least {START_FREE_MIB} MiB available before a GPU test")
    return sample


def process_identity(pid):
    try:
        path = Path(f"/proc/{pid}")
        if path.stat().st_uid != os.getuid():
            return None
        text = (path / "stat").read_text()
        fields = text[text.rfind(")") + 2:].split()
        if fields[0] in ("Z", "X"):
            return None
        return int(fields[19])
    except (FileNotFoundError, ProcessLookupError):
        return None


class OwnedProcesses:
    """Never signals an unrelated PID, a reused PID, or a process from an earlier boot."""
    def __init__(self, process):
        self.process = process
        self.boot_id = Path("/proc/sys/kernel/random/boot_id").read_text()
        self.identities = {}
        self.cleanup_signals = []
        self.leftovers_observed = set()
        self.sessions = {process.pid}  # callers launch the root with start_new_session=True
        self.original_root_identity = process_identity(process.pid)
        self.adopt(process.pid)

    def adopt(self, pid):
        identity = process_identity(pid)
        if identity is not None:
            if pid in self.identities and self.identities[pid] != identity:
                return
            self.identities.setdefault(pid, identity)
            try:
                if os.getsid(pid) == pid:
                    self.sessions.add(pid)
            except ProcessLookupError:
                pass

    def alive(self):
        if Path("/proc/sys/kernel/random/boot_id").read_text() != self.boot_id:
            return []
        return [pid for pid, identity in self.identities.items() if process_identity(pid) == identity]

    def capture(self):
        if Path("/proc/sys/kernel/random/boot_id").read_text() != self.boot_id:
            return
        current_root = process_identity(self.process.pid)
        root_reused = current_root is not None and self.original_root_identity is not None and current_root != self.original_root_identity
        if not root_reused:
            for session in list(self.sessions):
                leader = process_identity(session)
                if leader is not None and leader != self.identities.get(session):
                    continue
                members = subprocess.run(["pgrep", "-s", str(session)], capture_output=True, text=True)
                for value in members.stdout.split():
                    pid = int(value)
                    try:
                        if os.getsid(pid) == session:
                            self.adopt(pid)
                    except ProcessLookupError:
                        pass
        pending = self.alive()
        visited = set()
        while pending:
            pid = pending.pop()
            if pid in visited or pid not in self.alive():
                continue
            visited.add(pid)
            children = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
            for value in children.stdout.split():
                child = int(value)
                try:
                    text = Path(f"/proc/{child}/stat").read_text()
                    fields = text[text.rfind(")") + 2:].split()
                    if int(fields[1]) != pid or pid not in self.alive():
                        continue
                except (FileNotFoundError, ProcessLookupError):
                    continue
                self.adopt(child)
                pending.append(child)

    def stop(self, grace=3):
        self.capture()
        initially_alive = self.alive()
        self.leftovers_observed.update(initially_alive)
        for sig, wait in ((signal.SIGINT, grace), (signal.SIGTERM, 1), (signal.SIGKILL, 1)):
            for pid in reversed(self.alive()):
                try:
                    os.kill(pid, sig)
                    self.cleanup_signals.append({"pid": pid, "signal": sig.name})
                except ProcessLookupError:
                    pass
            deadline = time.monotonic() + wait
            while self.alive() and time.monotonic() < deadline:
                self.process.poll()
                time.sleep(.05)
            if not self.alive():
                break
        return {"leftoverPidsBeforeCleanup": sorted(self.leftovers_observed), "signals": list(self.cleanup_signals),
                "remainingPids": self.alive()}

    def finish(self):
        self.capture()
        if self.alive():
            return self.stop()
        return {"leftoverPidsBeforeCleanup": sorted(self.leftovers_observed),
                "signals": list(self.cleanup_signals), "remainingPids": []}
