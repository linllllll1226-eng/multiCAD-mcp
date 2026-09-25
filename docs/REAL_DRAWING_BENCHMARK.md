# Real-drawing pilot and remaining release evaluation

This pilot measures actual analyzer outputs against independently selected
labels from three public drawings and two explicit augmentations. It does not
close issue #10 or establish unattended reconstruction accuracy.

```powershell
uv run python scripts/benchmark_real_drawings.py --output ../real-pilot --check-baseline tests/fixtures/real_drawings/baseline.json
```

Run with a new output directory. The runner verifies source hashes, preserves
raw analyzer responses, predictions, transformed labels, per-case metrics and
red/blue failure overlays. It uses no CAD connection. An external/private
manifest can be supplied with `--manifest`; keep private results outside Git.

`--require-complete` intentionally exits nonzero for this pilot. The ordinary
command reports measurements, while `--check-baseline` rejects lost cases,
source-binding errors, recognition regressions against modest reviewed floors,
and any failure to reject a deliberately false completion claim.

## What the measurements mean

- Matching is maximum one-to-one: duplicate predictions cannot satisfy multiple
  labels. Line endpoint order and wrapped arc angles are handled explicitly.
- Geometry, text and typed dimensions have separate matched/expected counts and
  recall. Precision is null for partial labels, because unlabelled predictions
  cannot fairly be called false positives.
- Required annotations are scored separately from ordinary text: a missing
  mandatory note rejects completeness even when every geometry and dimension
  matches. Case and prediction records use `required_annotations` with `text`
  and an optional one-based `page`. The pilot does not infer mandatory notes;
  its predictions leave that category empty.
- A label carrying `page` can only match evidence carrying the same page.
  Identical coordinates, numerals or notes on another page cannot substitute.
  Missing page provenance fails matching; invalid page numbers, duplicate
  geometry label IDs and invalid close-line references reject the input.
- Close-line loss counts a pair with either member unmatched. The separately
  named merge-candidate rate counts a shared candidate compatible with both
  labels. It is not proof of the detector's internal merge operation.
- Labelled completeness is recall across the selected categories. Full
  recognition completeness additionally requires exhaustive labels, exact source
  binding, untruncated predictions and no unmatched predictions.
- A false pass is a completion claim that fails those conditions. Zero false
  passes from a system making zero completion claims is not a reliability result;
  deliberately false claims are tested separately.
- This scorer never authenticates CAD sessions. Even perfectly matching JSON
  leaves live DWG acceptance as `not_evaluated`. Use the isolated acceptance
  runner and independent save/close/new-process reopen evidence for that claim.

## Observed limits and work still required for #10

On Windows with only the locked dev+vision environment, PaddleOCR is unavailable.
Raster text and dimension misses are retained, not replaced by ground-truth
labels. An opt-in local run reusing already installed PaddleOCR 3.7.0 /
PaddlePaddle 3.2.0 and cached PP-OCRv5 server models recovered 9/13 selected
texts and 6/10 selected dimensions on the turned-part image. It still misread
some vertical numerals (for example 12 as 2). Provider availability does not
imply correct dimension recognition.

Vector analysis now exposes bounded line coordinates, rectangle/quad edges,
and conservative circle-fit candidates in unrotated PDF points. Standard
four-cubic circles and dense closed circular polylines are explicitly marked
approximate and requiring confirmation; they are not manufacturing dimensions
or a ready CAD plan. Unsupported curves, skipped nonstroke paths, sample
truncation and page truncation remain explicit. The pipeline cache version was
incremented so old path-only responses cannot hide the added coordinates.

The public section sample improved from 0/10 to 10/10 selected geometry labels;
its regression floor is now 10. This is selected-label recall, not complete
drawing reconstruction. The original SVG is converted to PDF without deriving
predictions from its ground truth. See the
[PyMuPDF path API](https://pymupdf.readthedocs.io/en/latest/page.html#Page.get_drawings)
for the source coordinate and path-item contracts.

The CI pilot does not supply an exhaustive representative corpus: true scans and
camera photos, broad Chinese engineering callouts, independently reviewed arc
labels, varied line density/noise, and a larger private release set remain
necessary. Bilingual added titles cannot substitute for real bilingual drawings.
The user's practice PDF and known failure cases remain private; public inclusion
requires an explicit redistribution decision and sanitization review.

For a production reconstruction claim, require complete labels and all mandatory
boundaries/annotations on every selected page, zero false completion passes,
zero lost close boundaries, correct typed dimensions, and independent persisted
DWG entity verification. Do not lower those requirements to make this pilot green.
