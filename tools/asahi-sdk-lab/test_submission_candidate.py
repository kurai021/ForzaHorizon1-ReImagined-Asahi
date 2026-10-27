"""Candidate transformations are CPU-only; no compiler, loader or game execution."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build_submission_candidate as b


class CandidateTests(unittest.TestCase):
    def test_compile_outputs_and_pch_are_isolated(self):
        source = b.SDK / b.FILES[1]
        command = f'/usr/bin/clang++ -O3 -DNDEBUG -I{b.SDK}/include -Xclang -include-pch -Xclang /old.pch -include /old.hxx -MD -MT /old.o -MF /old.d -o /old.o -c {source}'
        result = b.compile_command(command, Path('/candidate/overlay'), Path('/candidate/lab'),
                                   Path('/candidate/objects/new.o'), {'/old.hxx': '/candidate/inputs/pch.hxx'})
        for word in ('/old.pch', '/old.hxx', '/old.o', '/old.d', '-MD', '-MF', '-MT', '-include-pch', '-fsyntax-only'):
            self.assertNotIn(word, result)
        self.assertIn('-O3', result)
        self.assertIn('/candidate/overlay/src/graphics/vulkan/command_processor.cpp', result)
        self.assertIn('/candidate/inputs/pch.hxx', result)
        self.assertEqual(result.count('-o'), 1)

    def test_link_uses_only_new_objects_and_frozen_dependencies(self):
        line = ': && /usr/bin/clang++ -shared -Xlinker --dependency-file=old.d -o /old.so old.o /old.a -Wl,-rpath,/old && cp /old.so /live.so'
        result = b.link_command(line, {'/build/old.o': Path('/candidate/new.o')},
                                {'/old.a': Path('/candidate/deps/old.a')}, Path('/candidate/new.so'), Path('/build'))
        self.assertNotIn('cp', result)
        self.assertNotIn('/live.so', result)
        self.assertNotIn('--dependency-file=old.d', result)
        self.assertIn('/candidate/new.o', result)
        self.assertIn('/candidate/deps/old.a', result)
        self.assertIn('-Wl,-rpath,$ORIGIN', result)
        self.assertIn('-Wl,-z,defs', result)

    def test_link_rejects_old_or_missing_objects(self):
        for objects in ({}, {'/build/new.o': Path('/candidate/new.o')}):
            with self.assertRaises(RuntimeError):
                b.link_command('/usr/bin/clang++ -o /old.so old.o', objects, {}, Path('/candidate/new.so'), Path('/build'))
        with self.assertRaises(RuntimeError):
            b.link_command('/usr/bin/clang++ -o /old.so /old.a', {}, {}, Path('/candidate/new.so'))

    def test_shared_library_output_is_not_a_frozen_dependency(self):
        line = ': && /usr/bin/clang++ -shared -o /sdk/librexgpu-xenos.so fresh.o /sdk/librexruntime.so libdep.a -Xlinker --dependency-file=old.d && cp /sdk/librexgpu-xenos.so /live.so'
        self.assertEqual(b.link_dependencies(line, Path('/build')),
                         [Path('/sdk/librexruntime.so'), Path('/build/libdep.a')])

    def test_probe_selection_excludes_dependency_targets(self):
        name = 'asahi_sdk_probe'
        compile_line = f'/usr/bin/clang++ -o CMakeFiles/{name}.dir/main.o -c /main.cpp'
        link_line = f': && /usr/bin/clang++ CMakeFiles/{name}.dir/main.o -o {b.SDK}/out/linux-arm64/Release/{name} && :'
        lines = ['/usr/bin/clang++ -o dependency.o -c /dependency.cpp', compile_line, link_line]
        self.assertEqual(b.probe_commands(lines, name), ([compile_line], link_line))
        with self.assertRaises(RuntimeError):
            b.probe_commands(lines + [compile_line], name)

    def test_plan_audit_and_json_roundtrip(self):
        candidate = b.OUT / 'test'
        output = str(candidate / 'objects/new.o')
        plan = {'candidate': str(candidate), 'units': [{'output': output, 'command': ['/usr/bin/clang++', '-c', '/source.cpp', '-o', output]}]}
        b.audit_plan(json.loads(json.dumps(plan)))
        for extra in ('-MD', '&&', '/original/old.o', '-Wl,-rpath,/live'):
            bad = json.loads(json.dumps(plan))
            bad['units'][0]['command'].append(extra)
            with self.assertRaises(RuntimeError):
                b.audit_plan(bad)

    def test_resume_requires_review_and_preserves_failed_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary)
            output = candidate / 'new.o'
            output.write_bytes(b'partial')
            plan = {'candidate': str(candidate), 'token': 'test',
                    'units': [{'name': 'compile', 'output': str(output), 'command': []}]}
            state = {'status': 'failed', 'units': {'compile': {'status': 'started'}}}
            b.write(candidate / 'build.json', state)
            with patch.object(b, 'run_step') as run:
                with self.assertRaises(RuntimeError):
                    b.build_candidate(candidate / 'plan.json', plan, False)
                run.assert_not_called()
            def finish(*args):
                output.write_bytes(b'complete')
                return b.LAB / 'logs/test'
            with patch.object(b, 'run_step', side_effect=finish):
                b.build_candidate(candidate / 'plan.json', plan, True)
            self.assertEqual(next(candidate.glob('new.o.failed-*')).read_bytes(), b'partial')
            self.assertEqual(json.loads((candidate / 'build.json').read_text())['status'], 'linked')
            output.write_bytes(b'changed')
            with patch.object(b, 'run_step') as run:
                with self.assertRaises(RuntimeError):
                    b.build_candidate(candidate / 'plan.json', plan, True)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
