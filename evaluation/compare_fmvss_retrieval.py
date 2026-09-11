"""Paired retrieval diagnostic; oracle contracts never enter the production run.

Uses the same report corpus and retriever for predicted and reference queries.
Only clauses present in the prediction snapshot are compared, so discovery
misses do not masquerade as retrieval failures. No verdicts are generated.
"""
import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.run_fmvss305_benchmark import (
    _condition_query, _document_paths, _ingest_document, _load_dataset,
    _oracle_contracts, _retrieval_metrics,
    precompute_chunk_embeddings, retrieve_candidate_evidence_hybrid,
)
from app.services.extraction import _normalize_requirement_code


def paired_contracts(requirements, predictions):
    by_code = {}
    for item in predictions:
        code = _normalize_requirement_code(item["req_code"])
        if code in by_code:
            raise ValueError(f"Duplicate predicted clause: {code}")
        by_code[code] = item
    oracle, _ = _oracle_contracts(requirements)
    pairs = []
    missing = []
    for truth, reference in zip(requirements, oracle):
        predicted = by_code.get(_normalize_requirement_code(truth["clause"]))
        if predicted is None:
            missing.append(truth["requirement_id"])
        else:
            pairs.append((truth, predicted, reference))
    return pairs, missing


async def compare(contracts_path, output_path):
    if output_path.exists():
        raise FileExistsError(output_path)
    dataset = _load_dataset()
    raw = contracts_path.read_bytes()
    predictions = json.loads(raw)
    pairs, missing = paired_contracts(dataset["requirements"], predictions)
    if not pairs:
        raise ValueError("No matching clauses in the supplied prediction snapshot")
    _, report = _document_paths(dataset)
    chunks = _ingest_document(report, "Test report")
    embeddings = await precompute_chunk_embeddings(chunks)
    retrieved = {"predicted": {}, "reference": {}}
    for index, (truth, predicted, reference) in enumerate(pairs, 1):
        for arm, contract in (("predicted", predicted), ("reference", reference)):
            # Use the same source clause ID in both arms. Reference evidence
            # pages/statuses are used only by the evaluator below, never queries.
            candidates = await retrieve_candidate_evidence_hybrid(
                requirement_text=f"{contract['title']} {contract['description']}",
                chunks=chunks, chunk_embeddings=embeddings, top_k=5, min_score=0.3,
                condition_queries=[_condition_query(truth["clause"], c)
                                   for c in contract.get("conditions", [])],
            )
            retrieved[arm][truth["requirement_id"]] = [
                {"page_number": c.page_number, "chunk_id": c.chunk_id}
                for c in candidates
            ]
        print(f"Paired retrieval {index}/{len(pairs)}: {truth['clause']}", flush=True)
    selected = [pair[0] for pair in pairs]
    result = {
        "diagnostic": "paired queries on matched clauses; not end-to-end accuracy",
        "contracts_sha256": hashlib.sha256(raw).hexdigest(),
        "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
        "reference_sha256": hashlib.sha256(json.dumps(dataset, sort_keys=True).encode()).hexdigest(),
        "matched_clauses": len(pairs), "excluded_discovery_misses": missing,
        "chunks_with_embeddings": sum(bool(e) for e in embeddings),
        "total_chunks": len(chunks),
        "limitations": "Reference queries use annotated contracts. Embedding failures may trigger the retriever's normal BM25 fallback. This is a development-corpus diagnostic, not a holdout evaluation.",
        "metrics": {arm: _retrieval_metrics(selected, values) for arm, values in retrieved.items()},
    }
    with output_path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    for arm, metrics in result["metrics"].items():
        print(f"{arm}: Recall@3={metrics['recall_at_3']}%, Recall@5={metrics['recall_at_5']}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(compare(args.contracts, args.output))
