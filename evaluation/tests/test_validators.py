"""Unit tests for structured requirement contracts, evidence claims, SI unit conversions, and deterministic validators."""

import sys
from pathlib import Path
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.schemas.contract import parse_requirement_contract, RequirementContract
from app.schemas.claim import EvidenceClaim, extract_claims_from_chunk
from app.services.units import convert_value, are_units_compatible, normalize_unit_str
from app.services.validators.numeric_range import validate_numeric_range
from app.services.validators.threshold import validate_threshold
from app.services.validators.duration import validate_duration
from app.services.validators.boolean_flag import validate_boolean_flag
from app.services.validators.test_verdict import validate_test_verdict
from app.services.validators.semantic import validate_semantic
from app.services.contradiction import detect_contract_contradiction


class TestDeterministicValidators(unittest.TestCase):

    def test_exact_unit_conversion_matrix(self):
        """Test physical unit conversions across electrical, time, and temperature dimensions."""
        self.assertEqual(convert_value(2500.0, "v", "kv"), 2.5)
        self.assertEqual(convert_value(2.5, "kv", "v"), 2500.0)
        self.assertEqual(convert_value(150.0, "ua", "ma"), 0.15)
        self.assertEqual(convert_value(0.15, "ma", "ua"), 150.0)
        self.assertEqual(convert_value(1000.0, "ms", "s"), 1.0)
        self.assertEqual(convert_value(5000.0, "us", "ms"), 5.0)

        # Incompatible units should return None
        self.assertIsNone(convert_value(100.0, "v", "a"))
        self.assertIsNone(convert_value(50.0, "c", "s"))
        self.assertFalse(are_units_compatible("v", "a"))
        self.assertTrue(are_units_compatible("v", "kv"))
        self.assertTrue(are_units_compatible("ua", "a"))

    def test_numeric_range_fully_covered(self):
        """Discrete tested sweep spanning [400V, 800V] should return SUPPORTED."""
        contract = parse_requirement_contract(
            req_code="REQ-BCU-001",
            title="High Voltage Pack Nominal Operating Range",
            description="The BCU shall operate continuously over a nominal pack voltage range of 400.0 V DC to 800.0 V DC.",
            category="Electrical",
        )
        claims = [
            EvidenceClaim(
                document_name="04_Battery_Management_Test_Report.pdf",
                claim_type="discrete_sweep",
                discrete_points=[400.0, 600.0, 800.0],
                unit="V DC",
                quote="Traction pack nominal voltage range evaluated at 400.0 V, 600.0 V and 800.0 V DC.",
            )
        ]
        outcome = validate_numeric_range(contract, claims)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.status, "SUPPORTED")

    def test_numeric_range_partially_covered(self):
        """Testing from -20°C to +70°C against -40°C to +85°C should return PARTIAL."""
        contract = parse_requirement_contract(
            req_code="REQ-BCU-011",
            title="Extended Climatic Operating Temperature",
            description="The BCU shall maintain full functional operation across ambient temperature range of -40.0 °C to +85.0 °C.",
            category="Environmental",
        )
        claims = [
            EvidenceClaim(
                document_name="05_Environmental_EMC_Report.pdf",
                claim_type="numeric_range",
                min_value=-20.0,
                max_value=70.0,
                unit="°C",
                quote="Climatic chamber test (REQ-BCU-011) evaluated from -20.0 °C to +70.0 °C.",
            )
        ]
        outcome = validate_numeric_range(contract, claims)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.status, "PARTIAL")

    def test_numeric_range_completely_outside(self):
        """Evidence operating range completely disjoint from requirement should return CONFLICT."""
        contract = parse_requirement_contract(
            req_code="REQ-BCU-099",
            title="Auxiliary Supply Voltage Envelope",
            description="System must operate between 18.0 V to 32.0 V DC.",
            category="Electrical",
        )
        claims = [
            EvidenceClaim(
                document_name="Component_Datasheet.pdf",
                claim_type="numeric_range",
                min_value=10.0,
                max_value=15.0,
                unit="V",
                quote="Auxiliary supply restricted to 10.0 V to 15.0 V DC.",
            )
        ]
        outcome = validate_numeric_range(contract, claims)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.status, "CONFLICT")

    def test_threshold_upper_bound_satisfied(self):
        """Quiescent current of 112.4 uA against requirement <= 150.0 uA should return SUPPORTED."""
        contract = parse_requirement_contract(
            req_code="REQ-BCU-005",
            title="Quiescent Sleep State Current Draw",
            description="When vehicle ignition is off, quiescent current from auxiliary battery shall not exceed 150.0 uA.",
            category="Electrical",
        )
        claims = [
            EvidenceClaim(
                document_name="04_Battery_Management_Test_Report.pdf",
                claim_type="threshold",
                value=112.4,
                unit="uA",
                quote="Quiescent current measured at 112.4 uA in deep sleep mode.",
            )
        ]
        outcome = validate_threshold(contract, claims)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.status, "SUPPORTED")

    def test_threshold_upper_bound_violated(self):
        """Measured value exceeding allowable threshold should return CONFLICT."""
        contract = parse_requirement_contract(
            req_code="REQ-BCU-005",
            title="Quiescent Sleep State Current Draw",
            description="Quiescent current shall not exceed 150.0 uA.",
            category="Electrical",
        )
        claims = [
            EvidenceClaim(
                document_name="Lab_Report.pdf",
                claim_type="threshold",
                value=195.0,
                unit="uA",
                quote="Quiescent current measured at 195.0 uA.",
            )
        ]
        outcome = validate_threshold(contract, claims)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.status, "CONFLICT")

    def test_duration_latency_validation(self):
        """6.4 ms latency against <= 10.0 ms limit should return SUPPORTED."""
        contract = parse_requirement_contract(
            req_code="REQ-BCU-007",
            title="Safe State Contactor De-energization Latency",
            description="Emergency shutdown shall open contactors within maximum latency ≤ 10.0 ms.",
            category="Safety",
        )
        claims = [
            EvidenceClaim(
                document_name="04_Battery_Management_Test_Report.pdf",
                claim_type="threshold",
                value=6.40,
                unit="ms",
                quote="Contactor disconnect latency (REQ-BCU-007) measured at 6.40 ms.",
            )
        ]
        outcome = validate_duration(contract, claims)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.status, "SUPPORTED")

    def test_test_verdict_matrix_mapping(self):
        """Compliance matrix with 'NOT STARTED' should return MISSING."""
        contract = parse_requirement_contract(
            req_code="REQ-BCU-018",
            title="High Voltage Interlock Loop Fast Disconnect Latency",
            description="HVIL trip line shall disconnect within 5.0 ms.",
            category="Safety",
        )
        claims = [
            EvidenceClaim(
                document_name="07_Compliance_Verification_Matrix.xlsx",
                claim_type="test_verdict",
                test_result="NOT STARTED",
                quote="REQ-BCU-018 | HVIL Fast Disconnect | NOT STARTED | Test plan scheduled",
            )
        ]
        outcome = validate_test_verdict(contract, claims)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.status, "MISSING")

    def test_semantic_access_control_contradiction(self):
        """Credential authentication required in spec vs open access in architecture doc should return CONFLICT."""
        contract = parse_requirement_contract(
            req_code="REQ-BCU-026",
            title="Diagnostic Service Port Access Control",
            description="Diagnostic port shall require cryptographic seed-key credential authentication.",
            category="Cybersecurity",
        )
        claims = [
            EvidenceClaim(
                document_name="02_System_Architecture_Spec.pdf",
                claim_type="semantic",
                quote="Diagnostic service port operates in open access mode with no login required for factory calibration.",
            )
        ]
        outcome = validate_semantic(contract, claims)
        self.assertEqual(outcome.status, "CONFLICT")

    def test_conservative_unknown_fallback(self):
        """Generic qualitative compliance claim without technical values should return UNKNOWN."""
        contract = parse_requirement_contract(
            req_code="REQ-BCU-099",
            title="Custom Algorithm Performance",
            description="The algorithm shall optimize balancing trajectory.",
            category="Software",
        )
        claims = [
            EvidenceClaim(
                document_name="General_Architecture.pdf",
                claim_type="semantic",
                quote="The architecture supports optimization modules.",
            )
        ]
        outcome = validate_semantic(contract, claims)
        self.assertEqual(outcome.status, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
