"""SEC-12: credentials and document text stay out of logs (fabricated values only)."""
import logging
from unittest.mock import patch

from app.config import settings
from app.services.log_redaction import (
    RedactingFilter,
    body_summary,
    exception_summary,
    redact_secrets,
)

FAKE_GOOGLE = "AIza" + "Z" * 35
FAKE_DATABRICKS = "dapi" + "0123456789abcdef" * 2
FAKE_SK = "sk-" + "a1B2c3D4e5F6g7H8"


def test_redact_secrets_masks_known_credential_shapes():
    samples = [
        f"https://generativelanguage.googleapis.com/v1/models:embed?key={FAKE_GOOGLE}",
        f"Authorization: Bearer {FAKE_DATABRICKS}",
        f"databricks://token:{FAKE_DATABRICKS}@host:443?http_path=/sql",
        f"client_id=abc&client_secret=s3cr3tValue&catalog=x",
        f"OpenAI rejected {FAKE_SK}",
        "groq gsk_" + "Q" * 20 + " hf_" + "R" * 20,
    ]
    for sample in samples:
        redacted = redact_secrets(sample)
        for secret in (FAKE_GOOGLE, FAKE_DATABRICKS, FAKE_SK, "s3cr3tValue", "Q" * 20, "R" * 20):
            assert secret not in redacted, (sample, redacted)
        assert "[REDACTED]" in redacted


def test_redact_secrets_leaves_ordinary_text_alone():
    text = "Batch 3/12 done in 1.49s; max_tokens=4096; 12 tokens used"
    assert redact_secrets(text) == text


def test_body_summary_hides_content_by_default():
    body = "CONFIDENTIAL clause: the actuator shall open within 20 ms"
    with patch.object(settings, "LOG_PROVIDER_BODIES", False):
        summary = body_summary(body)
    assert "CONFIDENTIAL" not in summary
    assert summary.startswith(f"<{len(body)} chars, sha256:")


def test_body_summary_opt_in_still_redacts_secrets():
    with patch.object(settings, "LOG_PROVIDER_BODIES", True):
        summary = body_summary(f"error for key={FAKE_GOOGLE} on clause text")
    assert FAKE_GOOGLE not in summary
    assert "clause text" in summary


def test_exception_summary_redacts_urls_in_httpx_style_errors():
    exc = RuntimeError(f"Client error for url 'https://x/y?key={FAKE_GOOGLE}'")
    summary = exception_summary(exc)
    assert summary.startswith("RuntimeError: ")
    assert FAKE_GOOGLE not in summary


def test_filter_rewrites_formatted_record_including_args():
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, "call failed: %s", (f"Bearer {FAKE_DATABRICKS}",), None)
    RedactingFilter().filter(record)
    assert FAKE_DATABRICKS not in record.getMessage()


def test_schema_failure_log_does_not_include_raw_model_output(caplog, capsys):
    import asyncio
    from unittest.mock import AsyncMock

    from pydantic import BaseModel

    from app.services import llm_client

    class Shape(BaseModel):
        status: int

    raw = '{"status": "SECRET CLAUSE TEXT that is not an int"}'
    with patch.object(settings, "LOG_PROVIDER_BODIES", False), \
            patch.object(settings, "DATABRICKS_TOKEN", "fake-token-for-test"), \
            patch.object(llm_client, "call_databricks_chat_completions", AsyncMock(return_value=raw)), \
            caplog.at_level(logging.ERROR):
        result = asyncio.run(llm_client.generate_structured(
            prompt="p", response_model=Shape, model="system.ai.llama-4-maverick"))
    assert result is None
    assert caplog.records, "schema failure should still be logged"
    assert all("SECRET CLAUSE TEXT" not in r.getMessage() for r in caplog.records)
    assert "SECRET CLAUSE TEXT" not in capsys.readouterr().out


def test_sql_echo_is_independent_of_debug():
    assert settings.SQL_ECHO is False
