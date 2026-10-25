"""Analysis boundaries used by the controlled MSAA trials (no graphics initialization)."""
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import msaa_bench


class MsaaAnalysisTests(unittest.TestCase):
    def test_active_stop_prevents_even_process_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            lab = Path(temporary)
            (lab / "STOP-GPU.json").write_text("{}")
            with patch.object(msaa_bench, "LAB", lab), patch.object(msaa_bench.subprocess, "run") as process:
                with self.assertRaisesRegex(RuntimeError, "no GPU run permitted"):
                    msaa_bench.preflight()
                process.assert_not_called()

    def test_frame_rate_is_weighted_by_elapsed_time(self):
        result = msaa_bench.statistics_ms([33.33, 50, 66.67])
        self.assertEqual(result["frames"], 3)
        self.assertAlmostEqual(result["fps"], 20)
        self.assertEqual(result["p50Ms"], 50)
        self.assertEqual(result["p95Ms"], 66.67)
        self.assertAlmostEqual(result["atMost35MsPercent"], 100 / 3)

    def test_hard_exit_partial_csv_tail_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "frames.csv"
            path.write_text("epoch,ms\n1,33.33\n2,")
            rows, incomplete = msaa_bench.read_csv(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(incomplete, 1)

    def test_native_capture_dimensions_survive_png_conversion(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "capture.ppm"
            path.write_bytes(b"P6\n2 1\n255\n" + bytes([255, 0, 0, 0, 255, 0]))
            self.assertEqual(msaa_bench.png_from_ppm(path), [2, 1])
            data = path.with_suffix(".png").read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", data[16:24]), (2, 1))


if __name__ == "__main__":
    unittest.main()
