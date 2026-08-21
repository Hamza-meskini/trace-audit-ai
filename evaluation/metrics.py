"""Quantitative metrics calculation and failure classification engine for TraceAudit benchmarks.

Implements evaluation metrics for:
  1. Requirement Extraction (Precision, Recall, F1)
  2. Evidence Retrieval (Recall@1, Recall@3, Recall@5, MRR)
  3. Verification Classification (Accuracy, Confusion Matrix, Per-class Precision/Recall/F1)
  4. Conflict Detection (Precision, Recall, F1)
  5. Missing Evidence Detection (Precision, Recall, F1)
  6. Unsupported Claim Rate (Hallucination / False-Verification rate on missing evidence)
  7. Detailed failure classification
"""

from typing import Any, Optional
from dataclasses import dataclass, field
import re


# Standard normalized status labels
VALID_STATUSES = ["Supported", "Partial", "Missing", "Conflict"]

STATUS_ALIASES = {
    "supported": "Supported",
    "verified": "Supported",
    "pass": "Supported",
    "partial": "Partial",
    "partially_supported": "Partial",
    "partial evidence": "Partial",
    "missing": "Missing",
    "missing evidence": "Missing",
    "not_covered": "Missing",
    "conflict": "Conflict",
    "potential conflict": "Conflict",
    "contradiction": "Conflict",
    "potential_conflict": "Conflict",
}


def normalize_status(status_str: Optional[str]) -> str:
    """Normalize status string to standard capitalized name."""
    if not status_str:
        return "Missing"
    cleaned = status_str.strip().lower()
    return STATUS_ALIASES.get(cleaned, "Missing")


def normalize_code(code_str: Optional[str]) -> str:
    """Normalize requirement code string for fuzzy matching (e.g. 'REQ_BCU_001' -> 'REQ-BCU-001')."""
    if not code_str:
        return ""
    cleaned = code_str.strip().upper()
    cleaned = re.sub(r"[_\s]+", "-", cleaned)
    return cleaned


# ==============================================================================
# 1. REQUIREMENT EXTRACTION METRICS
# ==============================================================================

@dataclass
class ExtractionMetrics:
    total_ground_truth: int
    total_extracted: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    matched_codes: list[str] = field(default_factory=list)
    unmatched_ground_truth: list[str] = field(default_factory=list)
    extra_extracted: list[str] = field(default_factory=list)


def evaluate_extraction(
    ground_truth_reqs: list[dict[str, Any]],
    extracted_reqs: list[Any],
) -> ExtractionMetrics:
    """Compare extracted requirements against ground truth requirements."""
    gt_codes = {normalize_code(r.get("req_code") or r.get("requirement_id")): r for r in ground_truth_reqs}
    
    extracted_dict = {}
    for er in extracted_reqs:
        code = er.req_code if hasattr(er, "req_code") else er.get("req_code", "")
        code_norm = normalize_code(code)
        if code_norm:
            extracted_dict[code_norm] = er

    matched = []
    for code in gt_codes:
        if code in extracted_dict:
            matched.append(code)
        else:
            # Check for title similarity fallback
            gt_title = gt_codes[code].get("title", "").lower()
            for ext_code, ext_obj in extracted_dict.items():
                ext_title = (ext_obj.title if hasattr(ext_obj, "title") else ext_obj.get("title", "")).lower()
                if ext_title and (gt_title in ext_title or ext_title in gt_title):
                    matched.append(code)
                    break

    tp = len(set(matched))
    fn = len(gt_codes) - tp
    fp = max(0, len(extracted_dict) - tp)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

    return ExtractionMetrics(
        total_ground_truth=len(gt_codes),
        total_extracted=len(extracted_dict),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=round(precision * 100.0, 2),
        recall=round(recall * 100.0, 2),
        f1=round(f1 * 100.0, 2),
        matched_codes=matched,
        unmatched_ground_truth=[c for c in gt_codes if c not in matched],
        extra_extracted=[c for c in extracted_dict if c not in matched],
    )


# ==============================================================================
# 2. EVIDENCE RETRIEVAL METRICS (Recall@K, MRR)
# ==============================================================================

@dataclass
class RetrievalMetrics:
    total_queries: int
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    mean_reciprocal_rank: float
    detailed_hits: dict[str, Any] = field(default_factory=dict)


def _is_evidence_hit(retrieved_chunk: dict[str, Any], expected_sources: list[dict[str, Any]]) -> bool:
    """Determine if a retrieved chunk matches any expected ground truth source."""
    chunk_doc = (retrieved_chunk.get("document_name") or retrieved_chunk.get("document") or "").lower()
    chunk_page = retrieved_chunk.get("page_number")
    chunk_content = (retrieved_chunk.get("content") or retrieved_chunk.get("quote") or "").lower()

    for exp in expected_sources:
        exp_doc = (exp.get("document") or "").lower()
        exp_page = exp.get("page")
        exp_quote = (exp.get("quote") or "").lower()

        # Match by doc name & page
        if exp_doc and exp_doc in chunk_doc or chunk_doc in exp_doc:
            if exp_page is None or chunk_page is None or exp_page == chunk_page:
                return True
        
        # Match by quote overlap
        if exp_quote and len(exp_quote) > 15:
            overlap_words = [w for w in re.findall(r"\w+", exp_quote) if len(w) > 4]
            if overlap_words:
                match_count = sum(1 for w in overlap_words if w in chunk_content)
                if match_count / len(overlap_words) >= 0.5:
                    return True

    return False


def evaluate_retrieval(
    ground_truth_links: list[dict[str, Any]],
    retrieved_by_req: dict[str, list[dict[str, Any]]],
) -> RetrievalMetrics:
    """Compute Recall@1, Recall@3, Recall@5, and Mean Reciprocal Rank (MRR)."""
    # Group expected sources by requirement code
    gt_sources_by_req: dict[str, list[dict[str, Any]]] = {}
    for link in ground_truth_links:
        req_code = normalize_code(link.get("req_code") or link.get("requirement_id"))
        gt_sources_by_req.setdefault(req_code, []).append(link)

    # Filter only requirements that actually have expected evidence
    query_reqs = {k: v for k, v in gt_sources_by_req.items() if len(v) > 0}
    total_queries = len(query_reqs)
    if total_queries == 0:
        return RetrievalMetrics(0, 0.0, 0.0, 0.0, 0.0)

    hits_at_1 = 0
    hits_at_3 = 0
    hits_at_5 = 0
    reciprocal_ranks = []
    detailed_hits = {}

    for req_code, expected_sources in query_reqs.items():
        retrieved_chunks = retrieved_by_req.get(req_code, [])
        first_hit_rank = None

        for rank, chunk in enumerate(retrieved_chunks[:5], start=1):
            if _is_evidence_hit(chunk, expected_sources):
                if first_hit_rank is None:
                    first_hit_rank = rank
                if rank <= 1:
                    hits_at_1 += 1
                if rank <= 3:
                    hits_at_3 += 1
                if rank <= 5:
                    hits_at_5 += 1
                break  # count hit once per query

        detailed_hits[req_code] = {
            "first_hit_rank": first_hit_rank,
            "retrieved_count": len(retrieved_chunks),
            "expected_count": len(expected_sources),
        }

        if first_hit_rank is not None:
            reciprocal_ranks.append(1.0 / first_hit_rank)
        else:
            reciprocal_ranks.append(0.0)

    r1 = (hits_at_1 / total_queries) * 100.0
    r3 = (hits_at_3 / total_queries) * 100.0
    r5 = (hits_at_5 / total_queries) * 100.0
    mrr = (sum(reciprocal_ranks) / total_queries)

    return RetrievalMetrics(
        total_queries=total_queries,
        recall_at_1=round(r1, 2),
        recall_at_3=round(r3, 2),
        recall_at_5=round(r5, 2),
        mean_reciprocal_rank=round(mrr, 4),
        detailed_hits=detailed_hits,
    )


# ==============================================================================
# 3. VERIFICATION CLASSIFICATION METRICS
# ==============================================================================

@dataclass
class ClassMetrics:
    precision: float
    recall: float
    f1: float
    support: int
    true_positives: int
    false_positives: int
    false_negatives: int


@dataclass
class VerificationMetrics:
    total_evaluated: int
    accuracy: float
    macro_precision: float
    macro_recall: float
    macro_f1: float
    weighted_f1: float
    confusion_matrix: dict[str, dict[str, int]]
    per_class: dict[str, ClassMetrics]


def evaluate_verification(
    ground_truth_reqs: list[dict[str, Any]],
    actual_predictions: dict[str, str],  # req_code -> actual status
) -> VerificationMetrics:
    """Compute Confusion Matrix, Accuracy, and per-class / macro Precision, Recall, F1."""
    # Build 4x4 matrix: matrix[expected][actual]
    matrix: dict[str, dict[str, int]] = {
        exp: {act: 0 for act in VALID_STATUSES}
        for exp in VALID_STATUSES
    }

    gt_by_code = {
        normalize_code(r.get("req_code") or r.get("requirement_id")): normalize_status(r.get("expected_status"))
        for r in ground_truth_reqs
    }

    total = len(gt_by_code)
    correct = 0

    for code, expected in gt_by_code.items():
        actual_raw = actual_predictions.get(code)
        actual = normalize_status(actual_raw)
        if expected in matrix and actual in matrix[expected]:
            matrix[expected][actual] += 1
        if expected == actual:
            correct += 1

    accuracy = (correct / total * 100.0) if total > 0 else 0.0

    # Calculate per-class metrics
    per_class: dict[str, ClassMetrics] = {}
    macro_p_sum = 0.0
    macro_r_sum = 0.0
    macro_f1_sum = 0.0
    weighted_f1_sum = 0.0

    for c in VALID_STATUSES:
        tp = matrix[c][c]
        fp = sum(matrix[other][c] for other in VALID_STATUSES if other != c)
        fn = sum(matrix[c][other] for other in VALID_STATUSES if other != c)
        support = sum(matrix[c][other] for other in VALID_STATUSES)

        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * (p * r) / (p + r) if (p + r) > 0 else 0.0

        per_class[c] = ClassMetrics(
            precision=round(p * 100.0, 2),
            recall=round(r * 100.0, 2),
            f1=round(f1 * 100.0, 2),
            support=support,
            true_positives=tp,
            false_positives=fp,
            false_negatives=fn,
        )

        macro_p_sum += p
        macro_r_sum += r
        macro_f1_sum += f1
        weighted_f1_sum += (f1 * support)

    num_classes = len(VALID_STATUSES)
    macro_p = (macro_p_sum / num_classes) * 100.0
    macro_r = (macro_r_sum / num_classes) * 100.0
    macro_f1 = (macro_f1_sum / num_classes) * 100.0
    weighted_f1 = (weighted_f1_sum / total * 100.0) if total > 0 else 0.0

    return VerificationMetrics(
        total_evaluated=total,
        accuracy=round(accuracy, 2),
        macro_precision=round(macro_p, 2),
        macro_recall=round(macro_r, 2),
        macro_f1=round(macro_f1, 2),
        weighted_f1=round(weighted_f1, 2),
        confusion_matrix=matrix,
        per_class=per_class,
    )


# ==============================================================================
# 4. TARGETED SPECIALTY METRICS (Conflict, Missing, Unsupported Claim)
# ==============================================================================

@dataclass
class SpecialtyMetrics:
    conflict_precision: float
    conflict_recall: float
    conflict_f1: float
    missing_precision: float
    missing_recall: float
    missing_f1: float
    unsupported_claim_rate: float  # Hallucination rate on missing evidence
    total_missing_in_gt: int
    unsupported_claims_count: int


def evaluate_specialty_metrics(
    ground_truth_reqs: list[dict[str, Any]],
    actual_predictions: dict[str, str],
) -> SpecialtyMetrics:
    """Compute Conflict F1, Missing F1, and Unsupported Claim Rate."""
    gt_map = {
        normalize_code(r.get("req_code") or r.get("requirement_id")): normalize_status(r.get("expected_status"))
        for r in ground_truth_reqs
    }

    # Conflict binary detection
    conflict_tp = 0
    conflict_fp = 0
    conflict_fn = 0

    # Missing binary detection
    missing_tp = 0
    missing_fp = 0
    missing_fn = 0

    # Unsupported claim check (Expected Missing -> Predicted Supported)
    total_missing = 0
    unsupported_claims = 0

    for code, expected in gt_map.items():
        actual = normalize_status(actual_predictions.get(code))

        # Conflict
        if expected == "Conflict" and actual == "Conflict":
            conflict_tp += 1
        elif expected != "Conflict" and actual == "Conflict":
            conflict_fp += 1
        elif expected == "Conflict" and actual != "Conflict":
            conflict_fn += 1

        # Missing
        if expected == "Missing":
            total_missing += 1
            if actual == "Missing":
                missing_tp += 1
            elif actual == "Supported":
                # Severe error: Claimed supported when ground truth is completely missing evidence
                unsupported_claims += 1
            else:
                missing_fn += 1
        elif expected != "Missing" and actual == "Missing":
            missing_fp += 1

    conf_p = conflict_tp / (conflict_tp + conflict_fp) if (conflict_tp + conflict_fp) > 0 else 0.0
    conf_r = conflict_tp / (conflict_tp + conflict_fn) if (conflict_tp + conflict_fn) > 0 else 0.0
    conf_f1 = 2 * (conf_p * conf_r) / (conf_p + conf_r) if (conf_p + conf_r) > 0 else 0.0

    miss_p = missing_tp / (missing_tp + missing_fp) if (missing_tp + missing_fp) > 0 else 0.0
    miss_r = missing_tp / (missing_tp + missing_fn) if (missing_tp + missing_fn) > 0 else 0.0
    miss_f1 = 2 * (miss_p * miss_r) / (miss_p + miss_r) if (miss_p + miss_r) > 0 else 0.0

    unsupported_rate = (unsupported_claims / total_missing * 100.0) if total_missing > 0 else 0.0

    return SpecialtyMetrics(
        conflict_precision=round(conf_p * 100.0, 2),
        conflict_recall=round(conf_r * 100.0, 2),
        conflict_f1=round(conf_f1 * 100.0, 2),
        missing_precision=round(miss_p * 100.0, 2),
        missing_recall=round(miss_r * 100.0, 2),
        missing_f1=round(miss_f1 * 100.0, 2),
        unsupported_claim_rate=round(unsupported_rate, 2),
        total_missing_in_gt=total_missing,
        unsupported_claims_count=unsupported_claims,
    )


# ==============================================================================
# 5. FAILURE CLASSIFIER
# ==============================================================================

@dataclass
class FailureCase:
    requirement_id: str
    requirement_title: str
    category: str
    expected_status: str
    actual_status: str
    error_type: str  # extraction_error | retrieval_error | verification_error | conflict_detection_error | unsupported_claim
    explanation: str
    expected_evidence: list[dict[str, Any]]
    retrieved_evidence: list[dict[str, Any]]


def classify_failure(
    gt_req: dict[str, Any],
    actual_status: str,
    retrieved_chunks: list[dict[str, Any]],
    expected_evidence: list[dict[str, Any]],
) -> Optional[FailureCase]:
    """Analyze why a prediction failed and classify the root cause."""
    expected_status = normalize_status(gt_req.get("expected_status"))
    actual = normalize_status(actual_status)

    if expected_status == actual:
        return None

    req_id = gt_req.get("requirement_id") or gt_req.get("req_code", "")
    req_title = gt_req.get("title", "")
    category = gt_req.get("category", "General")

    # Check for hallucination / unsupported claim
    if expected_status == "Missing" and actual == "Supported":
        return FailureCase(
            requirement_id=req_id,
            requirement_title=req_title,
            category=category,
            expected_status=expected_status,
            actual_status=actual,
            error_type="unsupported_claim",
            explanation="System incorrectly claimed requirement was verified ('Supported') when no evidence exists in the document corpus.",
            expected_evidence=expected_evidence,
            retrieved_evidence=retrieved_chunks,
        )

    # Check for missed conflict
    if expected_status == "Conflict" and actual != "Conflict":
        return FailureCase(
            requirement_id=req_id,
            requirement_title=req_title,
            category=category,
            expected_status=expected_status,
            actual_status=actual,
            error_type="conflict_detection_error",
            explanation=f"Cross-document contradiction was not detected; system returned '{actual}' instead of 'Conflict'.",
            expected_evidence=expected_evidence,
            retrieved_evidence=retrieved_chunks,
        )

    # Check for retrieval failure (expected evidence was not in candidate chunks)
    if expected_evidence and not any(_is_evidence_hit(c, expected_evidence) for c in retrieved_chunks):
        return FailureCase(
            requirement_id=req_id,
            requirement_title=req_title,
            category=category,
            expected_status=expected_status,
            actual_status=actual,
            error_type="retrieval_error",
            explanation=f"Retriever failed to fetch relevant evidence chunks for '{req_id}'. Actual classification fell back to '{actual}'.",
            expected_evidence=expected_evidence,
            retrieved_evidence=retrieved_chunks,
        )

    # Otherwise verification logic misclassification
    return FailureCase(
        requirement_id=req_id,
        requirement_title=req_title,
        category=category,
        expected_status=expected_status,
        actual_status=actual,
        error_type="verification_error",
        explanation=f"Verification rules classified requirement as '{actual}', but ground truth expects '{expected_status}'.",
        expected_evidence=expected_evidence,
        retrieved_evidence=retrieved_chunks,
    )
