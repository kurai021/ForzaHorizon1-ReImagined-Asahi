"""Bounded CPU tests: real SDK keys/method text, mock device allocation and command sink."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import build_submission_candidate as b
from prepare_render_pass_reuse import compose, TARGETS, HELPER
from prepare_submission_profile import limit_compiler

HERE = Path(__file__).resolve().parent


def extract_method(text, name, occurrence=0):
    # All selected SDK methods end with an unindented closing brace.
    positions = []
    start = 0
    while True:
        start = text.find(name, start)
        if start < 0:
            break
        positions.append(start)
        start += len(name)
    at = positions[occurrence]
    begin = text.rfind('\n', 0, at) + 1
    # Return types for the framebuffer function span two lines.
    if text[begin:at].strip() == '':
        begin = text.rfind('\n', 0, begin-1) + 1
    end = text.index('\n}', at) + 2
    return text[begin:end]


class RenderPassReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = b.OUT / '063119c3d4972f3a'
        if not (cls.reference / 'plan.json').is_file():
            raise unittest.SkipTest('Preserved functional candidate/SDK required for extracted-method CPU tests')
        cls.temporary = tempfile.TemporaryDirectory(prefix='render-pass-reuse-')
        cls.directory = Path(cls.temporary.name)
        cls.addClassCleanup(cls.temporary.cleanup)
        # The normal preparer verified this pinned source before this suite.
        submission = Path(json.loads(subprocess.check_output(
            [sys.executable, '-B', str(HERE / 'prepare_submission_profile.py')],
            text=True, timeout=60))['overlay'])
        cls.overlay = compose(b.ROOT / submission)
        rt = (cls.overlay / TARGETS[1]).read_text()
        cp = (cls.overlay / 'src/graphics/vulkan/command_processor.cpp').read_text()
        methods = []
        for name in ('GetColorVulkanFormat', 'GetColorOwnershipTransferVulkanFormat',
                     'GetHostRenderTargetsRenderPass', 'GetHostRenderTargetsFramebuffer',
                     'GetLastUpdateRenderingAttachments'):
            methods.append(extract_method(rt, 'VulkanRenderTargetCache::' + name))
        for occurrence in (0, 1):
            methods.append(extract_method(cp, 'VulkanCommandProcessor::SubmitBarriersAndEnterRenderTargetCacheRenderPass', occurrence))
        methods.append(extract_method(cp, 'VulkanCommandProcessor::EndRenderPass'))
        extracted = '\n\n'.join(methods).replace('VulkanRenderTargetCache::', 'CacheHarness::').replace(
            'VulkanCommandProcessor::', 'ProcessorHarness::').replace('ui::vulkan::VulkanDevice', 'MockDevice')
        (cls.directory / 'extracted_reuse_methods.inc').write_text(extracted)
        plan = json.loads((cls.reference / 'plan.json').read_text())
        original_source = str(b.SDK / TARGETS[1])
        unit = next(u for u in plan['units'] if u['name'].startswith('plugin-c')
                    and original_source in u['command'])
        cls.binary = cls.directory / 'fixture'
        flags = list(unit['command'])
        flags[flags.index(original_source)] = str(HERE / 'render_pass_reuse_fixture.cpp')
        flags = flags[:flags.index('-c')]
        flags = [word for word in flags if word not in ('-DNDEBUG', '-O3')]
        flags[1:1] = ['-I' + str(cls.overlay / 'include'), '-I' + str(cls.directory),
                      '-I' + str(b.SDK / 'src/graphics'), '-I' + str(b.SDK / 'thirdparty/renderdoc'),
                      '-DREX_ASAHI_SDK_LAB=1', '-UNDEBUG', '-O1']
        flags += ['-L' + str(cls.reference / 'runtime'), '-lrexruntime',
                  '-Wl,-rpath,' + str(cls.reference / 'runtime'), '-o', str(cls.binary)]
        result = subprocess.run(flags, capture_output=True, text=True, timeout=30, preexec_fn=limit_compiler,
                                env=os.environ | {'TMPDIR': str(cls.directory)})
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def run_case(self, mode):
        env = {key: value for key, value in os.environ.items() if key != 'LD_PRELOAD'}
        env['VK_DRIVER_FILES'] = '/dev/null'
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True,
                                timeout=5, preexec_fn=limit_compiler, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS: CPU extracted-method regression', result.stdout)

    def test_exact_whitelist_and_reserved_bits(self): self.run_case('whitelist')
    def test_attachment_pitch_format_and_msaa_guards(self): self.run_case('identity')
    def test_enabled_dynamic_reuses_identical_scope(self): self.run_case('enabled-dynamic')
    def test_disabled_dynamic_preserves_original_identity(self): self.run_case('disabled-dynamic')
    def test_enabled_legacy_reuses_identical_scope(self): self.run_case('enabled-legacy')
    def test_disabled_legacy_preserves_original_identity(self): self.run_case('disabled-legacy')
    def test_dynamic_required_barrier_still_ends_scope(self): self.run_case('enabled-dynamic-barrier')
    def test_legacy_required_barrier_still_ends_scope(self): self.run_case('enabled-legacy-barrier')

    def test_optional_patch_is_not_in_baseline_lock(self):
        lock = json.loads((HERE / 'sdk.lock.json').read_text())
        self.assertFalse(any('render-pass-reuse' in p['path'] for p in lock['asahiPatches']))
        source = (self.overlay / TARGETS[1]).read_text()
        self.assertIn('REXCVAR_DEFINE_BOOL(fh1_asahi_render_pass_reuse, false,', source)
        self.assertEqual(source.count('render_pass_reuse::CacheKey('), 2)


if __name__ == '__main__':
    unittest.main()
