"""Prevent optimistic scores from incomplete, duplicated or unbound evidence."""

from copy import deepcopy

import pytest

from cad_vision.benchmark import dimension_matches, geometry_matches, match_records, score_case


def fixture():
    """Return independently specified labels and a matching prediction."""
    case = {
        "id": "sample",
        "source_sha256": "a" * 64,
        "coordinate_space": "pixels",
        "tolerance": 0.1,
        "complete_annotation": True,
        "geometry": [{"id": "edge", "kind": "line", "start": [0, 0], "end": [10, 0]}],
        "texts": [{"text": "THRU"}],
        "dimensions": [{"kind": "diameter", "value": 5}],
    }
    prediction = {
        **deepcopy(case),
        "case_id": "sample",
        "truncated": False,
        "claimed_complete": True,
        "stage": "source_analysis",
    }
    return case, prediction


def test_full_labels_are_not_live_acceptance():
    """Even a matching JSON file proves no CAD lifecycle."""
    case, prediction = fixture()
    result = score_case(case, prediction)
    assert result["recognition_complete"]
    assert result["live_dwg_acceptance"] == "not_evaluated"


@pytest.mark.parametrize(
    "change",
    [
        {"geometry": []},
        {"texts": []},
        {"dimensions": []},
        {"truncated": True},
        {"source_sha256": "wrong"},
        {"case_id": "wrong"},
        {"coordinate_space": "mm"},
    ],
)
def test_missing_evidence_is_a_false_pass(change):
    """A completion claim fails when any mandatory evidence is absent."""
    case, prediction = fixture()
    prediction.update(change)
    assert score_case(case, prediction)["false_pass"]


def test_partial_annotation_has_no_precision_or_full_pass():
    """Unlabelled objects cannot be counted as false positives."""
    case, prediction = fixture()
    case["complete_annotation"] = False
    result = score_case(case, prediction)
    assert result["metrics"]["geometry"]["precision"] is None
    assert not result["recognition_complete"]


def test_duplicates_and_extra_objects_fail_completeness():
    """One prediction cannot cover two labels; extras also preclude exact completeness."""
    assert match_records([1, 1], [1], lambda a, b: a == b)["matched"] == 1
    case, prediction = fixture()
    prediction["geometry"] *= 2
    assert score_case(case, prediction)["false_pass"]


def test_maximum_matching_avoids_greedy_order_bias():
    """A flexible first label must not steal the only match for the second."""
    result = match_records([0, 1], [0, 1], lambda a, b: a == 0 or b == 0)
    assert result["matched"] == 2


def test_wrong_dimension_semantics_fail():
    """Numerically equal radii and diameters are different annotations."""
    assert not dimension_matches({"kind": "radius", "value": 5}, {"kind": "diameter", "value": 5})
    assert not dimension_matches(
        {"kind": "linear", "value": 5, "unit": "mm"}, {"kind": "linear", "value": 5, "unit": "inch"}
    )
    assert not dimension_matches({"kind": "linear", "value": 1}, {"kind": "linear", "value": True})


def test_geometry_direction_angles_and_nonfinite():
    """Reverse lines and wrapped arcs match, but invalid geometry never does."""
    line = {"kind": "line", "start": [0, 0], "end": [10, 0]}
    assert geometry_matches(line, {**line, "start": [10, 0], "end": [0, 0]}, 0.01)
    assert not geometry_matches(line, {**line, "start": [float("nan"), 0]}, 0.01)
    arc = {"kind": "arc", "center": [0, 0], "radius": 5, "start_angle": 0, "end_angle": 90}
    assert geometry_matches(arc, {**arc, "start_angle": 360}, 0.01)
    assert not geometry_matches(arc, {**arc, "radius": True}, 0.01)


def test_close_line_missing_is_not_necessarily_merge():
    """Report missing boundaries separately from a shared candidate collision."""
    case, prediction = fixture()
    case["geometry"].append({"id": "second", "kind": "line", "start": [0, 1], "end": [10, 1]})
    case["close_line_pairs"] = [["edge", "second"]]
    result = score_case(case, prediction)
    assert result["close_line_loss_rate"] == 1
    assert result["close_line_merge_candidate_rate"] == 0
    case["tolerance"] = 1.1
    assert score_case(case, prediction)["close_line_merge_candidate_rate"] == 1


@pytest.mark.parametrize("key", ["geometry", "texts", "dimensions", "required_annotations"])
def test_malformed_predictions_rejected(key):
    """Invalid prediction records must not accidentally pass or be omitted."""
    case, prediction = fixture()
    prediction[key] = [None]
    with pytest.raises(ValueError):
        score_case(case, prediction)


def test_missing_required_annotation_rejects_completion():
    """Matched geometry and dimensions cannot compensate for an omitted callout."""
    case, prediction = fixture()
    case["required_annotations"] = [{"text": "去毛刺 / DEBURR", "page": 1}]
    result = score_case(case, prediction)
    assert result["false_pass"]
    assert result["metrics"]["required_annotations"]["missing_indices"] == [0]
    prediction["required_annotations"] = [{"text": "去毛刺 / DEBURR", "page": 1}]
    assert score_case(case, prediction)["recognition_complete"]


@pytest.mark.parametrize("key", ["geometry", "texts", "dimensions", "required_annotations"])
@pytest.mark.parametrize("page", [None, 2])
def test_cross_page_predictions_never_satisfy_labels(key, page):
    """Identical content on another page is not evidence for the labelled page."""
    case, prediction = fixture()
    if key == "required_annotations":
        case[key] = [{"text": "DEBURR"}]
        prediction[key] = [{"text": "DEBURR"}]
    case[key][0]["page"] = 1
    if page is not None:
        prediction[key][0]["page"] = page
    assert score_case(case, prediction)["false_pass"]


@pytest.mark.parametrize("page", [True, 0, -1, 1.5, "1"])
def test_invalid_page_provenance_is_rejected(page):
    """Boolean, fractional and nonpositive pages cannot alias page one."""
    case, prediction = fixture()
    prediction["geometry"][0]["page"] = page
    with pytest.raises(ValueError, match="positive integer"):
        score_case(case, prediction)


def test_invalid_labels_and_close_pairs_are_rejected():
    """Ambiguous ids and malformed pairs must not inflate completeness scores."""
    case, prediction = fixture()
    case["geometry"] *= 2
    with pytest.raises(ValueError, match="unique"):
        score_case(case, prediction)
    case, prediction = fixture()
    case["close_line_pairs"] = [["edge", "missing"]]
    with pytest.raises(ValueError, match="distinct"):
        score_case(case, prediction)
    case["close_line_pairs"] = []
    case["required_annotations"] = [{"text": ""}]
    with pytest.raises(ValueError, match="nonempty"):
        score_case(case, prediction)
