"""Run the TraceAudit multimodal diagnostic benchmark.

The evaluator keeps document identity in every provenance key. This avoids the
page-number collision that occurs when several evidence documents all have a
page 2 or page 3.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable


REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
BENCHMARK = REPO / "evaluation" / "multimodal_benchmark"
DOCS = BENCHMARK / "documents"
RESULTS = REPO / "evaluation" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(BACKEND))

from app.config import settings
from app.services.classification import batch_assess_requirements
from app.services.document_classifier import profile_documents
from app.services.extraction import extract_requirements_from_text
from app.services.retrieval import precompute_chunk_embeddings, retrieve_candidate_evidence_hybrid
from app.services.visual_analysis import describe_figure_candidates
from evaluation.run_fmvss305_benchmark import (
    ATOMIC_CLASSES,
    FINAL_CLASSES,
    _atomic_metrics,
    _condition_query,
    _extracted_contracts,
    _field,
    _ingest_document,
    _macro_f1,
    _normal_final,
    _oracle_contracts,
    _percent,
    _quote_coverage,
)
from evaluation.multimodal_benchmark.validate_multimodal_benchmark import validate


MODES = {
    "oracle-contracts-evidence": (True, True),
    "oracle-contracts": (True, False),
    "end-to-end": (False, False),
}
MULTIMODAL_DEFAULT_MODEL = "system.ai.llama-4-maverick"


def load_dataset() -> dict[str, Any]:
    return json.loads((BENCHMARK / "ground_truth.json").read_text(encoding="utf-8"))


def all_documents(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    return [dataset["documents"]["requirements"], *dataset["documents"]["evidence"]]


def oracle_evidence(requirement: dict[str, Any], chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select annotated blocks by document and page, preserving local context."""
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    index_by_id = {chunk["id"]: index for index, chunk in enumerate(chunks)}

    def add(chunk: dict[str, Any]) -> None:
        if chunk["id"] not in selected_ids:
            selected_ids.add(chunk["id"])
            selected.append(dict(chunk, score=1.0))

    for condition in requirement.get("conditions", []):
        for annotation in condition.get("evidence", []):
            candidates = [
                chunk for chunk in chunks
                if chunk.get("document_name") == annotation.get("document")
                and chunk.get("page_number") == annotation.get("page")
            ]
            if not candidates:
                continue
            best = max(candidates, key=lambda item: _quote_coverage(annotation.get("quote", ""), item.get("content", "")))
            add(best)
            block_type = str((best.get("metadata") or {}).get("block_type") or "").lower()
            if block_type in {"table", "figure", "formula", "checkbox"}:
                index = index_by_id[best["id"]]
                for neighbor_index in (index - 1, index + 1):
                    if 0 <= neighbor_index < len(chunks):
                        neighbor = chunks[neighbor_index]
                        if neighbor.get("document_name") == best.get("document_name") and neighbor.get("page_number") == best.get("page_number"):
                            add(neighbor)
    return selected


def evidence_keys(requirement: dict[str, Any]) -> set[tuple[str, int]]:
    return {
        (evidence["document"], int(evidence["page"]))
        for condition in requirement.get("conditions", [])
        for evidence in condition.get("evidence", [])
    }


def retrieval_metrics(requirements: list[dict[str, Any]], retrieved: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    hits = {1: 0, 3: 0, 5: 0}
    full = {1: 0, 3: 0, 5: 0}
    any_hits = {1: 0, 3: 0, 5: 0}
    expected_total = 0
    reciprocal: list[float] = []
    details: dict[str, Any] = {}
    for requirement in requirements:
        expected = evidence_keys(requirement)
        expected_total += len(expected)
        ranked = [(item.get("document_name"), int(item.get("page_number") or 0)) for item in retrieved.get(requirement["requirement_id"], [])]
        first = next((index for index, key in enumerate(ranked[:5], 1) if key in expected), None)
        reciprocal.append(1 / first if first else 0.0)
        for k in hits:
            found = expected & set(ranked[:k])
            hits[k] += len(found)
            any_hits[k] += int(bool(found))
            full[k] += int(expected.issubset(set(ranked[:k])))
        details[requirement["requirement_id"]] = {
            "expected_document_pages": sorted([f"{doc}:p{page}" for doc, page in expected]),
            "retrieved_document_pages": [f"{doc}:p{page}" for doc, page in ranked],
            "first_hit_rank": first,
        }
    total_requirements = len(requirements)
    return {
        "expected_document_pages": expected_total,
        **{f"recall_at_{k}": _percent(hits[k], expected_total) for k in hits},
        **{f"requirement_any_hit_at_{k}": _percent(any_hits[k], total_requirements) for k in any_hits},
        **{f"requirement_full_coverage_at_{k}": _percent(full[k], total_requirements) for k in full},
        "mrr": round(sum(reciprocal) / len(reciprocal), 4) if reciprocal else 0.0,
        "details": details,
    }


def profile_metrics(dataset: dict[str, Any], profiles: dict[str, Any]) -> dict[str, Any]:
    rows=[]
    for document in all_documents(dataset):
        profile=profiles.get(Path(document["filename"]).stem)
        predicted_role=str(_field(profile,"primary_role", ""))
        predicted_basis=str(_field(profile,"verification_basis", ""))
        role_ok=predicted_role == document.get("expected_role")
        basis_ok=predicted_basis == document.get("expected_verification_basis")
        rows.append({"document":document["filename"],"expected_role":document.get("expected_role"),"predicted_role":predicted_role,"role_correct":role_ok,"expected_verification_basis":document.get("expected_verification_basis"),"predicted_verification_basis":predicted_basis,"basis_correct":basis_ok,"confidence":_field(profile,"confidence",0.0)})
    return {"role_accuracy":_percent(sum(item["role_correct"] for item in rows),len(rows)),"verification_basis_accuracy":_percent(sum(item["basis_correct"] for item in rows),len(rows)),"documents":rows}


def report(results: dict[str, Any]) -> str:
    m=results["metrics"]
    lines=[
        "# TraceAudit Multimodal Diagnostic Benchmark",
        "",
        f"- Mode: `{results['mode']}`",
        f"- Model: `{results['model']}`",
        f"- Final accuracy: **{m['final_verdict']['accuracy']:.2f}%**",
        f"- Final atomic accuracy: **{m['final_atomic']['accuracy']:.2f}%**",
        f"- Document role accuracy: **{m['document_profile']['role_accuracy']:.2f}%**",
        f"- Retrieval Recall@3 (document + page): **{m['retrieval']['recall_at_3']:.2f}%**",
        f"- Unsafe false auto-closes: **{m['review_gate']['unsafe_false_auto_closes']}**",
        "",
        "## Requirement results",
        "",
        "| Requirement | Logic | Expected | Predicted | Review | Correct |",
        "|---|---|---|---|---|---|",
    ]
    for row in results["requirements"]:
        lines.append(f"| {row['requirement_id']} | {row['logic_operator']} | {row['expected_status']} | {row['predicted_status']} | {row['predicted_review_state']} | {'yes' if row['correct'] else 'no'} |")
    lines += ["", "## Interpretation", "", "`oracle-contracts-evidence` isolates document profiling, visual understanding, condition reasoning and aggregation. `oracle-contracts` adds real retrieval. `end-to-end` adds requirement and atomic-condition extraction."]
    return "\n".join(lines)


async def run(mode: str, model: str | None, thinking_level: str | None, batch_size: int) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode}")
    validation=validate()
    if not validation["valid"]:
        raise ValueError("Invalid benchmark: " + "; ".join(validation["errors"]))
    oracle_contracts, oracle_passages=MODES[mode]
    dataset=load_dataset(); truth=dataset["requirements"]; truth_by_id={item["requirement_id"]:item for item in truth}
    active_model=model or MULTIMODAL_DEFAULT_MODEL; active_thinking=thinking_level or settings.GEMINI_THINKING_LEVEL
    started=time.time()
    print("="*78); print("TRACEAUDIT MULTIMODAL DIAGNOSTIC BENCHMARK"); print(f"Mode: {mode} | Model: {active_model}"); print("="*78)

    print("[1/5] Ingesting structured PDFs...")
    document_specs=all_documents(dataset); chunks_by_name={}; all_chunks=[]
    for spec in document_specs:
        path=DOCS/spec["filename"]
        chunks=_ingest_document(path,spec["doc_type"])
        chunks_by_name[spec["filename"]]=chunks; all_chunks.extend(chunks)
        pages=len({item.get("page_number") for item in chunks if item.get("page_number")})
        print(f"  {spec['filename']}: {len(chunks)} chunks across {pages} pages")

    print("[2/5] Profiling documents and preparing contracts...")
    profiles=await profile_documents(documents=[SimpleNamespace(id=Path(spec["filename"]).stem,original_filename=spec["filename"]) for spec in document_specs],all_chunks=all_chunks,model=active_model,thinking_level=active_thinking)
    for spec in document_specs:
        profile_data=profiles[Path(spec["filename"]).stem].model_dump()
        for chunk in chunks_by_name[spec["filename"]]: chunk["document_profile"]=profile_data
    profile_score=profile_metrics(dataset,profiles)
    requirement_spec=dataset["documents"]["requirements"]
    requirement_chunks=chunks_by_name[requirement_spec["filename"]]
    if oracle_contracts:
        contracts, extraction_metrics=_oracle_contracts(truth)
    else:
        extracted=await extract_requirements_from_text(text="\n\n".join(item["content"] for item in requirement_chunks),doc_name=requirement_spec["filename"],model=active_model,thinking_level=active_thinking)
        contracts, extraction_metrics=_extracted_contracts(truth,extracted)
    print(f"  Document role accuracy: {profile_score['role_accuracy']:.2f}%")
    print(f"  Selected requirement recall: {extraction_metrics['selected_clause_recall']:.2f}%")

    print("[3/5] Retrieving evidence with document-aware scoring...")
    evidence_names={item["filename"] for item in dataset["documents"]["evidence"]}
    evidence_chunks=[item for name in evidence_names for item in chunks_by_name[name]]
    embeddings=await precompute_chunk_embeddings(evidence_chunks)
    retrieved_by_id={}
    for item in contracts:
        retrieved=await retrieve_candidate_evidence_hybrid(requirement_text=f"{item['title']} {item['description']}",chunks=evidence_chunks,chunk_embeddings=embeddings,top_k=5,min_score=0.25,condition_queries=[_condition_query(item["req_code"],condition) for condition in item.get("conditions",[])])
        retrieved_by_id[item["req_code"]]=[{"id":value.chunk_id,"chunk_id":value.chunk_id,"document_id":value.document_id,"document_name":value.document_name,"doc_type":value.doc_type,"page_number":value.page_number,"content":value.content,"score":value.score,"document_profile":value.document_profile,"metadata":value.metadata} for value in retrieved]
    retrieval_score=retrieval_metrics(truth,retrieved_by_id)
    print(f"  Evidence Recall@3: {retrieval_score['recall_at_3']:.2f}%")

    print("[4/5] Enriching retrieved figures and verifying conditions...")
    req_items=[]
    for item in contracts:
        candidates=oracle_evidence(truth_by_id[item["req_code"]],evidence_chunks) if oracle_passages else retrieved_by_id.get(item["req_code"],[])
        req_items.append({**item,"candidate_chunks":candidates})
    visual=await describe_figure_candidates(req_items,{Path(name).stem:str(DOCS/name) for name in evidence_names},model=active_model)
    assessments=await batch_assess_requirements(req_items=req_items,model=active_model,thinking_level=active_thinking,batch_size=batch_size,spec_doc_names={requirement_spec["filename"]})

    print("[5/5] Scoring predictions...")
    matrix={expected:{predicted:0 for predicted in FINAL_CLASSES} for expected in FINAL_CLASSES}
    final_conditions:dict[str,Iterable[Any]]={}; raw_conditions:dict[str,Iterable[Any]]={}; rows=[]
    false_supported=0; unsafe=0; review_correct=0
    for expected in truth:
        req_id=expected["requirement_id"]; assessment=assessments.get(req_id); predicted=_normal_final(_field(assessment,"coverage_status")) if assessment else "UNKNOWN"
        matrix[expected["expected_status"]][predicted]+=1
        review=_field(assessment,"review_state","Needs review") if assessment else "Needs review"
        review_match=str(review).lower()==expected["expected_review_state"].lower(); review_correct+=int(review_match)
        is_false=predicted=="SUPPORTED" and expected["expected_status"]!="SUPPORTED"; false_supported+=int(is_false); unsafe+=int(is_false and review=="Reviewed")
        diagnostics=_field(assessment,"pipeline_diagnostics",{}) or {}; raw=diagnostics.get("llm_condition_results",[]); final=list(_field(assessment,"condition_results",[]) or [])
        raw_conditions[req_id]=raw; final_conditions[req_id]=final
        rows.append({"requirement_id":req_id,"logic_operator":expected["logic"]["operator"],"expected_status":expected["expected_status"],"predicted_status":predicted,"correct":predicted==expected["expected_status"],"expected_review_state":expected["expected_review_state"],"predicted_review_state":review,"review_correct":review_match,"confidence":_field(assessment,"confidence",0.0) if assessment else 0.0,"ai_analysis":_field(assessment,"ai_analysis","") if assessment else "Requirement not extracted","retrieved_document_pages":[f"{item.get('document_name')}:p{item.get('page_number')}" for item in retrieved_by_id.get(req_id,[])],"raw_llm_condition_results":raw,"final_condition_results":[item.model_dump() if hasattr(item,"model_dump") else item for item in final],"pipeline_diagnostics":diagnostics})
    correct=sum(matrix[label][label] for label in FINAL_CLASSES)
    final_metrics={"accuracy":_percent(correct,len(truth)),"macro_f1":_macro_f1(matrix),"correct":correct,"total":len(truth),"confusion_matrix":matrix,"expected_distribution":dict(Counter(item["expected_status"] for item in truth)),"predicted_distribution":dict(Counter(item["predicted_status"] for item in rows))}
    metrics={"ingestion":validation["counts"],"document_profile":profile_score,"extraction":extraction_metrics,"retrieval":retrieval_score,"visual_evidence":visual,"raw_llm_atomic":_atomic_metrics(truth,raw_conditions),"final_atomic":_atomic_metrics(truth,final_conditions),"final_verdict":final_metrics,"review_gate":{"review_state_accuracy":_percent(review_correct,len(truth)),"false_supported":false_supported,"unsafe_false_auto_closes":unsafe}}
    result={"benchmark_id":dataset["benchmark_id"],"benchmark_version":dataset["version"],"mode":mode,"model":active_model,"thinking_level":active_thinking,"runtime_seconds":round(time.time()-started,2),"validation":validation,"documents":dataset["documents"],"document_profiles":{spec["filename"]:profiles[Path(spec["filename"]).stem].model_dump() for spec in document_specs},"metrics":metrics,"requirements":rows}
    model_slug="".join(character if character.isalnum() else "-" for character in active_model.lower()).strip("-")
    json_path=RESULTS/f"multimodal_{mode}_{model_slug}_results.json"; md_path=RESULTS/f"multimodal_{mode}_{model_slug}_report.md"
    json_path.write_text(json.dumps(result,indent=2,default=str),encoding="utf-8"); md_path.write_text(report(result),encoding="utf-8")
    print("Benchmark complete"); print(f"Final accuracy: {final_metrics['accuracy']:.2f}%"); print(f"Final atomic accuracy: {metrics['final_atomic']['accuracy']:.2f}%"); print(f"Unsafe false auto-closes: {unsafe}"); print(f"JSON: {json_path}"); print(f"Report: {md_path}")
    return result


def parse_args() -> argparse.Namespace:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode",choices=sorted(MODES),default="oracle-contracts-evidence")
    parser.add_argument(
        "--model",
        default=MULTIMODAL_DEFAULT_MODEL,
        help=f"Primary reasoning model (default: {MULTIMODAL_DEFAULT_MODEL})",
    )
    parser.add_argument("--thinking-level",default=None)
    parser.add_argument("--batch-size",type=int,default=3)
    parser.add_argument("--validate-only",action="store_true")
    return parser.parse_args()


def main() -> int:
    args=parse_args()
    if args.validate_only:
        result=validate(); print(json.dumps({key:result[key] for key in ("valid","errors","warnings","counts","final_distribution","atomic_distribution","document_diagnostics")},indent=2)); return 0 if result["valid"] else 1
    asyncio.run(run(args.mode,args.model,args.thinking_level,args.batch_size)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
