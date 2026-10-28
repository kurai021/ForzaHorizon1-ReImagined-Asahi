#!/usr/bin/env python3
"""CPU-only proofs for two independent readback housekeeping candidates; no SDK edit."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess

import sdk as pinned
from prepare_submission_profile import SDK
from resource_guard import require_memory
from analyse_readback_profile import load

HERE = Path(__file__).resolve().parent
HISTORY = 'src/graphics/vulkan/fh1_readback_history.h'
UTIL = 'include/rex/ui/vulkan/util.h'
OUT = pinned.ROOT / 'forza-recomp/out/asahi-profiling/readback-options'


def once(text, old, new):
    if text.count(old) != 1: raise ValueError('Pinned option source changed: ' + old[:70])
    return text.replace(old, new)


def history_variant(text):
    text = once(text, '    Entry& e = it->second;', '    Entry& e = it->second;\n    bool changed = inserted;')
    text = once(text, '    } else if (frame != e.last_frame) {',
                 '    } else if (frame != e.last_frame) {\n      changed = true;')
    text = once(text, '    if (e.run > e.longest_run) e.longest_run = e.run;',
                 '    if (e.run > e.longest_run) { e.longest_run = e.run; changed = true; }\n'
                 '    if (changed) forget_clean_ = false;')
    text = once(text, '  void Forget(uint64_t frame) {',
                 '  void Forget(uint64_t frame) {\n    if (forget_clean_ && last_forget_frame_ == frame) return;')
    text = once(text, '      entries_.erase(oldest);\n    }',
                 '      entries_.erase(oldest);\n    }\n    last_forget_frame_ = frame;\n    forget_clean_ = true;')
    text = once(text, '  std::unordered_map<uint64_t, Entry> entries_;',
                 '  std::unordered_map<uint64_t, Entry> entries_;\n'
                 '  uint64_t last_forget_frame_ = 0;\n  bool forget_clean_ = false;')
    return text


def inspectable(text, namespace):
    # Test-only observation/counters, not part of the proposed renderer variant.
    text = text.replace('fh1_readback_history', namespace)
    text = once(text, ' private:', ' public:')
    text = once(text, '  std::unordered_map<uint64_t, Entry> entries_;',
                 '  uint64_t inspection_visits = 0;\n  std::unordered_map<uint64_t, Entry> entries_;')
    text = once(text, '    for (auto it = entries_.begin(); it != entries_.end();) {',
                 '    for (auto it = entries_.begin(); it != entries_.end();) {\n      ++inspection_visits;')
    text = once(text, '      for (auto it = entries_.begin(); it != entries_.end(); ++it) {',
                 '      for (auto it = entries_.begin(); it != entries_.end(); ++it) {\n        ++inspection_visits;')
    return text


def method(text, name):
    start = text.index('inline uint32_t ' + name + '(')
    return text[start:text.index('\n}', start) + 2]


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main():
    require_memory(); pinned.verify(SDK, pinned.load_lock())
    inputs = {'history': pinned.sha256(SDK / HISTORY), 'util': pinned.sha256(SDK / UTIL),
              'preparer': pinned.sha256(Path(__file__)), 'fixture': pinned.sha256(HERE / 'readback_options_fixture.cpp')}
    history = (SDK / HISTORY).read_text(); util = (SDK / UTIL).read_text()
    variant = history_variant(history)
    token = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:16]
    directory = OUT / token / dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'baseline_history.h').write_text(inspectable(history, 'baseline_history'))
    (directory / 'memo_history.h').write_text(inspectable(variant, 'memo_history'))
    (directory / 'proposed_history.h').write_text(variant)
    (directory / 'memory_type_methods.inc').write_text('namespace rex::ui::vulkan::util {\n' +
        util[util.index('enum class MemoryPurpose {'):util.index('};', util.index('enum class MemoryPurpose {')) + 2] + '\n' +
        method(util, 'ChooseHostMemoryType') + '\n' + method(util, 'ChooseMemoryType') + '\n}\n')
    # Retained prefix only, replayed into synthetic empty histories; not a reconstruction of lost events.
    prefix = pinned.ROOT / 'forza-recomp/out/asahi-profiling/candidate-play/readback-20261028T213338Z/profile/readback.csv'
    replay = directory / 'retained-prefix.csv'
    metadata, rows = load(prefix)
    replay.write_text(''.join(f"{r['frame']},{r['key_id']}\n" for r in rows if r['kind'] == 0))
    shutil.copy2(HERE / 'readback_options_fixture.cpp', directory / 'fixture.cpp')
    shutil.copy2(Path(__file__), directory / 'preparer.py')
    command = ['clang++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror', '-I' + str(directory),
               str(directory / 'fixture.cpp'), '-o', str(directory / 'fixture')]
    (directory / 'compile-command.json').write_text(json.dumps(command, indent=2))
    for label, invocation in [('compile', command), ('proof', [str(directory / 'fixture'), str(replay)])]:
        result = subprocess.run(invocation, capture_output=True, text=True, timeout=30,
            preexec_fn=limits, env=os.environ | {'TMPDIR': str(directory)})
        (directory / (label + '.log')).write_text(result.stdout + result.stderr)
        if result.returncode: raise RuntimeError('CPU option proof failed: ' + str(directory))
    proof = json.loads((directory / 'proof.log').read_text())
    assert pinned.sha256(SDK / HISTORY) == inputs['history'] and pinned.sha256(SDK / UTIL) == inputs['util']
    record = {'inputs': inputs, 'proof': proof, 'retainedPrefixSourceSha256': pinned.sha256(prefix),
              'retainedPrefixIsWholeWindowQualified': False, 'sdkModified': False, 'gpuWork': False,
              'optimizationAppliedToGame': False,
              'limits': ['Exact history-state equivalence and synthetic visit counts, not game speedup.',
                         'Coherency option uses actual extracted SDK type selection with mock properties; selected live allocation type is unknown.',
                         'No GPU waits, copies, barriers, readback policy or allocation lifetimes may be removed by these options.']}
    (directory / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'directory': str(directory.relative_to(pinned.ROOT)), **record}, indent=2))


if __name__ == '__main__': main()
