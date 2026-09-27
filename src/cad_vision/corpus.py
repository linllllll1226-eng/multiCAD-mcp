"""Validate private/public manifests and score recorded predictions without CAD."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .benchmark import score_case


def read_json(path: Path) -> dict:
    """Reject duplicate keys and nonfinite JSON constants instead of hiding them."""

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"nonfinite JSON constant: {value}")

    data = json.loads(
        path.read_text("utf-8"), object_pairs_hook=object_pairs, parse_constant=invalid_constant
    )
    if not isinstance(data, dict):
        raise ValueError("JSON document must be an object")
    return data


def file_sha256(path: Path) -> str:
    """Hash source bytes in bounded chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> list[tuple[dict, Path]]:
    """Reject ambiguous identities and invalid labels before analyzing any source."""
    data = read_json(path)
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
    ):
        raise ValueError("manifest schema_version must be 1")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("manifest cases must be a nonempty list")
    seen = set()
    result = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("manifest cases must be objects")
        identity = case.get("id")
        if (
            not isinstance(identity, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identity) is None
        ):
            raise ValueError("case id must be a safe filename component")
        if identity in seen:
            raise ValueError(f"duplicate case id: {identity}")
        seen.add(identity)
        filename = case.get("file")
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError(f"missing source filename: {identity}")
        source = (path.parent / filename).resolve(strict=True)
        if not source.is_file() or file_sha256(source) != case.get("source_sha256"):
            raise ValueError(f"source hash mismatch: {identity}")
        score_case(case, {})  # Validate labels without inventing recognition evidence.
        result.append((case, source))
    return result


def score_predictions(manifest: Path, predictions: Path) -> dict:
    """Score a bound one-file-per-case snapshot; JSON never proves a live session."""
    cases = load_manifest(manifest)
    expected = {case["id"] + ".json" for case, _ in cases}
    present = {path.name for path in predictions.glob("*.json")}
    if present != expected:
        raise ValueError(
            f"prediction case coverage mismatch: missing={sorted(expected - present)}, "
            f"unexpected={sorted(present - expected)}"
        )
    results = []
    for case, _ in cases:
        path = predictions / (case["id"] + ".json")
        prediction = read_json(path)
        metrics = score_case(case, prediction)
        results.append(
            {
                **metrics,
                "source_sha256": case["source_sha256"],
                "prediction_sha256": file_sha256(path),
            }
        )
    return {
        "schema_version": 1,
        "manifest_sha256": file_sha256(manifest),
        "case_count": len(results),
        "cases": results,
        "binding_valid": all(case["binding_valid"] for case in results),
        "completion_claim_count": sum(case["claimed_complete"] for case in results),
        "false_pass_count": sum(case["false_pass"] for case in results),
        "recognition_complete": all(case["recognition_complete"] for case in results),
        "live_dwg_acceptance": "not_evaluated",
        "release_approved": False,
        "limitations": [
            "Label completeness and independent review are declarations, not authenticated facts.",
            "Source/prediction hashes bind this local snapshot; "
            "they do not authenticate CAD sessions.",
            "No recognition result establishes persisted-DWG or production-release acceptance.",
        ],
    }


def check_baseline(results: list[dict], baseline: dict) -> None:
    """Enforce reviewed floors without allowing malformed values to disable checks."""
    floors_by_id = baseline.get("minimum_matched")
    if not isinstance(floors_by_id, dict) or not floors_by_id:
        raise ValueError("baseline minimum_matched must be a nonempty object")
    by_id = {result["case_id"]: result for result in results}
    if len(by_id) != len(results) or set(by_id) != set(floors_by_id):
        raise ValueError("Benchmark case coverage changed; review the baseline")
    for identity, floors in floors_by_id.items():
        if not isinstance(floors, dict) or not floors:
            raise ValueError(f"baseline must declare at least one floor: {identity}")
        result = by_id[identity]
        if not result["binding_valid"] or not result["rejects_false_completion_claim"]:
            raise ValueError(f"Evidence or false-completion regression: {identity}")
        for category, minimum in floors.items():
            if category not in result["metrics"] or type(minimum) is not int or minimum < 0:
                raise ValueError(f"invalid baseline floor: {identity}/{category}")
            if result["metrics"][category]["matched"] < minimum:
                raise ValueError(f"Recognition regression: {identity}/{category}")
