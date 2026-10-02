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

Raster circles require radial image-edge support at 360 angles: at least 65% of
the circumference and 50% in each quadrant. Edge gradients must align with the
radius, which rejects circles suggested only by straight boundaries or hatching.
The radial tolerance is 3-8 pixels depending on radius. A 3 px median filter
preserves thin drawing strokes that the former 5 px filter could erase.
Supported centres are also checked for distinct concentric rings suppressed by
Hough's centre-distance limit. Raw/rejected/recovered counts and bounded
`circle_support_samples`, prioritizing retained circles, retain
the decision evidence; `circle_candidate_count` counts retained candidates.
Filtering happens before the output sample limit. Pipeline version 1.7.0 prevents
reuse of earlier unfiltered cache entries.

These are full-circle candidates, not verified CAD geometry. Partial, cropped,
faint or heavily occluded circles can be missed, and rings whose radial tolerance
bands overlap may merge. Arcs and semantic distinctions
between circular lettering and part geometry still require independent review.

Continuous horizontal/vertical ink spans supplement fragmented Hough lines,
prioritized by length before the output bound. Separate white gaps and close
parallel ink strokes remain separate. Diagonal Hough segments remain candidates;
dimension extensions, hatching and text are not semantically classified. Raw,
binary and combined line counts and truncation flags preserve that distinction.

Oblique boundaries also use OpenCV's line-segment detector. Endpoint extensions
require continuous nearby dark ink, stop at the first unsupported gap and stay
within 12 pixels or 15% of the observed segment length. Ten-degree angle bins
share the bounded sample budget so dense hatching at one angle does not suppress
shorter boundaries at another angle. `diagonal_line_samples` and their separate
candidate/truncation counts retain this evidence; stroke edges and intersecting
ink can still create duplicate or approximate segments. These are source-pixel
candidates, without semantic roles or permission to create formal CAD geometry.

Hybrid PDF analysis also renders meaningful embedded image regions for geometry,
independently of OCR policy. Rendered coordinates are mapped back through inverse
deskew and page derotation into canonical unrotated PDF points. Vector paths stay
separate from `raster_geometry_samples`. Each raster candidate carries the region,
render density and deskew provenance and requires confirmation. Rendering includes
vector overlays, and overlapping regions may duplicate candidates. The default
bounds are 20 regions, four million rendered pixels per region and sixteen million
pixels per document; omitted regions and errors are reported explicitly. Missing
optional image dependencies retain vector extraction with raster status unavailable.
These bounded samples do not establish complete geometry or a formal CAD plan.
The public corpus has partial labels, so this filter cannot establish precision
or production completeness.

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

On Windows, the absolute model cache path must contain only ASCII characters.
Paddle's native inference engine can report `Cannot open file` for an existing
model under a Chinese username or project directory. Set an explicit local path
before starting the server or benchmark; the OCR provider rejects an incompatible
path before creating the cache or loading Paddle:

```powershell
$env:PADDLE_PDX_CACHE_HOME = 'C:\Temp\multicad-paddle-models'
```

For native OCR acceptance, run the public pilot with a fresh output directory
and the explicit OCR gate:

```powershell
uv run python scripts/benchmark_real_drawings.py --output ..\ocr-default-new --require-native-ocr
uv run python scripts/benchmark_real_drawings.py --output ..\ocr-rotations-new --ocr-rotations 90 270 --require-native-ocr
```

The report records `native_ocr_gate` separately from regression and production
completeness. With `--require-native-ocr`, an error, unavailable provider, or a run
with no actual OCR success returns a nonzero exit code after saving the evidence.
Vector-only cases may report `not_required` when another case successfully runs
OCR. Without this option the pilot can still check geometry when OCR is absent;
a zero exit code then does not prove native OCR acceptance. Conflicts retain
`needs_confirmation`; partial labels never establish production completeness.

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
