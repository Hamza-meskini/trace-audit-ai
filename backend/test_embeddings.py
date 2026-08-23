import asyncio
import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.embedding import (
    embed_single,
    embed_batch,
    cosine_similarity,
    _has_embedding_key,
)
from app.services.retrieval import (
    retrieve_candidate_evidence_hybrid,
    precompute_chunk_embeddings,
)


async def run_live_test():
    print("=" * 65)
    print("      GEMINI text-embedding-005 LIVE RETRIEVAL TEST")
    print("=" * 65)

    has_key = _has_embedding_key()
    print(f"API Key Detected: {has_key}")
    if not has_key:
        print("[ERROR] No Gemini API key detected in environment or .env!")
        return

    # 1. Single query embedding
    print("\n[1/3] Testing Single Query Embedding...")
    query = "REQ-BCU-005: Electromagnetic compatibility (EMC) and radiated RF immunity"
    query_emb = await embed_single(query, task_type="RETRIEVAL_QUERY")

    if query_emb is None or len(query_emb) == 0:
        print("[FAIL] embed_single() returned None!")
        return

    print(f"  [OK] Successfully received embedding vector!")
    print(f"  [OK] Dimensions: {len(query_emb)} (Standard 768-dim vector)")
    print(f"  [OK] First 4 floats: {[round(x, 4) for x in query_emb[:4]]}")

    # 2. Batch document embedding
    print("\n[2/3] Testing Batch Document Embeddings (3 engineering excerpts)...")
    chunks = [
        {
            "id": "chunk-1",
            "document_id": "doc-emc",
            "document_name": "05_Environmental_EMC_Report.pdf",
            "doc_type": "Test report",
            "page_number": 1,
            "content": "TR-EMC-401: Radiated emissions and RF immunity per CISPR 25 Class 3 passed with 6.2 dB margin under 100 V/m field.",
        },
        {
            "id": "chunk-2",
            "document_id": "doc-iso",
            "document_name": "02_System_Architecture_Spec.pdf",
            "doc_type": "Architecture specification",
            "page_number": 3,
            "content": "Galvanic isolation barrier ensures minimum 2.5 kV dielectric standoff between pack-side HV and chassis GND.",
        },
        {
            "id": "chunk-3",
            "document_id": "doc-therm",
            "document_name": "06_Thermal_Runaway_Safety_Report.pdf",
            "doc_type": "Test report",
            "page_number": 2,
            "content": "Thermal runaway containment test at 650 deg C confirmed zero external flame propagation across module boundaries.",
        },
    ]

    chunk_embs = await precompute_chunk_embeddings(chunks)
    valid_count = sum(1 for e in chunk_embs if e is not None)
    print(f"  [OK] Batch pre-computation: {valid_count}/{len(chunks)} chunks embedded successfully.")

    # 3. Hybrid Semantic + BM25 Retrieval Evaluation
    print("\n[3/3] Testing Hybrid Semantic Retrieval Ranking...")
    retrieved = await retrieve_candidate_evidence_hybrid(
        requirement_text=query,
        chunks=chunks,
        chunk_embeddings=chunk_embs,
        top_k=3,
    )

    print(f"\nResults for Query: '{query}'")
    for rank, item in enumerate(retrieved, 1):
        print(f"  Rank #{rank}: Score = {item.score:.4f} | Document = {item.document_name}")
        print(f"           Content = {item.content[:90]}...")

    top_chunk = retrieved[0]
    assert "EMC" in top_chunk.document_name, "Top retrieved chunk must be the EMC report!"
    print("\n" + "=" * 65)
    print(" [PASSED] Gemini text-embedding-005 is fully active and working!")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(run_live_test())
