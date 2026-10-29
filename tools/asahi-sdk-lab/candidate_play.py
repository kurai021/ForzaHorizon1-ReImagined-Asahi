"""CPU-side candidate identity and owned-window checks; no implicit GPU launch."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import time

from resource_guard import process_identity

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "forza-recomp/out/asahi-profiling/candidates"
SWAPCHAIN = re.compile(r"VulkanPresenter: Created (\d+)x(\d+) swapchain\b")


def verified_runtime(candidate, base=CANDIDATES):
    candidate = candidate.resolve()
    if candidate.parent != base.resolve():
        raise RuntimeError("Use a direct child of the isolated candidates directory")
    plan = json.loads((candidate / "plan.json").read_text())
    state = json.loads((candidate / "build.json").read_text())
    if Path(plan["candidate"]).resolve() != candidate or state["status"] != "linked":
        raise RuntimeError("Candidate identity or completed build state differs")
    reports = sorted(candidate.glob("validation-*.json"))
    if not reports:
        raise RuntimeError("Candidate has no CPU validation report")
    report = json.loads(reports[-1].read_text())
    if report.get("cpuValidated") is not True:
        raise RuntimeError("Candidate did not pass CPU validation")
    required = {"forza", "librexruntime.so", "librexgpu-xenos.so"}
    if not required.issubset(report["runtimeFiles"]):
        raise RuntimeError("CPU validation lacks required runtime hashes")
    runtime = candidate / "runtime"
    for name, expected in report["runtimeFiles"].items():
        path = runtime / name
        if Path(name).name != name or path.is_symlink():
            raise RuntimeError("Unexpected candidate runtime path")
        with path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != expected:
                raise RuntimeError(f"Candidate output changed: {name}")
    return runtime


def session_flags(profile, physical, recording, start, duration, instrumented=True):
    if recording and (not instrumented or not physical or not 25 <= start <= 120 or not 1 <= duration <= 45):
        raise ValueError("Submission recording requires physical 720p and a bounded warmed window")
    flags = []
    if physical:
        flags += ["--fullscreen=false", "--fullscreen_exclusive=false",
                  "--window_width=1280", "--window_height=720"]
    # Override profile defaults explicitly, including in a disabled comparison run.
    if instrumented:
        flags += ["--fh1_submission_profile_csv=" + (str(profile / "submission.csv") if recording else "")]
    if recording:
        flags += [f"--fh1_submission_profile_start={start}", f"--fh1_submission_profile_seconds={duration}"]
    return flags


def readback_profile_flags(candidate, profile, enabled, start, duration, seconds):
    if enabled and (candidate is None or not 25 <= start <= 120 or not 1 <= duration <= 45 or
                    start + duration + 20 > seconds):
        raise ValueError('Readback recording needs a candidate and a warmed 1..45 s window with 20 s after export')
    if candidate is None:
        return []
    plan = json.loads((candidate.resolve() / 'plan.json').read_text())
    contains = plan['inputs']['overlay'].get('experiment') == 'resolve-readback-reasons'
    if enabled and not contains:
        raise ValueError('Candidate does not contain the optional readback recorder')
    if not contains:
        return []
    # Explicitly disable recording even if a saved profile requests it.
    flags = ['--fh1_readback_profile_csv=' + (str(profile / 'readback.csv') if enabled else '')]
    if enabled:
        flags += [f'--fh1_readback_profile_start={start}', f'--fh1_readback_profile_seconds={duration}']
    return flags


def require_cooldown(logs, now=None):
    now = time.time() if now is None else now
    for path in logs.glob("*/result.json"):
        if now - path.stat().st_mtime < 45:
            record = json.loads(path.read_text())
            if record.get("gpu"):
                raise RuntimeError("Keep at least 45 s cooldown after completed GPU observation")


def loaded_candidate_libraries(maps, runtime):
    required = {"librexruntime.so", "librexgpu-xenos.so"}
    seen = set()
    for line in maps.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) < 6:
            continue
        path = Path(fields[5])
        if path.name in required:
            if path.resolve() != (runtime / path.name).resolve():
                raise RuntimeError("Game mapped a different runtime/plugin than the verified candidate")
            seen.add(path.name)
    return seen == required


class SurfaceLog:
    """Incrementally read bounded chunks; retain only latest actual swapchain extent."""
    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.pending = ""
        self.extent = None
        self.events = 0
        self.new_extents = []

    def tick(self):
        self.new_extents = []
        if not self.path.exists():
            return
        if self.path.stat().st_size < self.offset:
            raise RuntimeError("Surface log was truncated during the session")
        with self.path.open("rb") as source:
            source.seek(self.offset)
            data = source.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise RuntimeError("Surface-log monitor exceeded its per-tick input bound")
        self.offset += len(data)
        text = self.pending + data.decode(errors="replace")
        lines = text.split("\n")
        self.pending = lines.pop()
        if len(self.pending) > 65536:
            raise RuntimeError("Unbounded partial surface-log line")
        for line in lines:
            match = SWAPCHAIN.search(line)
            if match:
                self.extent = tuple(map(int, match.groups()))
                self.events += 1
                self.new_extents.append(self.extent)
                if len(self.new_extents) > 256:
                    raise RuntimeError("Excessive swapchain churn during the surface-check interval")


def window_api_preflight(run=subprocess.run):
    """Read-only capability assertions before creating a game window (Hyprland 0.56 API)."""
    code = 'assert(type(hl.dsp.window.float) == "function"); assert(type(hl.dsp.window.resize) == "function")'
    result = run(["hyprctl", "eval", code], capture_output=True, text=True, timeout=3)
    if result.returncode or result.stdout.strip() != "ok":
        raise RuntimeError(f"Hyprland window API preflight failed: {(result.stdout + result.stderr)[:2000]}")


def abort_game_with_observation(process, owned, games):
    """Signal only the identity-checked game, leaving its GPU supervisor alive to observe exit."""
    signaled = []
    for value in games:
        pid = int(value)
        identity = owned.identities.get(pid)
        if identity is not None and process_identity(pid) == identity:
            try:
                os.kill(pid, signal.SIGINT)
                signaled.append(pid)
            except ProcessLookupError:
                pass
    # The supervisor may already have stopped the game for resource pressure.
    # Even with no live game to signal, let its bounded post-exit observation
    # finish before the outer owner performs whole-tree cleanup.
    try:
        code = process.wait(timeout=45)
        return {"signaledGames": signaled, "supervisorCompleted": True, "returncode": code}
    except subprocess.TimeoutExpired:
        # Caller still performs identity-checked owned-tree cleanup and leaves STOP active.
        return {"signaledGames": signaled, "supervisorCompleted": False}


def configure_owned_window(pid, identity, run=subprocess.run):
    """Hyprland per-window dispatch only; never edit rules, monitor settings or other clients."""
    def request(*args):
        result = run(["hyprctl", *args], capture_output=True, text=True, timeout=3)
        if result.returncode or (args[0] == "eval" and result.stdout.strip() != "ok"):
            details = (result.stdout + result.stderr)[:2000]
            raise RuntimeError(f"Owned-window compositor request failed ({args[0]}, exit {result.returncode}): {details}")
        return result.stdout
    if identity is None or process_identity(pid) != identity:
        raise RuntimeError("Game PID identity changed before configuring its window")
    clients = json.loads(request("-j", "clients"))
    matches = [client for client in clients if client.get("pid") == pid and client.get("mapped", True)]
    if not matches:
        return None
    if len(matches) != 1:
        raise RuntimeError("Expected exactly one mapped window for the owned game")
    client = matches[0]
    address = client["address"]
    if not re.fullmatch(r"0x[0-9a-fA-F]+", address):
        raise RuntimeError("Invalid owned-window address")
    monitors = json.loads(request("-j", "monitors"))
    monitor = next((m for m in monitors if m["id"] == client["monitor"]), None)
    if monitor is None:
        raise RuntimeError("Owned-window monitor is unavailable")
    scale = float(monitor["scale"])
    if not math.isfinite(scale) or not .5 <= scale <= 4:
        raise RuntimeError("Unexpected owned-window monitor scale")
    width, height = 1280 / scale, 720 / scale
    if not width.is_integer() or not height.is_integer():
        raise RuntimeError("Physical 720p requires nonintegral compositor coordinates; inspect instead of guessing")
    # Recheck both PID and address before dispatch; never target the focused window.
    current = json.loads(request("-j", "clients"))
    if process_identity(pid) != identity or not any(c.get("pid") == pid and c.get("address") == address for c in current):
        raise RuntimeError("Owned window identity changed before dispatch")
    # One compositor-thread Lua transaction checks identity and targets only that
    # window. No legacy dispatchers, global rules, focus selectors or reloads.
    code = (f'local w = hl.get_window("address:{address}"); '
            f'assert(w ~= nil and w.pid == {pid}, "Owned game window disappeared or changed PID"); '
            'hl.dispatch(hl.dsp.window.float({action="set", window=w})); '
            f'hl.dispatch(hl.dsp.window.resize({{x={int(width)}, y={int(height)}, relative=false, window=w}}))')
    request("eval", code)
    return {"pid": pid, "address": address, "monitor": client["monitor"], "scale": scale,
            "logicalSizeRequested": [int(width), int(height)], "physicalSizeRequired": [1280, 720]}
