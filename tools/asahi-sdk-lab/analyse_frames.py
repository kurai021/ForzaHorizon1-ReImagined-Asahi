#!/usr/bin/env python3
"""Summarize complete frame-cost CSV rows in explicitly selected time windows."""
import argparse
import csv
import json
from pathlib import Path
import statistics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--window", action="append", help="Start:end in seconds; repeatable")
    args = parser.parse_args()
    windows = []
    for window in args.window or ["20:60", "60:80", "80:110"]:
        try:
            lo, hi = map(float, window.split(":"))
            if not 0 <= lo < hi:
                raise ValueError()
        except ValueError:
            parser.error("Windows must be start:end with 0 <= start < end")
        windows.append((lo, hi))
    with args.csv.open() as source:
        raw = list(csv.DictReader(source))
    rows = [{key: float(value) for key, value in row.items()} for row in raw
            if all(value is not None and value != "" for value in row.values())]
    reports = []
    for lo, hi in windows:
        subset = [row for row in rows if lo <= row["t"] < hi]
        if not subset:
            continue
        times = sorted(row["ms"] for row in subset)
        report = {"windowSeconds": [lo, hi], "frames": len(subset),
                  "fps": 1000 / statistics.mean(times),
                  "p95Milliseconds": times[int(.95 * (len(times) - 1))]}
        report["mean"] = {key: statistics.mean(row[key] for row in subset) for key in
                         ("ms", "cpu_ms", "gpu_wait_ms", "guest_wait_ms", "draws", "texture_load_ms",
                          "upload_ms", "pipeline_ms", "readbacks", "readback_wait_ms", "texture_total_mb")}
        reports.append(report)
    print(json.dumps({"completeRows": len(rows), "incompleteRows": len(raw) - len(rows),
                      "windows": reports}, indent=2))
    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(main())
