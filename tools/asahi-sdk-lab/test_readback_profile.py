"""Bounded CPU recorder, extracted forwarding and offline qualification tests."""
import json
import csv
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from analyse_readback_profile import analyse, load, COLUMNS
import prepare_readback_profile as p
from candidate_play import readback_profile_flags
import build_submission_candidate as builder


def fragments(source, class_name):
    start = source.index('bool VulkanCommandProcessor::IssueCopy_ReadbackResolvePath() {')
    end = source.index('\nvoid VulkanCommandProcessor::EvictOldReadbackBuffers(', start)
    method = source[start:end]
    begin = method.find('  readback_profile::Trace* readback_trace_ptr')
    if begin < 0: begin = method.index('  auto ensure_readback_slot =')
    first = method[begin:method.index('  if (is_scaled) {')]
    native = method[method.index('  } else {\n    shared_memory_->Use'):method.index('\n  PushBufferMemoryBarrier(readback.buffers[write_index]')]
    native = native.removeprefix('  } else {\n').removesuffix('\n  }\n')
    tail = method[method.index('  PushBufferMemoryBarrier(readback.buffers[write_index]'):]
    output = f'''bool {class_name}::Run(uint32_t written_address, uint32_t written_length) {{
  const auto* vulkan_device = GetVulkanDevice();
  const auto& dfn = vulkan_device->functions();
  const auto device = vulkan_device->device();
  ReadbackResolveMode readback_mode = mode;
'''
    output += first + native + '\n' + tail
    evict_end = source.index('\nbool VulkanCommandProcessor::InitializeOcclusionQueryResources()', end)
    output += source[end:evict_end].replace('VulkanCommandProcessor::', class_name + '::')
    return output


class ReadbackProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('clang++') or not (p.SDK / p.FILES[1]).is_file():
            raise unittest.SkipTest('Pinned SDK and compiler required')
        cls.temporary = tempfile.TemporaryDirectory(prefix='asahi-readback-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        prepared = json.loads(subprocess.check_output(
            [sys.executable, '-B', str(p.HERE / 'prepare_submission_profile.py')], text=True, timeout=60))
        cls.submission = p.pinned.ROOT / prepared['overlay']
        cls.overlay = p.compose(cls.submission)
        original = (cls.submission / p.FILES[1]).read_text()
        transformed = (cls.overlay / p.FILES[1]).read_text()
        (cls.directory / 'readback_methods.inc').write_text(fragments(original, 'Reference') + '\n' + fragments(transformed, 'Instrumented'))
        for name in ('readback_profile_test', 'readback_methods_fixture'):
            command = ['clang++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-variable',
                       '-I' + str(cls.directory), '-I' + str(p.HERE), str(p.HERE / (name + '.cpp')),
                       '-o', str(cls.directory / name)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                    preexec_fn=p.limit_compiler, env=os.environ | {'TMPDIR': str(cls.directory)})
            if result.returncode: raise AssertionError(result.stdout + result.stderr)
            output = cls.directory / name.replace('_', '-')
            output.mkdir()
            checked = subprocess.run([str(cls.directory / name), str(output)], capture_output=True, text=True,
                                     timeout=10, preexec_fn=p.limit_compiler)
            if checked.returncode: raise AssertionError(checked.stdout + checked.stderr)
        cls.core = cls.directory / 'readback-profile-test'
        cls.methods = cls.directory / 'readback-methods-fixture'

    def test_forwarding_and_extracted_native_fragments(self):
        # Both off/on modes and two calls per scenario run in setUpClass.
        self.assertTrue(analyse(self.methods / 'some.csv')['usableForReasonAttribution'])
        self.assertTrue(analyse(self.methods / 'thumbnail.csv')['usableForReasonAttribution'])

    def test_skip_valid_does_not_hide_first_miss(self):
        report = analyse(self.methods / 'some.csv')
        self.assertEqual(report['flags']['slotMiss'], 1)
        self.assertEqual(report['flags']['skipValid'], 1)
        self.assertEqual(report['resolveTotals']['copies'], 1)
        self.assertEqual(report['resolveTotals']['allocations'], 1)

    def test_thumbnail_keeps_full_wait_and_both_copies(self):
        report = analyse(self.methods / 'thumbnail.csv')
        self.assertEqual(report['flags']['thumbnail'], 2)
        self.assertEqual(report['resolveTotals']['copies'], 2)
        self.assertGreater(report['containedResolveHostMs']['full_wait_ns'], 0)
        self.assertEqual(report['flags']['slotMiss'], 0)

    def test_hot_history_and_growth_are_distinct(self):
        self.assertEqual(analyse(self.methods / 'hot.csv')['flags']['historyHot'], 1)
        growth = analyse(self.methods / 'growth.csv')
        self.assertGreater(growth['containedResolveHostMs']['resize_wait_ns'], 0)
        self.assertGreater(growth['containedResolveHostMs']['full_wait_ns'], 0)

    def test_fast_ready_preserves_delayed_slot_without_new_wait(self):
        report = analyse(self.methods / 'fast-ready.csv')
        self.assertTrue(report['usableForReasonAttribution'])
        self.assertEqual(report['resolveTotals']['copies'], 2)
        self.assertEqual(report['resolveTotals']['allocations'], 0)
        self.assertEqual(report['containedResolveHostMs']['miss_wait_ns'], 0)
        self.assertEqual(report['containedResolveHostMs']['full_wait_ns'], 0)

    def test_eviction_count_and_age_reasons_are_retained(self):
        report = analyse(self.methods / 'evict-count.csv')
        self.assertTrue(report['usableForReasonAttribution'])
        self.assertGreater(report['flags']['evictCount'], 0)
        self.assertGreater(report['flags']['evictAge'], 0)

    def test_renderer_failures_reject_attribution(self):
        for name in ('allocation-failure', 'map-failure', 'resize-failure', 'full-failure', 'miss-failure'):
            with self.subTest(name=name):
                self.assertFalse(analyse(self.methods / (name + '.csv'))['usableForReasonAttribution'])

    def test_eviction_and_recreation_identity(self):
        report = analyse(self.core / 'generations.csv')
        self.assertTrue(report['usableForReasonAttribution'])
        _, rows = load(self.core / 'generations.csv')
        self.assertEqual([r['key_id'] for r in rows], [1, 1, 1])
        self.assertEqual([r['generation'] for r in rows], [1, 1, 2])

    def test_record_key_loss_and_clock_errors_reject(self):
        for name in ('capacity', 'keys', 'backwards', 'failure'):
            with self.subTest(name=name):
                self.assertFalse(analyse(self.core / (name + '.csv'))['usableForReasonAttribution'])

    def test_cross_window_duration_is_not_summed(self):
        report = analyse(self.core / 'ordinary.csv')
        self.assertTrue(report['usableForReasonAttribution'])
        self.assertEqual(report['crossingResolveDurationsExcluded'], 1)
        self.assertEqual(report['containedResolveHostMs']['wall_ns'], 0)

    def test_raw_keys_are_not_exported(self):
        metadata, rows = load(self.core / 'generations.csv')
        self.assertEqual(metadata['raw_keys_recorded'], '0')
        self.assertNotIn('key', rows[0])

    def test_partial_and_invalid_records_fail_closed(self):
        text = (self.methods / 'some.csv').read_text()
        bad = self.directory / 'bad.csv'
        bad.write_text(text.replace('#status=complete\n', ''))
        self.assertFalse(analyse(bad)['usableForReasonAttribution'])
        bad.write_text(text.replace('#schema=asahi-readback-v2', '#schema=other'))
        with self.assertRaises(ValueError): analyse(bad)
        bad.write_text(text + '#schema=asahi-readback-v2\n')
        with self.assertRaises(ValueError): analyse(bad)

    def test_routine_flood_preserves_late_work_with_no_larger_detail_buffer(self):
        report = analyse(self.core / 'routine-flood.csv')
        self.assertTrue(report['usableForReasonAttribution'], report['issues'])
        self.assertEqual(report['routineResolveEvents'], 100279)
        self.assertEqual(report['routineSummaryRows'], 1)
        self.assertEqual(report['detailedResolveEvents'], 3)
        self.assertEqual(report['resolveEvents'], 100282)
        self.assertEqual(report['resolveTotals']['copies'], 1)
        self.assertGreater(report['containedResolveHostMs']['full_wait_ns'], 0)
        self.assertEqual(report['metadata']['dropped'], '0')
        _, rows = load(self.core / 'routine-flood.csv')
        self.assertTrue(any(r['kind'] == 0 and r['wall_ns'] > 1000000 for r in rows))
        self.assertTrue(any(r['kind'] == 0 and r['flags'] & 16 for r in rows))

    def test_routine_boundary_crossing_keeps_detail(self):
        report = analyse(self.core / 'routine-crossing.csv')
        self.assertTrue(report['usableForReasonAttribution'])
        self.assertEqual(report['routineResolveEvents'], 0)
        self.assertEqual(report['crossingResolveDurationsExcluded'], 1)

    def test_v2_conservation_and_summary_integrity_fail_closed(self):
        text = (self.core / 'routine-flood.csv').read_text()
        bad = self.directory / 'bad-v2.csv'
        bad.write_text(text.replace('#routine_events=100279', '#routine_events=100280'))
        self.assertFalse(analyse(bad)['usableForReasonAttribution'])
        summary = next(line for line in text.splitlines() if line.startswith('2,'))
        bad.write_text(text.replace(summary + '\n', summary + '\n' + summary + '\n'))
        with self.assertRaises(ValueError): analyse(bad)

    def test_legacy_v1_reader_still_accepts_original_shape(self):
        metadata, rows = load(self.core / 'generations.csv')
        path = self.directory / 'legacy.csv'
        metadata = {k: v for k, v in metadata.items() if k not in (
            'routine_policy', 'routine_aggregation', 'detailed_records', 'routine_events', 'admitted_events', 'completed_events')}
        metadata['schema'] = 'asahi-readback-v1'
        with path.open('w') as stream:
            for key, value in metadata.items(): stream.write(f'#{key}={value}\n')
            writer = csv.DictWriter(stream, fieldnames=COLUMNS, extrasaction='ignore')
            writer.writeheader(); writer.writerows(rows)
        self.assertTrue(analyse(path)['usableForReasonAttribution'])

    def test_independent_default_off_overlay_and_strict_transform(self):
        manifest = json.loads((self.overlay / 'manifest.json').read_text())
        self.assertFalse(manifest['enabledByDefault'])
        self.assertEqual(manifest['experiment'], 'resolve-readback-reasons')
        source = (self.overlay / p.FILES[1]).read_text()
        self.assertNotIn('fh1_asahi_render_pass_reuse', source)
        lock = json.loads((p.HERE / 'sdk.lock.json').read_text())
        self.assertFalse(any('readback-profile' in entry['path'] for entry in lock['asahiPatches']))
        with self.assertRaises(ValueError): p.transform('', '')

    def test_independent_builder_rejects_combination_before_preparation(self):
        with patch.object(builder, 'require_memory') as memory:
            with self.assertRaises(ValueError): builder.prepare(True, True)
            memory.assert_not_called()

    def test_synthetic_layout_mode_returns_before_gpu_provider(self):
        source = (p.HERE / 'backend.cpp').read_text()
        layout = source.index('std::strcmp(mode, "readback-layout")')
        provider = source.index('VulkanProvider::Create(')
        self.assertLess(layout, provider)
        self.assertIn('VerifyReadbackLayoutCpu();\n      return 0;', source[layout:provider])

    def test_synthetic_gate_uses_real_resolve_and_is_explicitly_opt_in(self):
        gate = (p.HERE / 'readback_gate.inc').read_text()
        raster = (p.HERE / 'raster.cpp').read_text()
        self.assertIn('std::getenv("ASAHI_RUN_RESOLVE_GATE")', raster)
        self.assertEqual(gate.count('commands.IssueCopy()'), 2)
        self.assertNotIn('readback_profile::Trace', gate)
        self.assertIn('commands.readback_profile_->FinalizeIfReady()', gate)
        self.assertIn('std::memcmp(bytes, expected.data(), length)', gate)
        self.assertIn('guard = 64', gate)
        self.assertIn('std::this_thread::sleep_until', gate)

    def test_launcher_flags_disabled_by_default_and_reject_unsupported(self):
        candidate = self.directory / 'candidate'
        candidate.mkdir(exist_ok=True)
        plan = candidate / 'plan.json'
        plan.write_text(json.dumps({'inputs': {'overlay': {'experiment': 'resolve-readback-reasons'}}}))
        self.assertEqual(readback_profile_flags(candidate, self.directory, False, 120, 40, 180),
                         ['--fh1_readback_profile_csv='])
        flags = readback_profile_flags(candidate, self.directory, True, 120, 40, 180)
        self.assertIn('--fh1_readback_profile_seconds=40', flags)
        for start, duration in ((0, 40), (121, 1), (120, 46), (120, 41)):
            with self.assertRaises(ValueError): readback_profile_flags(candidate, self.directory, True, start, duration, 180)
        plan.write_text(json.dumps({'inputs': {'overlay': {}}}))
        with self.assertRaises(ValueError): readback_profile_flags(candidate, self.directory, True, 120, 40, 180)
        with self.assertRaises(ValueError): readback_profile_flags(None, self.directory, True, 120, 40, 180)


if __name__ == '__main__':
    unittest.main()
