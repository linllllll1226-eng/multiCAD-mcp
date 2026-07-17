"""Guarded-plan tests for annotation text entities."""

from cad_memory.executor import PlanExecutor
from cad_memory.models import DrawingPlan
from cad_memory.validator import PlanValidator
from cad_memory.verifier import PostExecutionVerifier


def _plan(text: str = "KEYWAY IS 8 DEEP") -> DrawingPlan:
    return DrawingPlan.model_validate(
        {
            "task_name": "guarded annotation",
            "unit": "mm",
            "user_confirmed": True,
            "preview_mode": True,
            "existing_layers": ["AI_PREVIEW_DIM"],
            "entities": [
                {
                    "entity_type": "text",
                    "coordinates": {"position": [10, 20]},
                    "dimensions": {"height": 2.5},
                    "layer": "AI_PREVIEW_DIM",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                    "text_override": text,
                }
            ],
        }
    )


class _Text:
    Handle = "T1"
    ObjectName = "AcDbText"
    Layer = "AI_PREVIEW_DIM"
    Linetype = "ByLayer"
    InsertionPoint = (10.0, 20.0, 0.0)
    TextString = "KEYWAY IS 8 DEEP"
    Height = 2.5


class _Document:
    def __init__(self) -> None:
        self.text = _Text()

    def StartUndoMark(self):  # noqa: N802
        pass

    def EndUndoMark(self):  # noqa: N802
        pass

    def HandleToObject(self, handle):  # noqa: N802
        assert handle == "T1"
        return self.text


class _Adapter:
    def __init__(self) -> None:
        self.document = _Document()

    def list_layers(self):
        return ["AI_PREVIEW_DIM"]

    def _get_document(self, operation):
        return self.document

    def draw_text(self, position, text, height, rotation, layer, color, _skip_refresh=False):
        assert position == (10.0, 20.0, 0.0)
        assert text == "KEYWAY IS 8 DEEP"
        assert height == 2.5
        assert rotation == 0.0
        assert layer == "AI_PREVIEW_DIM"
        assert color == "white"
        assert _skip_refresh is True
        return "T1"

    @staticmethod
    def refresh_view():
        pass


def test_guarded_text_validates_executes_and_verifies():
    plan = _plan()
    assert PlanValidator().validate(plan, available_layers=["AI_PREVIEW_DIM"]).passed
    adapter = _Adapter()
    execution = PlanExecutor().execute(adapter, plan)
    assert execution["success"]
    verification = PostExecutionVerifier().verify(adapter, plan, execution["handles"])
    assert verification["passed"]


def test_guarded_text_rejects_empty_content():
    report = PlanValidator().validate(_plan(""), available_layers=["AI_PREVIEW_DIM"])
    assert not report.passed
    assert any(issue.code == "text_missing" for issue in report.errors)
