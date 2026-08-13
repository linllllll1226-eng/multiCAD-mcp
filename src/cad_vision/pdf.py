"""Vector-first PDF extraction with an optional PyMuPDF dependency."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .dimensions import parse_dimension_text


def _rect(rect: Any) -> list[float]:
    return [
        round(float(rect.x0), 3),
        round(float(rect.y0), 3),
        round(float(rect.x1), 3),
        round(float(rect.y1), 3),
    ]


def _clipped_rect(rect: Any, page_rect: Any) -> list[float] | None:
    x0 = max(float(rect.x0), float(page_rect.x0))
    y0 = max(float(rect.y0), float(page_rect.y0))
    x1 = min(float(rect.x1), float(page_rect.x1))
    y1 = min(float(rect.y1), float(page_rect.y1))
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _canonical_page_rect(page: Any) -> Any:
    """Return the unrotated crop-page rectangle used by text/vector APIs."""
    return page.rect * page.derotation_matrix


def _union_area(rectangles: list[list[float]]) -> float:
    """Return exact axis-aligned union area for a bounded rectangle list."""
    if not rectangles:
        return 0.0
    x_values = sorted({value for rectangle in rectangles for value in (rectangle[0], rectangle[2])})
    area = 0.0
    for left, right in zip(x_values, x_values[1:]):
        if right <= left:
            continue
        intervals = sorted(
            (rectangle[1], rectangle[3])
            for rectangle in rectangles
            if rectangle[0] < right and rectangle[2] > left
        )
        if not intervals:
            continue
        covered = 0.0
        start, end = intervals[0]
        for next_start, next_end in intervals[1:]:
            if next_start > end:
                covered += end - start
                start, end = next_start, next_end
            else:
                end = max(end, next_end)
        covered += end - start
        area += (right - left) * covered
    return area


def _image_regions(page: Any) -> list[list[float]]:
    """Return embedded-image boxes in canonical unrotated page coordinates."""
    regions: list[list[float]] = []
    seen: set[tuple[float, float, float, float]] = set()
    canonical_page_rect = _canonical_page_rect(page)
    for image in page.get_images(full=True):
        try:
            rectangles = page.get_image_rects(int(image[0]))
        except Exception:
            continue
        for rectangle in rectangles:
            # get_image_rects(), like get_text() and get_drawings(), reports
            # unrotated coordinates.  Page.rect is rotated, so clip against its
            # derotated counterpart to keep every evidence source canonical.
            clipped = _clipped_rect(rectangle, canonical_page_rect)
            if clipped is None:
                continue
            key = (
                round(clipped[0], 6),
                round(clipped[1], 6),
                round(clipped[2], 6),
                round(clipped[3], 6),
            )
            if key not in seen:
                seen.add(key)
                regions.append(clipped)
    return regions


def _text_lines(page: Any) -> list[dict[str, Any]]:
    """Return vector text at line granularity in canonical page coordinates."""
    lines: list[dict[str, Any]] = []
    text_tree = page.get_text("dict")
    for block in text_tree.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(str(span.get("text", "")) for span in spans).strip()
            bbox = line.get("bbox")
            if not text or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            lines.append(
                {
                    "text": text,
                    "bbox": [round(float(value), 3) for value in bbox],
                }
            )
    return lines


def extract_vector_pdf(
    path: Path,
    max_pages: int = 10,
    include_samples: bool = True,
    sample_limit: int = 40,
    raster_page_threshold: float = 0.15,
    raster_region_threshold: float = 0.02,
    default_unit: str | None = None,
    unit_source: str | None = None,
) -> dict[str, Any]:
    """Extract vector paths, text, and dimension candidates without raster OCR."""
    try:
        import fitz  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency-specific
        raise RuntimeError(
            "Vector PDF analysis requires the optional 'vision' dependencies"
        ) from exc

    pages: list[dict[str, Any]] = []
    dimensions: list[dict[str, Any]] = []
    vector_item_counts: Counter[str] = Counter()
    total_words = 0
    total_paths = 0
    ocr_targets: list[dict[str, Any]] = []

    with fitz.open(path) as document:
        page_count = min(len(document), max_pages)
        for page_number in range(page_count):
            page = document[page_number]
            canonical_page_rect = _canonical_page_rect(page)
            drawings = page.get_drawings()
            words = page.get_text("words")
            text_lines = _text_lines(page)
            image_regions = _image_regions(page)
            page_area = max(
                float(canonical_page_rect.width) * float(canonical_page_rect.height),
                1.0,
            )
            raster_coverage = min(_union_area(image_regions) / page_area, 1.0)
            qualifying_regions = [
                region
                for region in image_regions
                if ((region[2] - region[0]) * (region[3] - region[1])) / page_area
                >= raster_region_threshold
            ]
            total_words += len(words)
            total_paths += len(drawings)

            page_dimensions: list[dict[str, Any]] = []
            for text_line in text_lines:
                line_text = text_line["text"]
                line_bbox = text_line["bbox"]
                for parsed in parse_dimension_text(
                    line_text,
                    default_unit=default_unit,
                    unit_source=unit_source,
                ):
                    parsed["page"] = page_number + 1
                    parsed["source_text"] = line_text
                    parsed["bbox"] = line_bbox
                    parsed["provenance"] = [
                        {
                            "provider": "pymupdf",
                            "source": "vector_text",
                            "page": page_number + 1,
                            "bbox": line_bbox,
                        }
                    ]
                    page_dimensions.append(parsed)
                    dimensions.append(parsed)

            samples: list[dict[str, Any]] = []
            for drawing in drawings:
                for item in drawing.get("items", []):
                    kind = str(item[0])
                    vector_item_counts[kind] += 1
                if include_samples and len(samples) < sample_limit:
                    samples.append(
                        {
                            "bbox": _rect(drawing["rect"]),
                            "item_kinds": [str(item[0]) for item in drawing["items"]],
                            "closed": bool(drawing.get("closePath", False)),
                        }
                    )

            if raster_coverage >= raster_page_threshold:
                ocr_recommendation = "page"
                recommendation_reason = "raster_coverage"
                target_regions: list[list[float]] = []
            elif not words:
                ocr_recommendation = "page"
                recommendation_reason = "no_vector_text"
                target_regions = []
            elif qualifying_regions:
                ocr_recommendation = "regions"
                recommendation_reason = "embedded_raster_regions"
                target_regions = [
                    [round(value, 3) for value in item] for item in qualifying_regions
                ]
            else:
                ocr_recommendation = "none"
                recommendation_reason = "vector_coverage_sufficient"
                target_regions = []

            if ocr_recommendation != "none":
                ocr_targets.append(
                    {
                        "page": page_number + 1,
                        "mode": ocr_recommendation,
                        "regions": target_regions,
                        "reason": recommendation_reason,
                    }
                )

            page_result: dict[str, Any] = {
                "page": page_number + 1,
                "size_points": [
                    round(canonical_page_rect.width, 3),
                    round(canonical_page_rect.height, 3),
                ],
                "vector_path_groups": len(drawings),
                "text_word_count": len(words),
                "dimension_count": len(page_dimensions),
                "raster_image_count": len(image_regions),
                "raster_coverage_ratio": round(raster_coverage, 6),
                "ocr_recommendation": ocr_recommendation,
                "ocr_reason": recommendation_reason,
            }
            if target_regions:
                page_result["ocr_regions"] = target_regions
            if include_samples:
                page_result["vector_samples"] = samples
                page_result["text_samples"] = [item["text"] for item in text_lines[:20]]
                page_result["image_samples"] = [
                    [round(value, 3) for value in region] for region in image_regions[:20]
                ]
            pages.append(page_result)

    return {
        "mode": "vector_pdf",
        "page_count_analyzed": len(pages),
        "vector_path_groups": total_paths,
        "vector_item_counts": dict(sorted(vector_item_counts.items())),
        "text_word_count": total_words,
        "dimensions": dimensions[:200],
        "pages": pages,
        "ocr_targets": ocr_targets,
        "raster_page_threshold": raster_page_threshold,
        "raster_region_threshold": raster_region_threshold,
    }
