"""Structured Requirement Contract schema for formal auditing and validation."""

from typing import Any, Optional, Union, Literal
from pydantic import BaseModel, Field, model_validator, field_validator
import re

from app.schemas.evidence_qualification import normalize_entity_scope
from app.schemas.contract_logic import ConditionValue, flat_projection
from app.schemas.predicate import normalize_comparison


RequirementType = Literal[
    "numeric_range",
    "threshold",
    "boolean",
    "duration",
    "test_result",
    "enumeration",
    "semantic",
    "other",
]

RequirementLogicOperator = Literal["ALL_OF", "ANY_OF", "IF_THEN"]
ConditionRole = Literal["VERIFICATION", "APPLICABILITY"]


class RequirementLogicContract(BaseModel):
    """Boolean structure connecting a requirement's atomic conditions."""

    operator: RequirementLogicOperator = "ALL_OF"
    condition_ids: list[str] = Field(default_factory=list)
    if_condition_id: Optional[str] = None
    then_condition_ids: list[str] = Field(default_factory=list)


class AtomicConditionContract(BaseModel):
    """Structured atomic condition within a requirement contract."""

    condition_id: str
    condition_role: ConditionRole = "VERIFICATION"
    description: Optional[str] = None
    source_span: Optional[str] = None
    source_parameter: Optional[str] = None
    canonical_parameter: Optional[str] = None
    parameter: Optional[str] = None
    operator: Optional[str] = None  # ">=", "<=", "==", "between", ">", "<", "in"
    right_operand: Optional[str] = None
    threshold: Optional[ConditionValue] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    unit: Optional[str] = None
    scope: Optional[str] = None  # e.g. "BCU", "ASIC", "Inverter", "System"
    mandatory: bool = True
    verification_method: Optional[str] = None  # "physical_test", "simulation", "calculation", "inspection"
    requires_visual_evidence: bool = False
    clause_ids: list[str] = Field(default_factory=list)

    @field_validator("operator", mode="before")
    @classmethod
    def normalize_operator(cls, value: Any) -> Optional[str]:
        # Retain unsupported legacy values for a diagnostic at the execution
        # boundary instead of crashing the entire project on deserialization.
        return normalize_comparison(value)


class SemanticClauseContract(BaseModel):
    """Grounded semantic proposition created before atomic construction."""

    clause_id: str
    clause_type: Literal["APPLICABILITY", "VERIFICATION", "QUALIFIER"] = "VERIFICATION"
    source_span: str
    subject: Optional[str] = None
    predicate: Optional[str] = None
    relationship: Optional[str] = None


class ClauseCoverageContract(BaseModel):
    """Trace one obligation-bearing source clause to its atomic conditions."""

    clause_id: Optional[str] = None
    clause: str
    condition_ids: list[str] = Field(default_factory=list)


class RequirementContract(BaseModel):
    """Structured engineering requirement contract."""

    requirement_id: str
    req_code: str
    title: str
    description: Optional[str] = None
    category: str = "General"
    requirement_type: RequirementType = "semantic"
    subject: Optional[str] = None
    parameter: Optional[str] = None
    operator: Optional[str] = None  # "between", "<=", "<", ">=", ">", "==", "in", "not_in"
    expected_value: Optional[ConditionValue] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    tolerance: Optional[float] = None
    unit: Optional[str] = None
    conditions: list[str] = Field(default_factory=list)
    atomic_conditions: list[AtomicConditionContract] = Field(default_factory=list)
    logic: RequirementLogicContract = Field(default_factory=RequirementLogicContract)
    logic_tree: Optional[dict[str, Any]] = None
    semantic_clauses: list[SemanticClauseContract] = Field(default_factory=list)
    clause_coverage: list[ClauseCoverageContract] = Field(default_factory=list)
    unmapped_obligations: list[str] = Field(default_factory=list)
    # None preserves compatibility for legacy/manually-created contracts that
    # pre-date extraction completeness reporting. New LLM extractions always
    # provide an explicit boolean.
    contract_complete: Optional[bool] = None
    decomposition_confidence: Optional[float] = None
    ambiguities: list[str] = Field(default_factory=list)
    validation_issues: list[str] = Field(default_factory=list)
    verification_method: Optional[str] = None  # "physical_test", "calculation", "simulation", "inspection"
    scope: Optional[str] = None  # "BCU", "ASIC", "Pack", "Inverter", "System"
    mandatory: bool = True
    raw_text: str = ""

    @property
    def verification_conditions(self) -> list[AtomicConditionContract]:
        """Obligations whose outcomes determine compliance."""
        return [
            condition for condition in self.atomic_conditions
            if condition.condition_role == "VERIFICATION"
        ]

    @property
    def applicability_conditions(self) -> list[AtomicConditionContract]:
        """Triggers or contextual gates that decide whether obligations apply."""
        return [
            condition for condition in self.atomic_conditions
            if condition.condition_role == "APPLICABILITY"
        ]

    @model_validator(mode="after")
    def normalize_logic_references(self) -> "RequirementContract":
        """Derive compatible legacy fields; preserve invalid IDs for diagnostics."""
        if self.logic_tree is not None:
            projection = flat_projection(self.logic_tree)
            self.logic = RequirementLogicContract(**(projection or {}))
            return self
        available = [condition.condition_id for condition in self.atomic_conditions]
        mandatory = [condition.condition_id for condition in self.atomic_conditions if condition.mandatory]
        # parse_requirement_contract builds the contract before appending its
        # conditions. Preserve explicit references during that construction
        # phase; they are validated when the populated contract is revalidated.
        if not available:
            return self
        if not self.logic.condition_ids:
            if self.logic.operator == "IF_THEN":
                self.logic.condition_ids = list(dict.fromkeys(
                    ([self.logic.if_condition_id] if self.logic.if_condition_id else [])
                    + self.logic.then_condition_ids
                ))
            else:
                self.logic.condition_ids = mandatory or available
        return self


# Regex helpers for deterministic contract parsing
RANGE_REGEX = re.compile(
    r"([+-]?\d+(?:\.\d+)?)\s*([°\w/µμ]+(?:\s+[a-zA-Z]+)?)?\s*(?:–|-|to)\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/µμ]+(?:\s+[a-zA-Z]+)?)?",
    re.IGNORECASE,
)

THRESHOLD_LE_REGEX = re.compile(
    r"(?:<=|≤|not\s+exceed|maximum\s+of|max(?:imum)?\s*[:=]?|limited\s+to\s*≤?|within\s+(?:maximum\s+)?latency\s*≤?|within\s*≤?|less\s+than\s+or\s+equal\s+to)\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/µμ%]+)?",
    re.IGNORECASE,
)

THRESHOLD_GE_REGEX = re.compile(
    r"(?:>=|≥|minimum\s+of|min(?:imum)?\s*[:=]?|at\s+least|exceed|greater\s+than\s+or\s+equal\s+to|store\s+minimum)\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/µμ%]+)?",
    re.IGNORECASE,
)

TOLERANCE_REGEX = re.compile(
    r"[±\+\/\-]+\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/µμ%]+)?",
    re.IGNORECASE,
)

IP_REGEX = re.compile(r"\b(IP\d{2}[A-Z]?)\b", re.IGNORECASE)


def parse_requirement_contract(
    req_code: str,
    title: str,
    description: Optional[str] = None,
    category: str = "General",
    structured_conditions: Optional[list[dict[str, Any]]] = None,
    semantic_clauses: Optional[list[dict[str, Any]]] = None,
    clause_coverage: Optional[list[dict[str, Any]]] = None,
    unmapped_obligations: Optional[list[str]] = None,
    contract_complete: Optional[bool] = None,
    logic: Optional[dict[str, Any]] = None,
    logic_tree: Optional[dict[str, Any]] = None,
    decomposition_confidence: Optional[float] = None,
    ambiguities: Optional[list[str]] = None,
    validation_issues: Optional[list[str]] = None,
) -> RequirementContract:
    """Build a structured RequirementContract from requirement text deterministically.
    
    If the requirement is not confidently numeric/threshold, it preserves the 
    semantic type without inventing values.
    """
    full_text = f"{title}. {description or ''}".strip()
    full_lower = full_text.lower()

    # Determine verification method
    v_method = "physical_test"
    if any(k in full_lower for k in [
        "by simulation", "simulation with", "simulated in", "simulation model",
        "simulation analysis", "in simulation", "via simulation", "simulation result",
        "matlab", "simulink", "spice", "ltspice", "cfd", "finite element",
    ]):
        v_method = "simulation"
    elif any(k in full_lower for k in ["by calculation", "analytical calculation", "calculated estimate", "calculation model", "by analysis", "analytical estimation"]):
        v_method = "calculation"
    elif any(k in full_lower for k in ["by inspection", "visual inspection", "inspection of"]):
        v_method = "inspection"

    # Determine scope / entity (single shared normalization path)
    scope = normalize_entity_scope(full_text, category or "")

    contract = RequirementContract(
        requirement_id=req_code,
        req_code=req_code,
        title=title,
        description=description,
        category=category,
        requirement_type="semantic",
        verification_method=v_method,
        scope=scope,
        raw_text=full_text,
        semantic_clauses=[
            SemanticClauseContract.model_validate(item)
            for item in (semantic_clauses or [])
        ],
        clause_coverage=[
            ClauseCoverageContract.model_validate(item)
            for item in (clause_coverage or [])
        ],
        unmapped_obligations=list(unmapped_obligations or []),
        contract_complete=contract_complete,
        logic=RequirementLogicContract.model_validate(logic or {}),
        logic_tree=logic_tree,
        decomposition_confidence=decomposition_confidence,
        ambiguities=list(ambiguities or []),
        validation_issues=list(validation_issues or []),
    )

    # A structured condition tree is canonical whenever the caller has one.
    # Prose parsing remains a fallback for ordinary uploaded requirements.
    if structured_conditions:
        for index, raw in enumerate(structured_conditions, 1):
            condition_id = str(raw.get("condition_id") or f"{req_code}-C{index}")
            operator = raw.get("operator")
            threshold = raw.get("threshold")
            min_value = raw.get("min_value")
            max_value = raw.get("max_value")

            if operator == "between" and isinstance(threshold, str):
                bounds = re.findall(r"[+-]?\d+(?:\.\d+)?", threshold)
                if len(bounds) >= 2:
                    min_value, max_value = float(bounds[0]), float(bounds[1])
            elif operator in ("<=", "<") and isinstance(threshold, (int, float)):
                max_value = float(threshold)
            elif operator in (">=", ">") and isinstance(threshold, (int, float)):
                min_value = float(threshold)

            contract.atomic_conditions.append(AtomicConditionContract(
                condition_id=condition_id,
                condition_role=raw.get("condition_role", "VERIFICATION"),
                description=raw.get("description"),
                source_span=raw.get("source_span"),
                source_parameter=raw.get("source_parameter"),
                canonical_parameter=raw.get("canonical_parameter") or raw.get("parameter"),
                parameter=raw.get("parameter"),
                operator=operator,
                right_operand=raw.get("right_operand"),
                threshold=threshold,
                min_value=min_value,
                max_value=max_value,
                unit=raw.get("unit") or None,
                scope=raw.get("scope") or scope,
                mandatory=bool(raw.get("mandatory", True)),
                verification_method=raw.get("verification_method") or v_method,
                requires_visual_evidence=bool(raw.get("requires_visual_evidence", False)),
                clause_ids=list(raw.get("clause_ids") or []),
            ))

        first = contract.atomic_conditions[0]
        contract.conditions = [c.description or c.condition_id for c in contract.atomic_conditions]
        contract.parameter = first.parameter
        contract.operator = first.operator
        contract.expected_value = first.threshold
        contract.min_value = first.min_value
        contract.max_value = first.max_value
        contract.unit = first.unit
        if len(contract.atomic_conditions) > 1:
            contract.requirement_type = "other"
        elif first.operator == "between":
            contract.requirement_type = "numeric_range"
        elif first.operator in ("<=", "<", ">=", ">"):
            contract.requirement_type = "duration" if (first.unit or "").lower() in {
                "ns", "us", "µs", "ms", "s", "sec", "seconds", "min", "minutes", "h", "hours",
            } else "threshold"
        elif first.operator == "==" and isinstance(first.threshold, bool):
            contract.requirement_type = "boolean"
        elif first.operator in {"==", "in", "not_in"}:
            contract.requirement_type = "enumeration"
        return RequirementContract.model_validate(contract.model_dump())

    # 1. Check for IP rating
    ip_match = IP_REGEX.search(full_text)
    if ip_match and ("ingress" in full_lower or "enclosure" in full_lower or "protection" in full_lower):
        contract.requirement_type = "enumeration"
        contract.parameter = "ingress_protection"
        contract.operator = "=="
        contract.expected_value = ip_match.group(1).upper()
        contract.unit = "IP"
        contract.atomic_conditions.append(AtomicConditionContract(
            condition_id=f"{req_code}-C1",
            description="Ingress protection level",
            parameter="ingress_protection",
            operator="==",
            threshold=ip_match.group(1).upper(),
            unit="IP",
            scope=scope,
            verification_method=v_method,
        ))
        return contract

    # 2. Check for numeric ranges: e.g. "400.0 V DC to 800.0 V DC", "-40°C to +85°C", "18–30 V"
    range_match = RANGE_REGEX.search(full_text)
    if range_match:
        try:
            min_v = float(range_match.group(1))
            unit_pre = range_match.group(2)
            max_v = float(range_match.group(3))
            unit_post = range_match.group(4)
            unit = (unit_post or unit_pre or "").strip()

            contract.requirement_type = "numeric_range"
            contract.operator = "between"
            contract.min_value = min_v
            contract.max_value = max_v
            contract.unit = unit or None
            
            # Extract tolerance if present
            tol_match = TOLERANCE_REGEX.search(full_text)
            if tol_match:
                contract.tolerance = float(tol_match.group(1))

            contract.atomic_conditions.append(AtomicConditionContract(
                condition_id=f"{req_code}-C1",
                description="Minimum operating limit",
                parameter="range_min",
                operator="<=",
                threshold=min_v,
                min_value=min_v,
                unit=contract.unit,
                scope=scope,
                verification_method=v_method,
            ))
            contract.atomic_conditions.append(AtomicConditionContract(
                condition_id=f"{req_code}-C2",
                description="Maximum operating limit",
                parameter="range_max",
                operator=">=",
                threshold=max_v,
                max_value=max_v,
                unit=contract.unit,
                scope=scope,
                verification_method=v_method,
            ))
            
            return contract
        except (ValueError, TypeError):
            pass

    # 3. Check for duration / latency threshold (<= X ms, <= X us, <= X s, <= X hours)
    if any(tw in full_lower for tw in ["latency", "time", "duration", "response", "delay", "disconnect", "cycle", "hours"]):
        le_m = THRESHOLD_LE_REGEX.search(full_text)
        if le_m:
            try:
                val = float(le_m.group(1))
                unit = le_m.group(2).strip() if le_m.group(2) else ""
                if unit.lower() in ("ms", "us", "µs", "s", "sec", "seconds", "hours", "h", "min", "minutes"):
                    contract.requirement_type = "duration"
                    contract.operator = "<="
                    contract.max_value = val
                    contract.unit = unit
                    contract.atomic_conditions.append(AtomicConditionContract(
                        condition_id=f"{req_code}-C1",
                        description="Latency / duration limit",
                        parameter="duration",
                        operator="<=",
                        threshold=val,
                        max_value=val,
                        unit=unit,
                        scope=scope,
                        verification_method=v_method,
                    ))
                    return contract
            except (ValueError, TypeError):
                pass

    # 4. Check for general <= threshold (e.g. power <= 45W, current <= 150 uA, error <= 0.5%)
    le_m = THRESHOLD_LE_REGEX.search(full_text)
    if le_m:
        try:
            val = float(le_m.group(1))
            unit = le_m.group(2).strip() if le_m.group(2) else ""
            contract.requirement_type = "threshold"
            contract.operator = "<="
            contract.max_value = val
            contract.unit = unit or None
            contract.atomic_conditions.append(AtomicConditionContract(
                condition_id=f"{req_code}-C1",
                description="Upper threshold limit",
                parameter="upper_bound",
                operator="<=",
                threshold=val,
                max_value=val,
                unit=unit or None,
                scope=scope,
                verification_method=v_method,
            ))
            return contract
        except (ValueError, TypeError):
            pass

    # 5. Check for general >= threshold (e.g. dielectric >= 2.5 kV, MTBF >= 250,000 hours, energy >= 4.5 J)
    ge_m = THRESHOLD_GE_REGEX.search(full_text)
    if ge_m:
        try:
            val = float(ge_m.group(1))
            unit = ge_m.group(2).strip() if ge_m.group(2) else ""
            contract.requirement_type = "threshold"
            contract.operator = ">="
            contract.min_value = val
            contract.unit = unit or None
            contract.atomic_conditions.append(AtomicConditionContract(
                condition_id=f"{req_code}-C1",
                description="Lower threshold limit",
                parameter="lower_bound",
                operator=">=",
                threshold=val,
                min_value=val,
                unit=unit or None,
                scope=scope,
                verification_method=v_method,
            ))
            return contract
        except (ValueError, TypeError):
            pass

    # 6. Check for boolean flags (e.g. secure boot, galvanic isolation, authentication)
    if any(kw in full_lower for kw in ["secure boot", "hardware root-of-trust", "galvanic isolation", "ecdsa", "authentication"]):
        contract.requirement_type = "boolean"
        contract.operator = "=="
        contract.expected_value = True
        contract.atomic_conditions.append(AtomicConditionContract(
            condition_id=f"{req_code}-C1",
            description="Feature implementation",
            parameter="feature_present",
            operator="==",
            threshold=True,
            scope=scope,
            verification_method=v_method,
        ))
        return contract

    contract.atomic_conditions.append(AtomicConditionContract(
        condition_id=f"{req_code}-C1",
        description=description or title,
        parameter=contract.parameter,
        operator=contract.operator,
        threshold=contract.expected_value,
        min_value=contract.min_value,
        max_value=contract.max_value,
        unit=contract.unit,
        scope=scope,
        verification_method=v_method,
    ))
    contract.conditions = [description or title]
    return contract
