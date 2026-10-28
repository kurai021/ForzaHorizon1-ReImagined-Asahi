"""CPU-only offset equivalence; no Vulkan device, game rebuild or guest mapping."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import prepare_physical_offset_probe as p


class ExtractionTests(unittest.TestCase):
    def test_rejects_missing_or_changed_method(self):
        for text in ('', 'inline u32 PhysicalHostOffset(u32 a) { return 0; }'):
            with self.assertRaises(ValueError):
                p.methods(text)

    def test_split_retains_runtime_granularity_and_thread_safe_static(self):
        if not (p.SDK / p.HEADER).is_file():
            self.skipTest('Pinned SDK required')
        original, split = p.methods((p.SDK / p.HEADER).read_text())
        self.assertIn(p.ORIGINAL_LINUX, original)
        self.assertEqual(split.count('allocation_granularity()'), 1)
        self.assertIn('static const u32 offset = allocation_granularity() > 0x1000', split)
        self.assertIn('if (guest_addr < 0xE0000000u) return 0u;', split)
        self.assertIn('return PhysicalHostOffsetCached();', split)

    def test_duplicate_definition_fails_closed(self):
        if not (p.SDK / p.HEADER).is_file():
            self.skipTest('Pinned SDK required')
        text = (p.SDK / p.HEADER).read_text()
        with self.assertRaises(ValueError):
            p.methods(text + text)


class PhysicalOffsetCpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (p.SDK / p.HEADER).is_file() or not shutil.which('clang++'):
            raise unittest.SkipTest('Pinned SDK and clang++ required')
        cls.temporary = tempfile.TemporaryDirectory(prefix='physical-offset-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        original, split = p.methods((p.SDK / p.HEADER).read_text())
        (cls.directory / 'physical_offset_methods.h').write_text(p.harness_header(original, split))
        other = cls.directory / 'other.cpp'
        other.write_text('''#include "physical_offset_methods.h"
extern "C" u32 baseline_other(u32 a) { return baseline::PhysicalHostOffset(a); }
extern "C" u32 split_other(u32 a) { return split::PhysicalHostOffset(a); }
''')
        cls.binaries = {}
        for platform, definitions in {
            'linux': ['-DREX_PLATFORM_LINUX=1'], 'windows-model': ['-DREX_PLATFORM_WIN32=1'],
            'mac-model': ['-DREX_PLATFORM_MAC=1'], 'fallback': []}.items():
            binary = cls.directory / platform
            command = ['clang++', '-std=c++23', '-O3', '-pthread', '-I' + str(cls.directory),
                       *definitions, str(p.HERE / 'physical_offset_fixture.cpp'), str(other), '-o', str(binary)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30, preexec_fn=p.limits,
                                    env=os.environ | {'TMPDIR': str(cls.directory)})
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[platform] = binary

    def check_case(self, platform, page):
        result = subprocess.run([str(self.binaries[platform]), 'check', str(page)],
                                capture_output=True, text=True, timeout=5, preexec_fn=p.limits)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['equivalent'])
        self.assertTrue(report['concurrentFirstUse'])
        self.assertTrue(report['crossTranslationUnit'])

    def test_linux_4k(self): self.check_case('linux', 4096)
    def test_linux_16k(self): self.check_case('linux', 16384)
    def test_linux_64k(self): self.check_case('linux', 65536)
    def test_windows_branch_model(self): self.check_case('windows-model', 4096)
    def test_mac_branch_model(self): self.check_case('mac-model', 16384)
    def test_other_platform_fallback(self): self.check_case('fallback', 16384)


if __name__ == '__main__':
    unittest.main()
