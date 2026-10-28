"""CPU manifest/launch tests; fake files only, no shared library or device loaded."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import prepare_drm_ioctl_profile as p


class DrmPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='drm-manifest-')
        self.addCleanup(self.temporary.cleanup)
        self.private = Path(self.temporary.name).resolve()
        self.base = self.private / 'probes'
        self.header = self.private / 'asahi_drm.h'
        self.header.write_text('fake UAPI\n')
        self.inputs = {'sourceSha256': '', 'headers': {str(self.header): p.sha(self.header)},
                       'exports': p.EXPORTS, 'privateRoot': str(self.private)}
        self.inputs['sourceSha256'] = hashlib.sha256(b'fake source\n').hexdigest()
        token = p.fingerprint(self.inputs)
        self.directory = self.base / token
        self.directory.mkdir(parents=True)
        (self.directory / 'drm_ioctl_profile.cpp').write_text('fake source\n')
        (self.directory / 'exports.map').write_text(p.EXPORTS)
        self.library = self.directory / 'libasahi-drm-ioctl-profile.so'
        self.library.write_bytes(b'not a real DSO')
        self.plan = {'schema': 1, 'token': token, 'inputs': self.inputs, 'library': str(self.library),
                     'gpuWork': False, 'sdkModified': False}
        self.result = {'token': token, 'sourceSha256': self.inputs['sourceSha256'],
                       'librarySha256': p.sha(self.library), 'cpuLoaderPassed': True, 'abi': 1}
        self.write_manifests()
        self.headers = patch.object(p, 'HEADERS', (self.header,))
        self.headers.start()
        self.addCleanup(self.headers.stop)

    def write_manifests(self):
        (self.directory / 'plan.json').write_text(json.dumps(self.plan))
        (self.directory / 'build.json').write_text(json.dumps(self.result))

    def verify(self):
        return p.verified_library(self.directory, self.base, self.private)

    def test_verified_library_and_direct_child_scope(self):
        self.assertEqual(self.verify(), self.library)
        with self.assertRaisesRegex(RuntimeError, 'direct child'):
            p.verified_library(self.directory, self.private, self.private)

    def test_altered_manifest_or_build_binding_is_rejected(self):
        for target, key, value in ((self.plan, 'schema', 2), (self.plan, 'token', 'wrong'),
                                   (self.plan, 'gpuWork', True), (self.plan, 'library', '/other.so'),
                                   (self.result, 'token', 'wrong'), (self.result, 'sourceSha256', 'wrong'),
                                   (self.result, 'cpuLoaderPassed', False), (self.result, 'abi', 2)):
            with self.subTest(key=key, value=value):
                original = target[key]
                target[key] = value
                self.write_manifests()
                with self.assertRaises(RuntimeError):
                    self.verify()
                target[key] = original
        self.write_manifests()

    def test_changed_library_source_exports_or_header_is_rejected(self):
        for path in (self.library, self.directory / 'drm_ioctl_profile.cpp',
                     self.directory / 'exports.map', self.header):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(b'changed')
                with self.assertRaises(RuntimeError):
                    self.verify()
                path.write_bytes(original)

    def test_library_symlink_is_rejected(self):
        other = self.directory / 'other.so'
        self.library.rename(other)
        self.library.symlink_to(other)
        with self.assertRaises(RuntimeError):
            self.verify()

    def test_missing_headers_or_wrong_compiled_root_are_rejected(self):
        for key, value in (('headers', {}), ('privateRoot', '/wrong-root')):
            with self.subTest(key=key):
                original = self.inputs[key]
                self.inputs[key] = value
                self.write_manifests()
                with self.assertRaises(RuntimeError):
                    self.verify()
                self.inputs[key] = original
        self.write_manifests()

    def test_launch_environment_has_margins_exact_target_and_no_side_effects(self):
        output = self.private / 'new-profile/drm.csv'
        with patch.object(p, 'verified_library', return_value=self.library) as verify, \
             patch.object(p, 'PRIVATE', self.private):
            env = p.launch_environment(self.directory, self.private / 'forza', output, 80, 30)
        verify.assert_called_once_with(self.directory)
        self.assertEqual(env['ASAHI_DRM_IOCTL_START'], '75')
        self.assertEqual(env['ASAHI_DRM_IOCTL_SECONDS'], '40')
        self.assertEqual(env['ASAHI_DRM_IOCTL_TARGET'], str(self.private / 'forza'))
        self.assertEqual(env['LD_PRELOAD'], str(self.library))
        self.assertNotIn('ASAHI_DRM_IOCTL_OWNER_PID', env)
        self.assertFalse(output.parent.exists())

    def test_launch_environment_rejects_window_and_output_errors(self):
        output = self.private / 'new.csv'
        with patch.object(p, 'verified_library', return_value=self.library), patch.object(p, 'PRIVATE', self.private):
            for start, duration in ((4, 30), (121, 30), (80, 0), (80, 36)):
                with self.subTest(start=start, duration=duration), self.assertRaises(ValueError):
                    p.launch_environment(self.directory, self.private / 'forza', output, start, duration)
            with self.assertRaises(ValueError):
                p.launch_environment(self.directory, self.private / 'forza', self.private.parent / 'outside.csv', 80, 30)
            output.write_text('preserve\n')
            with self.assertRaises(ValueError):
                p.launch_environment(self.directory, self.private / 'forza', output, 80, 30)
            self.assertEqual(output.read_text(), 'preserve\n')


if __name__ == '__main__':
    unittest.main()
