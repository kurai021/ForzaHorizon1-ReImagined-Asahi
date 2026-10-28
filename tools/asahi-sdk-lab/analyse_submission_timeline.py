#!/usr/bin/env python3
"""Offline job/frame associations for the pinned recorder; never GPU/critical-path timings."""
import argparse
from bisect import bisect_right
from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

from analyse_submission_profile import analyse, PHASES
from profile_frames import read_frames


def pearson(xs, ys):
    if len(xs) != len(ys):
        raise ValueError("Correlation inputs differ in length")
    if len(xs) < 2:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    xx = sum((x - mx)**2 for x in xs)
    yy = sum((y - my)**2 for y in ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(xx * yy) if xx and yy else None


def ranks(values):
    result = [0.0] * len(values)
    order = sorted(range(len(values)), key=values.__getitem__)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        for index in order[start:end]:
            result[index] = (start + end - 1) / 2
        start = end
    return result


def association(xs, ys):
    return {"pairs": len(xs), "pearson": pearson(xs, ys), "spearman": pearson(ranks(xs), ranks(ys))}


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def job_associations(jobs):
    features = {"words": [j["queue_delay"]["main_words"] + j["queue_delay"]["hoisted_words"] for j in jobs],
                "submitWallMs": [j["queue_submit"]["wall_ns"] / 1e6 for j in jobs],
                "submitSystemCpuMs": [j["queue_submit"]["system_ns"] / 1e6 for j in jobs],
                "replayWallMs": [j["replay"]["wall_ns"] / 1e6 for j in jobs]}
    return {a + "/" + b: association(features[a], features[b]) for a, b in
            (("words", "submitWallMs"), ("words", "submitSystemCpuMs"),
             ("words", "replayWallMs"), ("submitSystemCpuMs", "submitWallMs"))}


def load_jobs(path):
    aggregate = analyse(path)
    if not aggregate["usableForCpuAttribution"]:
        raise ValueError("Trace is not qualified: " + "; ".join(aggregate["issues"]))
    jobs = defaultdict(dict)
    with path.open() as source:
        for raw in csv.DictReader(line for line in source if not line.startswith("#")):
            if raw["kind"] != "job":
                continue
            row = {key: int(value) for key, value in raw.items() if key not in ("kind", "stage")}
            jobs[(row["submission"], row["frame"])][raw["stage"]] = row
    ordered = sorted(jobs.values(), key=lambda job: job["total"]["start_ns"])
    previous_end = 0
    for job in ordered:
        if set(job) != PHASES:
            raise ValueError("Timeline requires all ordinary-job stages")
        total, delay = job["total"], job["queue_delay"]
        for stage in job.values():
            for key in ("submission", "frame", "main_words", "hoisted_words", "result", "supported", "skipped"):
                if stage[key] != delay[key]:
                    raise ValueError("Job identity/counters differ across stages")
        if total["start_ns"] < previous_end or delay["end_ns"] > total["start_ns"]:
            raise ValueError("Submission worker jobs overlap or start before dequeue")
        previous_end = total["end_ns"]
        end = total["start_ns"]
        for name in ("reset_pool", "begin", "replay", "end", "queue_acquire", "queue_submit"):
            stage = job[name]
            if stage["start_ns"] < end or stage["end_ns"] > total["end_ns"]:
                raise ValueError("Job phase ordering/containment differs from recorder contract")
            end = stage["end_ns"]
    return aggregate, ordered


def cohort(rows):
    if not rows:
        return {"frames": 0}
    return {"frames": len(rows), "frameMeanMs": statistics.mean(r["ms"] for r in rows),
            "frameMedianMs": statistics.median(r["ms"] for r in rows),
            "readbackFrames": sum(r["readbacks"] > 0 for r in rows),
            "multiJobFrames": sum(r["jobs"] > 1 for r in rows),
            "means": {key: statistics.mean(r[key] for r in rows) for key in
                      ("draws", "jobs", "taggedSubmitWallMs", "taggedSubmitSystemCpuMs", "taggedReplayWallMs",
                       "temporalSubmitOverlapWallMs", "gpu_wait_ms", "readback_wait_ms")}}


def timeline(trace, frame_csv, frame_id_offset=2, guard_ms=1):
    if not 0 <= frame_id_offset <= 1000000 or not math.isfinite(guard_ms) or not .1 <= guard_ms <= 5:
        raise ValueError("Use a nonnegative frame-ID offset and a 0.1..5 ms clock guard")
    if frame_csv.stat().st_size > 64 * 1024**2:
        raise ValueError("Frame CSV exceeds 64 MiB")
    aggregate, jobs = load_jobs(trace)
    frames, tail = read_frames(frame_csv)
    if not frames or len(frames) > 100000 or not {"epoch", "readbacks", "readback_wait_ms"}.issubset(frames[0]):
        raise ValueError("Require 1..100000 frame rows with epoch and readback counters")
    metadata = aggregate["metadata"]
    if metadata["clock"] != "steady_ns":
        raise ValueError("Unexpected recorder clock")
    bridge_offset = int(metadata["origin_epoch_ns"]) - int(metadata["origin_mono_ns"])
    start, end = int(metadata["window_start_ns"]), int(metadata["window_end_ns"])
    if int(metadata["export_start_ns"]) < end or int(metadata["export_end_ns"]) < int(metadata["export_start_ns"]):
        raise ValueError("Export did not occur after the capture window")
    if int(metadata["export_start_ns"]) < max(job["total"]["end_ns"] for job in jobs):
        raise ValueError("Export started before admitted jobs finished")
    guard = round(guard_ms * 1e6)
    intervals = []
    previous_epoch = -1
    for row in frames:
        epoch = round(row["epoch"] * 1e9)
        if epoch <= previous_epoch:
            raise ValueError("Frame epoch stamps must increase strictly")
        previous_epoch = epoch
        finish = epoch - bridge_offset
        intervals.append((finish - round(row["ms"] * 1e6), finish))
    selected = [index for index, (begin, finish) in enumerate(intervals)
                if begin >= start + guard and finish <= end - guard]
    if not selected:
        raise ValueError("No fully contained frames in the recording window")
    offsets = [frames[i]["epoch"] - frames[i]["t"] for i in selected]
    spread_ms = (max(offsets) - min(offsets)) * 1000
    if spread_ms > 10:
        raise ValueError("Frame epoch/relative-time offset changed by more than 10 ms")
    by_frame = defaultdict(list)
    for job in jobs:
        delay = job["queue_delay"]
        index = delay["frame"] - frame_id_offset
        if not 0 <= index < len(frames):
            raise ValueError("Job frame tag is outside the supplied CSV")
        begin, finish = intervals[index]
        if not begin - guard <= delay["start_ns"] <= finish + guard:
            raise ValueError("Job enqueue is outside its tagged frame; do not guess another join")
        by_frame[index].append(job)
    contained_jobs = [job for job in jobs if job["total"]["end_ns"] <= end]
    single_jobs = [j for j in contained_jobs if len(by_frame[j["queue_delay"]["frame"] - frame_id_offset]) == 1]
    submit_intervals = [(j["queue_submit"]["start_ns"], j["queue_submit"]["end_ns"]) for j in jobs]
    submit_ends = [b for _, b in submit_intervals]
    prefix = [0]
    for a, b in submit_intervals:
        prefix.append(prefix[-1] + b - a)
    def covered_at(point):
        index = bisect_right(submit_ends, point)
        total = prefix[index]
        if index < len(submit_intervals):
            total += max(0, point - submit_intervals[index][0])
        return total
    metrics = []
    excluded = 0
    finished_later = tagged_jobs = 0
    for index in selected:
        row, owned = frames[index], by_frame[index]
        if any(job["total"]["end_ns"] > end for job in owned):
            excluded += 1
            continue
        begin, finish = intervals[index]
        overlap = (covered_at(finish) - covered_at(begin)) / 1e6
        tagged_jobs += len(owned)
        finished_later += sum(job["queue_submit"]["end_ns"] > finish + guard for job in owned)
        metrics.append({"frame": index + frame_id_offset, "csvTime": row["t"], "ms": row["ms"],
                        **{key: row.get(key, 0) for key in
                           ("draws", "gpu_wait_ms", "readback_wait_ms", "readbacks", "shaders", "pipelines")},
                        "jobs": len(owned),
                        "taggedSubmitWallMs": sum(j["queue_submit"]["wall_ns"] for j in owned) / 1e6,
                        "taggedSubmitSystemCpuMs": sum(j["queue_submit"]["system_ns"] for j in owned) / 1e6,
                        "taggedReplayWallMs": sum(j["replay"]["wall_ns"] for j in owned) / 1e6,
                        "temporalSubmitOverlapWallMs": overlap})
    if not metrics:
        raise ValueError("All frames were censored by cross-window jobs")
    cpu = sum(j["queue_submit"]["user_ns"] + j["queue_submit"]["system_ns"] for j in contained_jobs)
    wall = sum(j["queue_submit"]["wall_ns"] for j in contained_jobs)
    cpu_ahead = sum(j["queue_submit"]["user_ns"] + j["queue_submit"]["system_ns"] >
                    j["queue_submit"]["wall_ns"] for j in contained_jobs)
    total_readback = sum(r["readback_wait_ms"] for r in metrics)
    tail_readback = sum(r["readback_wait_ms"] for r in metrics if r["ms"] > 1000 / 15)
    return {"traceSha256": digest(trace), "frameCsvSha256": digest(frame_csv), "gpuWork": False,
            "alignment": {"jobFrameTagsValidated": len(jobs), "frameIdOffset": frame_id_offset,
                          "guardMs": guard_ms, "frameEpochOffsetSpreadMs": spread_ms,
                          "fullyContainedFrames": len(metrics), "crossWindowJobFramesExcluded": excluded,
                          "incompleteCsvTailRows": tail, "taggedJobsInSelectedFrames": tagged_jobs,
                          "jobsFinishingAfterTaggedFrameEndPlusGuard": finished_later},
            "jobAssociations": job_associations(contained_jobs),
            "jobAssociationsSingleJobFrames": job_associations(single_jobs),
            "frameAssociations": {key: association([r["ms"] for r in metrics], [r[key] for r in metrics])
                                  for key in ("taggedSubmitWallMs", "taggedSubmitSystemCpuMs", "taggedReplayWallMs",
                                              "temporalSubmitOverlapWallMs", "draws", "gpu_wait_ms", "readback_wait_ms")},
            "cohorts": {"all": cohort(metrics),
                        "near33ms": cohort([r for r in metrics if abs(r["ms"] - 1000 / 30) <= 2]),
                        "near50ms": cohort([r for r in metrics if abs(r["ms"] - 50) <= 2]),
                        "above66ms": cohort([r for r in metrics if r["ms"] > 1000 / 15])},
            "readbackWaitInAbove66msPercent": 100 * tail_readback / total_readback if total_readback else None,
            "submitCpuAccounting": {"cpuToWallRatio": cpu / wall if wall else None,
                                    "jobsWithCpuGreaterThanWall": cpu_ahead,
                                    "note": "Snapshot ordering and CPU accounting granularity can exceed wall on individual scopes; do not infer negative blocked time."},
            "slowestFrames": sorted(metrics, key=lambda row: row["ms"], reverse=True)[:10],
            "limits": ["Exploratory association in one scene/window, not causality or a unique critical path.",
                       "Tagged work may execute after that command-processor frame ends. Temporal overlap is wall-only, not prorated CPU or GPU time.",
                       "Deferred words are a workload proxy, not draws, native control streams, ioctl count or bytes submitted to the kernel.",
                       "Wait scopes overlap and can be nested; do not sum frame/job/wait groups into a frame breakdown.",
                       "Frame-ID offset 2 follows the pinned single-context recorder's initialization skip; every enqueue must validate, without fitting another offset.",
                       "No runtime, driver, synchronization, graphics quality or physics settings are changed."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("frames", type=Path)
    parser.add_argument("--frame-id-offset", type=int, default=2)
    parser.add_argument("--guard-ms", type=float, default=1)
    args = parser.parse_args()
    try:
        result = timeline(args.trace, args.frames, args.frame_id_offset, args.guard_ms)
    except (OSError, ValueError, KeyError, csv.Error) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
