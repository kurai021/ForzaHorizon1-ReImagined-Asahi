"""Simulated GPU diagnostics only: commands run Python, never Vulkan or the game."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asahi_sdk_lab as lab


class LabKernelHealthTests(unittest.TestCase):
    def test_late_kernel_error_stops_even_after_exit_zero(self):
        healthy = {"kernelReadable": True, "kernelError": False, "kernelLog": "", "errorLog": ""}
        unhealthy = healthy | {"kernelError": True, "kernelLog": "asahi GPU timeout"}
        memory = dict(availableMiB=8192, swapUsedMiB=0, pressureFullAvg10=0, pressureSomeAvg10=0)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            icd = directory / "not-a-real-icd.json"
            icd.write_text("{}")
            argv = ["lab", "--gpu", "--name", "simulated-late-fault", "--seconds", "5",
                    "--post-exit-seconds", "1", "--", sys.executable, "-B", "-c", "pass"]
            with patch.object(lab, "LAB", directory), patch.object(lab, "ICD", icd), \
                 patch.object(lab, "apple_device", return_value=[]), \
                 patch.object(lab, "memory_snapshot", return_value=memory), \
                 patch.object(lab, "require_memory", return_value=memory), \
                 patch.object(lab, "read_kernel", side_effect=[healthy, unhealthy]), \
                 patch.dict(lab.os.environ), \
                 patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(lab.main(), 0)
            result = json.loads(next((directory / "logs").glob("*/result.json")).read_text())
            self.assertEqual(result["returncode"], 0)
            self.assertTrue(result["kernelError"])
            self.assertGreaterEqual(result["postExitObservationSeconds"], 1)
            self.assertTrue((directory / "STOP-GPU.json").is_file())
            self.assertEqual(result["processCleanup"]["remainingPids"], [])

    def test_unreadable_journal_fails_closed(self):
        with patch.object(lab.subprocess, "run", side_effect=subprocess.TimeoutExpired("journalctl", 3)):
            result = lab.read_kernel("now")
        self.assertFalse(result["kernelReadable"])

    def test_kernel_fault_and_empty_error_journal_are_not_confused(self):
        for text, error in (("-- No entries --", False), ("asahi GPU timeout", True)):
            responses = [subprocess.CompletedProcess([], 0, text, ""),
                         subprocess.CompletedProcess([], 0, "-- No entries --", "")]
            with patch.object(lab.subprocess, "run", side_effect=responses):
                result = lab.read_kernel("now")
            self.assertTrue(result["kernelReadable"])
            self.assertEqual(result["kernelError"], error)


if __name__ == "__main__":
    unittest.main()
