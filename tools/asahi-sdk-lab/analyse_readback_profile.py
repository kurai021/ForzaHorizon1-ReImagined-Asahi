#!/usr/bin/env python3
"""Bounded offline resolve-readback reason analysis, never GPU execution attribution."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

COLUMNS = ('kind,frame,submission,key_id,generation,start_ns,end_ns,wall_ns,mode,length,index,size0,size1,flags,'
           'allocations,allocated_bytes,allocation_wall_ns,copies,copied_bytes,copy_wall_ns,resize_wait_ns,'
           'full_wait_ns,miss_wait_ns,failed_waits,retained_bytes').split(',')
V2_COLUMNS = COLUMNS + ['events']
WORK = ('allocations', 'allocated_bytes', 'allocation_wall_ns', 'copies', 'copied_bytes',
        'copy_wall_ns', 'resize_wait_ns', 'full_wait_ns', 'miss_wait_ns', 'failed_waits', 'retained_bytes')
FLAGS = {'thumbnail': 1, 'scaled': 2, 'entryPresent': 4, 'cpuValid': 8, 'historyHot': 16,
         'skipValid': 32, 'slotMiss': 64, 'evictAge': 128, 'evictCount': 256,
         'allocationFailed': 512, 'mapFailed': 1024}


def load(path):
    if path.stat().st_size > 16 * 1024**2:
        raise ValueError('Readback trace exceeds 16 MiB')
    metadata, rows = {}, []
    with path.open() as source:
        def lines():
            for line in source:
                if line.startswith('#'):
                    key, value = line[1:].rstrip('\n').split('=', 1)
                    if key in metadata:
                        raise ValueError('Duplicate readback metadata')
                    metadata[key] = value
                else:
                    yield line
        reader = csv.DictReader(lines())
        columns = reader.fieldnames  # force metadata/header consumption before schema selection
        v2 = metadata.get('schema') == 'asahi-readback-v2'
        if columns != (V2_COLUMNS if v2 else COLUMNS):
            raise ValueError('Unexpected readback columns')
        summary_keys = set()
        for raw in reader:
            if len(rows) >= (12288 if v2 else 8192) or None in raw or any(v in (None, '') for v in raw.values()):
                raise ValueError('Malformed or oversized readback records')
            row = {key: int(value) for key, value in raw.items()}
            if not v2: row['events'] = 1
            if any(v < 0 or v >= 2**64 for v in row.values()):
                raise ValueError('Invalid unsigned readback field')
            if row['kind'] not in ((0, 1, 2) if v2 else (0, 1)) or row['index'] not in (0, 1) or row['flags'] & ~2047:
                raise ValueError('Invalid readback kind/index/flags')
            if not 1 <= row['key_id'] <= 4096 or row['end_ns'] < row['start_ns']:
                raise ValueError('Invalid readback identity/interval')
            if row['kind'] == 2:
                if (row['key_id'] in summary_keys or not row['events'] or row['mode'] != 2 or row['flags'] != 44 or
                        any(row[k] for k in (*WORK, 'frame', 'submission', 'generation', 'length', 'index', 'size0', 'size1')) or
                        row['wall_ns'] > row['events'] * 1000000):
                    raise ValueError('Invalid or duplicate routine summary')
                summary_keys.add(row['key_id'])
            elif row['events'] != 1 or row['wall_ns'] != row['end_ns'] - row['start_ns']:
                raise ValueError('Invalid detailed event count/wall interval')
            if row['kind'] == 0 and row['mode'] not in (1, 2, 3):
                raise ValueError('Invalid effective resolve mode')
            if row['kind'] == 1 and row['mode'] != 0:
                raise ValueError('Eviction is not a resolve')
            if sum(row[k] for k in ('allocation_wall_ns', 'copy_wall_ns', 'resize_wait_ns', 'full_wait_ns', 'miss_wait_ns')) > row['wall_ns']:
                raise ValueError('Sequential timed readback phases exceed scope')
            rows.append(row)
    if (metadata.get('schema') not in ('asahi-readback-v1', 'asahi-readback-v2') or metadata.get('clock') != 'steady_ns' or
            metadata.get('scope') != 'resolve_only' or metadata.get('raw_keys_recorded') != '0' or
            metadata.get('gpu_execution_timestamps') != '0' or metadata.get('key_table_capacity') != '4096'):
        raise ValueError('Unexpected readback schema/scope')
    if v2 and (metadata.get('routine_policy') != 'some_valid_unscaled_no_work_le_1ms' or
               metadata.get('routine_aggregation') != 'per_key_across_generations'):
        raise ValueError('Unexpected routine aggregation policy')
    start, end = int(metadata['window_start_ns']), int(metadata['window_end_ns'])
    if not 0 <= start < end or end - start > 45 * 10**9:
        raise ValueError('Invalid readback admission window')
    if any(not start <= row['start_ns'] < end for row in rows):
        raise ValueError('Readback event outside admission window')
    if any(row['kind'] == 2 and row['end_ns'] > end for row in rows):
        raise ValueError('Routine summary crosses admission end')
    if v2 and (sum(r['kind'] != 2 for r in rows) > 8192 or sum(r['kind'] == 2 for r in rows) > 4096):
        raise ValueError('Detailed/summary storage bound exceeded')
    return metadata, rows


def analyse(path):
    metadata, rows = load(path)
    issues = []
    complete = metadata.get('status') == 'complete'
    if not complete: issues.append('no complete footer')
    footer = ('records', 'dropped', 'key_overflow', 'clock_errors', 'export_start_ns', 'export_end_ns')
    v2 = metadata['schema'] == 'asahi-readback-v2'
    if v2: footer += ('detailed_records', 'routine_events', 'admitted_events', 'completed_events')
    if any(key not in metadata for key in footer):
        issues.append('missing footer counters')
    else:
        if any(int(metadata[key]) < 0 for key in footer): raise ValueError('Negative footer counter')
        if int(metadata['records']) != len(rows): issues.append('record count differs')
        if v2:
            details = sum(r['kind'] != 2 for r in rows)
            routine = sum(r['events'] for r in rows if r['kind'] == 2)
            if (int(metadata['detailed_records']) != details or int(metadata['routine_events']) != routine or
                    int(metadata['admitted_events']) != int(metadata['completed_events']) or
                    int(metadata['completed_events']) != details + routine + int(metadata['dropped'])):
                issues.append('admission/detail/aggregate counts differ')
        for key in ('dropped', 'key_overflow', 'clock_errors'):
            if int(metadata[key]): issues.append(key + ' is nonzero')
        if not (int(metadata['window_end_ns']) <= int(metadata['export_start_ns']) <= int(metadata['export_end_ns'])):
            issues.append('invalid export timing')
        if rows and max(row['end_ns'] for row in rows) > int(metadata['export_start_ns']):
            issues.append('export before all admitted events finished')
    resolves = [r for r in rows if r['kind'] in (0, 2)]
    evictions = [r for r in rows if r['kind'] == 1]
    if not resolves: issues.append('no captured resolves; cannot infer zero cost')
    if any(r['failed_waits'] or r['flags'] & (512 | 1024) for r in rows):
        issues.append('renderer wait/allocation/map failure observed')
    end = int(metadata['window_end_ns'])
    durations = [r for r in resolves if r['end_ns'] <= end]
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    return {'sourceSha256': digest, 'metadata': metadata, 'completeFile': complete,
            'usableForReasonAttribution': not issues, 'issues': issues,
            'resolveEvents': sum(r['events'] for r in resolves), 'evictionEvents': len(evictions),
            'detailedResolveEvents': sum(r['kind'] == 0 for r in rows),
            'routineSummaryRows': sum(r['kind'] == 2 for r in rows),
            'routineResolveEvents': sum(r['events'] for r in rows if r['kind'] == 2),
            'crossingResolveDurationsExcluded': sum(r['events'] for r in resolves if r['end_ns'] > end),
            'flags': {name: sum(r['events'] for r in rows if r['flags'] & flag) for name, flag in FLAGS.items()},
            'resolveTotals': {key: sum(r[key] for r in resolves) for key in
                              ('allocations', 'allocated_bytes', 'copies', 'copied_bytes')},
            'containedResolveHostMs': {key: sum(r[key] for r in durations) / 1e6 for key in
                ('wall_ns', 'allocation_wall_ns', 'copy_wall_ns', 'resize_wait_ns', 'full_wait_ns', 'miss_wait_ns')},
            'limits': ['Resolve path only: excludes memexport, occlusion and early returns before a valid resolve key.',
                       'Flags may overlap. Nested frame/wait timings must not be added to these scopes.',
                       'Allocation bytes/capacities are API-requested sizes, not RSS, total VkDeviceMemory allocation size or process memory.',
                       'Opaque IDs identify the existing SDK key, not a proof of collision-free raw guest address/format identity.',
                       'Host timing includes observer overhead, not GPU execution; no equivalent-work/overhead game comparison yet.',
                       'Generation may be zero for an entry first observed as present; capture-window history is censored.',
                       'V2 kind=2 rows aggregate routine skips per key across generations: frame/submission/generation are zero, wall is a sum, not the bounding interval span. Individual routine timestamps cannot be reconstructed.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    args = parser.parse_args()
    try:
        report = analyse(args.csv)
    except (ValueError, KeyError, OSError, csv.Error) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))
    return 0 if report['usableForReasonAttribution'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
