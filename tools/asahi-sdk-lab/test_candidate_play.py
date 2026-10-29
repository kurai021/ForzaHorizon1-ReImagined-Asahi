"""CPU-only tests: no real compositor calls, process signals or Vulkan initialization."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import candidate_play as c
import play_probe


class CandidatePlayTests(unittest.TestCase):
    def test_candidate_runtime_hashes_are_mandatory(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            candidate = base / 'token'
            runtime = candidate / 'runtime'
            runtime.mkdir(parents=True)
            (candidate / 'plan.json').write_text(json.dumps({'candidate': str(candidate)}))
            (candidate / 'build.json').write_text(json.dumps({'status': 'linked'}))
            with self.assertRaises(RuntimeError):
                c.verified_runtime(candidate, base)
            files = {}
            for name in ('forza', 'librexruntime.so', 'librexgpu-xenos.so'):
                (runtime / name).write_bytes(name.encode())
                files[name] = hashlib.sha256(name.encode()).hexdigest()
            (candidate / 'validation-test.json').write_text(json.dumps({'cpuValidated': True, 'runtimeFiles': files}))
            self.assertEqual(c.verified_runtime(candidate, base), runtime)
            (runtime / 'librexgpu-xenos.so').write_bytes(b'changed')
            with self.assertRaises(RuntimeError):
                c.verified_runtime(candidate, base)

    def test_physical_and_recording_flags_preserve_rendering_policy(self):
        flags = c.session_flags(Path('/new-profile'), True, True, 80, 30)
        self.assertIn('--fullscreen=false', flags)
        self.assertIn('--window_width=1280', flags)
        self.assertIn('--window_height=720', flags)
        self.assertIn('--fh1_submission_profile_csv=/new-profile/submission.csv', flags)
        self.assertFalse(any('vsync' in flag or 'threaded_submission' in flag for flag in flags))
        self.assertIn('--fh1_submission_profile_csv=', c.session_flags(Path('/p'), True, False, 80, 30))
        self.assertFalse(any('fh1_submission' in flag for flag in c.session_flags(Path('/p'), True, False, 80, 30, False)))
        for start, duration in ((0, 30), (80, 46)):
            with self.assertRaises(ValueError):
                c.session_flags(Path('/p'), True, True, start, duration)

    def test_surface_log_uses_actual_latest_swapchain_not_requested_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / 'game.log'
            monitor = c.SurfaceLog(log)
            monitor.tick()
            self.assertIsNone(monitor.extent)
            log.write_text('resolution=720p\nVulkanPresenter: Created 2560x1080 swapchain with format 50\n')
            monitor.tick()
            self.assertEqual(monitor.extent, (2560, 1080))
            with log.open('a') as stream:
                stream.write('VulkanPresenter: Created 1280x720 swap')
            monitor.tick()
            self.assertEqual(monitor.extent, (2560, 1080))
            with log.open('a') as stream:
                stream.write('chain with format 50\n')
            monitor.tick()
            self.assertEqual(monitor.extent, (1280, 720))
            self.assertEqual(monitor.events, 2)
            with log.open('a') as stream:
                stream.write('VulkanPresenter: Created 2560x1080 swapchain\nVulkanPresenter: Created 1280x720 swapchain\n')
            monitor.tick()
            self.assertEqual(monitor.extent, (1280, 720))
            self.assertEqual(monitor.new_extents, [(2560, 1080), (1280, 720)])
            log.write_text('truncated\n')
            with self.assertRaises(RuntimeError):
                monitor.tick()

    def test_compositor_dispatch_only_targets_owned_window_with_scale(self):
        clients = [{'pid': 123, 'address': '0x123', 'monitor': 2},
                   {'pid': 456, 'address': '0x456', 'monitor': 1}]
        calls = []
        def request(command, **kwargs):
            calls.append(command)
            if command[1:] == ['-j', 'clients']:
                text = json.dumps(clients)
            elif command[1:] == ['-j', 'monitors']:
                text = json.dumps([{'id': 2, 'scale': 1.25}])
            else:
                text = 'ok'
            return subprocess.CompletedProcess(command, 0, text, '')
        with patch.object(c, 'process_identity', return_value=99):
            result = c.configure_owned_window(123, 99, request)
        self.assertEqual(result['logicalSizeRequested'], [1024, 576])
        dispatches = [call for call in calls if call[1] == 'eval']
        self.assertEqual(len(dispatches), 1)
        self.assertIn('address:0x123', dispatches[0][-1])
        self.assertIn('w.pid == 123', dispatches[0][-1])
        self.assertIn('x=1024, y=576, relative=false, window=w', dispatches[0][-1])
        self.assertNotIn('0x456', dispatches[0][-1])
        self.assertFalse(any('keyword' in call or 'reload' in call for call in calls))
        calls.clear()
        with patch.object(c, 'process_identity', return_value=100):
            with self.assertRaises(RuntimeError):
                c.configure_owned_window(123, 99, request)
        self.assertEqual(calls, [])

    def test_cooldown_requires_full_45_seconds_after_observation(self):
        with tempfile.TemporaryDirectory() as temporary:
            logs = Path(temporary)
            record = logs / 'run/result.json'
            record.parent.mkdir()
            record.write_text(json.dumps({'gpu': [{'name': 'synthetic'}]}))
            os.utime(record, (100, 100))
            with self.assertRaises(RuntimeError):
                c.require_cooldown(logs, now=144)
            c.require_cooldown(logs, now=145)

    def test_game_mappings_must_use_both_candidate_libraries(self):
        runtime = Path('/candidate/runtime')
        maps = '1000-2000 r-xp 0000 00:00 1 /candidate/runtime/librexruntime.so\n'
        self.assertFalse(c.loaded_candidate_libraries(maps, runtime))
        maps += '2000-3000 r-xp 0000 00:00 2 /candidate/runtime/librexgpu-xenos.so\n'
        self.assertTrue(c.loaded_candidate_libraries(maps, runtime))
        with self.assertRaises(RuntimeError):
            c.loaded_candidate_libraries(maps.replace('/candidate/runtime/librexruntime', '/original/librexruntime'), runtime)

    def test_api_preflight_only_checks_capabilities(self):
        calls = []
        def request(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, 'ok\n', '')
        c.window_api_preflight(request)
        self.assertIn('assert(type(', calls[0][-1])
        self.assertNotIn('hl.dispatch(', calls[0][-1])

    def test_compositor_failure_keeps_diagnostics(self):
        def failure(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, 'unsupported API', 'request failed')
        with self.assertRaisesRegex(RuntimeError, 'unsupported API'):
            c.window_api_preflight(failure)

    def test_abort_signals_only_original_game_and_leaves_supervisor_to_observe(self):
        from unittest.mock import Mock
        process = Mock()
        process.wait.return_value = 0
        owned = Mock(identities={123: 99, 456: 98})
        with patch.object(c, 'process_identity', side_effect=lambda pid: 99), patch.object(c.os, 'kill') as kill:
            result = c.abort_game_with_observation(process, owned, ['123', '456', '789'])
        self.assertEqual(result['signaledGames'], [123])
        self.assertTrue(result['supervisorCompleted'])
        kill.assert_called_once_with(123, c.signal.SIGINT)
        process.wait.assert_called_once_with(timeout=45)

    def test_abort_observation_is_bounded(self):
        from unittest.mock import Mock
        process = Mock()
        process.wait.side_effect = subprocess.TimeoutExpired('supervisor', 45)
        owned = Mock(identities={123: 99})
        with patch.object(c, 'process_identity', return_value=99), patch.object(c.os, 'kill'):
            result = c.abort_game_with_observation(process, owned, ['123'])
        self.assertFalse(result['supervisorCompleted'])

    def test_resource_abort_without_live_game_still_waits_for_supervisor_observation(self):
        from unittest.mock import Mock
        process = Mock()
        process.wait.return_value = 125
        owned = Mock(identities={})
        with patch.object(c.os, 'kill') as kill:
            result = c.abort_game_with_observation(process, owned, [])
        self.assertEqual(result['signaledGames'], [])
        self.assertTrue(result['supervisorCompleted'])
        process.wait.assert_called_once_with(timeout=45)
        kill.assert_not_called()

    def test_launcher_resource_abort_preserves_observation_before_owned_cleanup(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'game-base').mkdir()
            (directory / 'game-base/forza').touch()
            (directory / 'logs').mkdir()
            (directory / 'default.xex').touch()
            process = Mock(returncode=0)
            process.poll.return_value = None
            owned = Mock()
            owned.finish.return_value = {'remainingPids': []}
            args = ['play_probe.py', '--name', 'resource-abort-test', '--game-root', str(directory),
                    '--profile', str(directory / 'profile'), '--no-validation']
            sample = {'availableMiB': 8000}
            with patch.object(sys, 'argv', args), patch.object(play_probe, 'LAB', directory), \
                 patch.object(play_probe, 'require_memory', return_value=sample), \
                 patch.object(play_probe, 'memory_snapshot', return_value=sample), \
                 patch.object(play_probe, 'memory_problem', return_value='swap limit exceeded'), \
                 patch.object(play_probe.shutil, 'which', return_value='/bin/test'), \
                 patch.object(play_probe.subprocess, 'Popen', return_value=process), \
                 patch.object(play_probe.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '')), \
                 patch.object(play_probe, 'OwnedProcesses', return_value=owned), \
                 patch.object(play_probe, 'abort_game_with_observation', return_value={'supervisorCompleted': True}) as observe, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(play_probe.main(), 2)
            observe.assert_called_once_with(process, owned, [])
            owned.stop.assert_not_called()
            owned.finish.assert_called_once()
            self.assertEqual(json.loads((directory / 'STOP-GPU.json').read_text())['reason'], 'swap limit exceeded')

    def test_plan_only_never_creates_profile_or_starts_game(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'forza').touch()
            (directory / 'default.xex').touch()
            (directory / 'plan.json').write_text(json.dumps({'inputs': {'overlay': {}}}))
            profile = directory / 'new-profile'
            arguments = ['play_probe.py', '--name', 'cpu-plan', '--seconds', '180', '--candidate', str(directory),
                         '--game-root', str(directory), '--profile', str(profile), '--physical-720p',
                         '--scene-msaa', 'off', '--no-validation', '--plan-only']
            output = io.StringIO()
            with patch.object(sys, 'argv', arguments), patch.object(play_probe, 'verified_runtime', return_value=directory), \
                 patch.object(play_probe, 'PLAY_ROOT', directory), \
                 patch.object(play_probe, 'LAB', directory / 'lab'), \
                 patch.object(play_probe, 'require_memory'), patch.object(play_probe, 'require_cooldown'), \
                 patch.object(play_probe.shutil, 'which', return_value='/bin/test'), \
                 patch.object(play_probe.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)), \
                 patch.object(play_probe.subprocess, 'Popen') as launch, contextlib.redirect_stdout(output):
                self.assertEqual(play_probe.main(), 0)
            launch.assert_not_called()
            self.assertFalse(profile.exists())
            plan = json.loads(output.getvalue())
            self.assertFalse(plan['gameLaunched'])
            self.assertIn('--fh1_submission_profile_csv=', plan['command'])
            self.assertIn('LD_LIBRARY_PATH=' + str(directory), plan['command'])

    def drm_plan(self, *, submission=True, ambient=None):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'forza').touch()
            (directory / 'default.xex').touch()
            (directory / 'plan.json').write_text(json.dumps({'inputs': {'overlay': {}}}))
            profile = directory / 'new-profile'
            probe = directory / 'probe'
            arguments = ['play_probe.py', '--name', 'drm-plan', '--seconds', '180',
                         '--candidate', str(directory), '--game-root', str(directory), '--profile', str(profile),
                         '--physical-720p', '--scene-msaa', 'off', '--no-validation', '--plan-only',
                         '--drm-ioctl-probe', str(probe)]
            if submission:
                arguments.append('--submission-profile')
            env = {'LD_PRELOAD': str(probe / 'libprofile.so'), 'ASAHI_DRM_IOCTL_CSV': str(profile / 'drm-ioctl.csv'),
                   'ASAHI_DRM_IOCTL_TARGET': str(directory / 'forza'), 'ASAHI_DRM_IOCTL_START': '75',
                   'ASAHI_DRM_IOCTL_SECONDS': '40', 'ASAHI_DRM_IOCTL_MAX_RECORDS': '262144'}
            output = io.StringIO()
            with patch.object(sys, 'argv', arguments), patch.dict(os.environ, ambient or {}, clear=True), \
                 patch.object(play_probe, 'verified_runtime', return_value=directory), \
                 patch.object(play_probe, 'launch_environment', return_value=env) as launch_env, \
                 patch.object(play_probe, 'PLAY_ROOT', directory), patch.object(play_probe, 'LAB', directory / 'lab'), \
                 patch.object(play_probe, 'require_memory'), patch.object(play_probe, 'require_cooldown'), \
                 patch.object(play_probe.shutil, 'which', return_value='/bin/test'), \
                 patch.object(play_probe.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)), \
                 patch.object(play_probe.subprocess, 'Popen') as launch, \
                 contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                try:
                    result = play_probe.main()
                finally:
                    launch.assert_not_called()
                    self.assertFalse(profile.exists())
                launch_env.assert_called_once_with(probe, directory / 'forza', profile / 'drm-ioctl.csv', 80, 30)
                self.assertNotIn('LD_PRELOAD', os.environ)
            self.assertEqual(result, 0)
            return json.loads(output.getvalue()), env

    def test_drm_plan_preloads_only_game_after_timeout_not_supervisor_or_helpers(self):
        plan, env = self.drm_plan(ambient={'ASAHI_DRM_IOCTL_OWNER_PID': 'stale'})
        command = plan['command']
        timeout = command.index('timeout')
        preload = command.index('LD_PRELOAD=' + env['LD_PRELOAD'])
        executable = command.index(env['ASAHI_DRM_IOCTL_TARGET'])
        self.assertLess(timeout, preload)
        self.assertLess(preload, executable)
        self.assertEqual(command[timeout + 5:preload], ['env', '-u', 'ASAHI_DRM_IOCTL_OWNER_PID'])
        self.assertFalse(any(value.startswith(('LD_PRELOAD=', 'ASAHI_DRM_IOCTL_')) for value in command[:timeout]))
        self.assertFalse(plan['gameLaunched'])
        self.assertTrue(plan['drmIoctlProbeRequested'])
        self.assertTrue(plan['submissionRecording'])

    def test_drm_plan_requires_submission_recording_and_no_ambient_preload(self):
        for options in ({'submission': False}, {'ambient': {'LD_PRELOAD': '/ambient.so'}}):
            with self.subTest(options=options), self.assertRaises(SystemExit) as error:
                self.drm_plan(**options)
            self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
