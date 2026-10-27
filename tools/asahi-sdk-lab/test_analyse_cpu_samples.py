"""Synthetic perf script and clock-bridge tests; no sampling or GPU access."""
import unittest

from analyse_cpu_samples import align_frames, parse_samples, summarize


class CpuSampleAnalysisTests(unittest.TestCase):
    def test_leaf_only_and_thread_names_with_spaces(self):
        text = ("Xenia CPU  123/124  10.125000001: cpu-clock:u: \n"
                "\t 40084c hot_fn (/local/game)\n\t ffffff [unknown] ([unknown])\n\n"
                "Xenia CPU  123/125  10.126000001: cpu-clock:u: \n"
                "\t 40084c other_fn (/local/plugin.so)\n")
        rows, unparsed = parse_samples(text)
        self.assertEqual(unparsed, 0)
        self.assertEqual(rows[0]["monoNs"], 10_125_000_001)
        self.assertEqual(rows[0]["symbol"], "hot_fn")
        self.assertEqual(rows[0]["callchainFrames"], 2)
        self.assertEqual(summarize(rows)["unknownLeafSamples"], 0)
        self.assertEqual(summarize(rows)["threads"][0]["comm"], "Xenia CPU")

    def test_unknown_and_empty_samples_are_visible(self):
        self.assertEqual(summarize([])["samples"], 0)
        rows, _ = parse_samples("worker 1/2 1.000000000: cpu-clock:u:\n")
        self.assertEqual(summarize(rows)["unknownLeafSamples"], 1)
        _, unparsed = parse_samples("unrecognized header cpu-clock:u:\n")
        self.assertEqual(unparsed, 1)

    def test_inline_leaf_retains_its_symbol_and_real_module(self):
        text = ("CPU 1/2 1.000000000: cpu-clock:u:\n"
                "\t 40084c PhysicalHostOffset (inlined)\n"
                "\t 40084c guest_fn (/local/forza)\n")
        rows, _ = parse_samples(text)
        self.assertEqual(rows[0]["symbol"], "PhysicalHostOffset")
        self.assertEqual(rows[0]["dso"], "/local/forza")
        self.assertEqual(summarize(rows)["topLeaves"][0]["samples"], 1)

    def test_monotonic_bridge_not_startup_relative_guess(self):
        metadata = dict(requestedEpochNs=100_000_000_000, requestedMonoNs=10_000_000_000,
                        endedEpochNs=101_000_000_000, endedMonoNs=11_000_000_000)
        frames = [dict(epoch=100.1, ms=100, shaders=0, pipelines=1),
                  dict(epoch=100.2, ms=100, shaders=0, pipelines=0)]
        samples = [dict(monoNs=10_050_000_000, comm="CPU", tid=1, symbol="hot", dso="game"),
                   dict(monoNs=10_150_000_000, comm="CPU", tid=1, symbol="hot", dso="game")]
        report = align_frames(samples, frames, metadata)
        self.assertTrue(report["aligned"])
        self.assertEqual(report["cohorts"]["creationFrames"]["samples"], 1)
        self.assertEqual(report["cohorts"]["above50MsWithoutCreation"]["samples"], 1)
        metadata["endedEpochNs"] += 20_000_000
        self.assertFalse(align_frames(samples, frames, metadata)["aligned"])


if __name__ == "__main__":
    unittest.main()
