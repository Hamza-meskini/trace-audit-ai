"""Re-score saved outputs without inference. Original result files are preserved."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from evaluation.atomic_evaluation import decomposition, verification_metrics


def rescore(result_path: Path, truth_path: Path, extraction_path: Path | None = None) -> dict:
    result = json.loads(result_path.read_text(encoding="utf-8"))
    dataset = json.loads(truth_path.read_text(encoding="utf-8"))
    truth_hash = hashlib.sha256(truth_path.read_bytes()).hexdigest()
    recorded_hash = result.get("metrics", {}).get("integrity", {}).get("ground_truth_sha256")
    if recorded_hash and recorded_hash != truth_hash:
        raise ValueError("The reference dataset changed since this run")
    # Focused corpora such as FMVSS persist a separate evaluation-only ID map;
    # the full, unfiltered inference inputs remain in evaluated_contracts.
    contracts = result.get("scoring_contracts", result.get("evaluated_contracts"))
    provenance = "Embedded snapshot of inference contracts"
    if contracts is None:
        if extraction_path is None:
            raise ValueError("Legacy result has no contract snapshot; provide its original --extraction artifact")
        extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
        for key in ("benchmark_id", "benchmark_version", "model", "atomic_model"):
            if extraction.get(key) != result.get(key):
                raise ValueError(f"Extraction/result mismatch: {key}")
        contracts = [row for rows in extraction["predictions"].values() for row in rows]
        provenance = "Legacy reconstruction from explicitly supplied extraction artifact; exact historical pairing is not cryptographically verified"
    score = decomposition(dataset["requirements"], contracts)
    return {
        "source_result": str(result_path.resolve()), "contract_provenance": provenance,
        "source_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "extraction_sha256": hashlib.sha256(extraction_path.read_bytes()).hexdigest() if extraction_path else None,
        "ground_truth_sha256": truth_hash,
        "scorer_sha256": hashlib.sha256((REPO / "evaluation" / "atomic_evaluation.py").read_bytes()).hexdigest(),
        "previous_final_atomic": result["metrics"]["final_atomic"],
        "structured_decomposition": score,
        **{key: verification_metrics(dataset["requirements"],
                                    {r["requirement_id"]: r[field] for r in result["requirements"]}, score)
           for key, field in (("final_atomic", "final_condition_results"), ("raw_llm_atomic", "raw_llm_condition_results"))},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("--truth", type=Path, default=REPO / "evaluation/extraction_benchmark/ground_truth.json")
    parser.add_argument("--extraction", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = rescore(args.result, args.truth, args.extraction)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(json.dumps({key: {k: v for k, v in payload[key].items() if k not in ("details", "mismatches", "policy")}
                      for key in ("structured_decomposition", "final_atomic")}, indent=2))
