# Optional CAD Vision Pipeline

The enhanced MCP has two read-only source-analysis tools:

- `cad_vision_capabilities`: reports installed vector PDF, raster geometry, and
  OCR providers without connecting to AutoCAD.
- `cad_analyze_source`: analyzes one local PDF or image and returns bounded,
  structured JSON.

## Accuracy and efficiency strategy

1. Prefer vector extraction for vector PDFs. This preserves path geometry and
   embedded text instead of rasterizing everything and asking OCR to recover it.
2. Normalize raster drawings before interpretation. The image path estimates page
   skew, deskews the image, then reports line and circle candidates.
3. Convert common dimension annotations into typed records such as diameter,
   radius, angle, depth, count, tolerance, and thread.
4. Cache by source SHA-256, pipeline version, and analysis options. Repeating the
   same request avoids repeated PDF/CV work.
5. Keep results compact and bounded so MCP responses do not flood model context.

## Install optional dependencies

```powershell
cd D:\AI\multiCAD-mcp
uv sync --extra vision
```

PaddleOCR remains a separate optional provider. It is deliberately not installed
into the stable MCP environment by default because it is substantially heavier
than vector extraction and deterministic geometry preprocessing.

## Safety boundaries

- Source analysis does not connect to AutoCAD and cannot write a DWG.
- Network/UNC paths and unsupported file types are rejected.
- Input size defaults to 100 MB and can be changed with
  `MULTICAD_VISION_MAX_BYTES`.
- Optional input roots can be restricted with a semicolon-separated
  `MULTICAD_VISION_INPUT_ROOTS` value.
- Cache data stays local under `data/vision_cache` and is ignored by Git.
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
