# Scanned Drawing OCR

Version 0.4 adds optional local OCR for raster drawings and image-only PDFs. It
uses PaddleOCR to return bounded text evidence; it does not turn uncertain text
into trusted CAD geometry by itself.

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

- Vector PDFs: embedded paths and text are extracted directly; OCR is skipped
  when usable vector text exists.
- Scanned PDFs: pages are sent to OCR because no embedded text is available.
- PNG/JPEG/BMP/TIFF: raster geometry analysis and OCR can run together.
- AutoCAD is never connected during source analysis.

`cad_analyze_source` enables OCR by default and accepts:

- `ocr_language`: defaults to `ch`, which also recognizes Latin engineering text.
- `ocr_min_confidence`: defaults to `0.5`.
- `max_pages`: bounded to at most 50 pages.
- `use_cache`: caches identical source/options combinations locally.

Returned OCR evidence includes text, confidence, bounding box, page number, and
parsed engineering candidates such as diameter, radius, linear tolerance,
angle, depth, count, and thread annotations.

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
