"""SEC-02: outbound HTTP is limited to the Databricks workspace plus approved hosts."""
import asyncio
from unittest.mock import patch

import httpx
import pytest

from app.config import settings
from app.services import egress
from app.services.egress import EgressBlockedError, async_client, check_egress, host_allowed

WORKSPACE = "dbc-test.cloud.databricks.com"


@pytest.fixture
def workspace():
    with patch.object(settings, "DATABRICKS_HOST", WORKSPACE), \
            patch.object(settings, "DATABRICKS_BASE_URL", f"https://{WORKSPACE}/ai-gateway/mlflow/v1"), \
            patch.object(settings, "EGRESS_ALLOWED_HOSTS", []), \
            patch.object(settings, "EGRESS_ENFORCED", True):
        yield


def test_default_allows_only_the_databricks_workspace(workspace):
    assert egress.allowed_hosts() == [WORKSPACE]
    check_egress(f"https://{WORKSPACE}/serving-endpoints/x/invocations")
    for url in ("https://generativelanguage.googleapis.com/v1beta/models/x:embed",
                "https://api.tokenrouter.com/v1/chat/completions",
                "https://openrouter.ai/api/v1/chat/completions",
                f"https://{WORKSPACE}.evil.example/x"):
        with pytest.raises(EgressBlockedError):
            check_egress(url)


def test_extra_hosts_and_wildcards(workspace):
    with patch.object(settings, "EGRESS_ALLOWED_HOSTS", ["generativelanguage.googleapis.com", "*.example.org"]):
        assert host_allowed("generativelanguage.googleapis.com")
        assert host_allowed("a.example.org")
        assert not host_allowed("example.org")
        assert not host_allowed("api.openai.com")
    with patch.object(settings, "EGRESS_ALLOWED_HOSTS", ["*"]):
        assert host_allowed("anything.example")


def test_async_client_hook_blocks_before_the_request_is_sent(workspace):
    sent = []

    def handler(request):
        sent.append(str(request.url))
        return httpx.Response(200, json={})

    async def go(url):
        async with async_client(transport=httpx.MockTransport(handler)) as client:
            return await client.post(url, json={})

    assert asyncio.run(go(f"https://{WORKSPACE}/x")).status_code == 200
    with pytest.raises(EgressBlockedError):
        asyncio.run(go("https://api.openai.com/v1/chat/completions"))
    assert sent == [f"https://{WORKSPACE}/x"]


def test_gemini_embeddings_fall_back_when_google_is_not_allowed(workspace):
    from app.services import embedding

    with patch.object(settings, "GEMINI_API_KEY", "fake-key-for-test"), \
            patch.object(embedding, "_circuit_open", False):
        assert embedding._has_embedding_key() is False
        with patch.object(settings, "EGRESS_ALLOWED_HOSTS", ["generativelanguage.googleapis.com"]):
            assert embedding._has_embedding_key() is True


def test_benchmarked_model_is_the_production_contract_model(workspace):
    assert settings.ATOMIC_DECOMPOSITION_MODEL == "system.ai.llama-4-maverick"
    assert settings.ATOMIC_DECOMPOSITION_FALLBACK_MODEL == "system.ai.llama-4-maverick"
    with patch.object(settings, "LLM_MODEL", "system.ai.llama-4-maverick"), \
            patch.object(settings, "LLM_PROVIDER", "databricks"), \
            patch.object(settings, "SECONDARY_ADJUDICATOR_MODEL", ""), \
            patch.object(settings, "CITATION_GROUNDING_MODEL", ""):
        assert egress.model_configuration_problems() == []


def test_startup_report_flags_a_third_party_contract_model(workspace):
    with patch.object(settings, "ATOMIC_DECOMPOSITION_MODEL", "z-ai/glm-5.3-free"), \
            patch.object(settings, "LLM_DATABRICKS_ONLY", True):
        problems = egress.model_configuration_problems()
    assert any(p.startswith("ATOMIC_DECOMPOSITION_MODEL") for p in problems)
