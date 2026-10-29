"""Exercise publication boundaries with real Git trees and source archives."""
import contextlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import audit_publication as audit
import sdk


class PublicationAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        sdk.git(self.repo, "init", "--quiet")
        for name in audit.LICENSES:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("preserved attribution\n")
        self.commit()
        self.base = sdk.git(self.repo, "rev-parse", "HEAD")

    def commit(self):
        sdk.git(self.repo, "add", ".")
        env = os.environ | {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
        sdk.git(self.repo, "commit", "--quiet", "-m", "test source", env=env)

    def run_audit(self, *args):
        output = io.StringIO()
        with patch.object(audit, "ROOT", self.repo), patch.object(audit, "BASE", self.base), \
                patch("sys.argv", ["audit", *args]), contextlib.redirect_stdout(output):
            code = audit.main()
        return code, json.loads(output.getvalue())

    def test_source_archive_matches_git_but_extra_game_data_is_rejected(self):
        (self.repo / "source.py").write_text("print('source')\n")
        self.commit()
        archive = self.root / "source.tar"
        sdk.git(self.repo, "archive", "--format=tar", "-o", str(archive), "HEAD")
        self.assertEqual(self.run_audit("--archive", str(archive))[0], 0)
        with tarfile.open(archive, "a") as output:
            member = tarfile.TarInfo("extracted/default.xex")
            member.size = 4
            output.addfile(member, io.BytesIO(b"fake"))
        code, report = self.run_audit("--archive", str(archive))
        self.assertTrue(code)
        self.assertTrue(any("Unexpected archive member" in error for error in report["errors"]))

    def test_generated_cpp_is_rejected_even_though_it_is_text(self):
        path = self.repo / "forza-recomp/generated/default/forza_recomp.0.cpp"
        path.parent.mkdir(parents=True)
        path.write_text("generated test fixture\n")
        self.commit()
        self.assertTrue(self.run_audit()[0])

    def test_active_release_permissions_are_rejected(self):
        path = self.repo / ".github/workflows/release.yml"
        path.parent.mkdir(parents=True)
        path.write_text("permissions:\n  contents: write\n")
        self.commit()
        code, report = self.run_audit()
        self.assertTrue(code)
        self.assertTrue(any("workflow remains active" in error for error in report["errors"]))

    def test_changed_attribution_is_rejected(self):
        (self.repo / "LICENSE").write_text("missing attribution\n")
        self.commit()
        code, report = self.run_audit()
        self.assertTrue(code)
        self.assertTrue(any("Inherited license changed" in error for error in report["errors"]))

    def test_inherited_fixture_paths_are_distinct_from_new_machine_paths(self):
        path = self.repo / "fixture.py"
        path.write_text("fixture = " + repr("/" + "home/player/fixture") + "\n")
        self.commit()
        self.base = sdk.git(self.repo, "rev-parse", "HEAD")
        path.write_text(path.read_text() + "value = 1\n")
        self.commit()
        self.assertEqual(self.run_audit()[0], 0)
        path.write_text(path.read_text() + "private = " + repr("/" + "home/private-machine/work") + "\n")
        self.commit()
        self.assertTrue(self.run_audit()[0])

    def test_unrelated_host_incidents_are_not_project_reports(self):
        (self.repo / 'report.md').write_text('fs-' + 'verity: FILE ' + 'CORRUPTED, inode 123456\n')
        self.commit()
        code, report = self.run_audit()
        self.assertTrue(code)
        self.assertTrue(any('Unrelated host-system incident' in error for error in report['errors']))

    def test_source_include_fragments_are_publishable(self):
        (self.repo / 'fixture.inc').write_text('// Redistributable synthetic fixture.\n')
        self.commit()
        self.assertEqual(self.run_audit()[0], 0)


if __name__ == "__main__":
    unittest.main()
