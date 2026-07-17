# CAD Vision Benchmark

This benchmark compares the repository's previous deterministic source-analysis
capability with the new optional vision pipeline. It does not compare language
model intelligence.

| Metric | Previous | Enhanced | Change |
|---|---:|---:|---:|
| Vector PDF primary path recovery | 0.00% | 100.00% | +100.00 pp |
| Structured dimension accuracy | 12.50% | 100.00% | +87.50 pp |
| Residual skew error | 7.0000° | 0.0000° | 100.00% reduction |
| Repeated analysis latency | 7.919 ms | 0.886 ms | 8.94x faster |

## Local real-drawing regression (2026-07-17)

An untracked 1040 x 813 Anchor Slide engineering raster was used to compare the
same `summary -> detailed samples` workflow before and after pipeline `1.3.1`.
Model files were already present locally; timings include a fresh Python/OCR
pipeline initialization.

| Metric | Before | Pipeline 1.3.1 | Change |
|---|---:|---:|---:|
| Cold summary | 13.647 s | 10.686 s | 21.7% faster |
| Detailed request after summary | 6.479 s | 0.003 s | 99.95% faster |
| Two-request total | 20.126 s | 10.689 s | 46.9% faster |
| OCR text regions | 37 | 37 | unchanged |
| Parsed dimension candidates | 22 | 25 | +13.6% |

The three recovered candidates were keyway depth `8` plus diameter/depth from a
damaged `20V65` hole callout. The latter two carry `needs_confirmation=true` and
are not authoritative CAD dimensions. A second untracked Mount Bracket image
returned 28 text regions, 17 dimension candidates, and a 0.003 s detailed cache
hit. These local images are not distributed with the repository, so this section
is reproducibility evidence rather than a universal accuracy claim.

Close-line regression fixtures additionally prove that two 1-pixel strokes only
3 pixels apart remain separate while one 4-pixel-thick stroke is not split into
a false pair.

## Safety result

- AutoCAD connected: `False`
- DWG written: `False`
- Legacy write fallback added: `False`

## Interpretation

The enhanced path extracts vector geometry directly from vector PDFs, parses
common engineering dimensions into typed records, deskews raster drawings before
geometry detection, and caches identical analyses. The guarded CAD write workflow
is unchanged.

## Limitations

- Synthetic benchmark results do not equal accuracy on every real drawing.
- OCR performance is reported separately in [CAD_OCR.md](CAD_OCR.md).
- Raster geometry candidates still require model/user interpretation before CAD planning.
