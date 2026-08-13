from cad_vision.dimensions import normalize_engineering_text, parse_dimension_text


def test_normalize_common_ocr_variants() -> None:
    assert normalize_engineering_text("DIA 20") == "Ø20"
    assert normalize_engineering_text("100 +/- 0.1") == "100 ± 0.1"
    assert normalize_engineering_text("45 DEG") == "45 °"


def test_parse_structured_dimension_types() -> None:
    cases = {
        "DIA 20": ("diameter", 20.0),
        "R10": ("radius", 10.0),
        "45 DEG": ("angle", 45.0),
        "DEPTH 12": ("depth", 12.0),
        "3 PLCS": ("count", 3),
        "100 +/- 0.1": ("linear", 100.0),
        "M10x1.5": ("thread", "M10X1.5"),
    }
    for source, expected in cases.items():
        parsed = parse_dimension_text(source)
        assert parsed
        assert (parsed[0]["kind"], parsed[0]["value"]) == expected


def test_unrelated_text_is_not_promoted_to_dimension() -> None:
    assert parse_dimension_text("TYPICAL SUPPORT BRACKET") == []


def test_compound_hole_callout_returns_diameter_and_depth() -> None:
    parsed = parse_dimension_text("Ø20 DEEP 65")
    assert [(item["kind"], item["value"]) for item in parsed] == [
        ("diameter", 20.0),
        ("depth", 65.0),
    ]


def test_number_before_deep_is_parsed() -> None:
    parsed = parse_dimension_text("KEYWAY IS 8 DEEP")
    assert [(item["kind"], item["value"]) for item in parsed] == [("depth", 8.0)]


def test_damaged_hole_symbol_is_low_confidence_and_requires_confirmation() -> None:
    parsed = parse_dimension_text("20V65")
    assert [(item["kind"], item["value"]) for item in parsed] == [
        ("diameter", 20.0),
        ("depth", 65.0),
    ]
    assert all(item["needs_confirmation"] for item in parsed)
    assert all(item["confidence"] == 0.65 for item in parsed)

    suspicious_glyph = parse_dimension_text("20⌄65")
    assert [(item["kind"], item["value"]) for item in suspicious_glyph] == [
        ("diameter", 20.0),
        ("depth", 65.0),
    ]
    assert all(item["needs_confirmation"] for item in suspicious_glyph)
    assert all(item["confidence"] == 0.65 for item in suspicious_glyph)


def test_explicit_depth_symbol_is_not_reparsed_as_damaged_ocr() -> None:
    parsed = parse_dimension_text("DIA 20 ↧ 65")

    assert [(item["kind"], item["value"]) for item in parsed] == [
        ("diameter", 20.0),
        ("depth", 65.0),
    ]
    depth = parsed[1]
    assert depth["confidence"] == 1.0
    assert "needs_confirmation" not in depth
    assert "inference" not in depth


def test_tolerance_stays_attached_to_typed_dimension_without_duplicate_linear() -> None:
    diameter = parse_dimension_text("DIA 20 +/- 0.1")
    radius = parse_dimension_text("R10 +/- 0.05")
    linear = parse_dimension_text("100 +/- 0.1")

    assert [(item["kind"], item.get("tolerance")) for item in diameter] == [("diameter", 0.1)]
    assert [(item["kind"], item.get("tolerance")) for item in radius] == [("radius", 0.05)]
    assert [(item["kind"], item.get("tolerance")) for item in linear] == [("linear", 0.1)]


def test_length_unit_is_unresolved_until_evidence_resolves_it() -> None:
    unresolved = parse_dimension_text("DIA 20")[0]
    source_resolved = parse_dimension_text(
        "DIA 20",
        default_unit="mm",
        unit_source="source",
    )[0]
    explicit_inch = parse_dimension_text("DIA 0.5 IN +/- 0.01")[0]

    assert unresolved["unit"] is None
    assert unresolved["unit_source"] == "unresolved"
    assert unresolved["unit_resolved"] is False
    assert (source_resolved["unit"], source_resolved["unit_source"]) == ("mm", "source")
    assert (explicit_inch["unit"], explicit_inch["tolerance"]) == ("inch", 0.01)
    assert explicit_inch["unit_source"] == "annotation"


def test_compact_explicit_length_units_are_detected() -> None:
    diameter_mm = parse_dimension_text("DIA 20mm")[0]
    linear_inch = parse_dimension_text("25.4in")[0]
    linear_foot = parse_dimension_text("2ft")[0]

    assert (diameter_mm["kind"], diameter_mm["unit"]) == ("diameter", "mm")
    assert (linear_inch["kind"], linear_inch["unit"]) == ("linear", "inch")
    assert (linear_foot["kind"], linear_foot["unit"]) == ("linear", "foot")
    assert diameter_mm["unit_source"] == "annotation"
    assert linear_inch["unit_source"] == "annotation"
    assert linear_foot["unit_source"] == "annotation"


def test_count_diameter_depth_and_thread_evidence_are_all_preserved() -> None:
    hole = parse_dimension_text("4X DIA 10 DEPTH 20")
    metric_thread = parse_dimension_text("4X M10x1.5-6H DEPTH 12")
    inch_thread = parse_dimension_text("4X 1/4-20 UNC-2B")

    assert [(item["kind"], item["value"]) for item in hole] == [
        ("count", 4),
        ("diameter", 10.0),
        ("depth", 20.0),
    ]
    assert [(item["kind"], item["value"]) for item in metric_thread] == [
        ("count", 4),
        ("thread", "M10X1.5-6H"),
        ("depth", 12.0),
    ]
    assert metric_thread[1]["tolerance_class"] == "6H"
    assert metric_thread[2]["unit"] == "mm"
    assert [(item["kind"], item["value"]) for item in inch_thread] == [
        ("count", 4),
        ("thread", "1/4-20UNC-2B"),
    ]
    assert inch_thread[1]["unit"] == "inch"


def test_inch_thread_accepts_compact_and_spaced_standard_designators() -> None:
    compact = parse_dimension_text("1/4-20UNC-2B")
    spaced = parse_dimension_text("1/4-20 UNC-2B")

    for parsed in (compact, spaced):
        assert [(item["kind"], item["value"]) for item in parsed] == [("thread", "1/4-20UNC-2B")]
        assert parsed[0]["unit"] == "inch"
        assert parsed[0]["tolerance_class"] == "2B"
