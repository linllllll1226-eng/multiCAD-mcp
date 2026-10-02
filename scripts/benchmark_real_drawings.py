"""Measure a public real-drawing pilot; never turn partial labels into release approval."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cad_vision.analyzer import analyze_source, vision_capabilities  # noqa: E402
from cad_vision.benchmark import score_case  # noqa: E402
from cad_vision.corpus import check_baseline, load_manifest, read_json  # noqa: E402


def transformed(geometry, matrix):
    """Map labels through an orientation-preserving similarity in XY coordinates."""
    if len(matrix) != 2 or any(len(row) != 3 for row in matrix):
        raise ValueError("label transform must be a finite 2x3 similarity matrix")
    if any(not math.isfinite(value) for row in matrix for value in row):
        raise ValueError("label transform must be a finite 2x3 similarity matrix")
    a, b, _ = matrix[0]
    c, d, _ = matrix[1]
    scale = math.hypot(a, c)
    if (
        scale <= 0
        or not math.isclose(a, d, rel_tol=1e-9, abs_tol=1e-12)
        or not math.isclose(b, -c, rel_tol=1e-9, abs_tol=1e-12)
    ):
        raise ValueError("label transform cannot shear, reflect or distort circles/arcs")
    rotation = math.degrees(math.atan2(c, a))
    result = copy.deepcopy(geometry)
    for record in result:
        for key in ("start", "end", "center"):
            if key in record:
                x, y = record[key]
                record[key] = [float(row[0] * x + row[1] * y + row[2]) for row in matrix]
        if record.get("kind") in {"circle", "arc"}:
            record["radius"] = float(record["radius"] * scale)
        if record.get("kind") == "arc":
            for key in ("start_angle", "end_angle"):
                record[key] = (record[key] + rotation) % 360
    return result


def _draw_overlay(canvas, case, prediction, page_number):
    """Draw only one page's evidence in its unrotated or deskewed XY frame."""
    from PIL import Image, ImageDraw

    source_height = canvas.height
    framed = Image.new("RGB", (canvas.width, source_height + 32), "white")
    framed.paste(canvas, (0, 0))
    canvas = framed
    draw = ImageDraw.Draw(canvas)
    for records, color in [(prediction["geometry"], "#0088ff"), (case["geometry"], "#e00040")]:
        for item in records:
            if item.get("page", 1) != page_number:
                continue
            if item["kind"] == "line":
                draw.line([tuple(item["start"]), tuple(item["end"])], fill=color, width=2)
            elif item["kind"] in {"circle", "arc"}:
                x, y = item["center"]
                r = item["radius"]
                box = (x - r, y - r, x + r, y + r)
                if item["kind"] == "circle":
                    draw.ellipse(box, outline=color, width=2)
                else:
                    draw.arc(box, item["start_angle"], item["end_angle"], fill=color, width=2)
    scope = "EXHAUSTIVE LABELS" if case.get("complete_annotation") is True else "PARTIAL LABELS"
    draw.text((3, source_height + 2), "Expected:red Detected:blue", fill="black")
    draw.text((3, source_height + 17), f"{scope} | p{page_number}", fill="black")
    return canvas


def overlay(path, case, prediction, output):
    """Save page-separated overlays; never guess missing multi-page provenance."""
    import fitz
    from PIL import Image

    def validate_pages(page_count):
        """Reject records which would otherwise be drawn on an invented page."""
        for item in case["geometry"] + prediction["geometry"]:
            page = item.get("page", 1 if page_count == 1 else None)
            if type(page) is not int or not 1 <= page <= page_count:
                raise ValueError("Overlay geometry requires valid source page provenance")

    if path.suffix.lower() == ".pdf":
        outputs = []
        with fitz.open(path) as doc:
            validate_pages(len(doc))
            for index, page in enumerate(doc):
                # get_drawings() uses unrotated coordinates. Change only the
                # in-memory display rotation; the source file is never saved.
                page.set_rotation(0)
                pix = page.get_pixmap(colorspace=fitz.csRGB, alpha=False)
                canvas = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                canvas = _draw_overlay(canvas, case, prediction, index + 1)
                target = (
                    output
                    if len(doc) == 1
                    else output.with_name(f"{output.stem}.page-{index + 1:03d}{output.suffix}")
                )
                canvas.save(target)
                outputs.append(target)
        return outputs

    import cv2
    import numpy as np

    validate_pages(1)
    with Image.open(path) as source:
        pixels = np.array(source.convert("RGB"))
    height, width = pixels.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), prediction.get("deskew", 0), 1)
    canvas = Image.fromarray(
        cv2.warpAffine(pixels, matrix, (width, height), borderValue=(255, 255, 255))
    )
    canvas = _draw_overlay(canvas, case, prediction, 1)
    canvas.save(output)
    return [output]


def prepare_cases(manifest, output):
    """Convert public SVG to PDF and add explicitly synthetic degradation variants."""
    import cv2
    import fitz
    import numpy as np
    from PIL import Image, ImageFilter

    bound_cases = load_manifest(manifest)
    ids = {case["id"] for case, _ in bound_cases}
    for case, _ in bound_cases:
        variants = case.get("augmentations", [])
        if not isinstance(variants, list) or any(
            v not in ("skew_blur", "hybrid_title") for v in variants
        ):
            raise ValueError("unsupported benchmark augmentation recipe")
        for recipe in variants:
            identity = case["id"] + ("_skew_blur" if recipe == "skew_blur" else "_hybrid")
            if identity in ids:
                raise ValueError(f"duplicate generated case id: {identity}")
            ids.add(identity)
    cases = []
    for original, source in bound_cases:
        case = copy.deepcopy(original)
        if source.suffix.lower() == ".svg":
            target = output / (case["id"] + ".pdf")
            with fitz.open(source) as doc:
                expected_size = tuple(doc[0].rect[2:])
                target.write_bytes(doc.convert_to_pdf())
            # MuPDF's SVG conversion uses 1 point per SVG pixel.
            with fitz.open(target) as doc:
                if tuple(doc[0].rect[2:]) != expected_size:
                    raise ValueError("Unexpected SVG conversion scale")
            source = target
        cases.append((case, source))
        if "skew_blur" in original.get("augmentations", []):
            target = output / (case["id"] + "_skew_blur.png")
            with Image.open(source) as im:
                pixels = np.array(im.convert("RGB"))
                width, height = im.size
            matrix = cv2.getRotationMatrix2D((width / 2, height / 2), 3, 1)
            pixels = cv2.warpAffine(pixels, matrix, (width, height), borderValue=(255, 255, 255))
            Image.fromarray(pixels).filter(ImageFilter.GaussianBlur(0.6)).save(
                target, dpi=(150, 150)
            )
            variant = copy.deepcopy(case)
            variant["id"] = case["id"] + "_skew_blur"
            variant["geometry"] = transformed(case["geometry"], matrix)
            variant["augmentation"] = {
                "rotation_degrees": 3,
                "blur_sigma": 0.6,
                "dpi_metadata": 150,
            }
            cases.append((variant, target))
        if "hybrid_title" in original.get("augmentations", []):
            target = output / (case["id"] + "_hybrid.pdf")
            with Image.open(source) as image:
                width, height = image.size
            doc = fitz.open()
            page = doc.new_page(width=width, height=height + 47)
            page.insert_image(fitz.Rect(0, 0, width, height), filename=str(source))
            page.insert_text((10, height + 22), "TEST / THROUGH HOLE", fontsize=10)
            page.insert_text((10, height + 37), "测试图 / 通孔", fontname="china-s", fontsize=10)
            doc.save(target)
            doc.close()
            variant = copy.deepcopy(case)
            variant["id"] = case["id"] + "_hybrid"
            variant.setdefault("texts", []).extend(
                [{"text": "TEST / THROUGH HOLE"}, {"text": "测试图 / 通孔"}]
            )
            variant["augmentation"] = {
                "hybrid_pdf": True,
                "bilingual_title": "added, not original annotation",
            }
            cases.append((variant, target))
    return cases


def evaluate(case, source, output, ocr_rotation_angles=None):
    """Score actual public analyzer output; absent vector coordinates stay absent."""
    import cv2

    raw = analyze_source(
        str(source),
        use_cache=False,
        include_samples=True,
        ocr_policy="auto",
        ocr_rotation_angles=ocr_rotation_angles,
    )
    analysis = raw["analysis"]
    geometry = [
        {**item, "page": page["page"]}
        for page in analysis.get("pages", [])
        for item in page.get("geometry_samples", [])
    ]
    for x1, y1, x2, y2 in analysis.get("line_samples", []):
        geometry.append({"kind": "line", "start": [x1, y1], "end": [x2, y2]})
    for x, y, r in analysis.get("circle_samples", []):
        geometry.append({"kind": "circle", "center": [x, y], "radius": r})
    case = copy.deepcopy(case)
    skew = analysis.get("estimated_skew_degrees", 0)
    if "image_size_px" in analysis:
        width, height = analysis["image_size_px"]
        case["geometry"] = transformed(
            case["geometry"], cv2.getRotationMatrix2D((width / 2, height / 2), skew, 1)
        )
    case["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    case["coordinate_space"] = (
        "detector_deskewed_pixels" if source.suffix.lower() != ".pdf" else "pdf_points"
    )
    texts = [
        {"text": text, "page": page["page"]}
        for page in analysis.get("pages", [])
        for text in page.get("text_samples", [])
    ]
    texts += [
        {key: item[key] for key in ("text", "page", "bbox", "needs_confirmation") if key in item}
        for item in analysis.get("ocr", {}).get("text_samples", [])
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ]
    prediction = {
        "case_id": case["id"],
        "source_sha256": raw["source"]["sha256"],
        "coordinate_space": case["coordinate_space"],
        "geometry": geometry,
        "texts": texts,
        "dimensions": analysis.get("dimensions", []),
        "required_annotations": [],
        "claimed_complete": False,
        "truncated": True,
        "stage": "source_analysis",
        "deskew": skew,
    }
    # Public API samples remain candidates, not a CAD plan. Never infer
    # absent coordinates from bounding boxes or from the ground-truth labels.
    result = score_case(case, prediction)
    result["rejects_false_completion_claim"] = score_case(
        case, {**prediction, "claimed_complete": True}
    )["false_pass"]
    result["ocr_status"] = analysis.get("ocr", {}).get("status")
    result["augmentation"] = case.get("augmentation")
    result["limitations"] = [
        "Partial ground truth; precision is unavailable.",
        "Bounded source-analysis samples; no complete reconstruction or CAD acceptance.",
    ]
    result["overlays"] = [
        path.name
        for path in overlay(source, case, prediction, output / f"{case['id']}.overlay.png")
    ]
    for suffix, value in [
        ("raw", raw),
        ("prediction", prediction),
        ("labels", case),
        ("metrics", result),
    ]:
        (output / f"{case['id']}.{suffix}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return result


def native_ocr_failures(results):
    """Require actual native OCR success without rejecting vector-only pages."""
    errors = [
        f"{result['case_id']}: OCR status {result.get('ocr_status')!r}"
        for result in results
        if result.get("ocr_status") not in {"ok", "not_required"}
    ]
    if not any(result.get("ocr_status") == "ok" for result in results):
        errors.append("No case completed native OCR")
    return errors


def main():
    """Emit reproducible measurements; production gate fails on this partial pilot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "tests/fixtures/real_drawings/manifest.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument(
        "--require-native-ocr",
        action="store_true",
        help="Fail if native OCR is unavailable, fails, or never runs; retain all artifacts.",
    )
    parser.add_argument("--check-baseline", type=Path)
    parser.add_argument("--ocr-rotations", type=int, nargs="*", choices=(90, 180, 270), default=[])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    results = [
        evaluate(case, source, args.output, args.ocr_rotations)
        for case, source in prepare_cases(args.manifest, args.output)
    ]
    ocr_errors = native_ocr_failures(results)
    report = {
        "schema_version": 1,
        "corpus_status": "pilot_partial_labels_not_representative",
        "capabilities": vision_capabilities(),
        "ocr_rotation_angles": args.ocr_rotations,
        "native_ocr_gate": {
            "required": args.require_native_ocr,
            "passed": not ocr_errors,
            "errors": ocr_errors,
        },
        "case_count": len(results),
        "cases": results,
        "false_pass_count": sum(r["false_pass"] for r in results),
        "false_completion_claims_rejected": sum(
            r["rejects_false_completion_claim"] for r in results
        ),
        "production_gate_passed": bool(results) and all(r["recognition_complete"] for r in results),
        "live_dwg_acceptance": "not_evaluated",
    }
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# Real drawing pilot measurements",
        "",
        "Partial labels: precision and whole-drawing accuracy are unavailable. "
        "False-pass zero with zero completion claims is not evidence of robustness.",
        "",
        "| Case | Geometry matched/labelled | Text matched/labelled "
        "| Dimensions matched/labelled | OCR |",
        "| --- | --- | --- | --- | --- |",
    ]
    for result in results:
        groups = result["metrics"]
        values = [
            f"{groups[k]['matched']}/{groups[k]['expected']}"
            for k in ("geometry", "text", "typed_dimensions")
        ]
        lines.append(
            f"| {result['case_id']} | " + " | ".join(values) + f" | {result['ocr_status']} |"
        )
    lines += [
        "",
        "Production reconstruction gate: FAILED "
        "(incomplete labels, bounded output, no DWG lifecycle proof).",
        "Red overlays are selected expected boundaries; "
        "blue overlays are actual detector candidates.",
    ]
    (args.output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    if args.check_baseline:
        check_baseline(results, read_json(args.check_baseline))
    return int(
        (args.require_complete and not report["production_gate_passed"])
        or (args.require_native_ocr and bool(ocr_errors))
    )


if __name__ == "__main__":
    raise SystemExit(main())
