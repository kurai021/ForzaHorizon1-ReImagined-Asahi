"""Compile/run Vulkan-free telemetry tests with bounded compiler/process resources."""
import os
from pathlib import Path
import resource
import shutil
import subprocess
import tempfile
import unittest

from analyse_submission_profile import analyse
from prepare_submission_profile import syntax_command
HERE = Path(__file__).resolve().parent


def compiler_limit():
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))


class SubmissionProfileTests(unittest.TestCase):
    def test_cpu_only_recorder(self):
        compiler = shutil.which("clang++") or shutil.which("c++")
        if not compiler:
            self.skipTest("C++17 compiler required for telemetry core tests")
        with tempfile.TemporaryDirectory(prefix="asahi-submission-") as temporary:
            directory = Path(temporary)
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O2", "-pthread",
                       str(HERE / "submission_profile_test.cpp"), "-o", str(directory / "test")]
            build = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                   preexec_fn=compiler_limit, env=os.environ | {"TMPDIR": temporary})
            self.assertEqual(build.returncode, 0, build.stderr)
            result = subprocess.run([str(directory / "test"), temporary],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS: bounded submission stages", result.stdout)
            report = analyse(directory / "ordinary.csv")
            self.assertTrue(report["usableForCpuAttribution"], report["issues"])
            self.assertEqual(report["capturedJobs"], 1)
            self.assertEqual(report["capturedWaits"], 2)
            self.assertFalse(analyse(directory / "caps.csv")["usableForCpuAttribution"])
            self.assertEqual(analyse(directory / "stages.csv")["failedJobs"], 1)
            ordinary = (directory / "ordinary.csv").read_text()
            partial = directory / "partial.csv"
            partial.write_text(ordinary.replace("#status=complete\n", ""))
            self.assertFalse(analyse(partial)["usableForCpuAttribution"])
            self.assertFalse(analyse(partial)["completeFile"])
            # Bad clock/row structure cannot be silently interpreted as zero cost.
            bad = directory / "bad.csv"
            bad.write_text(ordinary.replace("job,100,5,reset_pool,", "job,100,5,unknown_stage,"))
            with self.assertRaises(ValueError):
                analyse(bad)

    def test_syntax_check_does_not_reuse_pch_or_write_original_objects(self):
        sdk = Path("/sdk")
        source = sdk / "src/graphics/vulkan/command_processor.cpp"
        command = f"clang++ -I/sdk/include -Xclang -include-pch -Xclang /old.pch -MD -MF /old.d -o /old.o -c {source}"
        result = syntax_command(command, Path("/overlay"), sdk)
        for original in ("/old.pch", "/old.d", "/old.o", str(source), "-c", "-include-pch"):
            self.assertNotIn(original, result)
        self.assertIn("/overlay/src/graphics/vulkan/command_processor.cpp", result)
        self.assertIn("-fsyntax-only", result)

    def test_profile_patch_is_not_part_of_baseline_lock(self):
        import json
        lock = json.loads((HERE / "sdk.lock.json").read_text())
        paths = [record["path"] for record in lock["asahiPatches"]]
        self.assertFalse(any("submission-profile" in path for path in paths))


if __name__ == "__main__":
    unittest.main()
