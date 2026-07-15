"""Benchmark local OCR routing on a deterministic scanned-drawing fixture."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from cad_vision import analyze_source, vision_capabilities

ROOT = Path(__file__).resolve().parents[1]
WORK_DIR = ROOT / "data" / "ocr_benchmark"
FIXTURE_PATH = WORK_DIR / "engineering_scan.png"
REPORT_PATH = WORK_DIR / "latest.json"
EXPECTED_KINDS = {"diameter", "radius", "linear", "thread"}


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _create_fixture(path: Path) -> None:
    image = Image.new("L", (1500, 620), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 180, 1420, 520), outline="black", width=5)
    draw.ellipse((180, 260, 360, 440), outline="black", width=5)
    draw.text((90, 45), "DIA 15", fill="black", font=_font(58))
    draw.text((430, 45), "R20", fill="black", font=_font(58))
    draw.text((700, 45), "100 +/- 0.1", fill="black", font=_font(58))
    draw.text((1160, 45), "M10x1.5", fill="black", font=_font(58))
    draw.text((470, 270), "扫描工程图 OCR", fill="black", font=_font(64))
    rotated = image.rotate(1.5, resample=Image.Resampling.BICUBIC, fillcolor="white")
    rotated.save(path, dpi=(300, 300))


def _timed(**kwargs: Any) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    result = analyze_source(str(FIXTURE_PATH), **kwargs)
    return result, round((time.perf_counter() - started) * 1000.0, 3)


def main() -> int:
    """Create the fixture, run baseline/OCR/cache passes, and write JSON metrics."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    cache_dir = WORK_DIR / "cache"
    if cache_dir.is_dir():
        shutil.rmtree(cache_dir)
    _create_fixture(FIXTURE_PATH)

    baseline, baseline_ms = _timed(use_cache=False, include_samples=False, use_ocr=False)
    ocr_result, cold_ocr_ms = _timed(
        use_cache=False,
        include_samples=True,
        use_ocr=True,
        ocr_language="ch",
        ocr_min_confidence=0.45,
    )

    os.environ["MULTICAD_VISION_CACHE"] = str(cache_dir)
    _timed(use_cache=True, include_samples=False, use_ocr=True)
    warm_result, warm_cache_ms = _timed(use_cache=True, include_samples=False, use_ocr=True)

    ocr = ocr_result["analysis"].get("ocr", {})
    detected_kinds = {item["kind"] for item in ocr.get("dimensions", [])}
    report = {
        "capabilities": vision_capabilities()["ocr"],
        "fixture": str(FIXTURE_PATH),
        "baseline": {
            "elapsed_ms": baseline_ms,
            "text_count": baseline["analysis"].get("ocr", {}).get("text_count", 0),
        },
        "ocr": {
            "status": ocr.get("status"),
            "elapsed_ms": cold_ocr_ms,
            "text_count": ocr.get("text_count", 0),
            "dimension_count": ocr.get("dimension_count", 0),
            "detected_kinds": sorted(detected_kinds),
            "expected_kind_recall": round(len(detected_kinds & EXPECTED_KINDS) / 4, 3),
            "text_samples": ocr.get("text_samples", []),
            "error": ocr.get("error"),
        },
        "cache": {
            "cache_hit": warm_result["cache_hit"],
            "warm_request_ms": warm_cache_ms,
        },
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ocr.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
