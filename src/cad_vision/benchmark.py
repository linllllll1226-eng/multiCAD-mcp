"""Score independently labelled drawings without treating missing evidence as success."""

from __future__ import annotations

import math
import unicodedata
from typing import Any


def _text(value: object) -> str:
    """Normalize Unicode and whitespace, preserving semantic characters."""
    return " ".join(unicodedata.normalize("NFKC", str(value)).split()).casefold()


def _point(value: object) -> tuple[float, float] | None:
    """Accept only finite XY coordinates from untrusted prediction files."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if any(type(item) not in (int, float) or not math.isfinite(item) for item in value):
        return None
    return float(value[0]), float(value[1])


def _same_page(expected: dict, actual: dict) -> bool:
    """Require the labelled page; missing provenance cannot satisfy a page label."""
    return "page" not in expected or (
        type(actual.get("page")) is int and actual["page"] == expected["page"]
    )


def geometry_matches(expected: dict, actual: dict, tolerance: float) -> bool:
    """Compare geometry by coordinates, allowing reversed line endpoints."""
    if not _same_page(expected, actual) or expected.get("kind") != actual.get("kind"):
        return False
    kind = expected.get("kind")
    if kind == "line":
        a, b = _point(expected.get("start")), _point(expected.get("end"))
        c, d = _point(actual.get("start")), _point(actual.get("end"))
        if a is None or b is None or c is None or d is None:
            return False
        return (
            min(max(math.dist(a, c), math.dist(b, d)), max(math.dist(a, d), math.dist(b, c)))
            <= tolerance
        )
    if kind in {"circle", "arc"}:
        first, second = _point(expected.get("center")), _point(actual.get("center"))
        r1, r2 = expected.get("radius"), actual.get("radius")
        if (
            first is None
            or second is None
            or not isinstance(r1, (int, float))
            or isinstance(r1, bool)
            or not isinstance(r2, (int, float))
            or isinstance(r2, bool)
            or not math.isfinite(r1)
            or not math.isfinite(r2)
            or min(r1, r2) <= 0
        ):
            return False
        if math.dist(first, second) > tolerance or abs(r1 - r2) > tolerance:
            return False
        if kind == "arc":
            for field in ("start_angle", "end_angle"):
                left, right = expected.get(field), actual.get(field)
                if (
                    not isinstance(left, (int, float))
                    or isinstance(left, bool)
                    or not isinstance(right, (int, float))
                    or isinstance(right, bool)
                    or not math.isfinite(left)
                    or not math.isfinite(right)
                ):
                    return False
                if abs((left - right + 180) % 360 - 180) > 1:
                    return False
        return True
    return False


def dimension_matches(expected: dict, actual: dict) -> bool:
    """Keep dimension kind, numeric value and specified units/qualifiers distinct."""
    if not _same_page(expected, actual) or expected.get("kind") != actual.get("kind"):
        return False
    left, right = expected.get("value"), actual.get("value")
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        if not (math.isfinite(left) and math.isfinite(right) and abs(left - right) <= 1e-6):
            return False
    elif not isinstance(left, str) or not isinstance(right, str) or _text(left) != _text(right):
        return False
    return all(
        actual.get(key) == expected[key]
        for key in ("unit", "tolerance", "count", "depth")
        if key in expected
    )


def match_records(expected: list, actual: list, matches: Any) -> dict:
    """Find maximum one-to-one matches so duplicates cannot satisfy two labels."""
    edges = [[j for j, item in enumerate(actual) if matches(label, item)] for label in expected]
    owners: dict[int, int] = {}

    def augment(index: int, seen: set[int]) -> bool:
        """Find an alternating path to a free prediction."""
        for candidate in edges[index]:
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate not in owners or augment(owners[candidate], seen):
                owners[candidate] = index
                return True
        return False

    for index in range(len(expected)):
        augment(index, set())
    matched = set(owners.values())
    count = len(owners)
    return {
        "expected": len(expected),
        "detected": len(actual),
        "matched": count,
        "precision": count / len(actual) if actual else (1.0 if not expected else 0.0),
        "recall": count / len(expected) if expected else None,
        "missing_indices": sorted(set(range(len(expected))) - matched),
        "unmatched_prediction_indices": sorted(set(range(len(actual))) - owners.keys()),
        "pairs": sorted([[label, candidate] for candidate, label in owners.items()]),
    }


def score_case(case: dict, prediction: dict) -> dict:
    """Score all labelled categories and reject incomplete or unbound evidence."""
    tolerance = float(case.get("tolerance", 3.0))
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("tolerance must be positive and finite")
    categories = ("geometry", "texts", "dimensions", "required_annotations")
    for key in categories:
        for name, records in (("case", case.get(key, [])), ("prediction", prediction.get(key, []))):
            if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
                raise ValueError(f"{name} {key} must be a list of objects")
            if any(
                "page" in item and (type(item["page"]) is not int or item["page"] < 1)
                for item in records
            ):
                raise ValueError(f"{name} {key} page must be a positive integer")
        if key in {"texts", "required_annotations"} and any(
            not isinstance(item.get("text"), str) or not item["text"].strip()
            for item in case.get(key, [])
        ):
            raise ValueError(f"case {key} must contain nonempty text")
    geometry_ids = [item.get("id") for item in case.get("geometry", [])]
    if any(not isinstance(value, str) or not value for value in geometry_ids) or len(
        set(geometry_ids)
    ) != len(geometry_ids):
        raise ValueError("geometry labels require unique nonempty ids")
    pairs = case.get("close_line_pairs", [])
    if not isinstance(pairs, list) or any(
        not isinstance(pair, list)
        or len(pair) != 2
        or any(not isinstance(value, str) or value not in geometry_ids for value in pair)
        or pair[0] == pair[1]
        for pair in pairs
    ):
        raise ValueError("close-line pairs must reference two distinct geometry labels")
    binding = (
        prediction.get("source_sha256") == case["source_sha256"]
        and prediction.get("case_id") == case["id"]
        and prediction.get("coordinate_space") == case["coordinate_space"]
    )
    geometry = match_records(
        case.get("geometry", []),
        prediction.get("geometry", []),
        lambda a, b: geometry_matches(a, b, tolerance),
    )
    dimensions = match_records(
        case.get("dimensions", []), prediction.get("dimensions", []), dimension_matches
    )
    texts = match_records(
        case.get("texts", []),
        prediction.get("texts", []),
        lambda a, b: (
            _same_page(a, b)
            and isinstance(b.get("text"), str)
            and _text(a["text"]) == _text(b["text"])
        ),
    )
    annotations = match_records(
        case.get("required_annotations", []),
        prediction.get("required_annotations", []),
        lambda a, b: (
            _same_page(a, b)
            and isinstance(b.get("text"), str)
            and _text(a["text"]) == _text(b["text"])
        ),
    )
    groups = {
        "geometry": geometry,
        "typed_dimensions": dimensions,
        "text": texts,
        "required_annotations": annotations,
    }
    if not case.get("complete_annotation", False):
        for group in groups.values():
            group["precision"] = None
    ids = {index: item["id"] for index, item in enumerate(case.get("geometry", []))}
    matched_ids = {ids[pair[0]] for pair in geometry["pairs"]}
    merged_or_missing = sum(not set(pair) <= matched_ids for pair in pairs)
    total = sum(group["expected"] for group in groups.values())
    correct = sum(group["matched"] for group in groups.values())
    complete = bool(total) and correct == total
    stage = prediction.get("stage")
    recognition_complete = bool(
        binding
        and complete
        and case.get("complete_annotation") is True
        and prediction.get("truncated") is False
        and all(not group["unmatched_prediction_indices"] for group in groups.values())
    )
    # This scorer cannot authenticate a CAD session. A JSON assertion of readback
    # must never manufacture saved/reopened DWG acceptance.
    collision_count = 0
    by_id = {item["id"]: item for item in case.get("geometry", [])}
    for pair in pairs:
        if not set(pair) <= matched_ids and any(
            all(geometry_matches(by_id[item], candidate, tolerance) for item in pair)
            for candidate in prediction.get("geometry", [])
        ):
            collision_count += 1
    return {
        "case_id": case["id"],
        "binding_valid": binding,
        "stage": stage,
        "annotation_complete": case.get("complete_annotation", False),
        "metrics": groups,
        "labelled_completeness": correct / total if total else None,
        "close_line_pair_count": len(pairs),
        "close_line_loss_rate": merged_or_missing / len(pairs) if pairs else None,
        "close_line_merge_candidate_rate": collision_count / len(pairs) if pairs else None,
        "false_pass": bool(prediction.get("claimed_complete") is True and not recognition_complete),
        "recognition_complete": recognition_complete,
        "live_dwg_acceptance": "not_evaluated",
    }
