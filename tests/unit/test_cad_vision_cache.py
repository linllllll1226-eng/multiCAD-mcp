"""Exercise recoverable cache failures through the source-analysis API."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from cad_vision import analyzer


@pytest.fixture
def drawing(tmp_path: Path, monkeypatch: Any) -> tuple[Path, Path]:
    fitz = pytest.importorskip("fitz")
    source = tmp_path / "drawing.pdf"
    with fitz.open() as document:
        page = document.new_page(width=200, height=100)
        page.draw_line((10, 30), (150, 30))
        page.insert_text((10, 20), "DIA 20")
        document.save(source)
    cache = tmp_path / "cache"
    monkeypatch.setenv("MULTICAD_VISION_CACHE", str(cache))
    return source, cache


@pytest.mark.parametrize("status", ["error", "unavailable"])
def test_transient_ocr_failure_is_retried_and_only_success_is_reused(
    drawing: tuple[Path, Path], monkeypatch: Any, status: str
) -> None:
    source, cache = drawing
    from PIL import Image

    source = source.with_suffix(".png")
    Image.new("RGB", (100, 100), "white").save(source)
    calls = []

    def provider(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"status": status if len(calls) == 1 else "ok", "dimensions": []}

    monkeypatch.setattr(analyzer, "extract_ocr", provider)
    failed = analyzer.analyze_source(str(source), ocr_policy="force")
    assert failed["analysis"]["ocr"]["status"] == status
    assert failed["analysis"]["ocr_coverage"]["complete"] is False
    assert not list(cache.glob("*.json"))
    recovered = analyzer.analyze_source(str(source), ocr_policy="force")
    warm = analyzer.analyze_source(str(source), ocr_policy="force")
    assert len(calls) == 2
    assert recovered["cache_hit"] is False and warm["cache_hit"] is True
    assert warm["analysis"]["ocr"]["status"] == "ok"


@pytest.mark.parametrize(
    "broken",
    [b'{"analysis":', b"\xff", b"null", b"[]", b'"text"', b'{"analysis": []}'],
)
def test_invalid_cache_is_recomputed_and_replaced(
    drawing: tuple[Path, Path], broken: bytes
) -> None:
    source, cache = drawing
    original = analyzer.analyze_source(str(source), ocr_policy="off")
    path = next(cache.glob("*.json"))
    path.write_bytes(broken)
    recovered = analyzer.analyze_source(str(source), ocr_policy="off")
    warm = analyzer.analyze_source(str(source), ocr_policy="off")
    assert recovered["cache_hit"] is False and warm["cache_hit"] is True
    assert recovered["analysis"] == original["analysis"]
    assert json.loads(path.read_text(encoding="utf-8"))["source"] == recovered["source"]


@pytest.mark.parametrize("changed", ["source", "pipeline_version", "runtime_fingerprint"])
def test_cache_with_mismatched_result_binding_is_not_reused(
    drawing: tuple[Path, Path], changed: str
) -> None:
    source, cache = drawing
    original = analyzer.analyze_source(str(source), ocr_policy="off")
    path = next(cache.glob("*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    if changed == "source":
        record["source"]["sha256"] = "0" * 64
    elif changed == "pipeline_version":
        record[changed] = "obsolete"
    else:
        record[changed] = {"source": {"provider": "wrong"}}
    path.write_text(json.dumps(record), encoding="utf-8")
    recovered = analyzer.analyze_source(str(source), ocr_policy="off")
    assert recovered["cache_hit"] is False
    assert recovered["analysis"] == original["analysis"]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_json_in_cache_is_not_returned(drawing: tuple[Path, Path], value: str) -> None:
    source, cache = drawing
    analyzer.analyze_source(str(source), ocr_policy="off")
    path = next(cache.glob("*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    record["analysis"]["unexpected_nonfinite"] = "PLACEHOLDER"
    path.write_text(json.dumps(record).replace('"PLACEHOLDER"', value), encoding="utf-8")
    recovered = analyzer.analyze_source(str(source), ocr_policy="off")
    assert recovered["cache_hit"] is False
    json.dumps(recovered, allow_nan=False)


def test_duplicate_json_keys_in_cache_are_not_returned(drawing: tuple[Path, Path]) -> None:
    source, cache = drawing
    analyzer.analyze_source(str(source), ocr_policy="off")
    path = next(cache.glob("*.json"))
    raw = path.read_text(encoding="utf-8")
    path.write_text('{"cache_hit": false,' + raw[1:], encoding="utf-8")
    assert analyzer.analyze_source(str(source), ocr_policy="off")["cache_hit"] is False


def test_cache_read_permission_failure_does_not_block_analysis(
    drawing: tuple[Path, Path], monkeypatch: Any
) -> None:
    source, cache = drawing
    analyzer.analyze_source(str(source), ocr_policy="off")
    read_text = Path.read_text

    def restricted(path: Path, *args: Any, **kwargs: Any) -> str:
        if path.parent == cache:
            raise PermissionError("cache cannot be read")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", restricted)
    recovered = analyzer.analyze_source(str(source), ocr_policy="off")
    assert recovered["cache_hit"] is False
    assert recovered["analysis"]["dimensions"]


def test_cache_directory_failure_does_not_block_analysis(drawing: tuple[Path, Path]) -> None:
    source, cache = drawing
    cache.write_text("A file occupies the configured cache directory")
    result = analyzer.analyze_source(str(source), ocr_policy="off")
    assert result["analysis"]["dimensions"]
    assert result["cache_hit"] is False
    assert cache.read_text(encoding="utf-8") == "A file occupies the configured cache directory"


def test_failed_atomic_replace_preserves_previous_record_and_removes_temporary_file(
    drawing: tuple[Path, Path], monkeypatch: Any
) -> None:
    source, cache = drawing
    analyzer.analyze_source(str(source), ocr_policy="off")
    path = next(cache.glob("*.json"))
    previous = path.read_bytes()
    replacements = []

    def blocked_replace(temporary: Any, destination: Any) -> None:
        replacements.append((Path(temporary), Path(destination)))
        assert Path(destination).read_bytes() == previous
        assert json.loads(Path(temporary).read_text(encoding="utf-8"))["analysis"]["dimensions"]
        raise PermissionError("destination temporarily locked")

    monkeypatch.setattr("os.replace", blocked_replace)
    # A blocked cache read forces a fresh analysis while the previous complete file exists.
    read_text = Path.read_text

    def blocked_read(file: Path, *args: Any, **kwargs: Any) -> str:
        if file == path:
            raise PermissionError("reader temporarily denied")
        return read_text(file, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", blocked_read)
    result = analyzer.analyze_source(str(source), ocr_policy="off")
    assert result["cache_hit"] is False
    assert len(replacements) == 1
    assert path.read_bytes() == previous
    assert list(cache.iterdir()) == [path]


def test_identical_content_at_another_filename_reports_current_source(
    drawing: tuple[Path, Path],
) -> None:
    source, cache = drawing
    cold = analyzer.analyze_source(str(source), ocr_policy="off")
    renamed = source.with_name("reviewed-copy.pdf")
    renamed.write_bytes(source.read_bytes())
    warm = analyzer.analyze_source(str(renamed), ocr_policy="off")
    assert warm["cache_hit"] is True
    assert warm["source"]["name"] == renamed.name
    assert warm["source"]["sha256"] == cold["source"]["sha256"]
    assert len(list(cache.glob("*.json"))) == 1
    assert (
        json.loads(next(cache.glob("*.json")).read_text(encoding="utf-8"))["source"]["name"]
        == source.name
    )


def test_legacy_cached_ocr_error_is_retried(drawing: tuple[Path, Path], monkeypatch: Any) -> None:
    source, cache = drawing
    calls = []

    def provider(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"status": "ok", "dimensions": []}

    monkeypatch.setattr(analyzer, "extract_ocr", provider)
    analyzer.analyze_source(str(source), ocr_policy="force")
    path = next(cache.glob("*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    record["analysis"]["ocr"]["status"] = "error"
    path.write_text(json.dumps(record), encoding="utf-8")
    recovered = analyzer.analyze_source(str(source), ocr_policy="force")
    assert recovered["cache_hit"] is False and len(calls) == 2
    assert recovered["analysis"]["ocr"]["status"] == "ok"


@pytest.mark.parametrize("changed", ["policy", "units", "dimensions", "dimension_count"])
def test_inconsistent_analysis_cache_is_recomputed(
    drawing: tuple[Path, Path], changed: str
) -> None:
    source, cache = drawing
    original = analyzer.analyze_source(str(source), ocr_policy="off")
    path = next(cache.glob("*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    if changed == "policy":
        record["analysis"]["ocr_policy"] = "force"
    elif changed == "units":
        record["analysis"]["unit_resolution"]["unit"] = "inch"
    elif changed == "dimensions":
        record["analysis"]["dimensions"] = {}
    else:
        record["analysis"]["dimension_count"] = -1
    path.write_text(json.dumps(record), encoding="utf-8")
    recovered = analyzer.analyze_source(str(source), ocr_policy="off")
    assert recovered["cache_hit"] is False
    assert recovered["analysis"] == original["analysis"]


def test_simultaneous_cache_writers_publish_complete_independent_files(
    drawing: tuple[Path, Path], monkeypatch: Any
) -> None:
    from cad_vision import cache as cache_module

    source, cache = drawing
    original = analyzer.analyze_source(str(source), ocr_policy="off")
    path = next(cache.glob("*.json"))
    replace = os.replace
    barrier = Barrier(2)
    temporary_files = []

    def synchronized_replace(temporary: Any, destination: Any) -> None:
        temporary_files.append(Path(temporary))
        assert Path(temporary).parent == Path(destination).parent == cache
        assert json.loads(Path(temporary).read_text(encoding="utf-8"))["writer"] in (0, 1)
        assert json.loads(path.read_text(encoding="utf-8"))["analysis"] == original["analysis"]
        barrier.wait(timeout=5)
        replace(temporary, destination)

    monkeypatch.setattr("os.replace", synchronized_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(cache_module.save_result, path, {**original, "writer": i}) for i in (0, 1)
        ]
        for future in futures:
            future.result(timeout=10)
    assert len(set(temporary_files)) == 2
    assert json.loads(path.read_text(encoding="utf-8"))["writer"] in (0, 1)
    assert list(cache.iterdir()) == [path]
    assert analyzer.analyze_source(str(source), ocr_policy="off")["cache_hit"] is True


def test_cache_opt_out_does_not_touch_a_blocked_cache_directory(
    drawing: tuple[Path, Path], caplog: Any
) -> None:
    source, cache = drawing
    cache.write_text("not a directory")
    result = analyzer.analyze_source(str(source), use_cache=False, ocr_policy="off")
    assert result["cache_hit"] is False and result["analysis"]["dimensions"]
    assert not caplog.records
    assert cache.read_text(encoding="utf-8") == "not a directory"


def test_successful_cache_preserves_unconfirmed_orientation_evidence(
    drawing: tuple[Path, Path], monkeypatch: Any
) -> None:
    source, cache = drawing
    calls = []
    evidence = {
        "kind": "linear",
        "value": 12,
        "unit": "mm",
        "source": "ocr",
        "needs_confirmation": True,
        "confirmation_reasons": ["orientation_disagreement"],
        "orientation_conflicts": ["2"],
    }

    def provider(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"status": "ok", "dimensions": [evidence], "text_samples": [{"text": "12"}]}

    monkeypatch.setattr(analyzer, "extract_ocr", provider)
    analyzer.analyze_source(
        str(source), ocr_policy="force", include_samples=False, ocr_rotation_angles=[90, 270]
    )
    warm = analyzer.analyze_source(
        str(source), ocr_policy="force", include_samples=True, ocr_rotation_angles=[270, 90, 90]
    )
    assert warm["cache_hit"] is True and len(calls) == 1
    assert warm["analysis"]["ocr"]["text_samples"] == [{"text": "12"}]
    assert warm["analysis"]["ocr"]["dimensions"][0] == evidence
    assert len(list(cache.glob("*.json"))) == 1
