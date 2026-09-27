# Scanned Drawing OCR

The optional local OCR path supports raster drawings, image-only PDFs, and hybrid
PDFs that mix vector title-block content with rasterized dimensions. It uses
PaddleOCR to return bounded text evidence; it does not turn uncertain text into
trusted CAD geometry by itself.

## Install

```powershell
cd D:\AI\multiCAD-mcp
uv sync --extra dev --extra vision --extra docs --extra ocr
```

The first OCR request may download official detection and recognition models.
By default they are stored in the Git-ignored ASCII-safe directory
`data/paddle_models`. Set `PADDLE_PDX_CACHE_HOME` before starting the MCP to use
another local directory.

## Routing

- Vector PDFs: embedded paths and text are always extracted directly.
- Hybrid PDFs: image coverage is evaluated per page. Raster-heavy pages are sent
  to OCR; smaller meaningful image regions are cropped and OCRed without
  rasterizing the rest of the page. A small logo below the configured region
  threshold does not trigger OCR by itself.
- Scanned PDFs: pages are sent to OCR because vector text coverage is incomplete.
- PNG/JPEG/BMP/TIFF: raster geometry analysis and OCR can run together.
- AutoCAD is never connected during source analysis.

`cad_analyze_source` enables OCR by default and accepts:

- `ocr_language`: defaults to `ch`, which also recognizes Latin engineering text.
- `ocr_min_confidence`: defaults to `0.5`.
- `ocr_policy`: `auto`, `force`, or `off`. An empty value preserves the legacy
  `use_ocr` mapping (`true` means `auto`; `false` means `off`).
- `raster_page_threshold`: raster coverage that triggers whole-page OCR; defaults
  to `0.15`.
- `raster_region_threshold`: minimum embedded-image coverage for region OCR;
  defaults to `0.02`.
- `source_unit` and `drawing_unit`: optional explicit unit evidence. A conflict is
  reported and length records remain unresolved.
- `max_pages`: bounded to at most 50 pages.
- `use_cache`: caches identical source/options combinations locally.
- `ocr_rotation_angles`: optional additional counterclockwise passes from
  `[90, 180, 270]`; defaults to no extra passes. `[90, 270]` can expose vertical
  text candidates. These cost extra inference and require real-corpus evaluation
  before being enabled by default. Invalid angles are rejected.

Rotation probes apply to raster sources and selected PDF pages/regions. Their
boxes are mapped back through the right-angle, crop, scale and page transforms.
Equal text at the same source location is merged across passes with
`orientation_observations` retained. Equal values at different locations or
pages remain separate. Conflicting readings (for example `2` and `12`) are both
kept with `needs_confirmation=true`; a rotation-only reading also needs review,
even if two rotated passes agree. A missing/invalid text box requires review.
These flags propagate to typed dimensions and survive a vector/OCR merge.
The recognition-completeness scorer refuses unconfirmed predictions.

Original image files and PDF files are never changed. Rotated temporary PNGs
are removed after success or failure. Multi-frame raster input is rejected for
rotation probes rather than silently selecting its first frame. The vision
cache version is 1.6.0 and normalized rotation settings are part of the cache key.
OCR output includes explicit text/dimension truncation flags; candidates remain
bounded evidence rather than an exhaustive reconstruction.

To compare the pilot with and without probes, use new directories:

```powershell
uv run python scripts/benchmark_real_drawings.py --output ../ocr-base --check-baseline tests/fixtures/real_drawings/baseline.json
uv run python scripts/benchmark_real_drawings.py --output ../ocr-rotated --ocr-rotations 90 270 --check-baseline tests/fixtures/real_drawings/baseline.json
```

Compare raw text, location, typed-dimension matches, disagreements and latency.
A higher candidate recall alone is not evidence that the selected dimensions
are correct. This option's coordinate and conflict behavior is covered by pixel
fixtures with simulated text; that is not a native-model accuracy measurement.

Compact and detailed-sample requests share one canonical cache entry. The OCR
pipeline also discards and rebuilds one corrupted native inference object after a
runtime error, then retries exactly once. User-injected/test providers are never
retried silently.

Returned OCR evidence includes text, confidence, page-coordinate bounding box,
page number, and parsed engineering candidates such as diameter, radius, linear
tolerance, angle, depth, count, and thread annotations. Equivalent vector/OCR
records merge only when their type, value, unit, page, and location agree; both
provenance entries remain attached. Equal values at different locations remain
separate evidence.

The parser can return multiple records from one compound callout. `4X Ø10 DEPTH
20` retains count, diameter, and depth; metric and inch thread classes remain
typed. Tolerance is attached to its diameter, radius, depth, or linear record
instead of creating a duplicate linear measurement. Length units default to
unresolved rather than assuming millimetres. A damaged OCR token such as `20V65`
is retained as a low-confidence candidate with `needs_confirmation=true`;
guarded planning must not promote it without source or user confirmation.

## Verified benchmark

Run:

```powershell
uv run python scripts/benchmark_cad_ocr.py
```

The deterministic 300-dpi fixture is rotated by 1.5° and contains Chinese text
plus four engineering annotations. On the accepted Windows test machine:

| Metric | Result |
|---|---:|
| OCR status | `ok` |
| Text regions | 5 |
| Engineering dimension candidates | 4 |
| Expected dimension-kind recall | 100% |
| First run including model download/initialization | 58,998.962 ms |
| Repeated identical request from analysis cache | 1.830 ms |

Recognized engineering samples were `DIA 15`, `R20`, `100 +/- 0.1`, and
`M10x1.5`; the Chinese sample `扫描工程图 OCR` was also recovered. These are
synthetic-fixture results, not a universal accuracy claim for arbitrary scans.

## Troubleshooting

- `status=unavailable`: install the `ocr` extra and restart the MCP.
- First request is slow: model download and inference initialization happen once.
- Model file cannot be opened under a Windows user profile containing non-ASCII
  characters: v0.4 redirects the model cache to `data/paddle_models` before
  importing PaddleOCR.
- Low-confidence or missing text: improve scan resolution/contrast, reduce skew,
  and keep the evidence in `AI_UNCERTAIN` until a user confirms it.
