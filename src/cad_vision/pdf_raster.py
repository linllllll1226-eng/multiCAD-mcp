"""Bounded PDF image-region geometry in canonical, unrotated page points."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Any

from .image import _dependencies, analyze_image_geometry
from .pdf import _canonical_page_rect, _image_regions


def add_raster_geometry(
    path: Path,
    analysis: dict[str, Any],
    region_threshold: float = 0.02,
    *,
    max_regions: int = 20,
    max_region_pixels: int = 4_000_000,
    max_total_pixels: int = 16_000_000,
) -> None:
    """Attach approximate image-region candidates without replacing vector paths.

    Rendering includes any vector overlays in the region. Every candidate keeps
    its region provenance and requires confirmation; overlapping region outputs
    may duplicate geometry. Raster coverage and budgets do not prove completeness.
    Missing optional image dependencies leave vector extraction available.
    """
    if not any(page.get("raster_image_count", 0) for page in analysis.get("pages", [])):
        analysis["raster_geometry_status"] = "not_required"
        analysis["raster_geometry_complete"] = False
        return
    try:
        cv2, _np = _dependencies()
    except RuntimeError:
        analysis["raster_geometry_status"] = "unavailable"
        analysis["raster_geometry_complete"] = False
        return
    import fitz

    used_pixels = 0
    processed = 0
    omitted = 0
    errors = 0
    with (
        fitz.open(path) as document,
        tempfile.TemporaryDirectory(prefix="cad-pdf-geometry-") as root,
    ):
        for page_result in analysis.get("pages", []):
            page_number = page_result["page"]
            page = document[page_number - 1]
            page_area = max(_canonical_page_rect(page).get_area(), 1.0)
            regions = [
                region
                for region in _image_regions(page)
                if fitz.Rect(region).get_area() / page_area >= region_threshold
            ]
            samples: list[dict[str, Any]] = []
            reports: list[dict[str, Any]] = []
            for index, region in enumerate(regions):
                clip = (fitz.Rect(region) * page.rotation_matrix) & page.rect
                budget = min(max_region_pixels, max_total_pixels - used_pixels)
                if processed >= max_regions or budget < 16 or clip.is_empty:
                    omitted += 1
                    reports.append({"region": region, "status": "omitted_budget"})
                    continue
                # Leave a rounding margin for the integer pixmap bounds.
                scale = min(2.0, math.sqrt(budget / max(clip.get_area(), 1.0)) * 0.99)
                bounds = (clip * fitz.Matrix(scale, scale)).irect
                while bounds.width * bounds.height > budget:
                    scale *= 0.9
                    bounds = (clip * fitz.Matrix(scale, scale)).irect
                try:
                    pixmap = page.get_pixmap(
                        matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False
                    )
                    used_pixels += pixmap.width * pixmap.height
                    processed += 1
                    source = Path(root) / f"page-{page_number}-region-{index}.png"
                    pixmap.save(source)
                    detected = analyze_image_geometry(source)
                    width, height = detected["image_size_px"]
                    undo = cv2.invertAffineTransform(
                        cv2.getRotationMatrix2D(
                            (width / 2.0, height / 2.0), detected["estimated_skew_degrees"], 1.0
                        )
                    )

                    def point(x: float, y: float) -> list[float]:
                        raw_x = undo[0, 0] * x + undo[0, 1] * y + undo[0, 2]
                        raw_y = undo[1, 0] * x + undo[1, 1] * y + undo[1, 2]
                        canonical = (
                            fitz.Point((raw_x + pixmap.x) / scale, (raw_y + pixmap.y) / scale)
                            * page.derotation_matrix
                        )
                        return [round(canonical.x, 3), round(canonical.y, 3)]

                    provenance = {
                        "provider": "opencv_numpy",
                        "source": "rendered_pdf_region",
                        "page": page_number,
                        "region": region,
                        "render_scale": scale,
                        "deskew_degrees": detected["estimated_skew_degrees"],
                    }
                    for x1, y1, x2, y2 in (
                        detected["line_samples"] + detected["diagonal_line_samples"]
                    ):
                        samples.append(
                            {
                                "kind": "line",
                                "start": point(x1, y1),
                                "end": point(x2, y2),
                                "needs_confirmation": True,
                                "provenance": [provenance],
                            }
                        )
                    for x, y, radius in detected["circle_samples"]:
                        samples.append(
                            {
                                "kind": "circle",
                                "center": point(x, y),
                                "radius": round(radius / scale, 3),
                                "needs_confirmation": True,
                                "provenance": [provenance],
                            }
                        )
                    reports.append(
                        {
                            **provenance,
                            "status": "ok",
                            "image_size_px": [width, height],
                            "line_candidate_count": detected["line_candidate_count"],
                            "diagonal_line_candidate_count": detected[
                                "diagonal_line_candidate_count"
                            ],
                            "circle_candidate_count": detected["circle_candidate_count"],
                            "samples_truncated": detected["line_samples_truncated"]
                            or detected["diagonal_line_samples_truncated"]
                            or detected["circle_candidate_count"] > len(detected["circle_samples"]),
                        }
                    )
                except (RuntimeError, ValueError, OSError) as exc:
                    errors += 1
                    reports.append({"region": region, "status": "error", "error": str(exc)})
            page_result["raster_geometry_samples"] = samples
            page_result["raster_geometry_regions"] = reports
            page_result["raster_geometry_is_cad_plan"] = False
    analysis["raster_geometry_status"] = "partial" if omitted or errors else "ok"
    analysis["raster_geometry_complete"] = False
    analysis["raster_geometry_regions_processed"] = processed
    analysis["raster_geometry_regions_omitted"] = omitted
    analysis["raster_geometry_region_errors"] = errors
    analysis["raster_geometry_rendered_pixels"] = used_pixels
