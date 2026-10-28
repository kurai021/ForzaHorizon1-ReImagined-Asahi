#!/usr/bin/env python3
"""CPU-only extracted-helper proof; never rebuild or modify the game/live SDK."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess

import sdk as pinned
from resource_guard import require_memory

HERE = Path(__file__).resolve().parent
SDK = pinned.ROOT / 'forza-recomp/out/asahi-sdk-lab/sdk'
HEADER = 'include/rex/system/xmemory.h'
OUT = pinned.ROOT / 'forza-recomp/out/asahi-profiling/physical-offset-probes'
ORIGINAL_LINUX = '''  if (guest_addr < 0xE0000000u) return 0u;
  static const u32 offset = allocation_granularity() > 0x1000 ? 0x1000u : 0u;
  return offset;'''
SPLIT_GETTER = '''#if REX_PLATFORM_LINUX
[[gnu::noinline]] inline u32 PhysicalHostOffsetCached() noexcept {
  static const u32 offset = allocation_granularity() > 0x1000 ? 0x1000u : 0u;
  return offset;
}
#endif
'''


def methods(text):
    marker = 'inline u32 PhysicalHostOffset([[maybe_unused]] u32 guest_addr) noexcept {'
    if text.count(marker) != 1:
        raise ValueError('Expected exactly one pinned PhysicalHostOffset definition')
    start = text.index(marker)
    end = text.index('\n}', start) + 2
    original = text[start:end]
    if original.count(ORIGINAL_LINUX) != 1:
        raise ValueError('Pinned Linux helper changed; do not silently adapt the proof')
    split = SPLIT_GETTER + original.replace(ORIGINAL_LINUX, '''  if (guest_addr < 0xE0000000u) return 0u;
  return PhysicalHostOffsetCached();''')
    return original, split


def harness_header(original, split):
    parts = ['#pragma once\n#include <atomic>\n#include <cstdint>\nusing u32 = uint32_t;\n',
             'inline u32 configured_granularity = 0;\n']
    for name, method in (('baseline', original), ('split', split)):
        parts += [f'namespace {name} {{\n', '''inline std::atomic<unsigned> queries{0};
[[gnu::noinline]] inline u32 allocation_granularity() noexcept {
  queries.fetch_add(1, std::memory_order_relaxed);
  return configured_granularity;
}
''', method, '\n}\n']
    return ''.join(parts)


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run(command, directory, name, seconds):
    result = subprocess.run(list(map(str, command)), capture_output=True, text=True,
                            timeout=seconds, preexec_fn=limits,
                            env=os.environ | {'TMPDIR': str(directory), 'VK_DRIVER_FILES': '/dev/null'})
    (directory / (name + '.log')).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f'CPU probe failed ({name}); inspect {directory}')
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark', action='store_true', help='Also run bounded single-worker synthetic timings')
    args = parser.parse_args()
    require_memory()
    if os.environ.get('LD_PRELOAD'):
        parser.error('Do not mix the CPU helper probe with an ambient interposer')
    pinned.verify(SDK, pinned.load_lock())
    header = SDK / HEADER
    original_sha = pinned.sha256(header)
    original, split = methods(header.read_text())
    compiler = Path('/usr/bin/clang++')
    version = subprocess.check_output([compiler, '--version'], text=True, timeout=5)
    inputs = {'sdkHeaderSha256': original_sha, 'fixtureSha256': pinned.sha256(HERE / 'physical_offset_fixture.cpp'),
              'preparerSha256': pinned.sha256(Path(__file__)), 'compilerVersion': version,
              'compilerSha256': pinned.sha256(compiler)}
    token = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:16]
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    directory = OUT / token / stamp
    directory.mkdir(parents=True, exist_ok=False)
    shutil.copy2(header, directory / 'xmemory-original.h')
    shutil.copy2(HERE / 'physical_offset_fixture.cpp', directory / 'fixture.cpp')
    shutil.copy2(Path(__file__), directory / 'preparer.py')
    (directory / 'physical_offset_methods.h').write_text(harness_header(original, split))
    (directory / 'other.cpp').write_text('''#include "physical_offset_methods.h"
extern "C" u32 baseline_other(u32 a) { return baseline::PhysicalHostOffset(a); }
extern "C" u32 split_other(u32 a) { return split::PhysicalHostOffset(a); }
''')
    # Match the relevant preserved Release settings, without PCH, Vulkan or runtime linkage.
    flags = [str(compiler), '-std=c++23', '-O3', '-DNDEBUG', '-march=armv8-a',
             '-fPIC', '-fvisibility=hidden', '-fvisibility-inlines-hidden',
             '-mcmodel=small', '-fno-strict-aliasing', '-ffp-model=strict',
             '-DREX_PLATFORM_LINUX=1', '-DREX_PLATFORM_WIN32=0', '-DREX_PLATFORM_MAC=0',
             '-I' + str(directory), '-pthread']
    binary = directory / 'fixture'
    command = flags + [str(directory / 'fixture.cpp'), str(directory / 'other.cpp'), '-o', str(binary)]
    (directory / 'compile-command.json').write_text(json.dumps(command, indent=2))
    run(command, directory, 'compile', 30)
    correctness = {}
    for page in (4096, 16384, 65536):
        correctness[str(page)] = json.loads(run([binary, 'check', page], directory, 'check-' + str(page), 5))
    assembly = flags + ['-S', str(directory / 'fixture.cpp'), '-o', str(directory / 'fixture.s')]
    run(assembly, directory, 'assembly', 30)
    benchmark = None
    if args.benchmark:
        benchmark = json.loads(run([binary, 'bench', 16384], directory, 'benchmark', 30))
    if pinned.sha256(header) != original_sha:
        raise RuntimeError('Live SDK helper changed during CPU proof')
    report = {'inputs': inputs, 'correctness': correctness, 'benchmark': benchmark,
              'generatedHeaderSha256': pinned.sha256(directory / 'physical_offset_methods.h'),
              'binarySha256': pinned.sha256(binary), 'assemblySha256': pinned.sha256(directory / 'fixture.s'),
              'sdkModified': False, 'gameOrRuntimeRebuilt': False, 'gpuWork': False,
              'renderPassReuseIncluded': False,
              'limits': ['Extracted methods with a controlled granularity provider, not actual guest mapping or a game benchmark.',
                         'Release-like small harness; does not reproduce generated-game register pressure, PCH or all TU contexts.',
                         'Single timing invocation, fixed dataset and alternating order; frequency/scheduler effects are not isolated.',
                         'Real 16 KiB physical aliases and coherent cross-component rebuild are later gates.']}
    (directory / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'directory': str(directory.relative_to(pinned.ROOT)), 'correctnessPassed': True,
                      'benchmarked': args.benchmark, 'gpuWork': False, 'sdkModified': False}, indent=2))


if __name__ == '__main__':
    main()
