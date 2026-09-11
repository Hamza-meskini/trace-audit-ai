"""Source association checks use invented clauses, never benchmark answers."""
from unittest.mock import AsyncMock, patch
import asyncio

import pytest

from app.services.extraction import (
    DiscoveredRequirement, RequirementDiscoveryResult, _discover_requirements,
    _discovered_source_block, _ground_discovered_description, _is_source_heading,
    _requirement_blocks, _requirement_code_from_block,
)


def discovered(code, description, title="Connector voltage"):
    return DiscoveredRequirement(req_code=code, title=title, description=description)


def test_wrapped_reference_is_not_a_new_clause():
    blocks = _requirement_blocks(
        "S12.1 The assembly shall satisfy\n"
        "S10.2 when tested at full load.\n"
        "S12.2 Protection against contact.\n"
        "S13. Test conditions."
    )
    assert [_requirement_code_from_block(b) for b in blocks] == ["S12.1", "S12.2", "S13"]
    assert "S10.2 when" in blocks[0]


def test_neighbouring_obligation_cannot_replace_subclause_description():
    item = discovered("S12.2(b)", "The voltage becomes less than 20 V within two seconds.")
    source = "S12.1 Cables shall have a blue covering.\nS12.2 Connectors."
    assert _ground_discovered_description(item, source).description == item.description
    assert "blue" not in item.description


def test_declarative_procedure_is_not_replaced_by_later_shall_sentence():
    item = discovered("S12.2", "Measure the current.", "Current measurement")
    result = _ground_discovered_description(item, "S12.2 Measure the current.\nThe lamp shall light.")
    assert result.description == "Measure the current."


def test_exact_heading_can_recover_its_own_obligation():
    item = discovered("REQ-LAB-001", "Calibration", "Calibration")
    result = _ground_discovered_description(item, "REQ-LAB-001 Calibration\nInstruments shall be calibrated annually.")
    assert result.description == "Instruments shall be calibrated annually."


def test_subclause_uses_nearest_parent_and_not_sibling_prefix():
    sources = {"S12.2": "connector parent", "S12.2(b)": "disconnect branch", "S12.20": "unrelated"}
    assert _discovered_source_block(discovered("S12.2(b)(2)", "voltage"), sources, "whole chunk") == "disconnect branch"
    assert _discovered_source_block(discovered("S12.20(a)", "voltage"), sources, "whole chunk") == "unrelated"


@pytest.mark.parametrize("body", [
    "Tires are inflated to the manufacturer's specifications.",
    "Measure the resistance.", "Voltage <= 20 V.",
    "If connected, the lamp illuminates.", "The cover shall be yellow.",
    "Doors lock automatically.",
])
def test_constraints_and_procedures_are_not_headings(body):
    assert not _is_source_heading("S12.2", "S12.2 " + body)


def test_discovery_does_not_force_heading_into_a_contract():
    response = RequirementDiscoveryResult(requirements=[
        discovered("S12.1", "Protection against contact.", "Protection against contact"),
        discovered("S12.1.1", "The cover shall prevent contact.", "Protective cover"),
    ])
    with patch("app.services.extraction.generate_structured", new=AsyncMock(return_value=response)) as call:
        result = asyncio.run(_discover_requirements(
            "S12.1 Protection against contact.\nS12.1.1 The cover shall prevent contact.",
            doc_name="invented.pdf", active_model="test", thinking_level=None,
            chunk_label="1", allow_rule_fallback=False, allow_model_fallback=False,
        ))
    assert [r.req_code for r in result] == ["S12.1.1"]
    assert call.await_count == 1
