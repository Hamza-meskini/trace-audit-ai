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

import re
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
    ("SafetyController", ("safety microcontroller", "watchdog", "brownout", "pmic", "core voltage", "lockstep")),
    ("SecuritySystem", ("cybersecurity", "secure logging", "security event", "secevent", "hsm", "secoc")),
    ("ECU", ("ecu", "electronic control unit")),
    ("Inverter", ("inverter", "tc-inv", "tc_inv", "gate driver", "resolver", "motor control", "foc", "flux weakening")),
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
    "SAFETYCONTROLLER": "safety_controller",
    "SECURITYSYSTEM": "security_system",
    "ECU": "ecu",
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

    # A generic System scope is often a parser fallback rather than an
    # affirmative claim that only whole-system evidence is admissible. A
    # specific component therefore remains unresolved (letting the semantic
    # reasoner inspect the requirement text) instead of becoming a false hard
    # mismatch. Two different *specific* entities are still incompatible.
    if r == "system":
        return None

    # Different specific entities.
    return False


# ── Parameter normalization & compatibility ─────────────────────────────────
# Small reusable alias groups. Deliberately NOT a giant ontology: each group
# only encodes distinctions that change verification meaning (e.g. junction
# temperature ≠ ambient temperature, pyro-fuse latency ≠ contactor latency).

_PARAMETER_GROUPS: dict[str, set[str]] = {
    "voltage": {"voltage", "v", "volt", "vdc", "pack_voltage", "cell_voltage",
                "operating_voltage", "range_min", "range_max", "voltage_min", "voltage_max",
                "standoff", "standoff_voltage", "withstand"},
    "current": {"current", "a", "amp", "amps", "ma", "current_threshold", "phase_current_threshold", "quiescent", "quiescent_current",
                "supply_current", "leakage_current", "short_circuit_current"},
    "temperature": {"temperature", "temp", "c", "ambient_temperature", "ambient_temp",
                    "operating_temperature"},
    "junction_temperature": {"junction_temperature", "junction_temp", "tj", "asic_junction_temperature"},
    "cell_temperature": {"cell_temperature", "cell_temp"},
    "coolant_temperature": {"coolant_temperature", "coolant_temp", "coolant_temp_target",
                            "coolant_temperature_target", "inlet_temperature", "inlet_coolant_temperature"},
    "latency": {"latency", "response_time", "reaction_time", "transition_time",
                "switching_time", "propagation_delay", "disconnect_time", "opening_time",
                "shutdown_latency", "reset_latency", "detection_latency", "trip_latency",
                "recovery_time", "assertion_latency", "window_min", "window_max", "service_window"},
    "pyro_fuse_latency": {"pyro_fuse_latency", "pyro_latency", "pyrotechnic_trigger_latency",
                          "pyro_trigger_latency", "squib_trigger_latency"},
    "contactor_latency": {"contactor_latency", "contactor_transition_latency",
                          "contactor_opening_time", "contactor_response_time"},
    "persistence_time": {"persistence_time", "persistence", "persistent", "persisted",
                         "hold_time", "dwell_time", "filter_time", "debounce",
                         "debounce_time", "duration"},
    "tolerance": {"tolerance", "measurement_tolerance", "accuracy", "precision"},
    "energy": {"energy", "j", "joule", "pulse_energy", "short_circuit_energy"},
    "isolation": {"isolation", "dielectric", "dielectric_strength", "galvanic_isolation",
                  "insulation", "isolation_voltage"},
    "resistance": {"resistance", "ohm", "contact_resistance"},
    "ingress_protection": {"ingress_protection", "ip", "ip_rating", "ip54", "ip67"},
    "mtbf": {"mtbf", "mean_time_between_failures"},
    "frequency": {"frequency", "hz", "baud", "baud_rate", "data_rate", "bit_rate"},
    "emissions_margin": {"emissions_margin", "attenuation_margin", "margin", "db_margin"},
    "tamper_evident": {"tamper_evident", "append_only", "audit_log_integrity"},
    "pressure": {"pressure", "mbar", "bar", "kpa"},
    "force": {"force", "n", "newton", "crash_force"},
    "heat_load": {"heat_load", "thermal_load", "pack_heat_load", "heat_dissipation",
                  "thermal_dissipation", "power", "kw", "w"},
    "thd": {"thd", "total_harmonic_distortion", "harmonic_distortion"},
}

_PARAMETER_GROUPS["frequency"].add("frequency_range")


def normalize_parameter(name: Optional[str]) -> Optional[str]:
    """Normalize a parameter name to its canonical group, if recognizable."""
    if not name:
        return None
    n = name.strip().lower().replace(" ", "_").replace("-", "_")
    if n == "brownout_threshold":
        return "voltage"
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
    "emissions_margin",
    "tamper_evident",
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
    "heat_load",
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
    # Time units need phrase-level interpretation. "for 60 s" is a duration,
    # while "disabled in 1.1 us" is a latency; a bare unit regex cannot tell
    # those apart.
    time_unit = r"(?:ns|us|µs|μs|ms|milliseconds?|s|seconds?|sec)"
    persistence_signal = re.search(
        rf"(?:\bfor\s+|\bheld\s+(?:for\s+)?|\bmaintained\s+(?:for\s+)?|"
        rf"\bpersist(?:ent|ed)?\s+(?:for\s+)?)\d+(?:\.\d+)?\s*{time_unit}\b",
        text,
        re.IGNORECASE,
    )
    latency_signal = re.search(
        rf"(?:\bwithin\s+|\bafter\s+|\b(?:disabled|opened|closed|tripped|triggered|"
        rf"asserted|recovered|responded)\s+in\s+)\d+(?:\.\d+)?\s*{time_unit}\b",
        text,
        re.IGNORECASE,
    )
    if persistence_signal and "persistence_time" not in found:
        found.append("persistence_time")
    if latency_signal and "latency" not in found:
        found.append("latency")

    # Other unit-bearing measured values provide reliable local parameter
    # signals even when the prose omits the quantity name.
    unit_signals = [
        ("voltage", r"\d(?:\.\d+)?\s*(?:mV|V|kV)(?:\s*(?:AC|DC))?\b"),
        ("current", r"\d(?:\.\d+)?\s*(?:uA|µA|μA|mA|A|kA)\b"),
        ("frequency", r"\d(?:\.\d+)?\s*(?:Hz|kHz|MHz|GHz)\b"),
        ("emissions_margin", r"\d(?:\.\d+)?\s*dB\b"),
        ("resistance", r"\d(?:\.\d+)?\s*(?:uOhm|µOhm|mOhm|Ohm|kOhm|K/W)\b"),
        ("heat_load", r"\d(?:\.\d+)?\s*(?:W|kW)\b"),
    ]
    for group, pattern in unit_signals:
        if group not in found and re.search(pattern, text, re.IGNORECASE):
            found.append(group)
    # Backward-compatible fallback for terse timing records such as
    # "response = 4 ms" where no stronger duration/latency phrase exists.
    if "latency" not in found and "persistence_time" not in found and re.search(
        rf"\d+(?:\.\d+)?\s*{time_unit}\b", text, re.IGNORECASE
    ):
        found.append("latency")
    return found


# ── Qualification model ──────────────────────────────────────────────────────


class EvidenceQualification(BaseModel):
    """Qualification verdict for one piece of evidence against one requirement.

    Produced BEFORE verification; consumed by the Python aggregator and by
    the LLM prompt (as context). An unqualified piece of evidence may still
    be semantically relevant, but it cannot establish verification.
    """

    evidence_id: str                      # e.g. "E1" — matches prompt labeling
    source_chunk_id: Optional[str] = None  # stable link back to the retrieved chunk
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
