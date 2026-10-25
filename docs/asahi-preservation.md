# Asahi preservation and local publication audit

Date: 2026-10-25. Destination:
`https://github.com/kurai021/ForzaHorizon1-ReImagined-Asahi`.
No remote push, release, tag publication or repository-setting change has been
authorized in this phase.

## Preserved baseline

- Project base: `43b64aeb63fb8553a906fca764e06614b8a6fe43`.
- SDK: `bd833a2ab69d305f99ddf57418fc38ffec8af93d`.
- Full pre-edit copy: 38,487 entries, 17,823,477,199 regular-file bytes, zero
  SHA-256/mode/symlink differences. The private parent directory has mode 0700.
- Git inventory: project, SDK and 23 recursive submodules (25 worktrees), with
  HEADs, refs, local configuration, staged/unstaged diffs and untracked-file status.
- A verified Git bundle preserves all 53 original refs and complete project history.
- The copy includes uncommitted/new port sources, the entire functional laboratory,
  built game/modules/libraries, generated code, profiles, caches and raw logs.
  Those private materials are retained locally, not copied into source publication.

The backup is a separate local Btrfs reflink copy; it protects against subsequent
file edits but is not an off-machine disaster-recovery copy. Its private manifests
and configuration inventory are intentionally outside this Git repository.

## Complete source reconstruction

`sdk.py verify` compares every expected tracked source and unexpected untracked
source in the SDK and all 23 submodules, not just a selected-file export.

| Tree | Reproduction fingerprint |
| --- | --- |
| SDK + 116 inherited patches + 3 Asahi patches | `2eac4ff0202b849e8a85aa87d4b20f04b5487cfa` |
| SDL + inherited haptics patch | `287364b08357c6efc9bee08cc03059c18f509350` |

The SDK tree has 1,384 tracked entries after patching. Remaining submodules match
their pinned revisions without unrecorded edits. Patch checksums, source commits
and submodule revisions are in `tools/asahi-sdk-lab/sdk.lock.json`. The verifier
also checks the version description used by CMake; commit ancestry and tags must
remain available. This closes a shallow-fetch version-metadata gap found during
the first clean reconstruction.

## Inventory of fork changes

| Area | Preserved or prepared work |
| --- | --- |
| ARM64 integration | Linux ARM64 SDK selection in `forza-recomp/CMakeLists.txt`; corrected physical-memory documentation |
| SDK changes | Three ordered, byte-exact patches; inherited 116 SDK patches and SDL patch retained |
| Synthetic laboratory | Backend/raster/memexport fixtures, offline SPIR-V validation, physical-alias regression and host compile target |
| Reproduction | Pinned source/patch/submodule lock; prepare/verify/build commands; locally extracted validation RPM lock |
| Runtime evidence | Bounded supervisor, memory monitoring, guest-log collection and CSV analysis; private outputs ignored |
| Verification | Tests for omitted files, altered checksums, version metadata, generated-code leakage, archive contamination and license/workflow retention |
| Fork policy | Asahi guide, local publication auditor, inherited publishing automation archived; updater bound to the fork and disabled pending release audit |

The diagnostic HSI bypass and temporary WAIT_REG_MEM logs were removed before
preservation. `critical_section.cpp` matches upstream. The patch, not a bypass,
fixes the physical alias discrepancy. No FPS optimization is included here.

## Clean-clone validation

An independent local clone was made with `--no-hardlinks`, initially at `46b7069`.
It began without the original SDK checkout, compiled libraries, generated code,
game output, validation tools or laboratory caches. SDK sources/submodules and
matching validation RPMs were downloaded again. The only commercial input was
an explicit ignored link to the existing local extraction, supplied after the
non-commercial SDK regressions.

| Check | Evidence / outcome |
| --- | --- |
| Fresh SDK preparation | `20261025T055628.926218Z-asahi-sdk-prepare`, 102.7 s |
| SDK configure/install | 38.0 s / 99.4 s, success |
| Full source comparison | Exact SDK and all submodule fingerprints match the preserved baseline |
| ARM64 host integration | `20261025T060113.732946Z-asahi-host-build`, success |
| Actual 16 KiB physical aliases | `20261025T060143.316831Z-regression-physical-alias`, PASS |
| Offline synthetic SPIR-V | Translation and `spirv-val`, PASS |
| Synthetic GPU integration | `20261025T060143.651009Z-regression-gpu-integration`, 9/9 PASS; validation 0 errors / 0 warnings |
| Game code generation/configuration | `20261025T060222.670737Z-asahi-game-configure`, success, 170.2 s |
| Game and facade builds | `20261025T060512.932705Z-asahi-game-build`, success, 449.6 s; second pass success |
| Bounded game smoke | `20261025T061303.843525Z-clean-clone-game-smoke`, first 3D scene visible; normal exit 0 at 90.5 s |

After retaining the SDK version ancestry, SDK installation and CPU/16 KiB/offline
regressions passed again (`20261025T062942.*`). The game relink/staging passed at
`20261025T063003.*`. A second empty SDK destination, prepared by the final script
without manual repair, reproduced all trees and the expected version description:
`20261025T063038.084894Z-clean-final-sdk-preparation`. No subsequent GPU run was
made while the smoke test's suspension marker remained in place; the GPU results
above are from the identical patched source tree before version metadata repair.

Raw evidence and captures remain private under the clean clone's ignored `out/`.
These are reproduction checks, not additional performance benchmarks.

### Filesystem event during the smoke test

The game exited normally and produced its scene. This limited smoke
test does not establish broad gameplay or long-session stability;
further GPU tests require separate authorization.

## Publication boundary

- New commits contain redistributable source, patches, metadata and documentation.
- The only tracked generated-directory file is the inherited SDK CMake glue,
  `forza-recomp/generated/rexglue.cmake`.
- Game extraction, generated game C++, executables, assets, caches, profiles,
  captured images and raw logs are excluded from new commits/source archives.
- Upstream historical imagery is retained, as approved for the traditional fork.
- Original MIT, Pinyon Shift BSD and bundled third-party attributions are preserved.
- `release.yml`, `pages.yml`, `discord-release.yml` and the scheduled server probe
  are outside the active workflow directory on `asahi`.
- Launcher update checks/downloads are disabled even for an old config requesting
  background checks; release URLs and issue links identify this fork.
- `main` remains upstream-compatible. Remote branch defaults, Actions settings,
  Pages configuration and publishing permissions have not been modified.

## Pending before any remote publication

1. Present/review the final local commits, source inventory and archive audit.
2. Authorize any further GPU smoke tests separately.
3. Validate launcher UI behavior with PySide6: the core policy tests pass locally,
   but this environment does not currently provide PySide6 for Qt integration tests.
4. Obtain explicit authorization for a push and for any remote default-branch or
   Actions/Pages setting change. The proposed reference is `asahi-experimental-1`;
   no release publication is part of this local phase.
5. Plan FPS improvements and full-race/long-run testing separately; the existing
   approximate 21 FPS result does not meet sustained 720p/30.
