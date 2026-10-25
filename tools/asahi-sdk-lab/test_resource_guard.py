"""Resource failures and descendant cleanup tested without GPU or memory stress."""
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import resource_guard as guard


class ResourceGuardTests(unittest.TestCase):
    def sample(self):
        return guard.parse_memory("MemTotal: 16000000 kB\nMemAvailable: 8000000 kB\nMemFree: 1000000 kB\nSwapTotal: 8000000 kB\nSwapFree: 8000000 kB\n")

    def test_physical_available_memory_is_not_just_free_pages(self):
        sample = self.sample()
        self.assertGreater(sample["availableMiB"], guard.START_FREE_MIB)
        self.assertLess(sample["freeMiB"], guard.ABORT_FREE_MIB)
        self.assertIsNone(guard.memory_problem(sample, sample))

    def test_low_ram_swap_growth_and_pressure_stop_the_test(self):
        initial = self.sample()
        for changes in ({"availableMiB": 3000}, {"swapUsedMiB": 513}, {"pressureFullAvg10": 1.1}):
            with self.subTest(changes=changes):
                self.assertIsNotNone(guard.memory_problem(initial | changes, initial))

    def test_reused_pid_is_never_signalled(self):
        process = unittest.mock.Mock(pid=999999)
        with patch.object(guard, "process_identity", return_value=100):
            owned = guard.OwnedProcesses(process)
        with patch.object(guard, "process_identity", return_value=101), patch.object(guard.os, "kill") as kill:
            owned.finish()
            kill.assert_not_called()

    def test_cleanup_stops_only_the_spawned_tree(self):
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        process = subprocess.Popen([sys.executable, "-c",
            "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); time.sleep(30)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            owned = guard.OwnedProcesses(process)
            time.sleep(.15)
            owned.capture()
            self.assertGreaterEqual(len(owned.alive()), 2)
            result = owned.stop(grace=.2)
            process.wait(timeout=2)
            self.assertEqual(result["remainingPids"], [])
            self.assertIsNone(unrelated.poll())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            unrelated.terminate()
            unrelated.wait()

    def test_orphan_in_owned_session_is_cleaned_after_parent_exits(self):
        process = subprocess.Popen([sys.executable, "-c",
            "import subprocess,sys; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(child.pid,flush=True)"],
            stdout=subprocess.PIPE, text=True, start_new_session=True)
        child = int(process.stdout.readline())
        try:
            process.wait(timeout=2)
            owned = guard.OwnedProcesses(process)
            result = owned.finish()
            self.assertEqual(result["remainingPids"], [])
            self.assertIsNone(guard.process_identity(child))
        finally:
            process.stdout.close()

    def test_forced_kill_and_cleanup_history_survive_final_check(self):
        process = subprocess.Popen([sys.executable, "-c",
            "import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(30)"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            owned = guard.OwnedProcesses(process)
            owned.stop(grace=.1)
            process.wait(timeout=2)
            report = owned.finish()
            self.assertIn("SIGKILL", [item["signal"] for item in report["signals"]])
            self.assertIn(process.pid, report["leftoverPidsBeforeCleanup"])
            self.assertEqual(report["remainingPids"], [])
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()


if __name__ == "__main__":
    unittest.main()
