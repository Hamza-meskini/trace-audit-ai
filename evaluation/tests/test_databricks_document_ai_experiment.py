import pytest

from evaluation.try_databricks_document_ai import (
    EVIDENCE_SCHEMA,
    PROFILE_SCHEMAS,
    _extraction_is_empty,
    _hostname,
    _validate_volume_dir,
)


def test_builtin_schemas_use_databricks_advanced_schema_shape():
    for schema in PROFILE_SCHEMAS.values():
        assert "type" not in schema
        assert "properties" not in schema
        assert schema

    evidence_kind = EVIDENCE_SCHEMA["evidence_observations"]["items"]["properties"]["evidence_kind"]
    assert evidence_kind["type"] == "enum"
    assert evidence_kind["labels"]
    assert "values" not in evidence_kind


def test_empty_extraction_is_not_treated_as_success():
    assert _extraction_is_empty({"response": {"field": {"value": None}}})
    assert not _extraction_is_empty({"response": {"field": {"value": "0.0 liters"}}})
    assert not _extraction_is_empty({"response": {"items": [{"name": {"value": "result"}}]}})


def test_connection_inputs_are_normalized_and_volume_path_is_scoped():
    assert _hostname("https://dbc-example.cloud.databricks.com/serving-endpoints") == "dbc-example.cloud.databricks.com"
    assert _validate_volume_dir("/Volumes/workspace/traceaudit/documents/") == "/Volumes/workspace/traceaudit/documents"
    with pytest.raises(ValueError):
        _validate_volume_dir("/Volumes/workspace/traceaudit/documents/../other")
    with pytest.raises(ValueError):
        _validate_volume_dir("/tmp/documents")
