"""ID-aware lexical retrieval and traceability (UND-07, review H3)."""

from app.services import retrieval
from app.services.requirement_ids import id_match_key, requirement_id_keys
from evaluation import document_understanding as du


def chunk(chunk_id, content):
    return {"id": chunk_id, "document_name": "report.pdf", "content": content, "metadata": {"block_type": "text"}}


QUERY = "REQ-NVA-ELE-003 Post-impact isolation resistance shall be at least 500 ohm/V"


def ids(results):
    return [item.chunk_id for item in results]


def test_match_keys_fold_case_separators_and_leading_zeros():
    assert id_match_key("REQ_NVA_ELE_003") == id_match_key("req-nva-ele-3") == id_match_key("REQ-NVA-ELE-003")
    assert id_match_key("REQ-NVA-ELE-030") != id_match_key("REQ-NVA-ELE-003")


def test_ranges_name_every_member():
    keys = requirement_id_keys("Covers REQ-NVA-ELE-001 to REQ-NVA-ELE-005 and REQ-NVA-THM-002.")
    assert id_match_key("REQ-NVA-ELE-004") in keys
    assert id_match_key("REQ-NVA-ELE-006") not in keys
    assert id_match_key("REQ-NVA-THM-002") in keys
    # A range across different prefixes is not expanded.
    assert id_match_key("REQ-B-002") not in requirement_id_keys("REQ-A-001 to REQ-B-003")


def test_differently_formatted_ids_are_kept_and_boosted():
    chunks = [
        chunk("underscored", "REQ_NVA_ELE_003 isolation resistance measured 650 ohm/V."),
        chunk("range", "Isolation checks for REQ-NVA-ELE-001 to REQ-NVA-ELE-005 passed."),
        chunk("other", "REQ-NVA-ELE-007 isolation resistance measured 650 ohm/V."),
    ]
    results = retrieval._bm25_retrieve(QUERY, chunks, top_k=5)
    assert set(ids(results)) == {"underscored", "range"}
    for item in results:
        assert item.metadata["requirement_id_match"] == "REQ-NVA-ELE-003"


def test_test_case_boost_requires_the_requirement_domain():
    chunks = [
        chunk("same-domain", "TC-ELE-003 isolation result recorded."),
        chunk("other-domain", "TC-THM-003 isolation result recorded."),
    ]
    scores = {item.chunk_id: item.score for item in retrieval._bm25_retrieve(QUERY, chunks, top_k=5)}
    assert scores["same-domain"] - scores["other-domain"] >= 6.0


def test_decimals_are_single_canonical_tokens():
    from app.services.embedding import tokenize

    assert tokenize("Within 5.0 s at 0.20 V; 2.00 A and S5.3 apply.") == [
        "within", "5", "s", "at", "0.2", "v", "2", "a", "and", "s5.3", "apply",
    ]


def test_a_decimal_does_not_number_boost_its_fragments():
    query = "REQ-X-001 ripple shall not exceed 0.2 V"
    chunks = [
        chunk("exact", "Ripple measured 0.2 V."),
        chunk("fragments", "Ripple measured on rail 2 with 0 faults."),
    ]
    scores = {item.chunk_id: item.score for item in retrieval._bm25_retrieve(query, chunks, top_k=5)}
    assert scores["exact"] > scores["fragments"] + 2.0


def test_nova_reports_rank_the_chunk_naming_each_requirement_first():
    chunks = []
    for pdf in sorted(du.DOCUMENT_DIR.glob("*Report*.pdf")):
        for item in du.parse_fixture_document(pdf).chunks:
            chunks.append({
                "id": f"{pdf.name}#{item.chunk_index}", "document_name": pdf.name,
                "content": item.content, "metadata": item.metadata, "page_number": item.page_number,
            })
    ranks = []
    for document in du.benchmark_documents():
        for requirement in document.get("requirements") or []:
            code = requirement["requirement_id"]
            query = f"{code} {requirement['title']} {requirement['requirement_text']}"
            results = retrieval._bm25_retrieve(query, chunks, top_k=5)
            naming = [
                index for index, item in enumerate(results)
                if id_match_key(code) in requirement_id_keys(item.content)
            ]
            ranks.append(naming[0] + 1 if naming else None)
    assert ranks == [1] * 48
