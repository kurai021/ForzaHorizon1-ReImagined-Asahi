# Asahi source preservation and publication boundary

The fork retains the working ARM64 port and reproducible SDK preparation without
publishing commercial inputs or local build products.

## Source baseline

- Upstream project base: `43b64aeb63fb8553a906fca764e06614b8a6fe43`.
- SDK revision: `bd833a2ab69d305f99ddf57418fc38ffec8af93d`.
- SDK tree after inherited patches and the three Asahi patches:
  `2eac4ff0202b849e8a85aa87d4b20f04b5487cfa`.
- SDL tree after its inherited haptics patch:
  `287364b08357c6efc9bee08cc03059c18f509350`.

The lock records source revisions, patch checksums, submodules and the SDK version
description used by CMake. `sdk.py verify` checks the complete tracked source
set, unexpected source files, submodule revisions and version ancestry.

## Maintained scope

- Linux ARM64 host/game integration and staging of matching runtime libraries.
- Compute memexport fallback and mandatory bidirectional 16 KiB physical aliases.
- Offline SPIR-V, alias, host integration and explicit synthetic GPU regressions.
- Bounded game/profiling tools with independent profiles and checked dependencies.
- Source/publication auditing and preservation of inherited licenses.

Independent source reconstruction previously reproduced the locked SDK/submodule
trees, built the game/facades, passed physical-alias and offline checks, and passed
nine synthetic integration cases without validation errors or warnings. This is
reproduction evidence, not sustained-30-FPS or broad gameplay validation.

The original functional SDK, installation, generated code and game extraction stay
local under the workspace's ignored paths. Source cleanup never installs an
experimental plugin or rewrites commercial/generated files.

## Publication rules

- Publish redistributable source, patches, locks and project-specific aggregate
  results only.
- Never commit extraction data, translated game C++, executable outputs, caches,
  profiles, raw traces, local configuration or source-history backup bundles.
- The inherited `forza-recomp/generated/rexglue.cmake` is source glue, not generated
  game code; it is the only tracked file in that generated directory.
- Preserve MIT, Pinyon Shift BSD and third-party attributions and inherited images.
- Keep publishing/announcement workflows quarantined and updater publication
  disabled until separately reviewed.
- Keep unrelated-system investigations and machine inventories out of source
  reports and source history.

`audit_publication.py --index` checks the staged source tree against the upstream
base, including path/content boundaries, credentials and workflow permissions.
`--archive` additionally verifies a source archive against the audited revision.
No command here authorizes a remote push, release or repository-setting change.

See [the build guide](asahi.md), [maintained tool reference](../tools/asahi-sdk-lab/README.md)
and [closed performance evaluation](asahi-readback-history-final-review.md).
