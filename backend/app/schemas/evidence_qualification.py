"""First-class Evidence Qualification schema and compatibility primitives.

Qualification answers one question BEFORE any verification runs:
"Is this piece of evidence allowed to prove (or refute) this requirement's conditions?"

Four independent compatibility axes:
  1. Source authority  — what kind of document is this? (reuses SourceAuthority)
  2. Verification method — can this document's method satisfy the required method?
  3. Entity scope      — is this evidence about the same entity as the requirement?
  4. Parameter         — is this evidence about the same physical quantity?

This module is intentionally dependency-free (no service imports) so that
schemas (claim, contract) and services can share the same normalization
without circular imports.
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field


QualificationStatus = Literal["QUALIFIED", "PARTIALLY_QUALIFIED", "NOT_QUALIFIED", "UNKNOWN"]

VerificationMethod = Literal[
    "physical_test",
    "simulation",
    "calculation",
    "inspection",
    "design",
    "datasheet",
    "matrix_record",
    "unknown",
]


# ── Authority → evidence verification method ─────────────────────────────────
# Single mapping from the centralized SourceAuthority taxonomy to the method
# a document of that authority can actually carry. Do not duplicate this
# anywhere else; always derive evidence methods through this table.

AUTHORITY_TO_METHOD: dict[str, VerificationMethod] = {
    "EMPIRICAL_TEST": "physical_test",
    "QUALIFICATION_TEST": "physical_test",
    "VALIDATION_REPORT": "physical_test",
    "COMPLIANCE_MATRIX": "matrix_record",
    "DATASHEET": "datasheet",
    "ARCHITECTURE_SPEC": "design",
    "SIMULATION": "simulation",
    "CALCULATION": "calculation",
    "INSPECTION": "inspection",
    "UNKNOWN": "unknown",
}


# ── Verification-method compatibility ───────────────────────────────────────
# Which evidence methods can satisfy which required verification methods.
# Principles:
#   - A real physical test is strictly stronger evidence and satisfies any
#     requirement method (you may always verify with a bench test what you
#     planned to verify by inspection).
#   - A formal matrix record tracks physical tests, so it satisfies physical_test.
#   - Simulation / calculation only satisfy requirements that explicitly ask
#     for that method (never "simulation is always invalid" — it depends on
#     the requirement).
#   - Design intent, datasheets and unclassified docs never satisfy physical_test,
#     simulation or calculation requirements on their own.

_METHOD_COMPAT: dict[str, set[VerificationMethod]] = {
    "physical_test": {"physical_test", "matrix_record"},
    "simulation": {"simulation", "physical_test", "matrix_record"},
    "calculation": {"calculation", "simulation", "physical_test", "matrix_record"},
    "inspection": {"inspection", "physical_test", "matrix_record"},
    "design": {"design", "physical_test", "matrix_record"},
    "datasheet": {"datasheet", "physical_test", "matrix_record"},
    "unknown": set(),
    "matrix_record": {"matrix_record", "physical_test"},
}


def normalize_required_method(method: Optional[str]) -> VerificationMethod:
    """Normalize a requirement's declared verification method."""
    m = (method or "").strip().lower().replace("-", "_").replace(" ", "_")
    if m in ("test", "physical", "bench_test", "physical_testing"):
        return "physical_test"
    if m in ("sim", "simulated", "model"):
        return "simulation"
    if m in ("analytical", "analysis"):
        return "calculation"
    if m in _METHOD_COMPAT:
        return m  # type: ignore[return-value]
    return "physical_test"


def methods_compatible(
    required_method: Optional[str],
    evidence_method: VerificationMethod,
) -> bool:
    """True when the evidence's method can establish the required method."""
    req_m = normalize_required_method(required_method)
    return evidence_method in _METHOD_COMPAT.get(req_m, set())


# ── Entity scope normalization ──────────────────────────────────────────────
# Single shared path for scope inference (previously duplicated between
# contract parsing and claim extraction).

_SCOPE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("ASIC", ("asic", "cell supervisory")),
    ("Inverter", ("inverter", "gate driver")),
    ("BCU", ("bcu",)),
    ("BMS", ("bms",)),
    ("Pack", ("pack",)),
    ("HVIL", ("hvil",)),
    ("DC-DC", ("dc-dc", "dcdc", "dc dc")),
    ("OnboardCharger", ("onboard charger", "obc",)),
]


# Entities that belong to the same physical subsystem: evidence about one
# member of the family is admissible for requirements about another member
# (a pack-level bench test exercises the BCU that manages that pack).
_SCOPE_FAMILY = {
    "BCU": "battery_system",
    "BMS": "battery_system",
    "PACK": "battery_system",
    "ASIC": "asic",
    "INVERTER": "inverter",
    "HVIL": "hvil",
    "DC-DC": "dcdc",
    "ONBOARDCHARGER": "obc",
    "SYSTEM": "system",
}


def normalize_entity_scope(text: str, doc_name: str = "") -> str:
    """Infer the entity scope (ASIC / BCU / Pack / ...) of a text or document."""
    t = f"{text} {doc_name}".lower()
    for scope, needles in _SCOPE_PATTERNS:
        if any(n in t for n in needles):
            return scope
    return "System"


def scopes_compatible(
    required_scope: Optional[str],
    evidence_scope: Optional[str],
) -> Optional[bool]:
    """Compare requirement scope with evidence scope.

    Returns:
      True  — same entity, same subsystem family, or system-level evidence
      False — demonstrably different entities (e.g. ASIC data for a BCU
              requirement, or component data for a system requirement)
      None  — cannot be established (unknown evidence scope)

    A scope mismatch is a qualification failure, never an automatic conflict;
    cross-document contradiction detection is a separate concern.
    """
    r = (required_scope or "System").strip().lower() or "system"
    e = (evidence_scope or "").strip().lower()

    if not e or e == "unknown":
        return None  # unknown entity — not a mismatch, just unestablished

    if e == r:
        return True
    if e == "system":
        # System-level evidence covers all sub-entities.
        return True

    r_family = _SCOPE_FAMILY.get(r.upper(), r)
    e_family = _SCOPE_FAMILY.get(e.upper(), e)
    if r_family == e_family:
        return True

    # Different specific entities (includes component evidence for a
    # system-level requirement).
    return False


# ── Parameter normalization & compatibility ─────────────────────────────────
# Small reusable alias groups. Deliberately NOT a giant ontology: each group
# only encodes distinctions that change verification meaning (e.g. junction
# temperature ≠ ambient temperature, pyro-fuse latency ≠ contactor latency).

_PARAMETER_GROUPS: dict[str, set[str]] = {
    "voltage": {"voltage", "v", "volt", "vdc", "pack_voltage", "cell_voltage",
                "operating_voltage", "range_min", "range_max", "voltage_min", "voltage_max",
                "standoff", "standoff_voltage", "withstand"},
    "current": {"current", "a", "amp", "amps", "ma", "quiescent", "quiescent_current",
                "supply_current", "leakage_current", "short_circuit_current"},
    "temperature": {"temperature", "temp", "c", "ambient_temperature", "ambient_temp",
                    "operating_temperature"},
    "junction_temperature": {"junction_temperature", "junction_temp", "tj", "asic_junction_temperature"},
    "cell_temperature": {"cell_temperature", "cell_temp"},
    "coolant_temperature": {"coolant_temperature", "coolant_temp", "inlet_temperature"},
    "latency": {"latency", "response_time", "reaction_time", "transition_time",
                "switching_time", "propagation_delay", "disconnect_time", "opening_time"},
    "pyro_fuse_latency": {"pyro_fuse_latency", "pyro_latency", "pyrotechnic_trigger_latency",
                          "pyro_trigger_latency", "squib_trigger_latency"},
    "contactor_latency": {"contactor_latency", "contactor_transition_latency",
                          "contactor_opening_time", "contactor_response_time"},
    "persistence_time": {"persistence_time", "persistence", "filter_time", "debounce",
                         "debounce_time", "duration"},
    "tolerance": {"tolerance", "measurement_tolerance", "accuracy", "precision"},
    "energy": {"energy", "j", "joule", "pulse_energy", "short_circuit_energy"},
    "isolation": {"isolation", "dielectric", "dielectric_strength", "galvanic_isolation",
                  "insulation", "isolation_voltage"},
    "resistance": {"resistance", "ohm", "contact_resistance"},
    "ingress_protection": {"ingress_protection", "ip", "ip_rating", "ip54", "ip67"},
    "mtbf": {"mtbf", "mean_time_between_failures"},
    "frequency": {"frequency", "hz", "baud", "baud_rate", "data_rate", "bit_rate"},
    "pressure": {"pressure", "mbar", "bar", "kpa"},
    "force": {"force", "n", "newton", "crash_force"},
    "thd": {"thd", "total_harmonic_distortion", "harmonic_distortion"},
}


def normalize_parameter(name: Optional[str]) -> Optional[str]:
    """Normalize a parameter name to its canonical group, if recognizable."""
    if not name:
        return None
    n = name.strip().lower().replace(" ", "_").replace("-", "_")
    if n in _PARAMETER_GROUPS:
        return n
    for canonical, aliases in _PARAMETER_GROUPS.items():
        if n in aliases:
            return canonical
    return None


def parameters_compatible(
    required_parameter: Optional[str],
    evidence_parameter: Optional[str],
    evidence_quote: str = "",
) -> Optional[bool]:
    """Check whether evidence discusses the same physical quantity as a condition.

    Returns True / False / None (cannot establish — e.g. neither side names a
    recognizable parameter, in which case numeric-only matching remains the
    caller's decision).
    """
    req_norm = normalize_parameter(required_parameter)
    ev_norm = normalize_parameter(evidence_parameter)

    if req_norm and ev_norm:
        # Both recognized: exact canonical match only. This is what keeps
        # "junction temperature" from satisfying "ambient temperature" and
        # "pyro-fuse latency" from satisfying "contactor latency".
        return req_norm == ev_norm

    if req_norm:
        # Requirement names a parameter; look for its aliases in the quote.
        aliases = _PARAMETER_GROUPS.get(req_norm, {req_norm})
        text_forms = {
            evidence_quote.lower(),
            evidence_quote.lower().replace("_", " "),
            (evidence_parameter or "").lower(),
        }
        for form in text_forms:
            for alias in aliases:
                if alias in form:
                    return True
        # A contradicting recognized parameter in the quote is a mismatch.
        return None

    # Requirement parameter unrecognized: nothing to contradict.
    return None


# ── Parameter extraction from free text ─────────────────────────────────────
# Groups checked in this order: specific distinctions first, generic last,
# so "ASIC junction temperature" resolves to junction_temperature rather than
# the generic temperature group.

_PARAMETER_PRIORITY: list[str] = [
    "junction_temperature",
    "cell_temperature",
    "coolant_temperature",
    "pyro_fuse_latency",
    "contactor_latency",
    "ingress_protection",
    "thd",
    "mtbf",
    "isolation",
    "persistence_time",
    "tolerance",
    "latency",
    "voltage",
    "current",
    "temperature",
    "energy",
    "resistance",
    "frequency",
    "pressure",
    "force",
]


def extract_parameters_from_text(text: str) -> list[str]:
    """Return all canonical parameter groups detectable in a text (priority order)."""
    if not text:
        return []
    t = f" {text.lower()} ".replace("_", " ").replace("-", " ")
    found: list[str] = []
    for group in _PARAMETER_PRIORITY:
        aliases = _PARAMETER_GROUPS.get(group, {group})
        for alias in aliases:
            alias_form = f" {alias.replace('_', ' ')} "
            if alias_form in t or f" {alias} " in t:
                found.append(group)
                break
    return found


# ── Qualification model ──────────────────────────────────────────────────────


class EvidenceQualification(BaseModel):
    """Qualification verdict for one piece of evidence against one requirement.

    Produced BEFORE verification; consumed by the Python aggregator and by
    the LLM prompt (as context). An unqualified piece of evidence may still
    be semantically relevant, but it cannot establish verification.
    """

    evidence_id: str                      # e.g. "E1" — matches prompt labeling
    document_name: str
    source_authority: str                 # SourceAuthority literal value
    entity_scope: str = "System"
    parameter: Optional[str] = None           # primary canonical parameter
    parameters_found: list[str] = Field(default_factory=list)  # all canonical groups present
    verification_method: VerificationMethod = "unknown"          # evidence's method
    required_verification_method: Optional[str] = None           # requirement's method
    method_compatible: bool = False
    scope_compatible: Optional[bool] = None   # True / False / None (unknown)
    parameter_compatible: Optional[bool] = None
    is_authoritative: bool = False        # empirical / qualification / validation / matrix
    qualification_status: QualificationStatus = "UNKNOWN"
    reason: str = ""
