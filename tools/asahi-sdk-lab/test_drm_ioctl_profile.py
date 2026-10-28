"""Bounded fake-libdrm tests. No real ioctl, GPU device or window is opened."""
import errno
import os
from pathlib import Path
import resource
import shutil
import subprocess
import tempfile
import unittest

from analyse_drm_ioctl_profile import analyse, load_profile, join_submissions
from analyse_submission_profile import COLUMNS
from prepare_drm_ioctl_profile import compiler_command, EXPORTS

HERE = Path(__file__).resolve().parent


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


class DrmIoctlProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which('clang++') or shutil.which('c++')
        if not cls.compiler or not Path('/usr/include/drm/asahi_drm.h').exists():
            raise unittest.SkipTest('C++17 compiler and installed Asahi DRM UAPI headers required')
        cls.temporary = tempfile.TemporaryDirectory(prefix='asahi-drm-profile-')
        cls.directory = Path(cls.temporary.name).resolve()
        cls.library = cls.directory / 'libprofile.so'
        cls.fake = cls.directory / 'libdrm.so.2'
        cls.target = cls.directory / 'fixture'
        exports = cls.directory / 'exports.map'
        exports.write_text(EXPORTS)
        commands = [compiler_command(cls.compiler, HERE / 'drm_ioctl_profile.cpp', cls.library, exports, cls.directory),
                    [cls.compiler, '-std=c++17', '-O2', '-UNDEBUG', '-I/usr/include/drm', '-DASAHI_FAKE_LIBDRM=1',
                     '-shared', '-fPIC', str(HERE / 'drm_ioctl_profile_fixture.cpp'), '-pthread',
                     '-Wl,-soname,libdrm.so.2', '-o', str(cls.fake)],
                    [cls.compiler, '-std=c++17', '-O2', '-UNDEBUG', '-I/usr/include/drm',
                     str(HERE / 'drm_ioctl_profile_fixture.cpp'), '-pthread', '-ldl', '-o', str(cls.target)]]
        for command in commands:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30, preexec_fn=limits)
            if result.returncode:
                cls.temporary.cleanup()
                raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_fixture(self, mode='normal', *, enabled=True, extra=None):
        folder = Path(tempfile.mkdtemp(prefix='case-', dir=self.directory))
        output = folder / 'drm.csv'
        env = {k: v for k, v in os.environ.items() if not k.startswith('ASAHI_DRM_IOCTL_')}
        env.update(LD_PRELOAD=str(self.library), ASAHI_DRM_IOCTL_CSV=str(output) if enabled else '',
                   ASAHI_DRM_IOCTL_TARGET=str(self.target), ASAHI_DRM_IOCTL_START='0',
                   ASAHI_DRM_IOCTL_SECONDS='.1', ASAHI_DRM_IOCTL_MAX_RECORDS='1024')
        env.update(extra or {})
        result = subprocess.run([str(self.target), mode, str(self.fake)], env=env, capture_output=True,
                                text=True, timeout=5, preexec_fn=limits)
        return result, output

    def test_exact_forwarding_and_complete_timing(self):
        result, path = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PASS: exact forwarding', result.stdout)
        report = analyse(path)
        self.assertTrue(report['usableForNativeAttribution'], report['issues'])
        self.assertEqual(report['nativeSubmit']['calls'], 3)
        self.assertEqual(int(report['metadata']['records']), 7)
        metadata, rows = load_profile(path)
        self.assertEqual({row['errno_value'] for row in rows if row['result'] == 0}, {errno.EDOM})
        self.assertEqual(metadata['raw_syscall_attempts'], 'unknown')
        self.assertTrue(all('Vulkan Submiss' in name for name in report['threads'].values()))

    def test_rtld_local_original_is_resolved_without_loading_a_driver(self):
        result, path = self.run_fixture('local')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(analyse(path)['usableForNativeAttribution'])

    def test_disabled_and_wrong_executable_do_not_create_output(self):
        for options in ({'enabled': False}, {'extra': {'ASAHI_DRM_IOCTL_TARGET': '/not-the-target'}}):
            result, path = self.run_fixture(**options)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(path.exists())

    def test_multithread_overflow_never_suppresses_original_calls(self):
        result, path = self.run_fixture('threads', extra={'ASAHI_DRM_IOCTL_MAX_RECORDS': '8'})
        self.assertEqual(result.returncode, 0, result.stderr)
        report = analyse(path)
        self.assertTrue(report['completeFile'])
        self.assertEqual(int(report['metadata']['records']), 8)
        self.assertEqual(int(report['metadata']['dropped']), 4 * 129 - 8)
        self.assertFalse(report['usableForNativeAttribution'])

    def test_multithread_publication_is_complete(self):
        result, path = self.run_fixture('threads')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = analyse(path)
        self.assertTrue(report['usableForNativeAttribution'], report['issues'])
        self.assertEqual(int(report['metadata']['records']), 516)
        self.assertEqual(len(report['threads']), 4)

    def test_early_exit_lacks_required_complete_footer(self):
        result, path = self.run_fixture('early')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(analyse(path)['completeFile'])

    def test_fork_child_is_not_recorded(self):
        result, path = self.run_fixture('fork')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = analyse(path)
        self.assertTrue(report['usableForNativeAttribution'], report['issues'])
        self.assertEqual(int(report['metadata']['records']), 7)

    def test_exec_child_inherits_owner_latch_without_overwriting_parent_trace(self):
        result, path = self.run_fixture('exec')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = analyse(path)
        self.assertTrue(report['usableForNativeAttribution'], report['issues'])
        self.assertEqual(int(report['metadata']['records']), 7)

    def test_calls_outside_window_are_forwarded_but_not_reported_as_zero_cost(self):
        for mode, extra in [('normal', {'ASAHI_DRM_IOCTL_START': '.05', 'ASAHI_DRM_IOCTL_SECONDS': '.05'}),
                            ('late', {})]:
            with self.subTest(mode=mode):
                result, path = self.run_fixture(mode, extra=extra)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = analyse(path)
                self.assertTrue(report['completeFile'])
                self.assertEqual(int(report['metadata']['records']), 0)
                self.assertFalse(report['usableForNativeAttribution'])

    def test_output_outside_compiled_private_root_is_refused(self):
        outside = self.directory.with_name(self.directory.name + '-outside.csv')
        self.assertFalse(outside.exists())
        result, _ = self.run_fixture(extra={'ASAHI_DRM_IOCTL_CSV': str(outside)})
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertFalse(outside.exists())

    def test_positive_result_is_forwarded_not_normalized(self):
        result, path = self.run_fixture('positive')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = analyse(path)
        self.assertEqual(report['nativeSubmit']['nonzeroReturns'], 3)
        self.assertFalse(report['usableForNativeAttribution'])

    def test_invalid_configuration_and_missing_original_fail_closed(self):
        for mode, extra in [('normal', {'ASAHI_DRM_IOCTL_SECONDS': 'nan'}),
                            ('normal', {'ASAHI_DRM_IOCTL_SECONDS': '46'}),
                            ('normal', {'ASAHI_DRM_IOCTL_MAX_RECORDS': '4.5'}),
                            ('missing', {})]:
            result, _ = self.run_fixture(mode, extra=extra)
            self.assertEqual(result.returncode, 125, result.stderr)
            self.assertIn('DRM profile:', result.stderr)

    def test_existing_output_is_not_overwritten(self):
        old = self.directory / 'existing.csv'
        old.write_text('preserve this evidence\n')
        result, _ = self.run_fixture(extra={'ASAHI_DRM_IOCTL_CSV': str(old)})
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertEqual(old.read_text(), 'preserve this evidence\n')

    def test_parser_rejects_bad_timing_and_missing_thread_identity(self):
        result, path = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = path.read_text()
        bad = path.with_name('bad.csv')
        lines = text.splitlines()
        row_index = next(i for i, line in enumerate(lines) if line.startswith('0,'))
        row = lines[row_index].split(',')
        row[5] = str(int(row[5]) + 1)
        lines[row_index] = ','.join(row)
        bad.write_text('\n'.join(lines)+'\n')
        with self.assertRaises(ValueError):
            analyse(bad)
        bad.write_text('\n'.join(line for line in text.splitlines() if not line.startswith('#thread_'))+'\n')
        self.assertFalse(analyse(bad)['usableForNativeAttribution'])

    def synthetic_submission(self, path):
        import csv
        metadata, calls = load_profile(path)
        first, last = min(r['start_ns'] for r in calls), max(r['end_ns'] for r in calls)
        sdk = path.with_name('sdk.csv')
        with sdk.open('w') as output:
            for key in ('origin_epoch_ns', 'origin_mono_ns', 'window_start_ns', 'window_end_ns'):
                output.write(f'#{key}={metadata[key]}\n')
            output.write('#schema=asahi-submission-v1\n#clock=steady_ns\n#gpu_execution_timestamps=0\n')
            writer = csv.writer(output)
            writer.writerow(COLUMNS)
            intervals = [('queue_delay', first-100, first-90), ('total', first-80, last+10),
                         ('reset_pool', first-70, first-60), ('begin', first-60, first-50),
                         ('replay', first-50, first-40), ('end', first-40, first-30),
                         ('queue_acquire', first-30, first-20), ('queue_submit', first-10, last+1)]
            for stage, start, end in intervals:
                writer.writerow(['job', 1, 1, stage, start, end, end-start, 0, 0,
                                 int(stage != 'queue_delay'), 100, 0, 0, 1, 0, 0])
            output.write(f"#jobs=1\n#waits=0\n#dropped_jobs=0\n#dropped_waits=0\n"
                         f"#export_start_ns={metadata['export_start_ns']}\n"
                         f"#export_end_ns={metadata['export_end_ns']}\n#status=complete\n")
        return sdk, metadata, calls

    def test_same_clock_submission_join_counts_only_scoped_calls(self):
        result, path = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        sdk, metadata, _ = self.synthetic_submission(path)
        joined = join_submissions(path, sdk)
        self.assertEqual(joined['jobs'], 1)
        self.assertEqual(joined['nativeCallsMeanPerJob'], 3)
        self.assertEqual(joined['perJob'][0]['otherDrmCalls'], 4)
        with self.assertRaises(ValueError):
            join_submissions(path, sdk, tid=0)
        sdk.write_text(sdk.read_text().replace(f"#origin_epoch_ns={metadata['origin_epoch_ns']}",
                                              f"#origin_epoch_ns={int(metadata['origin_epoch_ns'])+20000000}"))
        with self.assertRaisesRegex(ValueError, 'bridges disagree'):
            join_submissions(path, sdk)

    def test_join_requires_full_window_coverage_and_unique_or_explicit_tid(self):
        result, path = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        sdk, metadata, calls = self.synthetic_submission(path)
        original = path.read_text()
        start = int(metadata['window_start_ns'])
        path.write_text(original.replace(f'#window_start_ns={start}', f'#window_start_ns={start+1}'))
        with self.assertRaisesRegex(ValueError, 'does not cover'):
            join_submissions(path, sdk)
        selected = calls[0]['tid']
        other = selected + 100000
        name = 'Vulkan Submissi'.encode().hex()
        path.write_text(original.replace('#status=complete', f'#thread_{other}={name}\n#status=complete'))
        with self.assertRaisesRegex(ValueError, 'Expected one named'):
            join_submissions(path, sdk)
        self.assertEqual(join_submissions(path, sdk, selected)['nativeCallsMeanPerJob'], 3)

    def test_join_refuses_cross_boundary_and_overlapping_drm_calls(self):
        result, path = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        sdk, _, calls = self.synthetic_submission(path)
        original = sdk.read_text()
        lines = original.splitlines()
        index = next(i for i, line in enumerate(lines) if ',queue_submit,' in line)
        row = lines[index].split(',')
        row[4] = str(min(call['start_ns'] for call in calls) + 1)
        row[6] = str(int(row[5]) - int(row[4]))
        lines[index] = ','.join(row)
        sdk.write_text('\n'.join(lines) + '\n')
        with self.assertRaisesRegex(ValueError, 'crosses an SDK scope'):
            join_submissions(path, sdk)
        sdk.write_text(original)
        lines = path.read_text().splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith('1,'))
        row = lines[index].split(',')
        row[3] = str(calls[0]['start_ns'])
        row[5] = str(int(row[4]) - int(row[3]))
        lines[index] = ','.join(row)
        path.write_text('\n'.join(lines) + '\n')
        with self.assertRaisesRegex(ValueError, 'overlapping DRM scopes'):
            join_submissions(path, sdk)


if __name__ == '__main__':
    unittest.main()
