"""Verify that benchmark artifacts preserve geometry and source-page provenance."""

import hashlib
import importlib.util
import json
import math
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/benchmark_real_drawings.py"
SPEC = importlib.util.spec_from_file_location("real_benchmark_runner", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_similarity_transforms_arc_angles_radius_and_center():
    records = [
        {"kind": "arc", "center": [2, 3], "radius": 4, "start_angle": 350, "end_angle": 30},
        {"kind": "circle", "center": [2, 3], "radius": 4},
        {"kind": "line", "start": [2, 3], "end": [4, 3]},
    ]
    original = deepcopy(records)
    # Rotate +90 degrees in XY coordinates, scale by two, then translate.
    result = runner.transformed(records, [[0, -2, 10], [2, 0, 20]])
    assert result[0]["center"] == pytest.approx([4, 24])
    assert result[0]["radius"] == pytest.approx(8)
    assert result[0]["start_angle"] == pytest.approx(80)
    assert result[0]["end_angle"] == pytest.approx(120)
    assert result[1]["radius"] == pytest.approx(8)
    assert result[2]["end"] == pytest.approx([4, 28])
    assert records == original


def test_opencv_deskew_transform_matches_arc_endpoint():
    cv2 = pytest.importorskip("cv2")
    angle = 35
    center = [100, 80]
    radius = 30
    endpoint = [
        center[0] + radius * math.cos(math.radians(angle)),
        center[1] + radius * math.sin(math.radians(angle)),
    ]
    matrix = cv2.getRotationMatrix2D((150, 100), 17, 1)
    arc, line = runner.transformed(
        [
            {
                "kind": "arc",
                "center": center,
                "radius": radius,
                "start_angle": angle,
                "end_angle": 120,
            },
            {"kind": "line", "start": center, "end": endpoint},
        ],
        matrix,
    )
    reconstructed = [
        arc["center"][0] + arc["radius"] * math.cos(math.radians(arc["start_angle"])),
        arc["center"][1] + arc["radius"] * math.sin(math.radians(arc["start_angle"])),
    ]
    assert reconstructed == pytest.approx(line["end"])


@pytest.mark.parametrize(
    "matrix",
    [
        [[1, 1, 0], [0, 1, 0]],  # shear
        [[1, 0, 0], [0, -1, 0]],  # reflection changes arc sweep
        [[2, 0, 0], [0, 1, 0]],  # circle would become ellipse
        [[0, 0, 0], [0, 0, 0]],
        [[1, 0, float("nan")], [0, 1, 0]],
        [[1, 0], [0, 1]],
    ],
)
def test_non_similarity_transforms_are_rejected(matrix):
    with pytest.raises(ValueError, match="transform"):
        runner.transformed([], matrix)


def make_pdf(path, rotations=(0, 90)):
    fitz = pytest.importorskip("fitz")
    with fitz.open() as doc:
        for rotation in rotations:
            page = doc.new_page(width=180, height=120)
            page.draw_line((20, 40), (120, 40), color=(0, 0, 0))
            page.set_rotation(rotation)
        doc.save(path)


def test_pdf_overlays_separate_pages_and_use_unrotated_coordinates(tmp_path):
    from PIL import Image

    source = tmp_path / "drawing.PDF"
    make_pdf(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    case = {"geometry": [{"kind": "line", "start": [20, 40], "end": [120, 40], "page": 2}]}
    prediction = {
        "geometry": [
            {"kind": "line", "start": [20, 70], "end": [120, 70], "page": 1},
        ]
    }
    original = deepcopy((case, prediction))
    outputs = runner.overlay(source, case, prediction, tmp_path / "drawing.overlay.png")
    assert [p.name for p in outputs] == [
        "drawing.overlay.page-001.png",
        "drawing.overlay.page-002.png",
    ]
    with Image.open(outputs[0]) as first, Image.open(outputs[1]) as second:
        assert first.size == second.size == (180, 152)
        assert first.getpixel((60, 70)) == (0, 136, 255)
        assert second.getpixel((60, 70)) == (255, 255, 255)
        assert second.getpixel((60, 40)) == (224, 0, 64)
        assert first.getpixel((60, 40)) != (224, 0, 64)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    assert (case, prediction) == original


@pytest.mark.parametrize("page", [None, 0, True, 3])
@pytest.mark.parametrize("side", ["case", "prediction"])
def test_multi_page_overlays_reject_ambiguous_or_invalid_pages(tmp_path, page, side):
    source = tmp_path / "drawing.pdf"
    make_pdf(source)
    record = {"kind": "line", "start": [20, 40], "end": [120, 40]}
    if page is not None:
        record["page"] = page
    data = {"case": {"geometry": []}, "prediction": {"geometry": []}}
    data[side]["geometry"].append(record)
    with pytest.raises(ValueError, match="page provenance"):
        runner.overlay(source, data["case"], data["prediction"], tmp_path / "bad.png")
    assert not list(tmp_path.glob("bad*.png"))


def test_single_page_arc_overlay_keeps_legacy_filename(tmp_path):
    from PIL import Image

    source = tmp_path / "drawing.pdf"
    make_pdf(source, rotations=(270,))
    case = {
        "geometry": [
            {"kind": "arc", "center": [80, 70], "radius": 20, "start_angle": 0, "end_angle": 90},
        ]
    }
    target = tmp_path / "drawing.overlay.png"
    assert runner.overlay(source, case, {"geometry": []}, target) == [target]
    with Image.open(target) as im:
        assert im.getpixel((99, 70)) == (224, 0, 64)
        assert im.getpixel((80, 89)) == (224, 0, 64)
        assert im.getpixel((60, 70)) == (255, 255, 255)


def test_raster_arc_overlay(tmp_path):
    pytest.importorskip("cv2")
    from PIL import Image

    source = tmp_path / "drawing.png"
    Image.new("RGB", (160, 120), "white").save(source)
    case = {"geometry": []}
    prediction = {
        "geometry": [
            {"kind": "arc", "center": [80, 70], "radius": 20, "start_angle": 0, "end_angle": 90},
        ],
        "deskew": 0,
    }
    target = tmp_path / "drawing.overlay.png"
    runner.overlay(source, case, prediction, target)
    with Image.open(target) as im:
        assert im.getpixel((99, 70)) == (0, 136, 255)


def test_legend_does_not_cover_top_edge_geometry(tmp_path):
    pytest.importorskip("cv2")
    from PIL import Image

    source = tmp_path / "drawing.png"
    Image.new("RGB", (180, 120), "white").save(source)
    case = {"geometry": [{"kind": "line", "start": [20, 10], "end": [120, 10]}]}
    target = tmp_path / "drawing.overlay.png"
    runner.overlay(source, case, {"geometry": []}, target)
    with Image.open(target) as im:
        assert im.getpixel((60, 10)) == (224, 0, 64)


def test_evaluation_records_every_pdf_overlay(tmp_path):
    pytest.importorskip("cv2")
    source = tmp_path / "drawing.pdf"
    make_pdf(source)
    case = {
        "id": "two_pages",
        "complete_annotation": False,
        "geometry": [
            {"id": "edge", "kind": "line", "start": [20, 40], "end": [120, 40], "page": 2},
        ],
    }
    result = runner.evaluate(case, source, tmp_path)
    assert result["metrics"]["geometry"]["matched"] == 1
    assert result["overlays"] == [
        "two_pages.overlay.page-001.png",
        "two_pages.overlay.page-002.png",
    ]
    assert all((tmp_path / name).is_file() for name in result["overlays"])
    assert not result["recognition_complete"]
    assert result["live_dwg_acceptance"] == "not_evaluated"


def test_private_hole_id_does_not_implicitly_create_augmentations(tmp_path):
    pytest.importorskip("cv2")
    from PIL import Image

    source = tmp_path / "source.png"
    Image.new("RGB", (180, 120), "white").save(source)
    case = {
        "id": "hole",
        "file": source.name,
        "coordinate_space": "source_pixels",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "geometry": [],
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "cases": [case]}))
    assert len(runner.prepare_cases(manifest, tmp_path)) == 1
    case["augmentations"] = ["skew_blur", "hybrid_title"]
    manifest.write_text(json.dumps({"schema_version": 1, "cases": [case]}))
    cases = runner.prepare_cases(manifest, tmp_path)
    assert [value["id"] for value, _ in cases] == ["hole", "hole_skew_blur", "hole_hybrid"]


def test_generated_variant_id_collision_is_rejected_before_writing(tmp_path):
    pytest.importorskip("cv2")
    from PIL import Image

    source = tmp_path / "source.png"
    Image.new("RGB", (180, 120), "white").save(source)
    case = {
        "id": "hole",
        "file": source.name,
        "coordinate_space": "source_pixels",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "geometry": [],
        "augmentations": ["skew_blur"],
    }
    collision = {**case, "id": "hole_skew_blur", "augmentations": []}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "cases": [case, collision]}))
    with pytest.raises(ValueError, match="duplicate generated"):
        runner.prepare_cases(manifest, tmp_path)
    assert not (tmp_path / "hole_skew_blur.png").exists()
