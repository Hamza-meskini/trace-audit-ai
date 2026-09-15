"""Shared comparison vocabulary; normalization never guesses an action's meaning."""

from typing import Literal, Any

ComparisonOperator = Literal["<", "<=", "==", "!=", ">=", ">", "between", "in", "not_in"]
OPERATORS = {"<", "<=", "==", "!=", ">=", ">", "between", "in", "not_in"}


def normalize_comparison(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().lower().replace(" ", "_")
    return {
        "≤": "<=", "≥": ">=", "=": "==", "≠": "!=",
        "at_least": ">=", "gte": ">=", "greater_than_or_equal": ">=",
        "greater_than_or_equal_to": ">=", "is_greater_than_or_equal_to": ">=",
        "at_most": "<=", "lte": "<=", "less_than_or_equal": "<=",
        "less_than_or_equal_to": "<=", "is_less_than_or_equal_to": "<=",
        "not_exceeding": "<=", "greater_than": ">", "less_than": "<",
        "below": "<", "equal": "==", "equals": "==", "equal_to": "==",
        "within": "between", "in_range": "between", "within_range": "between",
    }.get(raw, raw or None)


def predicate_issues(condition: Any, *, require_operator: bool = False) -> list[str]:
    """Check supplied comparisons while allowing legacy prose-only predicates."""
    op = normalize_comparison(condition.operator)
    value = condition.threshold
    right = getattr(condition, "right_operand", None)
    if op is None:
        has_operands = value is not None or right or condition.min_value is not None or condition.max_value is not None
        return ["has no comparison operator"] if require_operator or has_operands else []
    if op not in OPERATORS:
        return [f"has unsupported comparison operator {op!r}"]
    if right:
        if op not in {"<", "<=", "==", "!=", ">=", ">"}:
            return ["uses a relational operand with a non-relational operator"]
        if value is not None or condition.min_value is not None or condition.max_value is not None:
            return ["mixes a relational operand with literal bounds"]
        return []
    if op == "between":
        low, high = condition.min_value, condition.max_value
        if low is None or high is None:
            return ["has an interval without both bounds"]
        return ["has reversed interval bounds"] if low > high else []
    if op in {"in", "not_in"}:
        return [] if isinstance(value, list) and value else ["requires a nonempty allowed-value list"]
    if value is None:
        # Older stored numeric contracts use only the appropriate bound.
        value = condition.max_value if op in {"<", "<="} else condition.min_value if op in {">", ">="} else None
    if value is None:
        return ["has a comparison without a right operand"]
    if isinstance(value, list):
        return ["uses a list with a scalar comparison"]
    if op in {"<", "<=", ">", ">="} and (not isinstance(value, (float, int)) or isinstance(value, bool)):
        return ["requires a numeric literal or an explicit relational operand"]
    return []
