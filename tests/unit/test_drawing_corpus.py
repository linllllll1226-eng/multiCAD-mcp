"""Reject malformed private evaluation inputs and false recognition claims."""

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from cad_vision.corpus import check_baseline, load_manifest, read_json, score_predictions


def fixture(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"source identity fixture; image decoding is not part of this scorer")
    case = {
        "id": "sample",
        "file": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "coordinate_space": "source_pixels",
        "complete_annotation": True,
        "geometry": [{"id": "edge", "kind": "line", "start": [0, 0], "end": [10, 0]}],
        "texts": [{"text": "DEBURR", "bbox": [0, 10, 40, 20]}],
        "dimensions": [{"kind": "linear", "value": 10}],
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "cases": [case]}))
    directory = tmp_path / "predictions"
    directory.mkdir()
    prediction = {
        **deepcopy(case),
        "case_id": "sample",
        "truncated": False,
        "claimed_complete": False,
    }
    (directory / "sample.json").write_text(json.dumps(prediction))
    return manifest, directory, case, prediction


def test_matching_predictions_are_never_release_or_live_acceptance(tmp_path):
    manifest, directory, _, _ = fixture(tmp_path)
    report = score_predictions(manifest, directory)
    assert report["recognition_complete"]
    assert report["completion_claim_count"] == 0
    assert report["false_pass_count"] == 0
    assert not report["release_approved"]
    assert report["live_dwg_acceptance"] == "not_evaluated"
    assert report["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_missing_dimension_is_counted_as_false_claim(tmp_path):
    manifest, directory, _, prediction = fixture(tmp_path)
    prediction.update({"claimed_complete": True, "dimensions": []})
    (directory / "sample.json").write_text(json.dumps(prediction))
    report = score_predictions(manifest, directory)
    assert report["completion_claim_count"] == report["false_pass_count"] == 1
    assert not report["recognition_complete"]


@pytest.mark.parametrize("mutation", ["empty", "duplicate", "unsafe_id", "hash", "schema"])
def test_invalid_manifests_fail_before_scoring(tmp_path, mutation):
    manifest, _, case, _ = fixture(tmp_path)
    payload = {"schema_version": 1, "cases": [case]}
    if mutation == "empty":
        payload["cases"] = []
    elif mutation == "duplicate":
        payload["cases"] = [case, case]
    elif mutation == "unsafe_id":
        case["id"] = "../escape"
    elif mutation == "hash":
        case["source_sha256"] = "a" * 64
    else:
        payload["schema_version"] = True
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_manifest(manifest)


@pytest.mark.parametrize("mutation", ["missing", "extra", "changed_source"])
def test_prediction_coverage_and_changed_source_fail(tmp_path, mutation):
    manifest, directory, _, _ = fixture(tmp_path)
    if mutation == "missing":
        (directory / "sample.json").unlink()
    elif mutation == "extra":
        (directory / "extra.json").write_text("{}")
    else:
        (tmp_path / "source.png").write_bytes(b"changed source")
    with pytest.raises(ValueError):
        score_predictions(manifest, directory)


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', "[]"])
def test_ambiguous_json_rejected(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        read_json(path)


@pytest.mark.parametrize("minimum", [None, True, -1, 1.5, "1", float("nan")])
def test_malformed_baseline_floors_fail(minimum):
    results = [
        {
            "case_id": "sample",
            "binding_valid": True,
            "rejects_false_completion_claim": True,
            "metrics": {"geometry": {"matched": 1}},
        }
    ]
    with pytest.raises(ValueError, match="invalid baseline"):
        check_baseline(results, {"minimum_matched": {"sample": {"geometry": minimum}}})


def test_baseline_rejects_duplicates_empty_floors_and_real_regressions():
    result = {
        "case_id": "sample",
        "binding_valid": True,
        "rejects_false_completion_claim": True,
        "metrics": {"geometry": {"matched": 1}},
    }
    baseline = {"minimum_matched": {"sample": {"geometry": 1}}}
    check_baseline([result], baseline)
    with pytest.raises(ValueError, match="coverage"):
        check_baseline([result, result], baseline)
    with pytest.raises(ValueError, match="at least one"):
        check_baseline([result], {"minimum_matched": {"sample": {}}})
    with pytest.raises(ValueError, match="regression"):
        check_baseline([result], {"minimum_matched": {"sample": {"geometry": 2}}})


def test_cli_exit_codes_and_no_overwrite(tmp_path):
    manifest, directory, case, prediction = fixture(tmp_path)
    script = Path(__file__).resolve().parents[2] / "scripts/score_drawing_predictions.py"
    output = tmp_path / "report.json"
    command = [
        sys.executable,
        str(script),
        "--manifest",
        str(manifest),
        "--predictions",
        str(directory),
        "--output",
        str(output),
        "--require-recognition-complete",
    ]
    assert subprocess.run(command, capture_output=True).returncode == 0
    saved = output.read_bytes()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert output.read_bytes() == saved
    case["complete_annotation"] = False
    manifest.write_text(json.dumps({"schema_version": 1, "cases": [case]}))
    command[command.index(str(output))] = str(tmp_path / "incomplete.json")
    assert subprocess.run(command, capture_output=True).returncode == 1
