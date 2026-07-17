"""Tests for background-safe, off-screen task rendering."""

from __future__ import annotations

from cad_vision.audit_renderer import (
    audit_primitives,
    compare_expected_manifest,
    normalize_entities,
    render_task_audit,
)


def _records():
    return [
        {
            "handle": "L1",
            "current_layer": "AI_PREVIEW_OUTLINE",
            "approximate_reference": False,
            "actual": {
                "handle": "L1",
                "object_type": "AcDbLine",
                "layer": "AI_PREVIEW_OUTLINE",
                "start": [0, 0, 0],
                "end": [100, 0, 0],
            },
            "planned": {"entity_type": "line"},
        },
        {
            "handle": "C1",
            "current_layer": "AI_PREVIEW_OUTLINE",
            "approximate_reference": False,
            "actual": {
                "handle": "C1",
                "object_type": "AcDbCircle",
                "layer": "AI_PREVIEW_OUTLINE",
                "center": [50, 30, 0],
                "radius": 10,
            },
            "planned": {"entity_type": "circle"},
        },
    ]


def test_renderer_creates_nonempty_png_and_svg(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTICAD_AUDIT_OUTPUT_ROOT", str(tmp_path))
    result = render_task_audit(
        {
            "task_id": "task-background",
            "task_name": "Background audit",
            "drawing_name": "Drawing1.dwg",
        },
        _records(),
        width=800,
        height=600,
    )
    png = tmp_path / "task-background" / "audit.png"
    svg = tmp_path / "task-background" / "audit.svg"
    assert result["background_safe"] is True
    assert result["window_capture_used"] is False
    assert png.stat().st_size > 100
    assert svg.stat().st_size > 100
    assert result["audit"]["entity_count"] == 2


def test_duplicate_geometry_is_reported():
    records = _records()
    records.append(
        {**records[0], "handle": "L2", "actual": {**records[0]["actual"], "handle": "L2"}}
    )
    audit = audit_primitives(normalize_entities(records))
    duplicate = [
        warning for warning in audit["warnings"] if warning["code"] == "DUPLICATE_GEOMETRY"
    ]
    assert duplicate == [
        {"code": "DUPLICATE_GEOMETRY", "handles": ["L1", "L2"], "severity": "warning"}
    ]


def _dimension_record(handle, text_position, *, label_measurement=20):
    return {
        "handle": handle,
        "actual": {
            "object_type": "AcDbAlignedDimension",
            "layer": "AI_PREVIEW_DIM",
            "measurement": label_measurement,
            "text_position": [*text_position, 0],
            "text_height": 3.5,
        },
        "planned": {
            "entity_type": "aligned_dimension",
            "coordinates": {"start": [0, 0, 0], "end": [20, 0, 0]},
            "dimensions": {"measurement": label_measurement},
        },
    }


def test_dimension_text_overlap_is_a_failed_layout_gate():
    records = _records()
    records.extend(
        [
            _dimension_record("D1", [40, 12]),
            _dimension_record("D2", [42, 12]),
        ]
    )
    audit = audit_primitives(normalize_entities(records))
    assert audit["dimension_layout_passed"] is False
    assert any(warning["code"] == "DIMENSION_TEXT_OVERLAP" for warning in audit["warnings"])


def test_plain_text_is_rendered_and_audited_as_annotation():
    records = _records()
    records.append(
        {
            "handle": "T1",
            "actual": {
                "object_type": "AcDbText",
                "layer": "AI_PREVIEW_DIM",
                "position": [50, 0, 0],
                "text": "THRU",
                "text_height": 2.5,
            },
            "planned": {
                "entity_type": "text",
                "coordinates": {"position": [50, 0]},
                "dimensions": {"height": 2.5},
                "text_override": "THRU",
            },
        }
    )
    primitives = normalize_entities(records)
    note = primitives[-1]
    assert note.kind == "dimension"
    assert note.label == "THRU"
    audit = audit_primitives(primitives)
    assert any(
        warning["code"] == "DIMENSION_TEXT_GEOMETRY_COLLISION" and warning["handles"][0] == "T1"
        for warning in audit["warnings"]
    )


def test_dimension_text_geometry_collision_is_reported():
    records = _records()
    records.append(_dimension_record("D1", [50, 0]))
    audit = audit_primitives(normalize_entities(records))
    assert audit["dimension_layout_passed"] is False
    collision = next(
        warning
        for warning in audit["warnings"]
        if warning["code"] == "DIMENSION_TEXT_GEOMETRY_COLLISION"
    )
    assert collision["handles"] == ["D1", "L1"]


def test_dimension_too_far_from_geometry_is_reported():
    records = _records()
    records.append(_dimension_record("D1", [50, 80]))
    audit = audit_primitives(normalize_entities(records))
    warning = next(
        warning for warning in audit["warnings"] if warning["code"] == "DIMENSION_TOO_FAR"
    )
    assert warning["distance"] == 40.0
    assert warning["maximum_distance"] == 28.0


def test_collinear_but_disjoint_geometry_does_not_collide_with_dimension_text():
    records = [
        {
            "handle": "L1",
            "actual": {
                "object_type": "AcDbLine",
                "layer": "AI_PREVIEW_OUTLINE",
                "start": [100, 1.75, 0],
                "end": [120, 1.75, 0],
            },
        },
        _dimension_record("D1", [0, 0]),
    ]
    audit = audit_primitives(normalize_entities(records))
    assert not any(
        warning["code"] == "DIMENSION_TEXT_GEOMETRY_COLLISION" for warning in audit["warnings"]
    )


def test_polyline_and_dimensions_are_normalized():
    records = [
        {
            "handle": "P1",
            "actual": {
                "object_type": "AcDb2dPolyline",
                "layer": "AI_PREVIEW_OUTLINE",
                "coordinates": [0, 0, 0, 10, 0, 0, 10, 5, 0],
                "closed": False,
            },
        },
        {
            "handle": "D1",
            "actual": {
                "object_type": "AcDbDiametricDimension",
                "layer": "AI_PREVIEW_DIM",
                "measurement": 15,
                "text_position": [5, 8, 0],
            },
            "planned": {
                "entity_type": "diametric_dimension",
                "coordinates": {"chord_point": [0, 0, 0], "far_chord_point": [10, 0, 0]},
                "dimensions": {"measurement": 15},
            },
        },
    ]
    primitives = normalize_entities(records)
    assert primitives[0].points == ((0.0, 0.0), (10.0, 0.0), (10.0, 5.0))
    assert primitives[1].label == "Ø15"


def test_source_manifest_blocks_a_missing_line():
    primitives = normalize_entities(_records())
    result = compare_expected_manifest(
        primitives,
        {
            "tolerance": 0.001,
            "minimum_counts": {"line": 2, "circle": 1},
            "required_segments": [
                {"start": [0, 0], "end": [100, 0]},
                {"start": [0, 20], "end": [100, 20]},
            ],
            "required_circles": [{"center": [50, 30], "radius": 10}],
        },
    )
    assert result["provided"] is True
    assert result["passed"] is False
    assert result["failed_count"] == 2


def test_source_manifest_accepts_complete_required_geometry():
    primitives = normalize_entities(_records())
    result = compare_expected_manifest(
        primitives,
        {
            "minimum_counts": {"line": 1, "circle": 1},
            "required_segments": [{"start": [100, 0], "end": [0, 0]}],
            "required_circles": [{"center": [50, 30], "radius": 10}],
        },
    )
    assert result["passed"] is True
    assert result["failed_count"] == 0


def test_source_manifest_accepts_segments_from_closed_polyline():
    primitives = normalize_entities(
        [
            {
                "handle": "RECT",
                "actual": {
                    "object_type": "AcDb2dPolyline",
                    "layer": "AI_PREVIEW_OUTLINE",
                    "coordinates": [0, 0, 0, 10, 0, 0, 10, 5, 0, 0, 5, 0],
                    "closed": True,
                },
            }
        ]
    )
    result = compare_expected_manifest(
        primitives,
        {
            "required_segments": [
                {"start": [0, 0], "end": [10, 0]},
                {"start": [0, 5], "end": [0, 0]},
            ]
        },
    )
    assert result["passed"] is True
    assert result["failed_count"] == 0


def test_source_manifest_blocks_missing_required_annotation():
    result = compare_expected_manifest(
        normalize_entities(_records()),
        {"required_annotations": [{"text": "THRU", "layer": "AI_PREVIEW_DIM"}]},
    )
    assert result["passed"] is False
    assert result["failed_count"] == 1
    assert result["checks"][0]["check"] == "required_annotation:0"


def test_source_manifest_accepts_required_annotation_in_view_bounds():
    records = _records()
    records.append(
        {
            "handle": "T1",
            "actual": {
                "object_type": "AcDbText",
                "layer": "AI_PREVIEW_DIM",
                "position": [50, 25, 0],
                "text": "DEPTH 65",
                "text_height": 2.5,
            },
            "planned": {"entity_type": "text"},
        }
    )
    result = compare_expected_manifest(
        normalize_entities(records),
        {
            "required_annotations": [
                {
                    "text": "depth 65",
                    "layer": "AI_PREVIEW_DIM",
                    "bounds": [40, 20, 60, 30],
                }
            ]
        },
    )
    assert result["passed"] is True
    assert result["checks"][0]["actual"] == ["T1"]
