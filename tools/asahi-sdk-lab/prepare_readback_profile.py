#!/usr/bin/env python3
"""Prepare/syntax-check resolve-only telemetry over the independent submission control."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import sdk as pinned
from prepare_submission_profile import SDK, FILES, limit_compiler, syntax_command

HERE = Path(__file__).resolve().parent
OUT = pinned.ROOT / 'forza-recomp/out/asahi-profiling/readback-overlays'
HELPER = 'include/rex/graphics/vulkan/asahi_readback_profile.h'


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Pinned readback source differs: ' + old[:90])
    return text.replace(old, new)


def transform(header, source):
    header = replace_once(header, 'namespace submission_profile { class Recorder; struct Job; }',
                          'namespace submission_profile { class Recorder; struct Job; }\nnamespace readback_profile { class Recorder; }')
    header = replace_once(header, '  std::unique_ptr<submission_profile::Recorder> submission_profile_;',
                          '  std::unique_ptr<submission_profile::Recorder> submission_profile_;\n'
                          '  std::unique_ptr<readback_profile::Recorder> readback_profile_;')
    source = replace_once(source, '#include "asahi_submission_profile.h"',
                          '#include "asahi_submission_profile.h"\n#include <rex/graphics/vulkan/asahi_readback_profile.h>')
    source = replace_once(source, 'REXCVAR_DEFINE_STRING(fh1_submission_profile_csv,', '''REXCVAR_DEFINE_STRING(fh1_readback_profile_csv, "", "GPU/Vulkan",
                     "Testing: bounded resolve readback reasons; empty disables telemetry")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
REXCVAR_DEFINE_DOUBLE(fh1_readback_profile_start, 80.0, "GPU/Vulkan", "Testing: readback capture start")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
REXCVAR_DEFINE_DOUBLE(fh1_readback_profile_seconds, 30.0, "GPU/Vulkan", "Testing: readback capture duration, 1..45 s")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);

REXCVAR_DEFINE_STRING(fh1_submission_profile_csv,''')
    source = replace_once(source, '  const std::string profile_path = REXCVAR_GET(fh1_submission_profile_csv);', '''  const std::string readback_profile_path = REXCVAR_GET(fh1_readback_profile_csv);
  if (!readback_profile_path.empty()) {
    readback_profile::Config config;
    config.start_seconds = REXCVAR_GET(fh1_readback_profile_start);
    config.duration_seconds = REXCVAR_GET(fh1_readback_profile_seconds);
    readback_profile_ = readback_profile::Recorder::Create(readback_profile_path.c_str(), config);
    if (!readback_profile_) REXGPU_WARN("Readback profile unavailable; use bounded settings and a new path");
  }
  const std::string profile_path = REXCVAR_GET(fh1_submission_profile_csv);''')
    source = replace_once(source, '    if (submission_profile_) submission_profile_->FinalizeIfReady();',
                          '    if (submission_profile_) submission_profile_->FinalizeIfReady();\n'
                          '    if (readback_profile_) readback_profile_->FinalizeIfReady();')
    begin = source.index('bool VulkanCommandProcessor::IssueCopy_ReadbackResolvePath() {')
    end = source.index('\nvoid VulkanCommandProcessor::EvictOldReadbackBuffers(', begin)
    method = source[begin:end]
    method = replace_once(method, '  auto ensure_readback_slot =',
                          '  readback_profile::Trace* readback_trace_ptr = nullptr;\n  auto ensure_readback_slot =')
    method = replace_once(method, '''    if (!ui::vulkan::util::CreateDedicatedAllocationBuffer(
            vulkan_device, size, VK_BUFFER_USAGE_TRANSFER_DST_BIT,
            ui::vulkan::util::MemoryPurpose::kReadback, new_buffer, new_memory)) {''', '''    if (!readback_trace_ptr->Allocate([&] { return ui::vulkan::util::CreateDedicatedAllocationBuffer(
            vulkan_device, size, VK_BUFFER_USAGE_TRANSFER_DST_BIT,
            ui::vulkan::util::MemoryPurpose::kReadback, new_buffer, new_memory); })) {
      readback_trace_ptr->Flag(readback_profile::kAllocationFailed);''')
    method = replace_once(method, '      REXGPU_ERROR("Failed to map a Vulkan resolve readback buffer");',
                          '      readback_trace_ptr->Flag(readback_profile::kMapFailed);\n'
                          '      REXGPU_ERROR("Failed to map a Vulkan resolve readback buffer");')
    method = replace_once(method, '    void* new_mapping = nullptr;',
                          '    if (auto* row = readback_trace_ptr->row()) row->allocated_bytes += size;\n'
                          '    void* new_mapping = nullptr;')
    # Two resource-replacement waits, distinct from full/miss waits. Preserve each call/return.
    if method.count('if (!AwaitAllQueueOperationsCompletion()) {') != 4:
        raise ValueError('Unexpected resolve wait sites')
    method = method.replace('if (!AwaitAllQueueOperationsCompletion()) {',
                            'if (!readback_trace_ptr->WaitFor(readback_profile::Wait::kResize, [&] { return AwaitAllQueueOperationsCompletion(); })) {', 2)
    method = replace_once(method, '''  ReadbackBuffer& readback = readback_buffers_[resolve_key];''', '''  const bool profile_present = readback_profile_ && readback_buffers_.find(resolve_key) != readback_buffers_.end();
  readback_profile::Trace readback_trace(readback_profile_.get(), resolve_key, profile_present, false,
                                        frame_current_, GetCurrentSubmission());
  readback_trace_ptr = &readback_trace;
  ReadbackBuffer& readback = readback_buffers_[resolve_key];
  if (auto* row = readback_trace.row()) {
    row->mode = readback_mode == ReadbackResolveMode::kFull ? 3 :
                readback_mode == ReadbackResolveMode::kSome ? 2 : 1;
    row->length = written_length; row->index = readback.current_index;
    row->size0 = readback.sizes[0]; row->size1 = readback.sizes[1];
    if (thumbnail_readback) row->flags |= readback_profile::kThumbnail;
    if (is_scaled) row->flags |= readback_profile::kScaled;
    if (readback.resolve_cpu_copy_valid) row->flags |= readback_profile::kCpuValid;
  }''')
    method = replace_once(method, '        REXCVAR_GET(vulkan_readback_skip_render_targets)) {\n      readback.resolve_cpu_copy_valid = true;\n',
                          '        REXCVAR_GET(vulkan_readback_skip_render_targets)) {\n'
                          '      readback_trace.Flag(readback_profile::kHistoryHot);\n'
                          '      readback.resolve_cpu_copy_valid = true;\n')
    method = replace_once(method, '''  if (readback_mode == ReadbackResolveMode::kSome && readback.resolve_cpu_copy_valid) {
    return true;''', '''  if (readback_mode == ReadbackResolveMode::kSome && readback.resolve_cpu_copy_valid) {
    readback_trace.Flag(readback_profile::kSkipValid);
    return true;''')
    method = replace_once(method, '''  } else {
    fh1_frame_cost::Timer cost_timer(cost.readback_wait_ms);
    if (!AwaitAllQueueOperationsCompletion()) {''', '''  } else {
    fh1_frame_cost::Timer cost_timer(cost.readback_wait_ms);
    if (!readback_trace.WaitFor(readback_profile::Wait::kFull, [&] { return AwaitAllQueueOperationsCompletion(); })) {''')
    method = replace_once(method, '''    is_cache_miss = true;
    read_index = write_index;''', '''    is_cache_miss = true;
    readback_trace.Flag(readback_profile::kSlotMiss);
    read_index = write_index;''')
    method = replace_once(method, 'if (!AwaitAllQueueOperationsCompletion()) {',
                          'if (!readback_trace.WaitFor(readback_profile::Wait::kMiss, [&] { return AwaitAllQueueOperationsCompletion(); })) {')
    method = replace_once(method, '      std::memcpy(destination, readback.mapped_data[read_index], written_length);',
                          '      readback_trace.Copy(written_length, [&] {\n'
                          '        std::memcpy(destination, readback.mapped_data[read_index], written_length);\n      });')
    source = source[:begin] + method + source[end:]
    evict_begin = source.index('void VulkanCommandProcessor::EvictOldReadbackBuffers(')
    evict_end = source.index('\nbool VulkanCommandProcessor::InitializeOcclusionQueryResources()', evict_begin)
    eviction = source[evict_begin:evict_end]
    eviction = replace_once(eviction, '''    for (uint32_t i = 0; i < 2; ++i) {
      if (readback.mapped_data[i] && readback.memories[i] != VK_NULL_HANDLE) {''', '''    readback_profile::Trace eviction_trace(
        &buffer_map == &readback_buffers_ ? readback_profile_.get() : nullptr,
        it->first, true, true, frame_current_, GetCurrentSubmission());
    if (auto* row = eviction_trace.row()) {
      row->size0 = readback.sizes[0]; row->size1 = readback.sizes[1];
      row->retained_bytes = uint64_t(readback.sizes[0]) + readback.sizes[1];
      if (readback.last_used_frame < eviction_frame_floor) row->flags |= readback_profile::kEvictAge;
      if (buffer_map.size() > kMaxReadbackBuffers) row->flags |= readback_profile::kEvictCount;
    }
    for (uint32_t i = 0; i < 2; ++i) {
      if (readback.mapped_data[i] && readback.memories[i] != VK_NULL_HANDLE) {''')
    source = source[:evict_begin] + eviction + source[evict_end:]
    return header, source


def compose(submission):
    manifest = json.loads((submission / 'manifest.json').read_text())
    if any(pinned.sha256(submission / name) != digest for name, digest in manifest['overlayFiles'].items()):
        raise RuntimeError('Submission control changed')
    inputs = {'submissionOverlay': manifest, 'preparerSha256': pinned.sha256(Path(__file__)),
              'helperSha256': pinned.sha256(HERE / 'asahi_readback_profile.h')}
    token = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:16]
    overlay = OUT / token
    if overlay.exists():
        existing = json.loads((overlay / 'manifest.json').read_text())
        if existing['inputs'] != inputs or any(pinned.sha256(overlay / name) != value
                                               for name, value in existing['overlayFiles'].items()):
            raise RuntimeError('Existing readback overlay changed; preserve it')
        return overlay
    texts = transform(*[(submission / name).read_text() for name in FILES])
    overlay.mkdir(parents=True, exist_ok=False)
    for name, text in zip(FILES, texts):
        (overlay / name).parent.mkdir(parents=True, exist_ok=True)
        (overlay / name).write_text(text)
    shutil.copy2(HERE / 'asahi_readback_profile.h', overlay / HELPER)
    result = {'inputs': inputs, 'overlayFiles': {name: pinned.sha256(overlay / name) for name in (*FILES, HELPER)},
              'baselineSdk': manifest['baselineSdk'], 'experiment': 'resolve-readback-reasons',
              'enabledByDefault': False, 'gpuWork': False, 'liveSdkModified': False, 'linkedPluginBuilt': False}
    (overlay / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return overlay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    original = subprocess.check_output([sys.executable, '-B', str(HERE / 'prepare_submission_profile.py')],
                                       text=True, timeout=60)
    submission = pinned.ROOT / json.loads(original)['overlay']
    overlay = compose(submission)
    if args.check:
        # Use the same real Release recipe as the submission preparer, never binary PCH/old objects.
        import shlex
        build = SDK / 'out/build/linux-arm64'
        listing = subprocess.check_output(['ninja', '-f', 'build-Release.ninja', '-t', 'commands',
            'src/graphics/CMakeFiles/rexgpu-xenos.dir/Release/vulkan/command_processor.cpp.o'], cwd=build, text=True, timeout=10)
        lines = [line for line in listing.splitlines() if shlex.split(line)[-1] == str(SDK / FILES[1])]
        if len(lines) != 1: raise RuntimeError('Unexpected pinned compile recipe')
        command = syntax_command(lines[0], overlay)
        directory = overlay / 'checks' / dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        directory.mkdir(parents=True)
        (directory / 'command.json').write_text(json.dumps(command, indent=2))
        result = subprocess.run(command, cwd=build, capture_output=True, text=True, timeout=90,
                                preexec_fn=limit_compiler, env=os.environ | {'TMPDIR': str(directory)})
        (directory / 'compiler.log').write_text(result.stdout + result.stderr)
        if result.returncode: raise RuntimeError('Readback syntax check failed: ' + str(directory))
    print(json.dumps({'overlay': str(overlay.relative_to(pinned.ROOT)), 'syntaxChecked': args.check,
                      'liveSdkModified': False, 'linkedPluginBuilt': False, 'gpuWork': False}, indent=2))


if __name__ == '__main__':
    main()
