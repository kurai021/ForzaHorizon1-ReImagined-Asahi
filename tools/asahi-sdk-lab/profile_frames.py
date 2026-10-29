#!/usr/bin/env python3
"""Offline frame-cost triage: budget, slow-frame cohorts and counter associations.

Reads existing CSVs only. CPU and nested wall-time counters overlap; these are
not an additive CPU/GPU breakdown or physical display timing measurements.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

REQUIRED = {"t", "ms", "cpu_ms", "gpu_wait_ms", "guest_wait_ms", "draws", "shaders", "pipelines"}
COUNTERS = ("cpu_ms", "gpu_wait_ms", "guest_wait_ms", "draws", "texture_load_ms",
            "upload_ms", "shader_ms", "pipeline_ms", "readbacks", "readback_wait_ms")


def read_frames(path):
    """Only a structurally truncated final row may be dropped after hard exit."""
    with path.open() as source:
        reader = csv.DictReader(source)
        if not REQUIRED.issubset(reader.fieldnames or ()):
            raise ValueError("Missing required frame-cost columns")
        raw = list(reader)
    rows = []
    incomplete = 0
    for index, row in enumerate(raw):
        if None in row or any(value in (None, "") for value in row.values()):
            if index != len(raw) - 1 or None in row:
                raise ValueError(f"Malformed CSV row {index + 2}")
            incomplete += 1
            continue
        values = {key: float(value) for key, value in row.items()}
        if not all(math.isfinite(value) and value >= 0 for value in values.values()) or values["ms"] <= 0:
            raise ValueError(f"Invalid numeric value at CSV row {index + 2}")
        if rows and values["t"] <= rows[-1]["t"]:
            raise ValueError("Frame timestamps must increase strictly")
        rows.append(values)
    return rows, incomplete


def summarize(rows):
    if not rows:
        return {"frames": 0}
    times = sorted(row["ms"] for row in rows)
    quantile = lambda p: times[max(0, math.ceil(p * len(times)) - 1)]
    return {"frames": len(rows), "fps": 1000 * len(rows) / sum(times),
            "meanMs": statistics.mean(times), "p50Ms": quantile(.5),
            "p95Ms": quantile(.95), "p99Ms": quantile(.99), "maxMs": times[-1],
            "above30FpsBudgetPercent": 100 * sum(t > 1000 / 30 for t in times) / len(times),
            "above50MsPercent": 100 * sum(t > 50 for t in times) / len(times),
            "above66_667MsPercent": 100 * sum(t > 1000 / 15 for t in times) / len(times),
            "counterMeans": {key: statistics.mean(row[key] for row in rows)
                             for key in COUNTERS if key in rows[0]},
            "shaderObjects": sum(row["shaders"] for row in rows),
            "pipelineObjects": sum(row["pipelines"] for row in rows)}


def profile(rows, start, end):
    selected = [row for row in rows if start <= row["t"] < end]
    creating = lambda row: row["shaders"] > 0 or row["pipelines"] > 0
    slow = lambda row: row["ms"] > 50
    cohorts = {"all": selected,
               "withShaderOrPipelineCreation": [row for row in selected if creating(row)],
               "withoutShaderOrPipelineCreation": [row for row in selected if not creating(row)],
               "above50Ms": [row for row in selected if slow(row)],
               "above50MsWithoutCreation": [row for row in selected if slow(row) and not creating(row)]}
    return {"windowSeconds": [start, end], "selection": "frame end t in [start, end)",
            "percentileMethod": "nearest rank",
            "cohorts": {name: summarize(group) for name, group in cohorts.items()},
            "slowestFrames": [{key: row[key] for key in ("t", "ms", "shaders", "pipelines", *COUNTERS)
                               if key in row}
                              for row in sorted(selected, key=lambda row: row["ms"], reverse=True)[:10]]}


def parse_window(value):
    try:
        start, end = map(float, value.split(":"))
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
            raise ValueError()
        return start, end
    except ValueError:
        raise argparse.ArgumentTypeError("Use a finite start:end in seconds, with 0 <= start < end")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--window", action="append", type=parse_window, required=True)
    args = parser.parse_args()
    try:
        rows, incomplete = read_frames(args.csv)
        windows = [profile(rows, *window) for window in args.window]
    except (OSError, ValueError) as error:
        parser.error(str(error))
    with args.csv.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    print(json.dumps({"sourceSha256": digest, "completeRows": len(rows),
        "incompleteTailRows": incomplete, "windows": windows,
        "limits": ["Command-processor cadence, not physical presentation timestamps or GPU execution time.",
                   "cpu_ms is CPU time of the command-processor thread only, not all game threads.",
                   "Other millisecond counters are wall-time scopes; they may overlap each other and CPU time.",
                   "Excluding creation frames describes a cohort, not a prediction with compilation removed.",
                   "Differences between manual routes are not a controlled MSAA effect."]}, indent=2))
    return 0 if all(window["cohorts"]["all"]["frames"] for window in windows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
