"""Measure a public real-drawing pilot; never turn partial labels into release approval."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cad_vision.analyzer import analyze_source, vision_capabilities  # noqa: E402
from cad_vision.benchmark import score_case  # noqa: E402


def transformed(geometry, matrix):
    """Map independently labelled coordinates into the detector's deskewed frame."""
    result = copy.deepcopy(geometry)
    for record in result:
        for key in ("start", "end", "center"):
            if key in record:
                x, y = record[key]
                record[key] = [float(row[0] * x + row[1] * y + row[2]) for row in matrix]
    return result


def overlay(path, case, prediction, output):
    """Render scientific expected/detected geometry overlays without changing inputs."""
    import cv2
    import fitz
    import numpy as np
    from PIL import Image, ImageDraw

    if path.suffix == ".pdf":
        with fitz.open(path) as doc:
            pix = doc[0].get_pixmap()
            canvas = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    else:
        with Image.open(path) as source:
            pixels = np.array(source.convert("RGB"))
        height, width = pixels.shape[:2]
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), prediction.get("deskew", 0), 1)
        canvas = Image.fromarray(
            cv2.warpAffine(pixels, matrix, (width, height), borderValue=(255, 255, 255))
        )
    draw = ImageDraw.Draw(canvas)
    for records, color in [(prediction["geometry"], "#0088ff"), (case["geometry"], "#e00040")]:
        for item in records:
            if item["kind"] == "line":
                draw.line([tuple(item["start"]), tuple(item["end"])], fill=color, width=2)
            elif item["kind"] == "circle":
                x, y = item["center"]
                r = item["radius"]
                draw.ellipse((x - r, y - r, x + r, y + r), outline=color, width=2)
    draw.rectangle((0, 0, min(canvas.width, 410), 16), fill="white")
    draw.text((3, 2), "Expected: red | detected: blue | PARTIAL LABELS", fill="black")
    canvas.save(output)


def prepare_cases(manifest, output):
    """Convert public SVG to PDF and add explicitly synthetic degradation variants."""
    import cv2
    import fitz
    import numpy as np
    from PIL import Image, ImageFilter

    data = json.loads(manifest.read_text("utf-8"))
    cases = []
    for original in data["cases"]:
        source = (manifest.parent / original["file"]).resolve()
        if hashlib.sha256(source.read_bytes()).hexdigest() != original["source_sha256"]:
            raise ValueError(f"Source hash mismatch: {source.name}")
        case = copy.deepcopy(original)
        if source.suffix == ".svg":
            target = output / (case["id"] + ".pdf")
            with fitz.open(source) as doc:
                target.write_bytes(doc.convert_to_pdf())
            # MuPDF's SVG conversion uses 1 point per SVG pixel.
            with fitz.open(target) as doc:
                if tuple(doc[0].rect[2:]) != (430, 430):
                    raise ValueError("Unexpected SVG conversion scale")
            source = target
        cases.append((case, source))
        if original["id"] == "hole":
            target = output / "hole_skew_blur.png"
            with Image.open(source) as im:
                pixels = np.array(im.convert("RGB"))
                width, height = im.size
            matrix = cv2.getRotationMatrix2D((width / 2, height / 2), 3, 1)
            pixels = cv2.warpAffine(pixels, matrix, (width, height), borderValue=(255, 255, 255))
            Image.fromarray(pixels).filter(ImageFilter.GaussianBlur(0.6)).save(
                target, dpi=(150, 150)
            )
            variant = copy.deepcopy(case)
            variant["id"] = "hole_skew_blur"
            variant["geometry"] = transformed(case["geometry"], matrix)
            variant["augmentation"] = {
                "rotation_degrees": 3,
                "blur_sigma": 0.6,
                "dpi_metadata": 150,
            }
            cases.append((variant, target))
            target = output / "hole_hybrid.pdf"
            doc = fitz.open()
            page = doc.new_page(width=319, height=500)
            page.insert_image(fitz.Rect(0, 0, 319, 453), filename=str(source))
            page.insert_text((10, 475), "TEST / THROUGH HOLE", fontsize=10)
            page.insert_text((10, 490), "测试图 / 通孔", fontname="china-s", fontsize=10)
            doc.save(target)
            doc.close()
            variant = copy.deepcopy(case)
            variant["id"] = "hole_hybrid"
            variant["texts"] += [{"text": "TEST / THROUGH HOLE"}, {"text": "测试图 / 通孔"}]
            variant["augmentation"] = {
                "hybrid_pdf": True,
                "bilingual_title": "added, not original annotation",
            }
            cases.append((variant, target))
    return cases


def evaluate(case, source, output):
    """Score actual public analyzer output; absent vector coordinates stay absent."""
    import cv2

    raw = analyze_source(str(source), use_cache=False, include_samples=True, ocr_policy="auto")
    analysis = raw["analysis"]
    geometry = [
        item for page in analysis.get("pages", []) for item in page.get("geometry_samples", [])
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
        "detector_deskewed_pixels" if source.suffix != ".pdf" else "pdf_points"
    )
    texts = [
        {"text": text}
        for page in analysis.get("pages", [])
        for text in page.get("text_samples", [])
    ]
    texts += [
        {"text": item["text"]}
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
    for suffix, value in [
        ("raw", raw),
        ("prediction", prediction),
        ("labels", case),
        ("metrics", result),
    ]:
        (output / f"{case['id']}.{suffix}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    overlay(source, case, prediction, output / f"{case['id']}.overlay.png")
    return result


def main():
    """Emit reproducible measurements; production gate fails on this partial pilot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "tests/fixtures/real_drawings/manifest.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--check-baseline", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    results = [
        evaluate(case, source, args.output)
        for case, source in prepare_cases(args.manifest, args.output)
    ]
    report = {
        "schema_version": 1,
        "corpus_status": "pilot_partial_labels_not_representative",
        "capabilities": vision_capabilities(),
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
        baseline = json.loads(args.check_baseline.read_text("utf-8"))
        by_id = {r["case_id"]: r for r in results}
        if set(by_id) != set(baseline["minimum_matched"]):
            raise ValueError("Benchmark case coverage changed; review the baseline")
        for case_id, floors in baseline["minimum_matched"].items():
            result = by_id[case_id]
            if not result["binding_valid"] or not result["rejects_false_completion_claim"]:
                raise ValueError(f"Evidence or false-completion regression: {case_id}")
            for category, minimum in floors.items():
                if result["metrics"][category]["matched"] < minimum:
                    raise ValueError(f"Recognition regression: {case_id}/{category}")
    return 1 if args.require_complete and not report["production_gate_passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
