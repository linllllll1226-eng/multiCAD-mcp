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
- PaddleOCR is intentionally not installed or benchmarked in the stable MCP.
- Raster geometry candidates still require model/user interpretation before CAD planning.
