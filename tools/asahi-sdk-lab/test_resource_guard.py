"""Resource failures and descendant cleanup tested without GPU or memory stress."""
import subprocess
import json
from pathlib import Path
import tempfile
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

    def test_local_swap_policy_defaults_on_and_accepts_explicit_boolean(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'policy.json'
            self.assertTrue(guard.swap_growth_guard_enabled(path))
            for enabled in (False, True):
                path.write_text(json.dumps({'schema': 1, 'enforceSwapGrowth': enabled}))
                self.assertIs(guard.swap_growth_guard_enabled(path), enabled)

    def test_malformed_or_broader_policy_cannot_disable_other_guards(self):
        cases = ('not json', '[]', '{"schema":1,"enforceSwapGrowth":"false"}',
                 '{"schema":true,"enforceSwapGrowth":false}',
                 '{"schema":2,"enforceSwapGrowth":false}',
                 '{"schema":1,"enforceSwapGrowth":false,"ignoreKernelErrors":true}',
                 ' ' * 1025)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'policy.json'
            for contents in cases:
                with self.subTest(contents=contents[:80]):
                    path.write_text(contents)
                    with self.assertRaises(RuntimeError):
                        guard.swap_growth_guard_enabled(path)

    def test_disabled_swap_cutoff_does_not_abort_for_swap_alone(self):
        initial = self.sample() | {'swapGrowthGuardEnabled': False}
        self.assertIsNone(guard.memory_problem(initial | {'swapUsedMiB': 4096}, initial))

    def test_disabled_swap_cutoff_preserves_ram_and_both_psi_checks(self):
        initial = self.sample() | {'swapGrowthGuardEnabled': False}
        for changes in ({'availableMiB': 3000}, {'pressureFullAvg10': 1.1}, {'pressureSomeAvg10': 5.1}):
            with self.subTest(changes=changes):
                self.assertIsNotNone(guard.memory_problem(initial | {'swapUsedMiB': 4096} | changes, initial))

    def test_swap_policy_is_latched_from_initial_snapshot(self):
        initial = self.sample() | {'swapGrowthGuardEnabled': True}
        later = initial | {'swapUsedMiB': 513, 'swapGrowthGuardEnabled': False}
        self.assertIsNotNone(guard.memory_problem(later, initial))
        self.assertIsNone(guard.memory_problem(later | {'swapGrowthGuardEnabled': True},
                                             initial | {'swapGrowthGuardEnabled': False}))

    def test_snapshot_records_disabled_policy_but_still_measures_swap(self):
        with patch.object(guard, 'local_resource_policy', return_value={
                'enforceSwapGrowth': False, 'enforceMemoryPressure': True}):
            sample = guard.memory_snapshot()
        self.assertFalse(sample['swapGrowthGuardEnabled'])
        self.assertIsNone(sample['swapGrowthLimitMiB'])
        self.assertGreaterEqual(sample['swapUsedMiB'], 0)

    def test_psi_policy_defaults_on_in_missing_and_legacy_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'policy.json'
            self.assertTrue(guard.local_resource_policy(path)['enforceMemoryPressure'])
            path.write_text(json.dumps({'schema': 1, 'enforceSwapGrowth': False}))
            self.assertTrue(guard.local_resource_policy(path)['enforceMemoryPressure'])

    def test_explicit_psi_policy_requires_boolean(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'policy.json'
            for value in (True, False):
                path.write_text(json.dumps({'schema': 1, 'enforceSwapGrowth': False,
                                            'enforceMemoryPressure': value}))
                self.assertIs(guard.local_resource_policy(path)['enforceMemoryPressure'], value)
            path.write_text(json.dumps({'schema': 1, 'enforceSwapGrowth': False,
                                        'enforceMemoryPressure': 'false'}))
            with self.assertRaises(RuntimeError):
                guard.local_resource_policy(path)

    def test_disabled_psi_preserves_ram_and_independent_swap_guard(self):
        initial = self.sample() | {'memoryPressureGuardEnabled': False}
        stalled = initial | {'pressureFullAvg10': 20, 'pressureSomeAvg10': 50}
        self.assertIsNone(guard.memory_problem(stalled, initial))
        self.assertIsNotNone(guard.memory_problem(stalled | {'availableMiB': 3000}, initial))
        self.assertIsNotNone(guard.memory_problem(stalled | {'swapUsedMiB': 513}, initial))

    def test_psi_policy_is_latched_from_initial_snapshot(self):
        initial = self.sample() | {'memoryPressureGuardEnabled': True}
        stalled = initial | {'pressureFullAvg10': 2, 'memoryPressureGuardEnabled': False}
        self.assertIsNotNone(guard.memory_problem(stalled, initial))
        self.assertIsNone(guard.memory_problem(stalled | {'memoryPressureGuardEnabled': True},
                                             initial | {'memoryPressureGuardEnabled': False}))

    def test_snapshot_records_disabled_psi_but_keeps_pressure_telemetry(self):
        with patch.object(guard, 'local_resource_policy', return_value={
                'enforceSwapGrowth': False, 'enforceMemoryPressure': False}):
            sample = guard.memory_snapshot()
        self.assertFalse(sample['memoryPressureGuardEnabled'])
        self.assertIn('pressureFullAvg10', sample)
        self.assertIn('pressureSomeAvg10', sample)

    def test_launch_reserve_remains_required_when_swap_cutoff_is_disabled(self):
        sample = self.sample() | {'swapGrowthGuardEnabled': False, 'availableMiB': 5000}
        with patch.object(guard, 'memory_snapshot', return_value=sample), self.assertRaises(RuntimeError):
            guard.require_memory()

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
