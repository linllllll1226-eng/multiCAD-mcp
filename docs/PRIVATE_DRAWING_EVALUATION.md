# Private drawing evaluation and remaining acceptance

The public pilot is a partial-label regression corpus. It cannot establish
whole-drawing accuracy. The offline scorer below supports a larger local corpus
without uploading drawings, running a model, or connecting to AutoCAD.

## Collect and label before running the detector

Keep a separate directory outside Git with the sources, a manifest, independently
reviewed labels, prediction snapshots and run evidence. Record source ownership,
permission, language, acquisition method, DPI, skew/noise and label-review history
in your private records. Include genuine vector PDFs, scans, camera photos, hybrid
PDFs and Chinese/English engineering notes. Generated degradation or translated
titles do not replace genuine acquisition/language diversity.

Use a second reviewer for exhaustive labels: all lines, circles/arcs, texts,
typed dimensions and mandatory annotations on every page. Do not derive labels
from detector output or mark selected boundaries as exhaustive. Record omitted
items explicitly and leave `complete_annotation` false until review is complete.
Private inclusion in a local evaluation does not authorize public redistribution.

## Manifest contract

The manifest has `schema_version: 1` and a nonempty `cases` array. Each case has:

| Field | Meaning |
| --- | --- |
| `id` | Unique filename-safe ASCII id; e.g. `scan_001` |
| `file` | Source path relative to the manifest; absolute local paths also work |
| `source_sha256` | Lowercase SHA-256 of the exact source bytes |
| `coordinate_space` | Declared coordinate frame, identical in the prediction |
| `tolerance` | Positive finite coordinate tolerance; not a manufacturing tolerance |
| `complete_annotation` | Boolean declaration of exhaustive independent labels |
| `geometry` | Objects with unique `id`, `kind` and geometry fields |
| `texts` / `required_annotations` | Objects with nonempty `text` |
| `dimensions` | Objects with `kind`, `value`, and relevant `unit`, `tolerance`, `count`, `depth` |
| `close_line_pairs` | Unique pairs of same-page line-label ids |

Line records use `start`/`end`; circles use `center`/positive `radius`; arcs add
`start_angle`/`end_angle` in degrees. Arc sweep follows increasing XY angles,
with a nonzero sweep smaller than 360 degrees. Every multi-page record should
carry one-based `page`. Text, dimension and required-note labels can include
`bbox: [x0, y0, x1, y1]`; matching then requires all box edges to agree within
the case coordinate tolerance. Missing or wrong locations do not match.

Hash a source on Windows with:

```powershell
(Get-FileHash -Algorithm SHA256 'D:\CAD-evaluation\sources\scan_001.png').Hash.ToLowerInvariant()
```

The corpus loader rejects changed source bytes, duplicate case ids, invalid
hashes, unsafe output ids, invalid geometry, non-boolean completion flags and
malformed JSON. Duplicate JSON keys and NaN/Infinity are rejected. `augmentations`
is optional and used only by the pilot analyzer runner; accepted recipes are
`skew_blur` and `hybrid_title`. A case name alone never triggers augmentation.

## Record predictions separately

Use a dedicated directory containing exactly one `<id>.json` per case. A
prediction contains `case_id`, `source_sha256`, `coordinate_space`, the four
record arrays, `truncated` and `claimed_complete` booleans, and an optional
descriptive `stage`. Records flagged `needs_confirmation=true` remain useful
candidate matches but cannot establish recognition completeness.

Do not copy ground-truth arrays into prediction files. Export observed detector
output and preserve its raw response separately. If deskewing changes coordinates,
record the transform and independently transform labels into the same frame;
the standalone scorer does not guess conversions or scales.

```powershell
uv run python scripts/score_drawing_predictions.py --manifest D:\CAD-evaluation\manifest.json --predictions D:\CAD-evaluation\predictions --output D:\CAD-evaluation\scores.json
```

The output file must not exist. Exit code 1 means source binding failed or a
completion claim was false. Add `--require-recognition-complete` to also fail
partial or incomplete recognition. Malformed inputs fail with an error before
report creation. The report gives one-to-one precision/recall, missing/excess
records, close-line loss/merge-candidate rates, completion claim and false-pass
counts, review requirements, and source/prediction/manifest hashes.

Precision is unavailable for partial labels. Zero false passes with zero claims
is not evidence of a reliable completion gate. Reviewer independence and label
exhaustiveness are declarations that this script cannot authenticate.

## Windows and AutoCAD completion gate

After the exact candidate commit passes the full Windows test, coverage, typing,
lint, documentation, wheel, dependency-audit and CodeQL gates:

1. Run native PaddleOCR on real drawings, comparing original and rotation-probe
   evidence. Measure all candidates and review conflicts; do not select a value
   because it happens to match the label.
2. Build guarded plans only for explicitly authorized test drawings. Resolve
   unknown dimensions and mandatory notes before committing geometry.
3. Follow [isolated DWG acceptance](ISOLATED_DWG_ACCEPTANCE.md) per case, including
   save/close and a new-server-process reopen, then independently compare actual
   entity types, coordinates, dimensions, required notes and omitted geometry.
4. Bind the evidence to the tested commit, exact source bytes, plan, DWG and
   per-case verifier results. Preserve failures as well as successes. A past
   release's saved evidence does not validate a new commit.

The offline report always says `live_dwg_acceptance: not_evaluated` and
`release_approved: false`, even for perfect recognition. Keep issues #10 and #13
open until representative data, independent labels, reviewed release thresholds
and fresh persisted-DWG acceptance are actually complete.
