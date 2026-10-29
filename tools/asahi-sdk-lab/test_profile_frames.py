"""CPU-only tests of offline performance triage; no local game data required."""
import argparse
from pathlib import Path
import tempfile
import unittest

from profile_frames import parse_window, profile, read_frames, summarize


def frame(t, ms, pipelines=0):
    return dict(t=t, ms=ms, cpu_ms=10, gpu_wait_ms=4, guest_wait_ms=6,
                draws=100, shaders=0, pipelines=pipelines)


class ProfileFramesTests(unittest.TestCase):
    def test_fps_is_elapsed_time_weighted(self):
        result = summarize([frame(1, 25), frame(2, 75)])
        self.assertEqual(result["fps"], 20)
        self.assertEqual(result["p95Ms"], 75)
        self.assertEqual(result["above30FpsBudgetPercent"], 50)

    def test_cohorts_do_not_hide_non_creation_stalls(self):
        rows = [frame(1, 30), frame(2, 100, 1), frame(3, 70)]
        groups = profile(rows, 1, 4)["cohorts"]
        self.assertEqual(groups["withShaderOrPipelineCreation"]["frames"], 1)
        self.assertEqual(groups["above50MsWithoutCreation"]["frames"], 1)
        self.assertEqual(groups["withoutShaderOrPipelineCreation"]["meanMs"], 50)

    def test_half_open_window(self):
        self.assertEqual(profile([frame(1, 20), frame(2, 20)], 1, 2)["cohorts"]["all"]["frames"], 1)
        self.assertEqual(summarize([]), {"frames": 0})

    def read_text(self, text):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "frames.csv"
            path.write_text(text)
            return read_frames(path)

    def test_partial_final_row_is_counted(self):
        header = "t,ms,cpu_ms,gpu_wait_ms,guest_wait_ms,draws,shaders,pipelines\n"
        rows, incomplete = self.read_text(header + "1,30,10,4,6,100,0,0\n2,40,")
        self.assertEqual(len(rows), 1)
        self.assertEqual(incomplete, 1)

    def test_bad_rows_are_not_silently_dropped(self):
        header = "t,ms,cpu_ms,gpu_wait_ms,guest_wait_ms,draws,shaders,pipelines\n"
        for text in ("1,30,\n2,40,10,4,6,100,0,0", "1,nan,10,4,6,100,0,0",
                     "1,0,10,4,6,100,0,0", "1,30,10,4,6,100,0,0,extra",
                     "2,30,10,4,6,100,0,0\n1,30,10,4,6,100,0,0"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.read_text(header + text)
        with self.assertRaises(ValueError):
            self.read_text("t,ms\n1,30\n")

    def test_window_validation(self):
        self.assertEqual(parse_window("80:170"), (80, 170))
        for value in ("nan:10", "1:inf", "-1:10", "10:1", "1:1", "1", "a:b"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                parse_window(value)


if __name__ == "__main__":
    unittest.main()
