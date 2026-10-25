"""Regression: preservation verification must include new files and refuse local edits."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import sdk


class SdkVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "sdk"
        self.repo.mkdir()
        subprocess.run(["git", "init", "--quiet", str(self.repo)], check=True)
        (self.repo / "source.txt").write_text("original\n")
        sdk.git(self.repo, "add", "source.txt")
        env = os.environ | {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
        sdk.git(self.repo, "commit", "--quiet", "-m", "baseline", env=env)
        self.commit = sdk.git(self.repo, "rev-parse", "HEAD")

    def test_clean_and_unexpected_new_file(self):
        self.assertEqual(sdk.verify_tree(self.repo, self.commit, [])["verifiedFiles"], 1)
        (self.repo / "forgotten.cpp").write_text("required local source\n")
        with self.assertRaisesRegex(RuntimeError, "forgotten.cpp"):
            sdk.verify_tree(self.repo, self.commit, [])

    def test_locked_patch_reproduces_new_file_and_detects_modification(self):
        (self.repo / "added.cpp").write_text("preserved source\n")
        sdk.git(self.repo, "add", "added.cpp")
        patch_file = self.root / "addition.patch"
        patch_file.write_text(sdk.git(self.repo, "diff", "--cached") + "\n")
        sdk.git(self.repo, "reset", "--quiet")
        patches = [{"path": str(patch_file)}]
        self.assertEqual(sdk.verify_tree(self.repo, self.commit, patches)["verifiedFiles"], 2)
        (self.repo / "added.cpp").write_text("unrecorded edit\n")
        with self.assertRaisesRegex(RuntimeError, "added.cpp"):
            sdk.verify_tree(self.repo, self.commit, patches)
        before = (self.repo / "added.cpp").read_bytes()
        with self.assertRaises(RuntimeError):
            sdk.prepare(self.repo, {"sdkCommit": self.commit,
                "inheritedPatches": patches, "asahiPatches": []})
        self.assertEqual((self.repo / "added.cpp").read_bytes(), before)

    def test_patch_checksum_tampering_is_rejected(self):
        lock = json.loads(sdk.LOCK.read_text())
        lock["asahiPatches"][0]["sha256"] = "0" * 64
        path = self.root / "lock.json"
        path.write_text(json.dumps(lock))
        with patch.object(sdk, "LOCK", path), self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            sdk.load_lock()

    def test_version_metadata_is_part_of_verification(self):
        sdk.git(self.repo, "tag", "v1.0.0")
        description = sdk.git(self.repo, "describe", "--tags", "--long", "--match", "v[0-9]*.[0-9]*", "--exclude", "*-*")
        lock = {"sdkCommit": self.commit, "inheritedPatches": [], "asahiPatches": [],
                "sdlPatches": [], "submodules": [], "versionDescribe": description}
        self.assertTrue(sdk.verify(self.repo, lock)["verified"])
        lock["versionDescribe"] = "v1.0.0-999-g0000000"
        with self.assertRaisesRegex(RuntimeError, "version history differs"):
            sdk.verify(self.repo, lock)


if __name__ == "__main__":
    unittest.main()
