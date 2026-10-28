#!/usr/bin/env python3
"""Compose one optional cache-identity patch with the existing submission overlay, CPU only."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import sdk as pinned
from prepare_submission_profile import FILES as SUBMISSION_FILES, SDK

HERE = Path(__file__).resolve().parent
OUT = pinned.ROOT / 'forza-recomp/out/asahi-profiling/render-pass-reuse-overlays'
TARGETS = ('include/rex/graphics/vulkan/render_target_cache.h',
           'src/graphics/vulkan/render_target_cache.cpp')
HELPER = 'include/rex/graphics/vulkan/asahi_render_pass_reuse.h'


def compose(submission):
    original = json.loads((submission / 'manifest.json').read_text())
    if any(pinned.sha256(submission / name) != digest for name, digest in original['overlayFiles'].items()):
        raise RuntimeError('Submission overlay changed')
    base = {name: pinned.sha256(SDK / name) for name in TARGETS}
    inputs = {'submissionOverlay': original, 'baseFiles': base,
              'patchSha256': pinned.sha256(HERE / 'render-pass-reuse-sdk.patch'),
              'helperSha256': pinned.sha256(HERE / 'asahi_render_pass_reuse.h'),
              'preparerSha256': pinned.sha256(Path(__file__))}
    token = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:16]
    overlay = OUT / token
    manifest_path = overlay / 'manifest.json'
    if overlay.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest['inputs'] != inputs or any(pinned.sha256(overlay / name) != digest
                                              for name, digest in manifest['overlayFiles'].items()):
            raise RuntimeError('Existing render-pass overlay changed; preserve and inspect it')
    else:
        overlay.mkdir(parents=True)
        for name in (*SUBMISSION_FILES, *TARGETS):
            destination = overlay / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2((submission if name in SUBMISSION_FILES else SDK) / name, destination)
        shutil.copy2(HERE / 'asahi_render_pass_reuse.h', overlay / HELPER)
        command = ['patch', '--batch', '--forward', '--fuzz=0', '-p1', '-d', str(overlay),
                   '-i', str(HERE / 'render-pass-reuse-sdk.patch')]
        for phase, invocation in (('dry-run', command + ['--dry-run']), ('apply', command)):
            checked = subprocess.run(invocation, capture_output=True, text=True, timeout=10)
            (overlay / (phase + '.log')).write_text(checked.stdout + checked.stderr)
            if checked.returncode:
                raise RuntimeError(f'Optional overlay {phase} failed; inspect {overlay}')
        manifest = {'inputs': inputs, 'overlayFiles': {name: pinned.sha256(overlay / name)
                    for name in (*SUBMISSION_FILES, *TARGETS, HELPER)}, 'baselineSdk': original['baselineSdk'],
                    'experiment': 'single-rgba8-1x-cache-identity', 'enabledByDefault': False,
                    'gpuWork': False, 'liveSdkModified': False, 'linkedPluginBuilt': False}
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    if any(pinned.sha256(SDK / name) != digest for name, digest in base.items()):
        raise RuntimeError('Original SDK files changed during overlay preparation')
    return overlay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    # The existing preparer verifies the pinned SDK and all baseline patches.
    prepared = subprocess.check_output([sys.executable, '-B', str(HERE / 'prepare_submission_profile.py')],
                                       text=True, timeout=60)
    overlay = compose(pinned.ROOT / json.loads(prepared)['overlay'])
    print(json.dumps({'overlay': str(overlay.relative_to(pinned.ROOT)), 'gpuWork': False,
                      'liveSdkModified': False, 'linkedPluginBuilt': False}, indent=2))


if __name__ == '__main__':
    main()
