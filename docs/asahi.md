# Fedora Asahi ARM64: source build and verification

This is an independent experimental port of ReImagined, not an upstream Asahi
release. Work is on `asahi`; `main` remains at the upstream baseline for later
synchronization. The proposed first reference is `asahi-experimental-1`, pending
publication authorization. See [the preservation audit](asahi-preservation.md).

## Validated scope

- Fedora Asahi Remix 44, native aarch64, actual 16384-byte host pages.
- Apple M2 Pro (G14S B1), Honeykrisp, Mesa 26.2.5; Vulkan 1.4.354 device.
- Clang 22.1.8, CMake 4.3, Ninja 1.13.2, Python 3.14.
- Base game (`FORZA_TITLE_UPDATE=0`), native baseline ARM64 build.
- SDK host compilation, physical aliases, offline synthetic SPIR-V and nine GPU
  integration cases. Game generation, both facade modules and initial 3D rendering.

The GPU supervisor deliberately checks the validated Apple M2 Pro device. Results
do not establish other Apple chips, Title Update 4, DLC, full races or launcher
packaging compatibility. The prior driving measurement was about 21 FPS at 720p
scale 1; no new performance changes are included in this preservation phase.

## Source and private data

The sources retain MIT/BSD and third-party attributions. Commercial game files,
translated game C++, built game executables, caches, profiles, captures and raw
laboratory logs stay local. Upstream's historical images remain in the traditional
fork; the Asahi commits add no game captures or extracted resources.

All scripts derive the project root from their own location. Their disposable
build products go under ignored `forza-recomp/out/asahi-sdk-lab/`. They do not
require the original developer's checkout, a prebuilt nightly overlay or temporary
session scripts.

## Prerequisites

Use native Linux ARM64, a C++23-capable Clang toolchain and the normal Fedora
development headers for SDL/Vulkan: X11/XCB, Wayland, xkbcommon, libudev, libusb
and the audio backends enabled by SDL. Git, Python 3, CMake, Ninja and pkg-config
are needed. `dnf download`, `rpm2cpio` and `cpio` prepare local validation tools;
`grim` and `eu-stack` provide optional captures/stacks. The CMake configure log
records which system dependencies it found. Package installation is a separate
administrator action; these scripts do not install global graphics components.

Keep sufficient free RAM for a three-job build. The game probe's 400 MiB emergency
floor is a monitored low-memory exception, not a substitute for the original
10 GiB free-memory recommendation. The helper ends a run at at most 180 seconds
with SIGINT, followed by a supervisor limit. Inspect any `STOP-GPU.json` and its
kernel log before a subsequent GPU run. A stopped directory remains stopped.

## 1. Obtain the source

After the `asahi` branch is authorized and published:

```sh
git clone --branch asahi https://github.com/kurai021/ForzaHorizon1-ReImagined-Asahi.git
```

During the local audit, a separate clone can instead be created from your local
repository using `git clone --no-hardlinks --branch asahi SOURCE DESTINATION`.
Run the following commands from the new clone's root.

## 2. Prepare and build the SDK

```sh
python3 -B tools/asahi-sdk-lab/build.py sdk --jobs 3
```

This obtains the pinned open-source SDK and its submodules, applies the inherited
116 SDK patches, three Asahi patches and one SDL patch, then configures, builds
and installs into the clone's private prefix. No game files are needed.

`tools/asahi-sdk-lab/sdk.lock.json` records source revisions and SHA-256 for every
patch. Commit ancestry and version tags are retained: the expected SDK version
description is `v0.10.0-24-gbd833a2`, not a shallow-clone `dev.unknown` fallback.

An existing SDK directory is **verified, not reset or overwritten**. An unexpected
file or modification causes preparation to fail. Use a separate new destination
to recover a failed preparation while preserving the old directory for inspection.

```sh
python3 -B tools/asahi-sdk-lab/sdk.py verify
python3 -B tools/asahi-sdk-lab/validation_tools.py --download
```

Validation RPMs are fetched from Fedora repositories, checked against the lock and
extracted into `local-tools`, without global installation. An independently
downloaded matching cache may be supplied through `--rpm-dir`.

## 3. Run regressions without game material

```sh
python3 -B -m unittest discover -s tools/asahi-sdk-lab -p 'test_*.py' -v
python3 -B tools/asahi-sdk-lab/regress.py --jobs 3 --require-16k
```

The second command verifies all source trees, compiles the host integration,
tests bidirectional physical aliases, translates/validates synthetic shaders and
requires nine GPU PASS cases with zero Vulkan validation messages. `--cpu-only`
omits GPU submission; `--require-16k` checks the real host page size, not an
emulation of 16 KiB on a 4 KiB machine.

## 4. Supply your existing local game extraction

Point `FORZA_GAME_ROOT` at your own complete extraction, then link it into this
clone. The destination must be absent; this does not replace an existing setup.

```sh
export FORZA_GAME_ROOT="/ruta/a/tu/extraccion"
mkdir -p extracted
ln -s "$FORZA_GAME_ROOT" extracted/game
python3 -B tools/asahi-sdk-lab/build.py check-game
python3 -B tools/asahi-sdk-lab/build.py game --jobs 3
python3 -B tools/asahi-sdk-lab/verify_artifacts.py
```

For an owned local ISO, `tools/asahi-sdk-lab/extract_iso.py` is an alternative
local extractor. It validates the executables and refuses an existing destination.
No commercial files are downloaded.

The build generates translated code in ignored `forza-recomp/generated/`, compiles
the game and both facade modules, and stages the matching runtime/plugin into
`forza-recomp/out/asahi-sdk-lab/game-base/`. Both libraries must come from the
same new SDK prefix. Preserve these private results separately from source exports.

## 5. Optional bounded game smoke test

Only after reviewing system stability and any suspension marker:

```sh
python3 -B tools/asahi-sdk-lab/play_probe.py --name game-smoke --seconds 90 \
  --autopress 3:A,7:START,11:A,14:A,18:A,22:A
```

This records GPU identity, validation, the guest log, memory floor, optional
screenshots and frame costs locally. The launcher is inherited application code;
the supported Asahi entry point for this source audit is the command-line flow
above, not the upstream x86-64 installer. Launcher update checks and downloads
are disabled; repository links identify this fork.

## Publication and synchronization

```sh
python3 -B tools/asahi-sdk-lab/audit_publication.py
```

This audits the fork's committed delta against upstream commit `43b64ae`, checks
private/generated paths, retained licenses and active workflow publication
permissions. `--index` checks staged changes. `--archive FILE.tar` also verifies
a local source archive byte for byte against the committed source tree.

The release, Pages, Discord announcement and scheduled server-probe workflows
are retained under `.github/workflows-disabled/`. The new Asahi source audit is
manual and read-only. These source changes apply to `asahi`; upstream-compatible
`main` still has upstream's files. Changing the remote default branch or remote
Actions/Pages settings requires a separate explicit authorization.

To incorporate upstream work later, fetch `upstream` and review changes before
fast-forwarding `main`; merge the reviewed baseline into `asahi`. Re-pin SDK and
patch hashes deliberately and rerun source verification/regressions. The original
SDK preparer rewrites its selected files: keep the isolated Asahi preparation
flow for this branch.
