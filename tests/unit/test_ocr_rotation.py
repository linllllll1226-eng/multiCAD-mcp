"""Exercise real pixel transforms without claiming simulated text is OCR accuracy."""

from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from cad_vision.analyzer import _merge_dimension_evidence, analyze_source
from cad_vision.ocr import _box, extract_ocr
from cad_vision.ocr_rotation import merge_orientation_evidence, normalized_rotations


def raster(path):
    image = Image.new("RGB", (140, 100), "white")
    ImageDraw.Draw(image).rectangle((30, 20, 49, 59), fill="black")
    image.save(path)


class PixelPipeline:
    """Locate fixture ink in actual transformed pixels; text is deliberately stubbed."""

    def __init__(self, conflicting=False):
        """Track inputs and optionally simulate disagreement between orientations."""
        self.conflicting = conflicting
        self.paths = []

    def predict(self, source, **kwargs):
        self.paths.append(Path(source))
        with Image.open(source) as image:
            pixels = image.convert("L")
            xs, ys = [], []
            for y in range(pixels.height):
                for x in range(pixels.width):
                    if pixels.getpixel((x, y)) < 128:
                        xs.append(x)
                        ys.append(y)
        text = "2" if self.conflicting and "probe-" not in Path(source).name else "12"
        return [
            {
                "rec_texts": [text],
                "rec_scores": [0.95],
                "rec_boxes": [[min(xs), min(ys), max(xs) + 1, max(ys) + 1]],
            }
        ]


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_rotated_pixels_map_to_original_box_and_agree(tmp_path, angle):
    source = tmp_path / "drawing.png"
    raster(source)
    original = source.read_bytes()
    pipeline = PixelPipeline()
    result = extract_ocr(source, rotation_angles=[angle], pipeline_factory=lambda *_: pipeline)
    assert result["status"] == "ok"
    assert result["text_count"] == result["dimension_count"] == 1
    text = result["text_samples"][0]
    assert text["bbox"] == [30, 20, 50, 60]
    assert text["orientation_degrees"] == [0, angle]
    assert len(text["orientation_observations"]) == 2
    assert not text["needs_confirmation"]
    assert source.read_bytes() == original
    assert len(pipeline.paths) == 2
    assert not pipeline.paths[1].exists()


def test_disagreeing_digits_are_both_retained_and_unconfirmed(tmp_path):
    source = tmp_path / "drawing.png"
    raster(source)
    pipeline = PixelPipeline(conflicting=True)
    result = extract_ocr(source, rotation_angles=[90, 270], pipeline_factory=lambda *_: pipeline)
    assert result["status"] == "ok" and result["orientation_review_required"]
    assert {x["value"] for x in result["dimensions"]} == {2, 12}
    assert all(x["needs_confirmation"] for x in result["dimensions"])
    assert all(
        "orientation_disagreement" in x["confirmation_reasons"] for x in result["dimensions"]
    )
    assert result["text_count"] == 2


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_pdf_crop_and_display_rotation_compose_with_probe(tmp_path, rotation):
    fitz = pytest.importorskip("fitz")
    source = tmp_path / "drawing.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=240, height=200)
        page.set_cropbox(fitz.Rect(20, 30, 220, 180))
        page.draw_rect(fitz.Rect(40, 50, 60, 90), color=None, fill=(0, 0, 0))
        page.set_rotation(rotation)
        doc.save(source)
    pipeline = PixelPipeline()
    result = extract_ocr(
        source,
        rotation_angles=[90, 270],
        page_numbers=[],
        regions={1: [[30, 40, 80, 110]]},
        pipeline_factory=lambda *_: pipeline,
    )
    assert result["status"] == "ok"
    assert result["text_count"] == 1
    assert result["text_samples"][0]["bbox"] == pytest.approx([40, 50, 60, 90], abs=0.6)
    assert result["text_samples"][0]["page"] == 1
    assert result["dimensions"][0]["provenance"][0]["orientation_degrees"] == [0, 90, 270]


def observation(text="12", page=1, x=10, rotation=0):
    return {
        "text": text,
        "confidence": 0.9,
        "bbox": [x, 10, x + 10, 30],
        "page": page,
        "rotation_degrees": rotation,
    }


def test_equal_values_on_distinct_pages_locations_or_same_pass_are_not_collapsed():
    inputs = [
        observation(),
        observation(),
        observation(page=2),
        observation(x=40),
        observation(rotation=90),
    ]
    original = deepcopy(inputs)
    assert len(merge_orientation_evidence(inputs)) == 4
    assert inputs == original


def test_rotation_only_candidates_remain_unconfirmed_even_if_probes_agree():
    result = merge_orientation_evidence([observation(rotation=90), observation(rotation=270)])
    assert len(result) == 1
    assert result[0]["confirmation_reasons"] == ["rotation_only_candidate"]


def test_vector_merge_preserves_orientation_review_requirement():
    vector = {"kind": "linear", "value": 12, "page": 1, "bbox": [10, 10, 20, 30]}
    ocr = {
        **vector,
        "needs_confirmation": True,
        "confirmation_reasons": ["orientation_disagreement"],
        "orientation_conflicts": ["2"],
    }
    result = _merge_dimension_evidence([vector], [ocr])
    assert len(result) == 1
    assert result[0]["needs_confirmation"]
    assert result[0]["orientation_conflicts"] == ["2"]


@pytest.mark.parametrize("value", [[0], [45], [True], ["90"], "90", (90,)])
def test_invalid_rotations_reject_before_provider_execution(value):
    with pytest.raises(ValueError, match="ocr_rotation_angles"):
        normalized_rotations(value)


def test_rotation_option_changes_cache_identity(tmp_path, monkeypatch):
    source = tmp_path / "drawing.png"
    raster(source)
    monkeypatch.setenv("MULTICAD_VISION_CACHE", str(tmp_path / "cache"))
    calls = []

    def fake_ocr(*args, **kwargs):
        calls.append(kwargs["rotation_angles"])
        return {"status": "ok", "dimensions": []}

    monkeypatch.setattr("cad_vision.analyzer.extract_ocr", fake_ocr)
    first = analyze_source(str(source), ocr_policy="auto")
    second = analyze_source(str(source), ocr_policy="auto", ocr_rotation_angles=[270, 90, 90])
    third = analyze_source(str(source), ocr_policy="auto", ocr_rotation_angles=[90, 270])
    assert not first["cache_hit"] and not second["cache_hit"] and third["cache_hit"]
    assert calls == [[], [90, 270]]


@pytest.mark.parametrize(
    "box", [[0, 0, float("nan"), 1], [0, 0, 0, 1], [0, 0, True, 1], [[0, 0], [1, float("inf")]]]
)
def test_nonfinite_and_degenerate_boxes_are_not_location_evidence(box):
    assert _box(box) is None


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_confidence_cannot_masquerade_as_success(tmp_path, confidence):
    class BadPipeline:
        def predict(self, *args, **kwargs):
            return [{"rec_texts": ["12"], "rec_scores": [confidence], "rec_boxes": [[1, 1, 2, 2]]}]

    result = extract_ocr(tmp_path / "unused.png", pipeline_factory=lambda *_: BadPipeline())
    assert result["status"] == "error"


def test_missing_text_location_requires_review_even_without_rotations(tmp_path):
    class NoBoxPipeline:
        def predict(self, *args, **kwargs):
            return [{"rec_texts": ["12"], "rec_scores": [0.95]}]

    result = extract_ocr(tmp_path / "unused.png", pipeline_factory=lambda *_: NoBoxPipeline())
    assert result["status"] == "ok"
    assert result["dimensions"][0]["needs_confirmation"]
    assert result["dimensions"][0]["confirmation_reasons"] == ["missing_text_location"]


def test_rotated_pdf_without_selection_renders_each_page(tmp_path):
    fitz = pytest.importorskip("fitz")
    source = tmp_path / "drawing.pdf"
    with fitz.open() as doc:
        for _ in range(2):
            page = doc.new_page(width=140, height=100)
            page.draw_rect(fitz.Rect(30, 20, 50, 60), color=None, fill=(0, 0, 0))
        doc.save(source)
    pipeline = PixelPipeline()
    result = extract_ocr(source, rotation_angles=[90], pipeline_factory=lambda *_: pipeline)
    assert result["status"] == "ok" and result["page_count_analyzed"] == 2
    assert {item["page"] for item in result["text_samples"]} == {1, 2}
    assert len(pipeline.paths) == 4 and all(not path.exists() for path in pipeline.paths)


def test_rotation_failure_removes_probe_files_and_does_not_report_success(tmp_path):
    source = tmp_path / "drawing.png"
    raster(source)
    paths = []

    class FailingProbe:
        def predict(self, path, **kwargs):
            paths.append(Path(path))
            if "probe-" in Path(path).name:
                raise RuntimeError("rotated inference failed")
            return []

    result = extract_ocr(source, rotation_angles=[90], pipeline_factory=lambda *_: FailingProbe())
    assert result["status"] == "error"
    assert len(paths) == 2 and not paths[1].exists()
    assert source.exists()


def test_multiframe_images_are_not_silently_reduced_to_first_page(tmp_path):
    source = tmp_path / "drawing.tiff"
    first = Image.new("RGB", (140, 100), "white")
    second = Image.new("RGB", (140, 100), "black")
    first.save(source, save_all=True, append_images=[second])
    pipeline = PixelPipeline()
    result = extract_ocr(source, rotation_angles=[90], pipeline_factory=lambda *_: pipeline)
    assert result["status"] == "error"
    assert "single-frame" in result["error"]
    assert pipeline.paths == []
