# Readback-history optimization: final result

**Closed without promotion.** The proposed mutation-aware History cleanup
optimization did not demonstrate a consistent practical benefit. The functional
SDK/game remains unchanged; sustained native-720p/30 FPS is not established.

## Protocol

One fixed-order off/on pair used the same isolated binary, independently copied
save/cache seeds, physical 1280×720, scale 1, MSAA Off, target 30 FPS, ordinary
synchronization and identical scripted stationary input. Each run lasted 180 s,
followed by 30 s observation, with more than 45 s cooldown between runs.
Both runs completed with verified physical extent and candidate library mappings.
The primary comparison uses complete CSV rows ending in t=80–170 s.

| Metric | Off | On |
| --- | ---: | ---: |
| Frames | 2473 | 2465 |
| FPS | **27.475** | **27.382** |
| Mean frame ms | 36.397 | 36.521 |
| P95 frame ms | 50.373 | 50.406 |
| P99 frame ms | 50.554 | 50.577 |
| Frames >50 ms | 158 | 154 |
| Frames >66.667 ms | **7** | **9** |
| Frames >100 ms | 4 | 5 |
| Maximum frame ms | 117.137 | 116.852 |
| Mean command-processor CPU ms | 8.833 | 8.848 |
| Mean draws/frame | 2182.16 | 2136.99 |
| CPU readback copies | 9 | 10 |

The −0.34% FPS difference is not proof of a causal regression. CPU means and
percentiles are essentially similar; the slow-frame tail is mixed. The secondary
t=120–160 s window is also workload-dependent: nine readbacks off versus zero on,
so its better on-mode extreme tail cannot be credited to cleanup memoization.

## Scope and decision

CPU and synthetic GPU checks preserved tested History state, copies, known pixels
and neighboring memory. Synthetic iteration savings did not become an observable
gameplay benefit in this pair. Ordinary gameplay did not export cleanup frequency
or entry-visit telemetry, and identical initial seeds do not establish identical
traffic or rendered work.

Both modes used the same diagnostic binary; this is not an overhead comparison
against an uninstrumented build. One final on-mode CSV row was truncated after
t=177 s, outside the complete analysis windows. These are command-processor
timings, not physical display timestamps or broad driving/stability coverage.

The optimization is **not integrated or installed**. Its prototype, dedicated
fixtures and launch options were retired. The split physical-offset and render-
pass reuse experiments also showed no consistent benefit and were retired.
The coherency mock was not advanced into a production patch. Retained tooling
focuses on reproducible builds, correctness checks and useful generic diagnostics.

No further capture or optimization experiment is scheduled by this evaluation.
Raw game measurements remain private, outside source publication. Only aggregate
Forza results are retained in this report.
