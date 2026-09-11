"""Status-blind structured evaluation. This module is never an inference input.

Policy v3: one original predicate is one atom, including an explicit interval.
An interval can also be expanded for a separate constraint-coverage diagnostic;
that expansion never copies a verification status to multiple reference atoms.
Unknown semantic equivalences remain unresolved rather than receiving credit.
"""
from __future__ import annotations

import itertools
import math
import re
from collections import Counter
from typing import Any

from scipy.optimize import linear_sum_assignment

SCORING_VERSION = 3
POLICY = {
    "version": SCORING_VERSION,
    "identity": "Parameter/source/description token overlap; global one-to-one assignment; no gold IDs, positions, statuses, or evidence quotes",
    "correctness": "Identity plus operator, converted value/unit, role, mandatory flag, and annotated scope/method. Visual-routing metadata is scored separately.",
    "granularity": "One original predicate per atom. Inclusive intervals expand only in the separate normalized-constraint diagnostic.",
    "limitations": "Deterministic semantic proxy, not expert-certified equivalence. Unresolved matches need human review. No benchmark-specific aliases.",
}


def pct(n: int, d: int) -> float:
    return round(100 * n / d, 2) if d else 0.0


def as_dict(value: Any) -> dict:
    return value.model_dump() if hasattr(value, "model_dump") else dict(value)


def words(value: Any) -> set[str]:
    # Numbers are checked in structured fields, not used as semantic identity.
    stop = {"the", "a", "an", "shall", "must", "is", "are", "be", "of", "for", "to", "and", "in", "at", "with"}
    return {w for w in re.findall(r"[a-z]+", str(value or "").lower()) if w not in stop}


def overlap(a: Any, b: Any) -> float:
    x, y = words(a), words(b)
    return 2 * len(x & y) / (len(x) + len(y)) if x and y else 0.0


def operator(value: Any) -> str:
    text = str(value or "").strip().lower()
    return {"≤": "<=", "≥": ">=", "=": "==", "equals": "==", "equal_to": "==",
            "gte": ">=", "at_least": ">=", "lte": "<=", "at_most": "<=",
            "greater_than": ">", "less_than": "<", "in_range": "between"}.get(text, text)


def unit(value: Any) -> tuple[str, float]:
    """Small explicit dimensional registry; unknown units require literal agreement.

    AC/DC and RMS qualifiers are preserved. No physical meaning is guessed from
    a bare C, an absent unit, or an unfamiliar spelling.
    """
    raw = str(value or "").strip().replace("µ", "u").replace("μ", "u")
    aliases = {"seconds": "s", "second": "s", "sec": "s", "milliseconds": "ms",
               "millisecond": "ms", "minutes": "min", "minute": "min", "hours": "h", "hour": "h",
               "volts": "V", "volt": "V", "amperes": "A", "ampere": "A", "amps": "A",
               "percent": "%", "percentage": "%", "ohms": "ohm", "Ω": "ohm",
               "°C": "deg C", "N*m": "Nm", "N m": "Nm"}
    raw = aliases.get(raw, raw)
    registry = {
        "s": ("time", 1), "ms": ("time", .001), "us": ("time", .000001), "min": ("time", 60), "h": ("time", 3600),
        "A": ("current", 1), "mA": ("current", .001), "uA": ("current", .000001),
        "V": ("voltage", 1), "mV": ("voltage", .001), "kV": ("voltage", 1000),
        "W": ("power", 1), "kW": ("power", 1000),
        "m": ("length", 1), "mm": ("length", .001), "cm": ("length", .01),
        "ohm": ("resistance", 1), "mOhm": ("resistance", .001), "kohm": ("resistance", 1000),
        "Hz": ("frequency", 1), "kHz": ("frequency", 1000),
        "%": ("ratio", .01), "ratio": ("ratio", 1),
    }
    return registry.get(raw, (raw, 1.0))


def scalar_equal(a: Any, b: Any, scale_a: float = 1, scale_b: float = 1) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    try:
        x, y = float(a) * scale_a, float(b) * scale_b
        return math.isfinite(x) and math.isfinite(y) and math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12)
    except (ValueError, TypeError):
        return str(a).strip().casefold() == str(b).strip().casefold()


def identity(a: dict, b: dict) -> float:
    fields = ("parameter", "canonical_parameter", "source_parameter")
    parameter = max((overlap(a.get(x), b.get(y)) for x in fields for y in fields), default=0)
    text = max((overlap(a.get(x), b.get(y)) for x in ("description", "source_span")
                for y in ("description", "source_span")), default=0)
    return max(parameter, text) if parameter >= .65 or text >= .65 else 0.0


def field_checks(a: dict, b: dict) -> dict[str, bool]:
    ua, sa = unit(a.get("unit")); ub, sb = unit(b.get("unit"))
    checks = {
        "operator": bool(operator(a.get("operator"))) and operator(a.get("operator")) == operator(b.get("operator")),
        "unit": ua == ub,
        "value": all(scalar_equal(a.get(k), b.get(k), sa, sb) for k in ("threshold", "min_value", "max_value")),
        "role": a.get("condition_role", "VERIFICATION") == b.get("condition_role", "VERIFICATION"),
        "mandatory": a.get("mandatory", True) == b.get("mandatory", True),
    }
    for key in ("scope", "verification_method"):
        # Missing reference annotations cannot certify this dimension.
        if a.get(key) is not None:
            checks[key] = str(a[key]).casefold() == str(b.get(key) or "").casefold()
    if isinstance(a.get("threshold"), bool) or isinstance(b.get("threshold"), bool):
        negations = {"not", "no", "never", "without"}
        # A reference often states "no fault" while its predicate is
        # fault_present == False. Comparing description negations would
        # double-count the already encoded False value.
        checks["polarity"] = (words(a.get("parameter")) & negations) == (words(b.get("parameter")) & negations)
        for c in (a, b):
            if c.get("threshold") is True and words(c.get("description")) & negations and not words(c.get("parameter")) & negations:
                checks["polarity"] = False
    return checks


def align(expected: list[dict], predicted: list[dict]) -> list[dict]:
    """Global matching with dummy unmatched columns. No labels enter the cost.

    Related conditions with wrong fields may align for error diagnosis, but
    receive no extraction or combined status credit. Tied alternative matches
    are unresolved, including duplicates whose result attribution is ambiguous.
    """
    if not expected or not predicted:
        return []
    candidates = {}
    weights = [[0.0] * (len(predicted) + len(expected)) for _ in expected]
    for i, a in enumerate(expected):
        for j, b in enumerate(predicted):
            semantic = identity(a, b)
            if not semantic:
                continue
            checks = field_checks(a, b)
            weight = semantic + .2 * sum(checks.values()) / len(checks)
            weights[i][j] = weight
            candidates[i, j] = {"expected_index": i, "predicted_index": j,
                                 "identity_score": round(semantic, 4), "checks": checks,
                                 "equivalent": all(checks.values())}
    rows, cols = linear_sum_assignment(weights, maximize=True)
    output = []
    for i, j in zip(rows, cols):
        if (i, j) not in candidates:
            continue
        # The same global objective without this edge must be lower, unless
        # duplicate result attribution cannot safely be chosen by position.
        alternate = [row[:] for row in weights]
        alternate[i][j] = -1000
        ar, ac = linear_sum_assignment(alternate, maximize=True)
        tied = abs(sum(weights[x][y] for x, y in zip(rows, cols)) - sum(alternate[x][y] for x, y in zip(ar, ac))) < 1e-9
        row = dict(candidates[i, j])
        row["ambiguous"] = tied
        if tied:
            row["equivalent"] = False
        output.append(row)
    return output


def expand_intervals(conditions: list[dict]) -> list[dict]:
    output = []
    for c in conditions:
        if operator(c.get("operator")) == "between" and c.get("min_value") is not None and c.get("max_value") is not None:
            for bound, op in (("min_value", ">="), ("max_value", "<=")):
                output.append({**c, "operator": op, "threshold": c[bound], "min_value": None, "max_value": None})
        else:
            output.append(dict(c))
    return output


def logic_value(logic: dict, state: dict[str, bool]) -> bool:
    op = logic.get("operator")
    if op == "CONDITION":
        return state[logic["condition_id"]]
    if op == "IF_THEN":
        if "antecedent" in logic:
            return not logic_value(logic["antecedent"], state) or logic_value(logic["consequent"], state)
        return not state[logic["if_condition_id"]] or all(state[k] for k in logic["then_condition_ids"])
    if op not in ("ALL_OF", "ANY_OF", "AND", "OR"):
        raise ValueError("Unknown logic operator")
    children = logic.get("children")
    values = [logic_value(child, state) for child in children] if children is not None else [state[k] for k in logic["condition_ids"]]
    if not values:
        raise ValueError("Empty logic")
    return all(values) if op in ("ALL_OF", "AND") else any(values)


def logic_equivalent(expected: dict, predicted: dict, pairs: list[dict]) -> bool | None:
    ec, pc = expected.get("conditions", []), predicted.get("conditions", [])
    if len(ec) != len(pc) or len(pairs) != len(ec) or any(p["ambiguous"] for p in pairs):
        return None
    if not ec or len(ec) > 12:
        return None
    try:
        for bits in itertools.product((False, True), repeat=len(ec)):
            es = {c["condition_id"]: bits[i] for i, c in enumerate(ec)}
            ps = {pc[p["predicted_index"]]["condition_id"]: bits[p["expected_index"]] for p in pairs}
            if logic_value(expected.get("logic_tree") or expected["logic"], es) != logic_value(predicted.get("logic_tree") or predicted["logic"], ps):
                return False
        return True
    except (KeyError, TypeError, ValueError):
        return None


def decomposition(requirements: list[dict], contracts: list[dict]) -> dict:
    ids = [c.get("req_code") for c in contracts]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate requirement IDs in evaluation input")
    by_id = dict(zip(ids, contracts))
    details = []
    expected_total = sum(len(r.get("conditions", [])) for r in requirements)
    predicted_total = sum(len(r.get("conditions", [])) for r in contracts)
    counts = Counter()
    for req in requirements:
        pred = by_id.get(req["requirement_id"], {})
        ec, pc = req.get("conditions", []), pred.get("conditions", [])
        pairs = align(ec, pc)
        for pair in pairs:
            pair["expected_condition_id"] = ec[pair["expected_index"]]["condition_id"]
            pair["predicted_condition_id"] = pc[pair["predicted_index"]]["condition_id"]
            pair["metadata_checks"] = {"visual": ec[pair["expected_index"]].get("requires_visual_evidence", False) == pc[pair["predicted_index"]].get("requires_visual_evidence", False)}
            counts["visual_correct"] += int(pair["metadata_checks"]["visual"])
        good = sum(p["equivalent"] for p in pairs)
        expanded_ec, expanded_pc = expand_intervals(ec), expand_intervals(pc)
        normalized_good = sum(p["equivalent"] for p in align(expanded_ec, expanded_pc))
        logic = logic_equivalent(req, pred, pairs)
        counts.update(correct=good, paired=len(pairs), ambiguous=sum(p["ambiguous"] for p in pairs),
                      field_errors=sum(not p["equivalent"] and not p["ambiguous"] for p in pairs),
                      normalized_correct=normalized_good, normalized_expected=len(expanded_ec),
                      logic_correct=logic is True, logic_evaluable=logic is not None,
                      exact_contract=good == len(ec) == len(pc) and logic is True)
        details.append({"requirement_id": req["requirement_id"], "pairs": pairs,
                        "expected_count": len(ec), "predicted_count": len(pc),
                        "unmatched_expected_ids": [c["condition_id"] for i, c in enumerate(ec) if i not in {p["expected_index"] for p in pairs}],
                        "unmatched_predicted_ids": [c["condition_id"] for i, c in enumerate(pc) if i not in {p["predicted_index"] for p in pairs}],
                        "logic_equivalent": logic})
    return {"policy": POLICY, "expected": expected_total, "predicted": predicted_total,
            "correct": counts["correct"], "recall": pct(counts["correct"], expected_total),
            "precision": pct(counts["correct"], predicted_total), "f1": pct(2 * counts["correct"], expected_total + predicted_total),
            "identity_pairs": counts["paired"], "ambiguous_pairs": counts["ambiguous"],
            "field_error_pairs": counts["field_errors"], "unmatched_expected": expected_total - counts["paired"],
            "routing_metadata": {"visual_correct": counts["visual_correct"], "paired": counts["paired"],
                                 "visual_accuracy_on_pairs": pct(counts["visual_correct"], counts["paired"])},
            "unmatched_predicted": predicted_total - counts["paired"],
            "normalized_constraint_coverage": {"correct": counts["normalized_correct"], "expected": counts["normalized_expected"],
                "predicted": sum(len(expand_intervals(r.get("conditions", []))) for r in contracts),
                "recall": pct(counts["normalized_correct"], counts["normalized_expected"]),
                "note": "Inclusive ranges only; no predicate renaming or status propagation across split/merged atoms."},
            "logic": {"correct": counts["logic_correct"], "evaluable": counts["logic_evaluable"], "total": len(requirements),
                      "accuracy": pct(counts["logic_correct"], len(requirements)),
                      "exact_contracts": counts["exact_contract"]}, "details": details}


def verification_metrics(requirements: list[dict], results: dict[str, list], alignment: dict) -> dict:
    """Attach results by the model's own condition IDs *after* structured alignment.

    Invalid/duplicate result IDs never receive credit. Result descriptions,
    quotes, and expected/predicted statuses cannot influence the alignment.
    """
    by_req = {r["requirement_id"]: r for r in alignment["details"]}
    correct = eligible = available = 0
    mismatches = []
    for req in requirements:
        rid = req["requirement_id"]
        entries = [as_dict(v) for v in results.get(rid, [])]
        ids = Counter(e.get("condition_id") for e in entries)
        by_id = {e.get("condition_id"): e for e in entries if ids[e.get("condition_id")] == 1}
        pairs = {p["expected_index"]: p for p in by_req[rid]["pairs"]}
        for i, c in enumerate(req["conditions"]):
            pair = pairs.get(i)
            equivalent = pair is not None and pair["equivalent"]
            eligible += int(equivalent)
            result = by_id.get(pair["predicted_condition_id"]) if equivalent else None
            valid = result is not None and result.get("status") in {"PROVEN", "FAILED", "PENDING", "UNTESTED", "INCONCLUSIVE", "NOT_APPLICABLE"}
            available += int(valid)
            matches = valid and result["status"] == c["expected_status"]
            correct += int(matches)
            if not matches:
                mismatches.append({"requirement_id": rid, "condition_id": c["condition_id"],
                                   "expected": c["expected_status"], "predicted": result.get("status") if result else None,
                                   "reason": "unaligned_or_incorrect_contract" if not equivalent else "missing_or_invalid_result" if not valid else "wrong_status"})
    total = sum(len(r["conditions"]) for r in requirements)
    return {"accuracy": pct(correct, total), "end_to_end_accuracy": pct(correct, total),
            "correct": correct, "total": total, "aligned": eligible,
            "alignment_coverage": pct(eligible, total), "aligned_accuracy": pct(correct, eligible) if eligible else None,
            "valid_result_count": available, "missing_or_invalid_result_count": eligible - available,
            "missing_or_unaligned_predictions": total - eligible,
            "alignment_methods": {"structured_contract": eligible}, "mismatches": mismatches,
            "note": "Combined score requires a correct extracted atom and correct status. Aligned accuracy includes missing/invalid results as failures."}
