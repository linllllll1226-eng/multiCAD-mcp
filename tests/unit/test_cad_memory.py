"""Unit tests for the local SQLite CAD memory."""

from cad_memory.database import SQLiteMemoryStore


def test_only_confirmed_corrections_are_enforceable(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "cad_memory.db")
    store.add_correction(
        category="dimension",
        trigger="diameter",
        wrong_behavior="manual prefix",
        correct_behavior="native diameter dimension",
        confirmed_by_user=False,
    )
    confirmed = store.add_correction(
        category="dimension",
        trigger="diameter",
        wrong_behavior="ØØ15",
        correct_behavior="empty TextOverride",
        confirmed_by_user=True,
    )
    assert store.search_corrections("diameter") == [confirmed]
    assert len(store.search_corrections("diameter", include_unconfirmed=True)) == 2


def test_profile_round_trip_and_confirmed_delete(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "cad_memory.db")
    profile = store.save_drawing_profile(
        name="metric-preview",
        unit="mm",
        layer_rules={"outline": "AI_PREVIEW_OUTLINE"},
        dimension_rules={"text_override": ""},
        notes="local",
    )
    assert (
        store.load_drawing_profile("metric-preview")["layer_rules"]["outline"]
        == "AI_PREVIEW_OUTLINE"
    )
    try:
        store.delete_record("drawing_profiles", profile["id"], confirmed=False)
    except PermissionError:
        pass
    else:
        raise AssertionError("unconfirmed deletion must fail")
    assert store.delete_record("drawing_profiles", profile["id"], confirmed=True)


def test_required_schema_exists(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "cad_memory.db")
    with store._connection() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"corrections", "drawing_profiles", "execution_results"} <= names


def test_required_schema_columns_exist(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "cad_memory.db")
    expected = {
        "corrections": {
            "id",
            "category",
            "trigger",
            "wrong_behavior",
            "correct_behavior",
            "context",
            "confirmed_by_user",
            "created_at",
        },
        "drawing_profiles": {
            "id",
            "name",
            "unit",
            "layer_rules",
            "dimension_rules",
            "notes",
        },
        "execution_results": {
            "id",
            "task_name",
            "planned_data",
            "actual_data",
            "passed",
            "errors",
            "created_at",
        },
    }
    with store._connection() as connection:
        for table, required_columns in expected.items():
            actual_columns = {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            assert required_columns <= actual_columns
