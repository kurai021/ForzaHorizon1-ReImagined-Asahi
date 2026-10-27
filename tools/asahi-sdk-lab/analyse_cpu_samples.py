#!/usr/bin/env python3
"""Summarize perf-script user CPU leaf samples and optionally align frame cohorts."""
import argparse
import bisect
from collections import Counter
import json
from pathlib import Path
import re

from profile_frames import read_frames, summarize as summarize_frames

HEADER = re.compile(r"^\s*(.*?)\s+(\d+)/(\d+)\s+(\d+\.\d+):\s+cpu-clock:u:\s*$")
FRAME = re.compile(r"^\s+([0-9a-fA-F]+)\s+(.+)\s+\((.*)\)\s*$")


def parse_samples(text):
    samples = []
    current = None
    unparsed = 0
    for line in text.splitlines():
        match = HEADER.match(line)
        if match:
            comm, pid, tid, timestamp = match.groups()
            seconds, fraction = timestamp.split(".")
            current = {"comm": comm, "pid": int(pid), "tid": int(tid),
                       "monoNs": int(seconds) * 10**9 + int(fraction.ljust(9, "0")[:9]),
                       "symbol": "[missing leaf]", "dso": "[unknown]", "callchainFrames": 0}
            samples.append(current)
        elif "cpu-clock:u:" in line:
            unparsed += 1
            current = None
        elif current is not None and (match := FRAME.match(line)):
            if current["callchainFrames"] == 0:
                current.update(ip=match[1], symbol=match[2], dso=match[3])
            elif current["dso"] == "inlined" and match[1] == current["ip"] and match[3] != "inlined":
                # perf expands an inline leaf before its containing machine frame.
                # Keep that leaf attribution but recover its real DSO at the same IP.
                current["dso"] = match[3]
            current["callchainFrames"] += 1
    return samples, unparsed


def summarize(samples):
    counts = Counter((sample["comm"], sample["tid"]) for sample in samples)
    leaves = Counter((sample["symbol"], sample["dso"]) for sample in samples)
    return {"samples": len(samples),
            "unknownLeafSamples": sum(sample["symbol"] in ("[unknown]", "[missing leaf]") for sample in samples),
            "threads": [{"comm": comm, "tid": tid, "samples": count,
                         "sampleSharePercent": 100 * count / len(samples)}
                        for (comm, tid), count in counts.most_common()],
            "topLeaves": [{"symbol": symbol, "module": Path(dso).name, "samples": count,
                           "sampleSharePercent": 100 * count / len(samples)}
                          for (symbol, dso), count in leaves.most_common(30)]}


def align_frames(samples, frames, metadata):
    if not frames or "epoch" not in frames[0]:
        raise ValueError("Frame alignment requires nonempty epoch-bearing CSV rows")
    if not samples:
        return {"aligned": False, "reason": "No target samples to align"}
    offset = metadata["requestedEpochNs"] - metadata["requestedMonoNs"]
    final_offset = metadata["endedEpochNs"] - metadata["endedMonoNs"]
    drift = final_offset - offset
    if abs(drift) > 10_000_000:
        return {"aligned": False, "reason": "Realtime/monotonic bridge drift exceeded 10 ms", "bridgeDriftNs": drift}
    ends = [round(row["epoch"] * 10**9) for row in frames]
    if any(a >= b for a, b in zip(ends, ends[1:])):
        raise ValueError("Frame epoch timestamps must increase strictly for alignment")
    groups = {"allMatched": [], "creationFrames": [], "above50MsWithoutCreation": [], "otherFrames": []}
    unmatched = 0
    for sample in samples:
        epoch = sample["monoNs"] + offset
        index = bisect.bisect_left(ends, epoch)
        if index == len(frames) or ends[index] - epoch > frames[index]["ms"] * 10**6 + 1_000_000:
            unmatched += 1
            continue
        row = frames[index]
        groups["allMatched"].append(sample)
        group = ("creationFrames" if row["shaders"] or row["pipelines"] else
                 "above50MsWithoutCreation" if row["ms"] > 50 else "otherFrames")
        groups[group].append(sample)
    first_epoch = min(sample["monoNs"] for sample in samples) + offset
    last_epoch = max(sample["monoNs"] for sample in samples) + offset
    window_frames = [row for row, end in zip(frames, ends) if first_epoch <= end <= last_epoch]
    result = {"aligned": True, "bridgeDriftNs": drift, "unmatchedSamples": unmatched,
            "sampleWindowFrameCost": summarize_frames(window_frames),
            "sampleWindowSeconds": (last_epoch - first_epoch) / 10**9,
            "boundaryToleranceNs": 1_000_000, "cohorts": {key: summarize(value) for key, value in groups.items()}}
    if "t" in frames[0]:
        origin = frames[0]["epoch"] - frames[0]["t"]
        result["csvRelativeWindowSeconds"] = [first_epoch / 10**9 - origin, last_epoch / 10**9 - origin]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path, help="perf script --ns -F comm,pid,tid,time,event,ip,sym,dso output")
    parser.add_argument("--sampler", type=Path, required=True, help="cpu-sampler.json from the same run")
    parser.add_argument("--frames", type=Path, help="frame-cost.csv from the same run")
    args = parser.parse_args()
    try:
        metadata = json.loads(args.sampler.read_text())
        samples, unparsed = parse_samples(args.script.read_text())
        selected = [sample for sample in samples if sample["pid"] == metadata["pid"]]
        report = {"all": summarize(selected), "foreignProcessSamples": len(samples) - len(selected),
                  "unparsedHeaders": unparsed, "recordingCompleted": metadata["completed"],
                  "limits": ["Samples represent user-space CPU activity, not GPU cost or waiting time.",
                             "Sample shares are relative to recorded user CPU samples, not wall-frame percentages.",
                             "Caller stacks may be incomplete; this report attributes only sampled leaf symbols.",
                             "Frame-cohort alignment is temporal association, not a causal cost decomposition."]}
        if args.frames:
            rows, incomplete = read_frames(args.frames)
            report["incompleteFrameTailRows"] = incomplete
            report["frameAlignment"] = align_frames(selected, rows, metadata)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))
    return 0 if selected and not unparsed and metadata["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
