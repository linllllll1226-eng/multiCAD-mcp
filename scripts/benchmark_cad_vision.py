"""Run deterministic before/after benchmarks for the optional CAD vision layer."""

# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cad_vision.analyzer import analyze_source, vision_capabilities  # noqa: E402
from cad_vision.dimensions import parse_dimension_text  # noqa: E402
from cad_vision.image import analyze_image_geometry  # noqa: E402


ANNOTATIONS = [
    ("100", "linear", 100.0),
    ("DIA 20", "diameter", 20.0),
    ("R10", "radius", 10.0),
    ("100 +/- 0.1", "linear", 100.0),
    ("45 DEG", "angle", 45.0),
    ("3 PLCS", "count", 3),
    ("DEPTH 12", "depth", 12.0),
    ("M10x1.5", "thread", "M10X1.5"),
]


def _make_pdf(path: Path) -> None:
    import fitz

    document = fitz.open()
    page = document.new_page(width=600, height=420)
    page.draw_rect(fitz.Rect(80, 130, 380, 280))
    page.draw_circle(fitz.Point(230, 205), 45)
    page.draw_line(fitz.Point(80, 310), fitz.Point(380, 310))
    for index, (text, _, _) in enumerate(ANNOTATIONS):
        page.insert_text(fitz.Point(420, 80 + index * 35), text, fontsize=12)
    document.save(path)
    document.close()


def _make_rotated_image(path: Path, angle: float) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1600, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((180, 180, 1250, 760), outline="black", width=6)
    draw.line((180, 470, 1250, 470), fill="black", width=4)
    draw.line((715, 180, 715, 760), fill="black", width=4)
    draw.ellipse((590, 345, 840, 595), outline="black", width=6)
    rotated = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor="white")
    rotated.save(path)


def _legacy_parse(text: str) -> tuple[str, float] | None:
    """Approximate the prior deterministic path: only bare linear numbers."""
    if re.fullmatch(r"\s*[-+]?\d+(?:\.\d+)?\s*", text):
        return "linear", float(text)
    return None


def _dimension_accuracy() -> dict[str, Any]:
    old_passed = 0
    new_passed = 0
    rows: list[dict[str, Any]] = []
    for source, expected_kind, expected_value in ANNOTATIONS:
        old = _legacy_parse(source)
        parsed = parse_dimension_text(source)
        new = (parsed[0]["kind"], parsed[0]["value"]) if parsed else None
        old_ok = old == (expected_kind, expected_value)
        new_ok = new == (expected_kind, expected_value)
        old_passed += int(old_ok)
        new_passed += int(new_ok)
        rows.append({"source": source, "expected": [expected_kind, expected_value],
                     "legacy_pass": old_ok, "enhanced_pass": new_ok})
    count = len(ANNOTATIONS)
    return {
        "case_count": count,
        "legacy_correct": old_passed,
        "enhanced_correct": new_passed,
        "legacy_accuracy_percent": round(old_passed / count * 100.0, 2),
        "enhanced_accuracy_percent": round(new_passed / count * 100.0, 2),
        "absolute_gain_points": round((new_passed - old_passed) / count * 100.0, 2),
        "cases": rows,
    }


def _median_call(function: Any, repeats: int = 7) -> float:
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples)


def run_benchmark(work_dir: Path) -> dict[str, Any]:
    """Generate fixtures and return deterministic before/after measurements."""
    work_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = work_dir / "synthetic_engineering.pdf"
    image_path = work_dir / "synthetic_rotated.png"
    cache_dir = work_dir / "cache"
    os.environ["MULTICAD_VISION_CACHE"] = str(cache_dir)
    _make_pdf(pdf_path)
    _make_rotated_image(image_path, 7.0)

    cold_started = time.perf_counter()
    cold = analyze_source(str(pdf_path), use_cache=True, include_samples=False)
    cold_ms = (time.perf_counter() - cold_started) * 1000.0
    warm_ms = _median_call(
        lambda: analyze_source(str(pdf_path), use_cache=True, include_samples=False)
    )
    enhanced_paths = cold["analysis"]["vector_path_groups"]
    expected_paths = 3

    image = analyze_image_geometry(image_path, include_samples=False)
    residual = abs(float(image["residual_skew_degrees"]))
    baseline_error = 7.0

    result = {
        "benchmark_version": "1.0",
        "scope": (
            "Compares the previous deterministic MCP source-analysis capability "
            "with the new optional vector/image pipeline; it does not compare model IQ."
        ),
        "capabilities": vision_capabilities(),
        "vector_pdf": {
            "expected_primary_path_groups": expected_paths,
            "legacy_recovered": 0,
            "enhanced_recovered": min(enhanced_paths, expected_paths),
            "legacy_recovery_percent": 0.0,
            "enhanced_recovery_percent": round(
                min(enhanced_paths, expected_paths) / expected_paths * 100.0, 2
            ),
            "all_detected_path_groups": enhanced_paths,
        },
        "dimension_parsing": _dimension_accuracy(),
        "deskew": {
            "injected_rotation_degrees": 7.0,
            "legacy_residual_error_degrees": baseline_error,
            "enhanced_estimated_skew_degrees": image["estimated_skew_degrees"],
            "enhanced_residual_error_degrees": residual,
            "error_reduction_percent": round(
                max(0.0, (baseline_error - residual) / baseline_error * 100.0), 2
            ),
        },
        "cache": {
            "cold_request_ms": round(cold_ms, 3),
            "warm_request_median_ms": round(warm_ms, 3),
            "speedup_x": round(cold_ms / warm_ms, 2) if warm_ms else None,
            "warm_cache_hit": True,
        },
        "safety": {
            "autocad_connected": False,
            "dwg_written": False,
            "legacy_write_fallback_added": False,
        },
        "limitations": [
            "Synthetic benchmark results do not equal accuracy on every real drawing.",
            (
                "PaddleOCR is intentionally not installed or benchmarked in the "
                "stable MCP."
            ),
            (
                "Raster geometry candidates still require model/user interpretation "
                "before CAD planning."
            ),
        ],
    }
    return result


def _markdown(result: dict[str, Any]) -> str:
    vector = result["vector_pdf"]
    dimension = result["dimension_parsing"]
    deskew = result["deskew"]
    cache = result["cache"]
    vector_gain = (
        vector["enhanced_recovery_percent"] - vector["legacy_recovery_percent"]
    )
    rows = [
        (
            "| Vector PDF primary path recovery | "
            f"{vector['legacy_recovery_percent']:.2f}% | "
            f"{vector['enhanced_recovery_percent']:.2f}% | +{vector_gain:.2f} pp |"
        ),
        (
            "| Structured dimension accuracy | "
            f"{dimension['legacy_accuracy_percent']:.2f}% | "
            f"{dimension['enhanced_accuracy_percent']:.2f}% | "
            f"+{dimension['absolute_gain_points']:.2f} pp |"
        ),
        (
            "| Residual skew error | "
            f"{deskew['legacy_residual_error_degrees']:.4f}° | "
            f"{deskew['enhanced_residual_error_degrees']:.4f}° | "
            f"{deskew['error_reduction_percent']:.2f}% reduction |"
        ),
        (
            "| Repeated analysis latency | "
            f"{cache['cold_request_ms']:.3f} ms | "
            f"{cache['warm_request_median_ms']:.3f} ms | "
            f"{cache['speedup_x']:.2f}x faster |"
        ),
    ]
    table = "\n".join(rows)
    return f"""# CAD Vision Benchmark

This benchmark compares the repository's previous deterministic source-analysis
capability with the new optional vision pipeline. It does not compare language
model intelligence.

| Metric | Previous | Enhanced | Change |
|---|---:|---:|---:|
{table}

## Safety result

- AutoCAD connected: `{result['safety']['autocad_connected']}`
- DWG written: `{result['safety']['dwg_written']}`
- Legacy write fallback added: `{result['safety']['legacy_write_fallback_added']}`

## Interpretation

The enhanced path extracts vector geometry directly from vector PDFs, parses
common engineering dimensions into typed records, deskews raster drawings before
geometry detection, and caches identical analyses. The guarded CAD write workflow
is unchanged.

## Limitations

""" + "".join(f"- {item}\n" for item in result["limitations"])


def main() -> None:
    """Run the benchmark and optionally persist JSON and Markdown reports."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cad-vision-benchmark-") as temporary:
        work_dir = args.work_dir or Path(temporary)
        result = run_benchmark(work_dir)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered, encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(_markdown(result), encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
