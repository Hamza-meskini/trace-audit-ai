"""Regression tests for verified defects found in the end-to-end review.

Each test fails on the code before its fix:
- strict inequalities were evaluated as inclusive (5.0 passed "< 5");
- Excel export turned document text starting with "=" into live formulas and
  aborted the whole workbook on a single control character;
- the Gemini API key travelled in the URL, which httpx error messages (and so
  our warning logs) include;
- every new embedding rewrote the entire cache file on the event loop.
"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, PropertyMock, patch

import httpx
import openpyxl

from app.config import Settings
from app.schemas.claim import EvidenceClaim
from app.schemas.contract import AtomicConditionContract, RequirementContract
from app.schemas.verification_result import ConditionVerificationResult
from app.services import embedding
from app.services.excel_export import _set_value
from app.services.units import satisfies_bound
from app.services.verdict_aggregator import _numeric_condition_status
from app.services.verification_reasoner import _structured_numeric_status


def _condition(operator: str, threshold: float, unit: str = "ms") -> AtomicConditionContract:
    return AtomicConditionContract(
        condition_id="C1",
        description=f"latency {operator} {threshold} {unit}",
        parameter="latency",
        operator=operator,
        threshold=threshold,
        unit=unit,
    )


class StrictInequalityTests(unittest.TestCase):
    CASES = [
        # operator, threshold, observed, expected
        ("<", 5, 5.0, "FAILED"),
        ("<", 5, 4.99, "PROVEN"),
        ("<=", 5, 5.0, "PROVEN"),
        ("<=", 5, 5.01, "FAILED"),
        (">", 12, 12.0, "FAILED"),
        (">", 12, 12.01, "PROVEN"),
        (">=", 12, 12.0, "PROVEN"),
        (">=", 12, 11.99, "FAILED"),
    ]

    def test_satisfies_bound_keeps_strict_operators_strict(self):
        for operator, threshold, observed, expected in self.CASES:
            with self.subTest(operator=operator, observed=observed):
                self.assertEqual(
                    satisfies_bound(observed, operator, threshold), expected == "PROVEN"
                )
        self.assertIsNone(satisfies_bound(1.0, "between", 2.0))

    def test_conversion_rounding_counts_as_equal(self):
        # 0.005 s converts to 5 ms up to float rounding in either direction.
        self.assertFalse(satisfies_bound(5.000000000000001, "<", 5))
        self.assertFalse(satisfies_bound(4.999999999999999, "<", 5))
        self.assertTrue(satisfies_bound(5.000000000000001, "<=", 5))
        self.assertTrue(satisfies_bound(4.999999999999999, ">=", 5))

    def test_reasoner_structured_status_respects_strict_bounds(self):
        for operator, threshold, observed, expected in self.CASES:
            with self.subTest(operator=operator, observed=observed):
                result = ConditionVerificationResult(
                    condition_id="C1",
                    status="PROVEN",
                    observed_value=str(observed),
                    observed_unit="ms",
                    evidence_value_role="OBSERVED",
                )
                status, _, _ = _structured_numeric_status(_condition(operator, threshold), result)
                self.assertEqual(status, expected)

    def test_reasoner_compares_after_unit_conversion(self):
        result = ConditionVerificationResult(
            condition_id="C1",
            status="PROVEN",
            observed_value="0.005",
            observed_unit="s",
            evidence_value_role="OBSERVED",
        )
        self.assertEqual(_structured_numeric_status(_condition("<", 5), result)[0], "FAILED")
        self.assertEqual(_structured_numeric_status(_condition("<=", 5), result)[0], "PROVEN")

    def test_aggregator_numeric_claim_respects_strict_bounds(self):
        for operator, threshold, observed, expected in self.CASES:
            with self.subTest(operator=operator, observed=observed):
                cond = _condition(operator, threshold)
                contract = RequirementContract(
                    requirement_id="REQ-1",
                    req_code="REQ-1",
                    title="Latency",
                    raw_text=f"Latency shall be {operator} {threshold} ms.",
                    atomic_conditions=[cond],
                )
                claim = EvidenceClaim(
                    document_name="test_report.pdf",
                    claim_type="threshold",
                    parameter="latency",
                    value=observed,
                    unit="ms",
                    quote=f"Measured latency: {observed} ms",
                )
                self.assertEqual(_numeric_condition_status(contract, cond, claim), expected)


class ExcelCellSafetyTests(unittest.TestCase):
    def _roundtrip(self, value):
        workbook = openpyxl.Workbook()
        cell = _set_value(workbook.active.cell(row=1, column=1), value)
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        return cell, openpyxl.load_workbook(buffer).active["A1"]

    def test_document_text_starting_with_equals_is_stored_as_text(self):
        payload = '=HYPERLINK("http://attacker.example","Open evidence")'
        cell, reloaded = self._roundtrip(payload)
        self.assertEqual(cell.data_type, "s")
        self.assertEqual(reloaded.data_type, "s")
        self.assertEqual(reloaded.value, payload)

    def test_control_characters_are_stripped_instead_of_aborting_export(self):
        _, reloaded = self._roundtrip("Result\x03 PASS\x1f")
        self.assertEqual(reloaded.value, "Result PASS")

    def test_numbers_and_plain_text_are_unchanged(self):
        cell, reloaded = self._roundtrip(42)
        self.assertEqual((cell.data_type, reloaded.value), ("n", 42))
        _, reloaded = self._roundtrip("-40 degC ambient")
        self.assertEqual(reloaded.value, "-40 degC ambient")


class _RecordingClient:
    """Stands in for httpx.AsyncClient and records each POST."""

    calls: list[tuple[str, dict]] = []
    status_code = 200

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        type(self).calls.append((url, dict(headers or {})))
        request = httpx.Request("POST", url, headers=headers)
        if self.status_code != 200:
            return httpx.Response(self.status_code, request=request)
        if "batchEmbedContents" in url:
            body = {"embeddings": [{"values": [float(i), 0.5]} for i, _ in enumerate(json["requests"])]}
        else:
            body = {"embedding": {"values": [0.25, 0.75]}}
        return httpx.Response(200, json=body, request=request)


class GeminiEmbeddingTests(unittest.IsolatedAsyncioTestCase):
    KEY = "AIza-test-secret-123"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cache_dir = Path(self.tmp.name)
        self.journal = cache_dir / "embeddings.jsonl"
        self.legacy = cache_dir / "embeddings.json"
        _RecordingClient.calls = []
        _RecordingClient.status_code = 200
        patches = [
            patch.object(embedding, "_CACHE_DIR", cache_dir),
            patch.object(embedding, "_CACHE_FILE", self.legacy),
            patch.object(embedding, "_JOURNAL_FILE", self.journal),
            patch.object(embedding, "_cache_loaded", False),
            patch.object(embedding, "_circuit_open", False),
            patch.object(embedding, "_consecutive_rate_limits", 0),
            patch.object(embedding, "_embedding_cache", {}),
            patch.object(embedding.httpx, "AsyncClient", _RecordingClient),
            patch.object(embedding.asyncio, "sleep", AsyncMock()),
            patch.object(
                Settings, "effective_gemini_api_key", new_callable=PropertyMock, return_value=self.KEY
            ),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.tmp.cleanup)

    def _reload_from_disk(self) -> dict:
        embedding._embedding_cache.clear()
        embedding._cache_loaded = False
        embedding._ensure_cache_loaded()
        return dict(embedding._embedding_cache)

    async def test_api_key_is_sent_in_header_not_url(self):
        await embedding.embed_single("pack voltage 400 V", task_type="RETRIEVAL_QUERY")
        await embedding.embed_batch(["chunk one", "chunk two"])

        self.assertEqual(len(_RecordingClient.calls), 2)
        for url, headers in _RecordingClient.calls:
            self.assertNotIn(self.KEY, url)
            self.assertNotIn("key=", url)
            self.assertEqual(headers.get("x-goog-api-key"), self.KEY)

    async def test_failed_batch_does_not_log_the_api_key(self):
        _RecordingClient.status_code = 403
        with self.assertLogs("traceaudit.embedding", level="WARNING") as logs:
            await embedding.embed_batch(["chunk one"])
        self.assertTrue(logs.output)
        self.assertFalse(any(self.KEY in line for line in logs.output))

    async def test_new_vectors_are_appended_not_rewritten(self):
        self.legacy.write_text(json.dumps({"legacy": [9.0]}), encoding="utf-8")
        legacy_before = self.legacy.read_bytes()

        await embedding.embed_single("query text", task_type="RETRIEVAL_QUERY")
        await embedding.embed_batch(["chunk one", "chunk two"])

        self.assertEqual(self.legacy.read_bytes(), legacy_before)  # never rewritten
        lines = [line for line in self.journal.read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual(len(lines), 3)
        reloaded = self._reload_from_disk()
        self.assertEqual(len(reloaded), 4)  # legacy snapshot + three appended vectors
        self.assertEqual(reloaded["legacy"], [9.0])

    async def test_torn_journal_line_does_not_lose_later_entries(self):
        self.journal.write_text('\n{"k": "torn", "v": [1.0, 2', encoding="utf-8")  # crash mid-write
        embedding._append_cache_entries({"after": [3.0]})

        with self.assertLogs("traceaudit.embedding", level="WARNING"):
            reloaded = self._reload_from_disk()
        self.assertEqual(reloaded, {"after": [3.0]})


if __name__ == "__main__":
    unittest.main()
