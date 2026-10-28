"""CPU-only independent option proofs; no patch installation or graphics device."""
import json
import os
import subprocess
import sys
import unittest

import prepare_readback_options as p


class ReadbackOptionsTests(unittest.TestCase):
    def test_changed_history_fails_closed(self):
        with self.assertRaises(ValueError): p.history_variant('')

    def test_history_variant_keeps_bounds_and_mutation_invalidation(self):
        if not (p.SDK / p.HISTORY).is_file(): self.skipTest('Pinned SDK required')
        text = p.history_variant((p.SDK / p.HISTORY).read_text())
        self.assertIn('kHotRunFrames = 150', text)
        self.assertIn('kForgetFrames = 54000', text)
        self.assertIn('kMaxKeys = 2048', text)
        self.assertIn('if (changed) forget_clean_ = false;', text)
        self.assertIn('forget_clean_ && last_forget_frame_ == frame', text)

    def test_extracted_state_and_memory_type_models(self):
        prefix = p.pinned.ROOT / 'forza-recomp/out/asahi-profiling/candidate-play/readback-20261028T213338Z/profile/readback.csv'
        if not (p.SDK / p.HISTORY).is_file() or not prefix.is_file():
            self.skipTest('Pinned SDK and preserved prefix required')
        result = subprocess.run([sys.executable, '-B', str(p.HERE / 'prepare_readback_options.py')],
            capture_output=True, text=True, timeout=60, env=os.environ | {'TMPDIR': '/tmp/opencode'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        proof = json.loads(result.stdout)['proof']
        self.assertTrue(proof['historyExactStateEquivalent'])
        self.assertTrue(proof['coherencyTypeSelectionModelPassed'])
        self.assertEqual(proof['syntheticDuplicateFrameBaselineVisits'], 1536)
        self.assertEqual(proof['syntheticDuplicateFrameMemoVisits'], 256)
        self.assertFalse(proof['gamePerformanceMeasured'])


if __name__ == '__main__': unittest.main()
