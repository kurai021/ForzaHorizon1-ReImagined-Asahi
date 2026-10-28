"""Deterministic CPU-only timeline/association tests; no SDK, game or GPU calls."""
import csv
import io
from pathlib import Path
import tempfile
import unittest

from analyse_submission_profile import COLUMNS
from analyse_submission_timeline import association, ranks, timeline

MONO = 10**9
EPOCH = 1700000000 * 10**9


def trace_text():
    buffer = io.StringIO()
    buffer.write(f'#schema=asahi-submission-v1\n#clock=steady_ns\n#gpu_execution_timestamps=0\n'
                 f'#origin_epoch_ns={EPOCH}\n#origin_mono_ns={MONO}\n'
                 f'#window_start_ns={MONO}\n#window_end_ns={MONO+180000000}\n')
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS)
    for frame, start, duration in ((2, 30, 40), (3, 80, 10), (4, 120, 10)):
        phase_start = MONO + start * 1000000
        total_end = phase_start + 90000 + duration * 1000000
        stages = [('queue_delay', phase_start, phase_start + 10000),
                  ('total', phase_start + 20000, total_end)]
        for index, name in enumerate(('reset_pool', 'begin', 'replay', 'end', 'queue_acquire')):
            begin = phase_start + 20000 + index * 10000
            stages.append((name, begin, begin + 10000))
        stages.append(('queue_submit', phase_start + 90000, total_end))
        for name, begin, end in stages:
            writer.writerow(['job', frame, frame, name, begin, end, end-begin,
                             0, 0 if name == 'queue_delay' else 1000,
                             int(name != 'queue_delay'), frame*10, 0, 0, 1, 0, 0])
    buffer.write(f'#jobs=3\n#waits=0\n#dropped_jobs=0\n#dropped_waits=0\n'
                 f'#export_start_ns={MONO+181000000}\n#export_end_ns={MONO+182000000}\n#status=complete\n')
    return buffer.getvalue()


def frame_text():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['epoch', 't', 'ms', 'cpu_ms', 'gpu_wait_ms', 'guest_wait_ms', 'draws', 'shaders', 'pipelines',
                     'readbacks', 'readback_wait_ms'])
    for index in range(3):
        writer.writerow([(EPOCH+(60+index*50)*1000000)/1e9, .05+index*.05, 50, 2, 1, 0, 10, 0, 0, 0, 0])
    return buffer.getvalue()


class SubmissionTimelineTests(unittest.TestCase):
    def evaluate(self, trace=None, frames=None, **kwargs):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'trace.csv').write_text(trace if trace is not None else trace_text())
            (directory / 'frames.csv').write_text(frames if frames is not None else frame_text())
            return timeline(directory / 'trace.csv', directory / 'frames.csv', **kwargs)

    def test_rank_ties_and_constant_associations(self):
        self.assertEqual(ranks([3, 1, 1]), [2, .5, .5])
        self.assertAlmostEqual(association([1, 2, 3], [3, 2, 1])['spearman'], -1)
        self.assertIsNone(association([1, 1], [1, 2])['pearson'])
        self.assertIsNone(association([], [])['spearman'])

    def test_owned_work_is_not_temporal_frame_overlap(self):
        result = self.evaluate()
        self.assertEqual(result['alignment']['jobFrameTagsValidated'], 3)
        self.assertEqual(result['alignment']['fullyContainedFrames'], 3)
        self.assertEqual(result['alignment']['jobsFinishingAfterTaggedFrameEndPlusGuard'], 1)
        by_id = {row['frame']: row for row in result['slowestFrames']}
        self.assertAlmostEqual(by_id[2]['taggedSubmitWallMs'], 40)
        self.assertAlmostEqual(by_id[2]['temporalSubmitOverlapWallMs'], 29.91, places=3)
        self.assertAlmostEqual(by_id[3]['temporalSubmitOverlapWallMs'], 20.09, places=3)
        self.assertFalse(result['gpuWork'])

    def test_incomplete_trace_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'not qualified'):
            self.evaluate(trace=trace_text().replace('#status=complete\n', ''))

    def test_wrong_csv_or_frame_offset_is_not_fitted(self):
        with self.assertRaises(ValueError):
            self.evaluate(frame_id_offset=3)
        with self.assertRaises(ValueError):
            self.evaluate(frames=frame_text().replace('1700000000.', '1700000005.'))

    def test_offset_drift_and_invalid_guard_are_refused(self):
        with self.assertRaises(ValueError):
            self.evaluate(guard_ms=float('nan'))
        rows = list(csv.reader(io.StringIO(frame_text())))
        rows[-1][1] = str(float(rows[-1][1]) + .02)
        buffer = io.StringIO()
        csv.writer(buffer).writerows(rows)
        with self.assertRaisesRegex(ValueError, 'offset changed'):
            self.evaluate(frames=buffer.getvalue())

    def test_stage_counter_mismatch_is_refused(self):
        text = trace_text().replace(',20,0,0,1,0,0', ',999,0,0,1,0,0', 1)
        with self.assertRaisesRegex(ValueError, 'counters differ'):
            self.evaluate(trace=text)

    def test_phase_order_and_worker_overlap_are_refused(self):
        # Move a valid-duration phase outside its total scope.
        text = trace_text().replace(f'replay,{MONO+30040000},{MONO+30050000}',
                                    f'replay,{MONO+30010000},{MONO+30020000}')
        with self.assertRaisesRegex(ValueError, 'phase ordering'):
            self.evaluate(trace=text)
        # The second worker job now starts before the first job has returned.
        # Replace every second-job timestamp consistently (20 ms earlier).
        lines = trace_text().splitlines()
        for index, line in enumerate(lines):
            if line.startswith('job,3,3,'):
                row = line.split(',')
                row[4], row[5] = str(int(row[4])-20000000), str(int(row[5])-20000000)
                lines[index] = ','.join(row)
        with self.assertRaisesRegex(ValueError, 'worker jobs overlap'):
            self.evaluate(trace='\n'.join(lines)+'\n')

    def test_missing_readback_counters_are_not_interpreted_as_zero(self):
        rows = list(csv.reader(io.StringIO(frame_text())))
        buffer = io.StringIO()
        csv.writer(buffer).writerows([row[:-2] for row in rows])
        with self.assertRaisesRegex(ValueError, 'readback counters'):
            self.evaluate(frames=buffer.getvalue())

    def test_frame_epochs_must_increase(self):
        rows = list(csv.reader(io.StringIO(frame_text())))
        rows[-1][0] = rows[-2][0]
        buffer = io.StringIO()
        csv.writer(buffer).writerows(rows)
        with self.assertRaisesRegex(ValueError, 'increase strictly'):
            self.evaluate(frames=buffer.getvalue())

    def test_export_during_capture_is_refused(self):
        text = trace_text().replace(f'#export_start_ns={MONO+181000000}', f'#export_start_ns={MONO+179000000}')
        with self.assertRaisesRegex(ValueError, 'after the capture window'):
            self.evaluate(trace=text)

    def test_cross_window_jobs_censor_tagged_frame_not_gpu_work(self):
        lines = trace_text().splitlines()
        for index, line in enumerate(lines):
            if line.startswith('job,4,4,'):
                row = line.split(',')
                if row[3] in ('queue_submit', 'total'):
                    row[5] = str(int(row[5]) + 70000000)
                    row[6] = str(int(row[5]) - int(row[4]))
                    row[15] = '1'
                lines[index] = ','.join(row)
        text = '\n'.join(lines)+'\n'
        with self.assertRaisesRegex(ValueError, 'before admitted jobs finished'):
            self.evaluate(trace=text)
        text = text.replace(f'#export_start_ns={MONO+181000000}', f'#export_start_ns={MONO+201000000}')
        text = text.replace(f'#export_end_ns={MONO+182000000}', f'#export_end_ns={MONO+202000000}')
        result = self.evaluate(trace=text)
        self.assertEqual(result['alignment']['fullyContainedFrames'], 2)
        self.assertEqual(result['alignment']['crossWindowJobFramesExcluded'], 1)
        self.assertFalse(result['gpuWork'])


if __name__ == '__main__':
    unittest.main()
