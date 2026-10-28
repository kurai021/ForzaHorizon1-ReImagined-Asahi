#!/usr/bin/env python3
"""Validate process-local drmIoctl telemetry, not raw syscall attempts or GPU execution."""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path

COLUMNS = 'ticket,tid,request,start_ns,end_ns,wall_ns,user_ns,system_ns,cpu_known,result,errno_value'.split(',')
MAX_RECORDS = 262144


def load_profile(path):
    if path.stat().st_size > 64 * 1024**2:
        raise ValueError('DRM trace exceeds 64 MiB')
    metadata = {}
    rows = []
    with path.open() as source:
        def lines():
            for line in source:
                if line.startswith('#'):
                    key, value = line[1:].rstrip('\n').split('=', 1)
                    if key in metadata:
                        raise ValueError('Duplicate DRM trace metadata')
                    metadata[key] = value
                else:
                    yield line
        reader = csv.DictReader(lines())
        if reader.fieldnames != COLUMNS:
            raise ValueError('Unexpected DRM trace columns')
        for raw in reader:
            if len(rows) >= MAX_RECORDS or None in raw or any(v in (None, '') for v in raw.values()):
                raise ValueError('Malformed or oversized DRM trace')
            row = {key: int(value) for key, value in raw.items()}
            if row['ticket'] != len(rows) or row['tid'] <= 0:
                raise ValueError('DRM event ticket/TID invalid')
            if any(value < 0 or value >= 2**64 for key, value in row.items() if key != 'result'):
                raise ValueError('Invalid unsigned DRM event value')
            if not -(2**31) <= row['result'] < 2**31 or row['cpu_known'] not in (0, 1):
                raise ValueError('Invalid DRM result/clock flag')
            if row['end_ns'] < row['start_ns'] or row['wall_ns'] != row['end_ns'] - row['start_ns']:
                raise ValueError('Inconsistent DRM wall interval')
            rows.append(row)
    if (metadata.get('schema') != 'asahi-drm-ioctl-v1' or metadata.get('clock') != 'monotonic_ns' or
            metadata.get('cpu_clock') != 'getrusage_thread' or
            metadata.get('boundary') != 'drmIoctl' or metadata.get('raw_syscall_attempts') != 'unknown' or
            metadata.get('payload_recorded') != '0'):
        raise ValueError('Unexpected DRM schema/measurement boundary')
    start, end = int(metadata['window_start_ns']), int(metadata['window_end_ns'])
    capacity = int(metadata['capacity'])
    if (not 0 < int(metadata['pid']) < 2**31 or not 0 < int(metadata['submit_request']) < 2**64 or
            not 0 < int(metadata['origin_mono_ns']) < 2**64 or not 0 < int(metadata['origin_epoch_ns']) < 2**64):
        raise ValueError('Invalid DRM process/request/clock identity')
    if not 0 <= start < end or end - start > 45 * 10**9 or not 1 <= capacity <= MAX_RECORDS or len(rows) > capacity:
        raise ValueError('Invalid DRM window/capacity')
    if any(not start <= row['start_ns'] < end for row in rows):
        raise ValueError('DRM event began outside admission window')
    return metadata, rows


def summarize(rows, end):
    contained = [row for row in rows if row['end_ns'] <= end]
    walls = sorted(row['wall_ns'] for row in contained)
    return {'calls': len(rows), 'nonzeroReturns': sum(row['result'] != 0 for row in rows),
            'crossingCallsExcludedFromDurations': len(rows) - len(contained),
            'containedCalls': len(contained), 'wallTotalMs': sum(walls) / 1e6,
            'wallMeanUs': sum(walls) / len(walls) / 1000 if walls else None,
            'wallP50Us': walls[math.ceil(.5 * len(walls)) - 1] / 1000 if walls else None,
            'wallP95Us': walls[math.ceil(.95 * len(walls)) - 1] / 1000 if walls else None,
            'cpuKnownCalls': sum(row['cpu_known'] for row in contained),
            'userCpuTotalMs': sum(row['user_ns'] for row in contained if row['cpu_known']) / 1e6,
            'systemCpuTotalMs': sum(row['system_ns'] for row in contained if row['cpu_known']) / 1e6}


def analyse(path):
    metadata, rows = load_profile(path)
    complete = metadata.get('status') == 'complete'
    issues = []
    footer = ('records', 'dropped', 'clock_errors', 'thread_overflow', 'admission_closed_ns',
              'export_start_ns', 'export_end_ns')
    if not complete:
        issues.append('no complete footer (early exit or pending telemetry writers)')
    if any(key not in metadata for key in footer):
        issues.append('missing required footer fields')
    else:
        if any(int(metadata[key]) < 0 for key in footer):
            raise ValueError('Invalid negative DRM footer counter')
        if int(metadata['records']) != len(rows):
            issues.append('footer record count differs')
        if int(metadata['dropped']): issues.append('bounded recorder lost events')
        if int(metadata['clock_errors']): issues.append('recorder clock errors')
        if int(metadata['thread_overflow']): issues.append('bounded thread-name table overflow')
        if not (int(metadata['window_end_ns']) <= int(metadata['admission_closed_ns']) <=
                int(metadata['export_start_ns']) <= int(metadata['export_end_ns'])):
            issues.append('invalid export/admission timing')
        if rows and max(row['end_ns'] for row in rows) > int(metadata['export_start_ns']):
            issues.append('export began before recorded calls finished')
    threads = {}
    for key, value in metadata.items():
        if key.startswith('thread_') and key[7:].isdigit():
            if len(value) > 32:
                raise ValueError('Invalid encoded thread name')
            threads[int(key[7:])] = bytes.fromhex(value).decode(errors='replace')
    if any(row['tid'] not in threads for row in rows):
        issues.append('events lack retained thread identities')
    if any(not row['cpu_known'] for row in rows):
        issues.append('CPU accounting unavailable')
    submit = int(metadata['submit_request'])
    native = [row for row in rows if row['request'] == submit]
    if not native: issues.append('no observed native-submit requests; zero cost cannot be inferred')
    if any(row['result'] != 0 for row in native): issues.append('native-submit request returned nonzero')
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['request']].append(row)
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    end = int(metadata['window_end_ns'])
    return {'sourceSha256': digest, 'metadata': metadata, 'completeFile': complete,
            'usableForNativeAttribution': not issues, 'issues': issues,
            'threads': {str(tid): name for tid, name in threads.items()},
            'nativeSubmit': summarize(native, end),
            'requests': {str(request): summarize(group, end) for request, group in grouped.items()},
            'limits': ['Counts are drmIoctl invocations. Underlying libdrm may retry ioctl; raw kernel syscall attempts are not counted.',
                       'Host wall/user/system timing includes observer cost, not GPU execution.',
                       'No payload, memory address or buffer content is recorded; request matching alone does not independently verify the DRM device.',
                       'Complete capture does not prove the actual driver used this symbol or bound instrumentation overhead without a live reachability/overhead check.',
                       'Thread comm names are identities for inspection, not automatic proof of Vulkan submission/frame ownership.']}


def join_submissions(path, submission, tid=None):
    from analyse_submission_timeline import load_jobs
    report = analyse(path)
    if not report['usableForNativeAttribution']:
        raise ValueError('DRM trace is not qualified for a submission join')
    metadata, rows = load_profile(path)
    sdk, jobs = load_jobs(submission)
    sm = sdk['metadata']
    if sm.get('clock') != 'steady_ns':
        raise ValueError('Unexpected SDK clock; no automatic clock conversion')
    if not (max(int(sm['window_end_ns']), max(job['total']['end_ns'] for job in jobs)) <=
            int(sm['export_start_ns']) <= int(sm['export_end_ns'])):
        raise ValueError('SDK export timing does not follow completed jobs/window')
    offset = int(metadata['origin_epoch_ns']) - int(metadata['origin_mono_ns'])
    sdk_offset = int(sm['origin_epoch_ns']) - int(sm['origin_mono_ns'])
    if abs(offset - sdk_offset) > 10000000:
        raise ValueError('DRM/SDK realtime bridges disagree by more than 10 ms')
    scopes = sorted((job['queue_submit'] for job in jobs), key=lambda row: row['start_ns'])
    if (int(metadata['window_start_ns']) > min(int(sm['window_start_ns']), scopes[0]['start_ns']) or
            int(metadata['window_end_ns']) < max(int(sm['window_end_ns']), max(row['end_ns'] for row in scopes))):
        raise ValueError('DRM window does not cover all SDK queue-submit scopes')
    names = {int(key): value for key, value in report['threads'].items()}
    if tid is None:
        candidates = [value for value, name in names.items() if name.startswith('Vulkan Submiss')]
        if len(candidates) != 1:
            raise ValueError('Expected one named SDK submission thread; inspect or supply an explicit --tid')
        tid = candidates[0]
    if tid <= 0 or tid not in names:
        raise ValueError('Requested TID is absent from DRM thread metadata')
    calls = sorted((row for row in rows if row['tid'] == tid), key=lambda row: row['start_ns'])
    if any(a['end_ns'] > b['start_ns'] for a, b in zip(calls, calls[1:])):
        raise ValueError('Nested/overlapping DRM scopes on the selected thread cannot be summed')
    native_request = int(metadata['submit_request'])
    per_job = []
    unmatched = []
    index = 0
    for scope in scopes:
        while index < len(calls) and calls[index]['end_ns'] <= scope['start_ns']:
            unmatched.append(calls[index])
            index += 1
        owned = []
        while index < len(calls) and calls[index]['start_ns'] < scope['end_ns']:
            call = calls[index]
            if call['start_ns'] < scope['start_ns'] or call['end_ns'] > scope['end_ns']:
                raise ValueError('DRM call crosses an SDK scope boundary; do not guess attribution')
            owned.append(call)
            index += 1
        native = [row for row in owned if row['request'] == native_request]
        per_job.append({'submission': scope['submission'], 'frame': scope['frame'],
                        'nativeCalls': len(native), 'otherDrmCalls': len(owned) - len(native),
                        'nativeWallMs': sum(row['wall_ns'] for row in native) / 1e6,
                        'nativeSystemCpuMs': sum(row['system_ns'] for row in native) / 1e6,
                        'queueSubmitWallMs': scope['wall_ns'] / 1e6,
                        'queueSubmitSystemCpuMs': scope['system_ns'] / 1e6})
    unmatched += calls[index:]
    inside = [row for row in unmatched if row['start_ns'] >= int(sm['window_start_ns']) and
              row['end_ns'] <= int(sm['window_end_ns']) and row['request'] == native_request]
    counts = sorted(row['nativeCalls'] for row in per_job)
    with submission.open('rb') as source:
        sdk_digest = hashlib.file_digest(source, 'sha256').hexdigest()
    return {'drmTraceSha256': report['sourceSha256'], 'submissionTraceSha256': sdk_digest,
            'selectedTid': tid, 'selectedThreadName': names[tid], 'jobs': len(per_job),
            'jobsWithZeroNativeCalls': sum(count == 0 for count in counts),
            'nativeCallsMeanPerJob': sum(counts) / len(counts),
            'nativeCallsP50PerJob': counts[math.ceil(.5 * len(counts)) - 1],
            'nativeCallsP95PerJob': counts[math.ceil(.95 * len(counts)) - 1],
            'nativeCallsMaxPerJob': counts[-1],
            'nativeCallsOutsideQueueScopesInsideSdkWindow': len(inside),
            'bridgeOffsetDifferenceNs': offset - sdk_offset,
            'perJob': per_job,
            'limits': ['Counts are drmIoctl invocations, not libdrm internal syscall retry attempts.',
                       'Automatic TID selection uses a unique expected thread comm plus interval containment; inspect identity before accepting live attribution.',
                       'SDK and DRM stamps use Linux monotonic time. Coarse epoch bridge consistency is not an independent nanosecond drift calibration.',
                       'The SDK v1 trace has no PID. Supply a retained same-run pair; bridge agreement alone cannot prove process identity.',
                       'Nested scopes contain observer overhead; do not subtract CPU snapshots to infer GPU or blocked time.',
                       'Native calls outside queue scopes are retained as an explicit finding, not silently assigned to a job.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--submissions', type=Path, help='Same-run ordinary SDK submission trace, covered by the DRM window')
    parser.add_argument('--tid', type=int, help='Explicit inspected submission-thread TID for an optional join')
    args = parser.parse_args()
    try:
        report = analyse(args.csv)
        if args.submissions:
            report['submissionJoin'] = join_submissions(args.csv, args.submissions, args.tid)
        elif args.tid is not None:
            raise ValueError('--tid requires --submissions')
    except (OSError, ValueError, KeyError, csv.Error) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))
    return 0 if report['usableForNativeAttribution'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
