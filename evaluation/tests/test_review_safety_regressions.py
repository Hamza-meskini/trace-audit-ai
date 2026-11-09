"""Regression tests for verified defects found in the end-to-end review.

Each fix test fails on the code before its fix:
- Excel export turned document text starting with "=" into live formulas and
  aborted the whole workbook on a single control character;
- the Gemini API key travelled in the URL, which httpx error messages (and so
  our warning logs) include;
- every new embedding rewrote the entire cache file on the event loop.

BoundStatementTests pins current behaviour that a naive fix would break; see
its docstring.
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
from app.schemas.claim import extract_claims_from_chunk
from app.schemas.contract import AtomicConditionContract, RequirementContract
from app.services import embedding, llm_client
from app.services.excel_export import _set_value
from app.services.verdict_aggregator import _numeric_condition_status


class BoundStatementTests(unittest.TestCase):
    """Evidence often states a bound ("porosity is below 8 percent") rather than
    a measurement. Claim extraction keeps only the number (8.0), so today's
    inclusive comparison is what makes "below 8" satisfy "< 8".

    Strict "<" / ">" handling is a real gap (a measured 5.0 passes "< 5"), but
    making the comparison strict without carrying the evidence's comparator
    flips these benchmark cases (REQ-NVA-QLT-004, REQ-NVA-ENV-004) to FAILED.
    Any strictness fix must keep this test passing.
    """

    def test_bound_statement_satisfies_matching_strict_requirement(self):
        cond = AtomicConditionContract(
            condition_id="C1",
            description="weld porosity < 8 percent",
            parameter="porosity",
            operator="<",
            threshold=8,
            unit="percent",
        )
        contract = RequirementContract(
            requirement_id="REQ-1",
            req_code="REQ-1",
            title="Weld porosity",
            raw_text="Weld porosity shall be < 8 percent.",
            atomic_conditions=[cond],
        )
        quote = "PASS - Executed for REQ-1; the recorded result confirms: Weld porosity is below 8 percent."
        claims = [
            claim
            for claim in extract_claims_from_chunk(
                {"content": quote, "document_name": "verification_report.pdf", "page_number": 1},
                contract,
            )
            if claim.claim_type == "threshold"
        ]
        self.assertTrue(claims)
        self.assertEqual(claims[0].value, 8.0)  # the comparator "below" is not kept
        self.assertEqual(_numeric_condition_status(contract, cond, claims[0]), "PROVEN")


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
        if ":generateContent" in url:
            body = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        elif "batchEmbedContents" in url:
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
            # These tests exercise the Gemini path, so Google is an approved host.
            patch.object(embedding.settings, "EGRESS_ALLOWED_HOSTS", ["generativelanguage.googleapis.com"]),
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

    async def test_generate_content_sends_key_in_header_not_url(self):
        text = await llm_client.call_gemini_generate_content("Say ok", model="gemini-2.5-flash")

        self.assertEqual(text, "ok")
        url, headers = _RecordingClient.calls[-1]
        self.assertIn(":generateContent", url)
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
