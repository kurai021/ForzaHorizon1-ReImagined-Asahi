"""The preservation fork must not fetch or install upstream launcher releases."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import GITHUB_URL, updater
from forza_launcher.config import Config


class AsahiUpdatePolicyTests(unittest.TestCase):
    def test_default_policy_does_not_use_the_network_or_create_downloads(self):
        self.assertFalse(updater.UPDATES_ENABLED)
        with tempfile.TemporaryDirectory() as temporary, patch.object(updater.fetch, "urlopen") as network:
            folder = Path(temporary) / "downloads"
            with self.assertRaisesRegex(updater.UpdateError, "source-only Asahi"):
                updater.check()
            with self.assertRaisesRegex(updater.UpdateError, "source-only Asahi"):
                updater.download(updater.Asset("update", "https://example.invalid/", "0" * 64, 1), folder)
            network.assert_not_called()
            self.assertFalse(folder.exists())

    def test_identity_and_untrusted_upstream_assets(self):
        self.assertEqual(GITHUB_URL, "https://github.com/kurai021/ForzaHorizon1-ReImagined-Asahi")
        self.assertIn("repos/kurai021/ForzaHorizon1-ReImagined-Asahi/", updater.API_URL)
        release = updater.parse_release({"tag_name": "v0.1.8", "assets": [{
            "name": "update.tar.gz", "size": 1, "digest": "sha256:" + "0" * 64,
            "browser_download_url": "https://github.com/Dyotson/ForzaHorizon1-ReImagined/releases/download/v0.1.8/update.tar.gz"}]})
        self.assertEqual(release.assets, ())

    def test_new_profiles_default_to_no_background_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertFalse(Config(Path(temporary) / "launcher.json").d["check_updates"])


if __name__ == "__main__":
    unittest.main()
