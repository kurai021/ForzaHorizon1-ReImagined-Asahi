#!/usr/bin/env python3
"""Validate bounded submission traces and summarize host stages, never GPU execution."""
import argparse
from collections import defaultdict
import csv
import json
import math
from pathlib import Path

COLUMNS = "kind,submission,frame,stage,start_ns,end_ns,wall_ns,user_ns,system_ns,cpu_known,main_words,hoisted_words,result,supported,skipped,crossed_end".split(",")
PHASES = {"reset_pool", "begin", "replay", "end", "queue_acquire", "queue_submit", "total", "queue_delay"}
UNKNOWN_RESULT = -(2**31)


def analyse(path):
    if path.stat().st_size > 64 * 1024**2:
        raise ValueError("Trace exceeds 64 MiB")
    metadata = {}
    groups = defaultdict(lambda: {"rows": 0, "crossingRows": 0, "wall": [], "user": 0, "system": 0, "cpuRows": 0})
    jobs = defaultdict(set)
    unsupported, skipped, failed, unknown_result = set(), set(), set(), set()
    wait_rows = unknown_cpu = count = 0
    with path.open() as source:
        def lines():
            for line in source:
                if line.startswith("#"):
                    key, value = line[1:].strip().split("=", 1)
                    if key in metadata:
                        raise ValueError(f"Duplicate metadata: {key}")
                    metadata[key] = value
                else:
                    yield line
        reader = csv.DictReader(lines())
        if reader.fieldnames != COLUMNS:
            raise ValueError("Unexpected trace columns")
        start = int(metadata["window_start_ns"])
        end = int(metadata["window_end_ns"])
        if not 0 <= start < end or end - start > 45 * 10**9:
            raise ValueError("Invalid capture interval")
        for raw in reader:
            count += 1
            if count > 150000 or None in raw or any(v in (None, "") for v in raw.values()):
                raise ValueError("Malformed or oversized trace")
            row = {key: int(raw[key]) for key in COLUMNS if key not in ("kind", "stage")}
            if any(value < 0 or value >= 2**64 for key, value in row.items() if key != "result"):
                raise ValueError("Invalid unsigned counter")
            if not -(2**31) <= row["result"] < 2**31:
                raise ValueError("Invalid result value")
            if any(row[key] not in (0, 1) for key in ("cpu_known", "supported", "skipped", "crossed_end")):
                raise ValueError("Invalid boolean flag")
            if row["end_ns"] < row["start_ns"] or row["wall_ns"] != row["end_ns"] - row["start_ns"]:
                raise ValueError("Inconsistent wall interval")
            crossing = row["end_ns"] > end
            if row["crossed_end"] != crossing or row["start_ns"] < start:
                raise ValueError("Invalid boundary qualification")
            kind, stage = raw["kind"], raw["stage"]
            key = (row["submission"], row["frame"])
            if kind == "job" and stage in PHASES:
                if stage in jobs[key]:
                    raise ValueError("Duplicate job stage")
                jobs[key].add(stage)
                if not row["supported"]: unsupported.add(key)
                if row["skipped"]: skipped.add(key)
                if row["result"] == UNKNOWN_RESULT: unknown_result.add(key)
                elif row["result"] != 0: failed.add(key)
            elif kind == "wait" and stage in ("host_submission", "fence_completion"):
                wait_rows += 1
            else:
                raise ValueError("Unknown record kind/stage")
            group = groups[kind + "/" + stage]
            group["rows"] += 1
            if crossing:
                group["crossingRows"] += 1
                continue
            group["wall"].append(row["wall_ns"])
            if row["cpu_known"]:
                group["cpuRows"] += 1
                group["user"] += row["user_ns"]
                group["system"] += row["system_ns"]
            elif stage != "queue_delay":
                unknown_cpu += 1
    if metadata.get("schema") != "asahi-submission-v1" or metadata.get("gpu_execution_timestamps") != "0":
        raise ValueError("Unexpected schema or timing domain")
    complete = metadata.get("status") == "complete"
    dropped_jobs = int(metadata.get("dropped_jobs", 0))
    dropped_waits = int(metadata.get("dropped_waits", 0))
    if dropped_jobs < 0 or dropped_waits < 0:
        raise ValueError("Invalid loss counters")
    issues = []
    if not complete: issues.append("capture has no complete footer (hard exit or pending jobs)")
    footer = ("jobs", "waits", "dropped_jobs", "dropped_waits", "export_start_ns", "export_end_ns")
    if complete and any(key not in metadata for key in footer):
        issues.append("complete marker lacks required footer fields")
    if not jobs: issues.append("no captured jobs")
    if dropped_jobs or dropped_waits: issues.append("bounded telemetry lost records")
    if unsupported: issues.append("native/split job path lacks full stage coverage")
    if skipped: issues.append("jobs skipped after an earlier rendering failure")
    if failed or unknown_result: issues.append("failed jobs or unknown job results")
    missing = sum(stages != PHASES for key, stages in jobs.items() if key not in failed | skipped | unsupported)
    if missing: issues.append("successful jobs lack expected stages")
    if unknown_cpu: issues.append("CPU accounting unavailable on measured rows")
    if complete and (int(metadata.get("jobs", -1)) != len(jobs) or int(metadata.get("waits", -1)) != wait_rows):
        issues.append("footer counts do not match records")
    if len(jobs) > 16384 or wait_rows > 8192:
        raise ValueError("Trace exceeds producer record bounds")
    summaries = {}
    for name, group in groups.items():
        values = sorted(group["wall"])
        summaries[name] = {"rows": group["rows"], "crossingRowsExcluded": group["crossingRows"],
            "containedRows": len(values), "wallTotalMs": sum(values) / 1e6,
            "wallMeanMs": sum(values) / len(values) / 1e6 if values else None,
            "wallP95Ms": values[math.ceil(.95 * len(values)) - 1] / 1e6 if values else None,
            "cpuKnownRows": group["cpuRows"], "userCpuTotalMs": group["user"] / 1e6,
            "systemCpuTotalMs": group["system"] / 1e6}
    return {"metadata": metadata, "completeFile": complete, "usableForCpuAttribution": not issues,
        "issues": issues, "capturedJobs": len(jobs), "capturedWaits": wait_rows,
        "failedJobs": len(failed), "unsupportedJobs": len(unsupported), "skippedJobs": len(skipped),
        "stages": summaries, "limits": [
            "All durations are host-side; fence waits are not GPU execution timestamps.",
            "job/total contains phase scopes. Waits can overlap other-thread work; do not sum all groups.",
            "CPU accounting has microsecond resolution and includes observer overhead.",
            "Cross-window intervals are counted but excluded from duration totals."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    args = parser.parse_args()
    try:
        report = analyse(args.csv)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))
    return 0 if report["usableForCpuAttribution"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
