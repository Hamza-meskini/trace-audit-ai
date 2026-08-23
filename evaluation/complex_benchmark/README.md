# TraceAudit AI - 100-Requirement Complex Benchmark

This benchmark is a formal scientific stress-test for automated compliance and verification pipelines in automotive systems engineering.

---

## 📊 Benchmark Summary

- **Total Requirements:** 100
- **Total Technical Documents:** 20 (DOCX, PDF, XLSX)
- **Class Distribution:** Exactly 20 of each class (Balanced 5-Class)
  - `SUPPORTED`: 20
  - `PARTIAL`: 20
  - `CONFLICT`: 20
  - `MISSING`: 20
  - `UNKNOWN`: 20
- **Difficulty Distribution:**
  - Relatively Difficult: 20
  - Hard: 30
  - Very Hard: 30
  - Adversarial / Expert-Level: 20

---

## 🏎️ Covered Automotive Domains

1. High-Voltage Battery Systems & BMS (`REQ-AUT-001` to `010`)
2. Traction Inverter & Motor Control (`REQ-AUT-011` to `020`)
3. DC-DC Converter & Power Distribution (`REQ-AUT-021` to `030`)
4. On-Board Charger (OBC) & AC/DC Fast Charging (`REQ-AUT-031` to `040`)
5. Vehicle Thermal Management & Heat Pump (`REQ-AUT-041` to `050`)
6. CAN-FD, Ethernet & In-Vehicle Networking (`REQ-AUT-051` to `060`)
7. Diagnostics (UDS ISO 14229) & Bootloader/OTA (`REQ-AUT-061` to `070`)
8. Functional Safety (ISO 26262 ASIL-D) & Watchdogs (`REQ-AUT-071` to `080`)
9. Automotive Cybersecurity (ISO 21434 & SecOC) (`REQ-AUT-081` to `090`)
10. Environmental Dynamics, Vibration & EMC (`REQ-AUT-091` to `100`)

---

## 📁 File Structure

```
evaluation/complex_benchmark/
  ├── requirements.json              # Full 100 requirements with structured conditions
  ├── ground_truth.json              # Ground truth status, evidence linkages, notes
  ├── validate_benchmark.py          # Benchmark validation script
  ├── documents/                     # 20 technical documents (PDF, DOCX, XLSX)
  │     ├── 01_System_Requirements_Specification_SRS.docx
  │     ├── 02_System_Architecture_Interface_Spec.pdf
  │     ├── ...
  │     └── 20_Master_Compliance_Verification_Matrix.xlsx
  └── README.md
```
