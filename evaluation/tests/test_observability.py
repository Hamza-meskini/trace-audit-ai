from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.services import observability


def test_observation_context_is_nested_and_restored():
    assert observability.current_observation_context() == {}
    with observability.observation_context(audit_run_id="audit-1", stage="root"):
        assert observability.current_observation_context() == {
            "audit_run_id": "audit-1",
            "stage": "root",
        }
        with observability.observation_context(requirement_id="REQ-1", stage="verification"):
            assert observability.current_observation_context() == {
                "audit_run_id": "audit-1",
                "requirement_id": "REQ-1",
                "stage": "verification",
            }
        assert observability.current_observation_context()["stage"] == "root"
        assert "requirement_id" not in observability.current_observation_context()
    assert observability.current_observation_context() == {}


def test_traced_content_is_private_by_default(monkeypatch):
    monkeypatch.setattr(observability.settings, "DATABRICKS_MLFLOW_CAPTURE_CONTENT", False)
    value = observability.traced_content("customer secret")
    assert value["characters"] == 15
    assert len(value["sha256"]) == 64
    assert "content" not in value


def test_traced_content_capture_is_bounded(monkeypatch):
    monkeypatch.setattr(observability.settings, "DATABRICKS_MLFLOW_CAPTURE_CONTENT", True)
    monkeypatch.setattr(observability.settings, "DATABRICKS_MLFLOW_MAX_CONTENT_CHARS", 4)
    value = observability.traced_content("abcdef")
    assert value["content"] == "abcd"
    assert value["truncated"] is True
