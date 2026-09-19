# TraceAudit AI — Benchmark Test Package & Expected Results

This test package is designed for manual end-to-end verification of the **TraceAudit AI** platform deployed on Databricks Apps. It contains realistic, complex automotive battery management system (BMS) engineering documents with exact numerical thresholds, oscilloscope waveforms, thermal telemetry logs, dimensional schematics, and physical inspection photographs.

---

## 1. Document Inventory

The `test_audit_package/` folder contains 4 documents:

| # | File Name | Document Role | Key Contents |
|---|---|---|---|
| **1** | `01_System_Requirements_Specification_BMS.pdf` | **Requirements Source (SRS)** | 10 formal technical requirements with strict numerical tolerances, timing limits, ranges, and visual criteria. |
| **2** | `02_HV_Electrical_and_Safety_Test_Report.pdf` | **Evidence Document A** | High-voltage lab test report featuring oscilloscope discharge curve, contactor trip timing, and megohmmeter insulation resistance table. |
| **3** | `03_Thermal_Chamber_and_Environmental_Log.pdf` | **Evidence Document B** | Multi-channel thermal chamber log featuring thermocouple telemetry plots, thermal delta calculations, and cold ambient qualification data. |
| **4** | `04_Physical_Inspection_and_Enclosure_Report.pdf` | **Evidence Document C** | Physical inspection report featuring calibrated high-voltage label photograph, optical creepage distance diagram, and IP67 water tank submersion certificate. |

---

## 2. Master Verification Matrix & Expected Results

| Requirement ID | Title | Required Threshold | Observed Evidence | Expected Verdict | Expected Finding Severity | Relevant Document & Location |
|---|---|---|---|---|---|---|
| **REQ-BMS-001** | High-Voltage Active Bus Discharge | $V_{\text{bus}} < 60.0\text{ V}$ within $t \le 5.00\text{ s}$ | $41.8\text{ V}$ measured at $t = 3.42\text{ s}$ | **Supported** | None | `02_HV_Electrical...pdf` — Page 2, Fig 2.1 & Table 2.1 |
| **REQ-BMS-002** | Galvanic Isolation Resistance | Isolation $\ge 500.0\ \Omega/\text{V}$ | $+1,420\ \Omega/\text{V}$ (pos) & $-1,380\ \Omega/\text{V}$ (neg) | **Supported** | None | `02_HV_Electrical...pdf` — Page 3, Section 3.2 |
| **REQ-BMS-003** | Over-Voltage Contactor Trip Reaction | Trip disconnect time $t \le 20.0\text{ ms}$ upon $V_{\text{cell}} > 4.250\text{ V}$ | Contactor state open at $14.6\text{ ms}$ | **Supported** | None | `02_HV_Electrical...pdf` — Page 4, Waveform 4.1 |
| **REQ-BMS-004** | Fast-Charge Cell Thermal Delta | Maximum cell $\Delta T \le 5.0\ ^\circ\text{C}$ across all modules during 3C charge | Module 4 TC-08 reached $44.2\ ^\circ\text{C}$ vs Module 1 TC-01 at $36.8\ ^\circ\text{C}$ ($\Delta T = \mathbf{7.4\ ^\circ\text{C}}$) | **Potential conflict** | **High** | `03_Thermal_Chamber...pdf` — Page 2, Table 1.2 & Chart 1.1 |
| **REQ-BMS-005** | Extreme Cold Ambient Operational Range | Operational across range $-40.0\ ^\circ\text{C}$ to $+85.0\ ^\circ\text{C}$ | Chamber test validated $-20.0\ ^\circ\text{C}$ to $+70.0\ ^\circ\text{C}$ only; $-40\ ^\circ\text{C}$ not tested | **Partial evidence** | **Medium** | `03_Thermal_Chamber...pdf` — Page 3, Section 2.3 |
| **REQ-BMS-006** | Thermal Runaway Warning Signal Latency | Warning broadcast latency $t < 100\text{ ms}$ upon trigger | CAN-FD frame `0x018` transmitted in $48\text{ ms}$ | **Supported** | None | `03_Thermal_Chamber...pdf` — Page 4, Section 3.1 |
| **REQ-BMS-007** | High-Voltage Enclosure Hazard Labeling | Permanent ISO 7010-W012 triangle height $h \ge 40.0\text{ mm}$ on top cover *(Requires Visual Inspection)* | Visual callout ruler measures symbol height at $45.0\text{ mm}$ adjacent to manual service disconnect | **Supported** | None | `04_Physical_Inspection...pdf` — Page 2, Photograph Fig 1.1 |
| **REQ-BMS-008** | High-Voltage Terminal Creepage Distance | Surface creepage distance $d \ge 8.00\text{ mm}$ to chassis | Optical CMM microscope measured creepage path at $8.65\text{ mm}$ | **Supported** | None | `04_Physical_Inspection...pdf` — Page 3, Diagram 2.1 |
| **REQ-BMS-009** | Submersion Ingress Protection (IP67) | Compliance with IP67: zero water ingress at $1.00\text{ m}$ depth for $30.0\text{ min}$ | Post-submersion inspection: $0.0\text{ mL}$ ingress, internal pressure $25\text{ kPa}$ held | **Supported** | None | `04_Physical_Inspection...pdf` — Page 4, Certificate 3.1 |
| **REQ-BMS-010** | Pyrotechnic Disconnect on Crash Acceleration | Fire pyro-fuse within $5.0\text{ ms}$ if crash shock $a_x > 15.0\text{ g}$ for $> 10.0\text{ ms}$ | **No mechanical shock or crash sled test report provided in evidence set** | **Missing evidence** | **Critical** | *Omitted from all evidence documents* |

---

## 3. Expected KPI Summary

When the audit completes in TraceAudit AI, the dashboard should report:

- **Total Requirements Analyzed**: `10`
- **Supported (Fully Compliant)**: `7` ($70\%$)
- **Potential Conflict**: `1` ($10\%$) — `REQ-BMS-004` (Fast-charge thermal gradient exceeded)
- **Partial Evidence**: `1` ($10\%$) — `REQ-BMS-005` (Missing $-40\ ^\circ\text{C}$ extreme qualification)
- **Missing Evidence**: `1` ($10\%$) — `REQ-BMS-010` (Missing crash acceleration test data)
- **Total Open Findings**: `3`
  - **Critical**: `1` (`REQ-BMS-010`)
  - **High**: `1` (`REQ-BMS-004`)
  - **Medium**: `1` (`REQ-BMS-005`)

---

## 4. How to Test in Your Databricks App

1. Open your Databricks App URL in your browser:
   `https://traceaudit-7474660913574544.aws.databricksapps.com`
2. Click **Start an audit** (or navigate to `/new-audit`):
   - **Step 1 (Project)**:
     - Project Name: `EV Battery Management System (BMS) Verification`
     - Product Category: `Safety`
     - Company: `Apex EV Mobility / Tier-1`
   - **Step 2 (Requirements)**:
     - Upload `01_System_Requirements_Specification_BMS.pdf`
   - **Step 3 (Evidence)**:
     - Upload `02_HV_Electrical_and_Safety_Test_Report.pdf`
     - Upload `03_Thermal_Chamber_and_Environmental_Log.pdf`
     - Upload `04_Physical_Inspection_and_Enclosure_Report.pdf`
   - **Step 4 (Model & Review)**:
     - Select your preferred LLM and click **Start Audit**.
3. **Inspect the Results**:
   - Navigate to **Traceability Register** (`/traceability`): inspect how atomic requirements connect to the evidence documents.
   - Navigate to **Requirements Matrix** (`/requirements`): click into `REQ-BMS-004` to see the atomic condition failure on $\Delta T$, and click into `REQ-BMS-007` to view the document inspector showing the warning label photograph.
   - Navigate to **Findings & Triage** (`/findings`): review the 3 open findings and test changing their review state to *Approved* or *Needs review*.
   - Navigate to **Reports** (`/reports`): export the full Requirement Register as CSV.
