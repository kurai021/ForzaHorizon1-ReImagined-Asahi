"""CPU-only sampler boundary/cleanup tests; no perf privilege or game required."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from cpu_sampler import CpuSampler, record_command, thread_snapshot
from profiling_tools import perf_environment, verify_rpm
from resource_guard import process_identity


class CpuSamplerTests(unittest.TestCase):
    def test_sampling_is_bounded_user_space_and_one_process(self):
        command = record_command(Path("/local"), 123, Path("/output"))
        self.assertIn("cpu-clock:u", command)
        self.assertIn("--no-buildid-cache", command)
        self.assertEqual(command[command.index("--pid") + 1], "123")
        self.assertNotIn("--all-cpus", command)
        self.assertEqual(command[-3:], ["--", "sleep", "30"])
        for args in ((0, 30, 49), (123, 46, 49), (123, 30, 100)):
            with self.assertRaises(ValueError):
                record_command(Path("/local"), args[0], Path("/output"), args[1], args[2])

    def test_perf_environment_is_private(self):
        before = os.environ.copy()
        env = perf_environment(Path("/local"))
        self.assertEqual(env["LD_LIBRARY_PATH"], "/local/usr/lib64")
        self.assertEqual(env["PERF_CONFIG"], "/dev/null")
        self.assertEqual(env["DEBUGINFOD_URLS"], "")
        self.assertEqual(dict(os.environ), before)

    def test_reused_or_missing_target_is_not_sampled(self):
        self.assertEqual(thread_snapshot(os.getpid(), None), [])
        self.assertEqual(thread_snapshot(os.getpid(), -1), [])
        snapshot = thread_snapshot(os.getpid(), process_identity(os.getpid()))
        self.assertTrue(any(row["tid"] == os.getpid() for row in snapshot))
        self.assertTrue(all(row["userTicks"] >= 0 for row in snapshot))
        with tempfile.TemporaryDirectory() as temporary, patch("cpu_sampler.process_identity", return_value=None):
            with self.assertRaises(RuntimeError):
                CpuSampler(123, Path(temporary))

    def test_bad_rpm_hash_is_rejected_before_rpm_command(self):
        with tempfile.TemporaryDirectory() as temporary, patch("profiling_tools.subprocess.run") as run:
            path = Path(temporary) / "bad.rpm"
            path.write_bytes(b"not a package")
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                verify_rpm(path, "0" * 64)
            run.assert_not_called()

    def test_collector_can_finish_without_signalling_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            perf = directory / "usr/bin/perf"
            perf.parent.mkdir(parents=True)
            perf.write_text(f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
                            "Path(sys.argv[sys.argv.index('--output')+1]).write_bytes(b'synthetic')\n")
            perf.chmod(0o700)
            sampler = CpuSampler(os.getpid(), directory, perf_root=directory, seconds=1)
            sampler.process.wait(timeout=3)
            sampler.tick()
            report = sampler.finish()
            self.assertTrue(report["completed"])
            self.assertEqual(report["processCleanup"]["remainingPids"], [])
            self.assertIsNotNone(process_identity(os.getpid()))
            self.assertEqual(sampler.finish(), report)

    def test_failed_collector_is_not_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            perf = directory / "usr/bin/perf"
            perf.parent.mkdir(parents=True)
            perf.write_text(f"#!{sys.executable}\nraise SystemExit(2)\n")
            perf.chmod(0o700)
            sampler = CpuSampler(os.getpid(), directory, perf_root=directory, seconds=1)
            sampler.process.wait(timeout=3)
            with self.assertRaisesRegex(RuntimeError, "perf exited 2"):
                sampler.tick()
            self.assertFalse(sampler.finish()["completed"])

    def test_data_ceiling_invalidates_even_an_exit_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            perf = directory / "usr/bin/perf"
            perf.parent.mkdir(parents=True)
            perf.write_text(f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
                            "Path(sys.argv[sys.argv.index('--output')+1]).write_bytes(b'too large')\n")
            perf.chmod(0o700)
            sampler = CpuSampler(os.getpid(), directory, perf_root=directory, seconds=1)
            sampler.process.wait(timeout=3)
            with patch("cpu_sampler.MAX_DATA_BYTES", 1), self.assertRaisesRegex(RuntimeError, "exceeded"):
                sampler.tick()
            self.assertFalse(sampler.finish()["completed"])


if __name__ == "__main__":
    unittest.main()
