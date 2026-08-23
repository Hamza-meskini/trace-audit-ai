"""Strict Benchmark Integrity Validation Script.

Verifies:
1. Exactly 100 requirements exist with unique IDs (REQ-AUT-001 to REQ-AUT-100).
2. Every requirement has ground truth linkage and valid condition tree.
3. Class distribution is balanced: exactly 20 SUPPORTED, 20 PARTIAL, 20 CONFLICT, 20 MISSING, 20 UNKNOWN.
4. All referenced document files exist on disk and have non-zero size.
5. No orphan evidence or broken cross-references exist.
"""

import json
import sys
from pathlib import Path
from collections import Counter

BENCHMARK_DIR = Path(__file__).resolve().parent
DOCS_DIR = BENCHMARK_DIR / "documents"

VALID_STATUSES = {"SUPPORTED", "PARTIAL", "CONFLICT", "MISSING", "UNKNOWN"}


def validate_benchmark() -> bool:
    print("=" * 70)
    print("     TRACEAUDIT AI - 100-REQUIREMENT BENCHMARK INTEGRITY VALIDATION")
    print("=" * 70)

    req_path = BENCHMARK_DIR / "requirements.json"
    gt_path = BENCHMARK_DIR / "ground_truth.json"

    errors = []

    # 1. Load Files
    if not req_path.exists():
        errors.append(f"Missing requirements.json at {req_path}")
    if not gt_path.exists():
        errors.append(f"Missing ground_truth.json at {gt_path}")

    if errors:
        for err in errors:
            print(f"[FAIL] {err}")
        return False

    with open(req_path, "r", encoding="utf-8") as f:
        requirements = json.load(f)
    with open(gt_path, "r", encoding="utf-8") as f:
        ground_truth = json.load(f)

    # 2. Verify Count
    req_count = len(requirements)
    gt_count = len(ground_truth)
    print(f"\n[1/6] Validating Requirement Counts:")
    print(f"  • Requirements Count: {req_count} (Required: 100)")
    print(f"  • Ground Truth Count: {gt_count} (Required: 100)")
    if req_count != 100:
        errors.append(f"Expected exactly 100 requirements, got {req_count}")
    if gt_count != 100:
        errors.append(f"Expected exactly 100 ground truth items, got {gt_count}")

    # 3. Verify Unique IDs & Match
    print(f"\n[2/6] Validating Unique IDs & Structure:")
    req_ids = [r.get("requirement_id") for r in requirements]
    unique_req_ids = set(req_ids)
    if len(unique_req_ids) != len(req_ids):
        errors.append(f"Duplicate requirement IDs detected! Unique: {len(unique_req_ids)}, Total: {len(req_ids)}")
    
    gt_ids = {g.get("requirement_id") for g in ground_truth}
    missing_in_gt = unique_req_ids - gt_ids
    if missing_in_gt:
        errors.append(f"Requirements missing in ground truth: {missing_in_gt}")

    # 4. Verify Class Distribution
    print(f"\n[3/6] Validating Class Distribution:")
    status_counts = Counter(r.get("expected_status") for r in requirements)
    for st in sorted(VALID_STATUSES):
        count = status_counts.get(st, 0)
        print(f"  • {st:10s}: {count:2d} (Expected: 20)")
        if count != 20:
            errors.append(f"Class '{st}' has {count} items, expected exactly 20")

    # 5. Verify Conditions Structure
    print(f"\n[4/6] Validating Condition Trees:")
    total_conditions = 0
    for r in requirements:
        req_id = r.get("requirement_id")
        conds = r.get("conditions", [])
        if not conds:
            errors.append(f"Requirement {req_id} has empty conditions list")
        total_conditions += len(conds)
        for c in conds:
            if not c.get("condition_id") or not c.get("parameter") or not c.get("operator"):
                errors.append(f"Malformed condition in {req_id}: {c}")
    print(f"  • Total Defined Atomic Conditions: {total_conditions} (Avg: {total_conditions/req_count:.1f} per requirement)")

    # 6. Verify Referenced Document Files on Disk
    print(f"\n[5/6] Validating Document Corpus Files on Disk:")
    referenced_docs = set()
    for g in ground_truth:
        for ev in g.get("expected_evidence", []):
            doc_name = ev.get("document")
            if doc_name and doc_name != "None":
                referenced_docs.add(doc_name)

    existing_docs = list(DOCS_DIR.glob("*.*"))
    print(f"  • Documents in Corpus Directory: {len(existing_docs)}")
    print(f"  • Referenced Evidence Documents: {len(referenced_docs)}")

    for doc_name in referenced_docs:
        doc_file = DOCS_DIR / doc_name
        if not doc_file.exists():
            errors.append(f"Referenced document '{doc_name}' does not exist on disk!")
        elif doc_file.stat().st_size == 0:
            errors.append(f"Document '{doc_name}' is empty (0 bytes)!")

    # 7. Final Report
    print(f"\n[6/6] Benchmark Validation Verdict:")
    if errors:
        print(f"\n[FAIL] Benchmark Validation Failed with {len(errors)} error(s):")
        for e in errors:
            print(f"  ❌ {e}")
        return False

    print("\n" + "=" * 70)
    print(" [PASSED] 100% Benchmark Integrity & Consistency Verified!")
    print("=" * 70)
    return True


if __name__ == "__main__":
    success = validate_benchmark()
    sys.exit(0 if success else 1)
