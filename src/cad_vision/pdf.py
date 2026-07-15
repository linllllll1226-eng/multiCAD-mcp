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


def extract_vector_pdf(
    path: Path,
    max_pages: int = 10,
    include_samples: bool = True,
    sample_limit: int = 40,
) -> dict[str, Any]:
    """Extract vector paths, text, and dimension candidates without raster OCR."""
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - dependency-specific
        raise RuntimeError(
            "Vector PDF analysis requires the optional 'vision' dependencies"
        ) from exc

    pages: list[dict[str, Any]] = []
    dimensions: list[dict[str, Any]] = []
    vector_item_counts: Counter[str] = Counter()
    total_words = 0
    total_paths = 0

    with fitz.open(path) as document:
        page_count = min(len(document), max_pages)
        for page_number in range(page_count):
            page = document[page_number]
            drawings = page.get_drawings()
            words = page.get_text("words")
            blocks = page.get_text("blocks")
            total_words += len(words)
            total_paths += len(drawings)

            page_dimensions: list[dict[str, Any]] = []
            for block in blocks:
                for line in str(block[4]).splitlines():
                    for parsed in parse_dimension_text(line):
                        parsed["page"] = page_number + 1
                        parsed["source_text"] = line.strip()
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

            page_result: dict[str, Any] = {
                "page": page_number + 1,
                "size_points": [round(page.rect.width, 3), round(page.rect.height, 3)],
                "vector_path_groups": len(drawings),
                "text_word_count": len(words),
                "dimension_count": len(page_dimensions),
            }
            if include_samples:
                page_result["vector_samples"] = samples
                page_result["text_samples"] = [str(block[4]).strip() for block in blocks[:20]]
            pages.append(page_result)

    return {
        "mode": "vector_pdf",
        "page_count_analyzed": len(pages),
        "vector_path_groups": total_paths,
        "vector_item_counts": dict(sorted(vector_item_counts.items())),
        "text_word_count": total_words,
        "dimensions": dimensions[:200],
        "pages": pages,
    }
