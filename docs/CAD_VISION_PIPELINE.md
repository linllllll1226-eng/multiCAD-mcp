# Optional CAD Vision Pipeline

The enhanced MCP has two read-only source-analysis tools:

- `cad_vision_capabilities`: reports installed vector PDF, raster geometry, and
  OCR providers without connecting to AutoCAD.
- `cad_analyze_source`: analyzes one local PDF or image and returns bounded,
  structured JSON.

## Accuracy and efficiency strategy

1. Prefer vector extraction for vector PDFs. This preserves path geometry and
   embedded text instead of rasterizing everything and asking OCR to recover it.
2. Inspect every PDF page for embedded image regions and raster coverage. The
   `auto` policy OCRs only incomplete pages or meaningful raster regions, even
   when a vector title block is present. `force` and `off` remain explicit.
3. Normalize raster drawings before interpretation. The image path estimates page
   skew, deskews the image, then reports line and circle candidates.
4. Convert common annotations into typed diameter, radius, angle, depth, count,
   tolerance, and thread records. Length units remain unresolved until annotation,
   source, or drawing-profile evidence resolves them.
5. Merge co-located equivalent vector/OCR dimensions while retaining both
   provenance chains. Do not merge equal values at different locations.
6. Cache by source SHA-256, pipeline version, OCR policy, raster thresholds, unit
   resolution, and other semantic options. A compact summary and a later
   bounded-sample request share one canonical entry, so changing
   `include_samples` does not rerun OCR/PDF/CV work.
7. Preserve close parallel boundaries with two detectors: Hough lines for normal
   gaps and binary ink-stroke separation for lines only 2-3 pixels apart. One
   thick stroke is not split into a false pair.
8. Keep results compact and bounded so MCP responses do not flood model context.

Damaged OCR callouts such as `20V65` are retained as low-confidence,
`needs_confirmation=true` diameter/depth candidates. They improve recall without
becoming trusted production dimensions.

## Install optional dependencies

```powershell
cd D:\AI\multiCAD-mcp
uv sync --extra vision --extra ocr
```

The `vision` extra provides vector PDF and raster geometry analysis. The `ocr`
extra installs PaddleOCR plus its local Paddle inference engine. Embedded vector
PDF evidence remains preferred, but it no longer suppresses OCR for raster-heavy
pages or embedded raster dimension regions. The first OCR request downloads
official model weights to `data/paddle_models`, or to
`PADDLE_PDX_CACHE_HOME` when that variable is set.

## Safety boundaries

- Source analysis does not connect to AutoCAD and cannot write a DWG.
- Network/UNC paths and unsupported file types are rejected.
- Input size defaults to 100 MB and can be changed with
  `MULTICAD_VISION_MAX_BYTES`.
- Optional input roots can be restricted with a semicolon-separated
  `MULTICAD_VISION_INPUT_ROOTS` value.
- Cache data stays local under `data/vision_cache` and is ignored by Git.
- OCR model files stay local under `data/paddle_models` and are ignored by Git.
- Any later CAD write still requires
  `cad_plan_validate -> cad_execute_plan -> cad_verify_execution`.

## Benchmark

Run the deterministic benchmark with:

```powershell
& D:\AI\multiCAD-mcp\.venv\Scripts\python.exe `
  D:\AI\multiCAD-mcp\scripts\benchmark_cad_vision.py `
  --json D:\AI\multiCAD-mcp\docs\CAD_VISION_BENCHMARK.json `
  --markdown D:\AI\multiCAD-mcp\docs\CAD_VISION_BENCHMARK.md
```

The benchmark measures vector path recovery, structured dimension parsing,
deskew residual error, and repeat-analysis cache speed. It uses synthetic fixtures
and explicitly does not claim universal real-drawing accuracy.

Run the scanned-drawing OCR benchmark with:

```powershell
uv run python scripts/benchmark_cad_ocr.py
```

See [CAD_OCR.md](CAD_OCR.md) for measured results and provider troubleshooting.
