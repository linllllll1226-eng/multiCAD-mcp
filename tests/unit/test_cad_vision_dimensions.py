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
