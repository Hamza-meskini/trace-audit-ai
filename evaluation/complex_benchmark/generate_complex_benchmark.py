"""Complex Benchmark Generator for TraceAudit AI.

Generates:
1. evaluation/complex_benchmark/requirements.json (100 requirements)
2. evaluation/complex_benchmark/ground_truth.json (100 ground truth items)
3. 20 technical documents (PDF, DOCX, XLSX, TXT) with realistic formatting,
   including valid evidence, partial proofs, contradictions, and distractors.
4. evaluation/complex_benchmark/README.md
"""

import json
import os
import sys
from pathlib import Path

# Setup paths
BENCHMARK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCHMARK_DIR))

from generator_data import BENCHMARK_REQUIREMENTS
from generator_corpus import BENCHMARK_DOCUMENTS, BENCHMARK_GROUND_TRUTH

import pymupdf  # PyMuPDF for high-quality PDF generation
import docx
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side


def generate_json_files():
    """Generate requirements.json and ground_truth.json."""
    req_file = BENCHMARK_DIR / "requirements.json"
    gt_file = BENCHMARK_DIR / "ground_truth.json"

    with open(req_file, "w", encoding="utf-8") as f:
        json.dump(BENCHMARK_REQUIREMENTS, f, indent=2)
    print(f"  [OK] Saved {len(BENCHMARK_REQUIREMENTS)} requirements to {req_file.name}")

    with open(gt_file, "w", encoding="utf-8") as f:
        json.dump(BENCHMARK_GROUND_TRUTH, f, indent=2)
    print(f"  [OK] Saved {len(BENCHMARK_GROUND_TRUTH)} ground truth items to {gt_file.name}")


def generate_docx_srs(target_path: Path):
    """Generate 01_System_Requirements_Specification_SRS.docx containing all 100 requirements."""
    doc = docx.Document()
    
    # Title
    title = doc.add_heading("Automotive Powertrain & Electronic Control System Specification", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    p = doc.add_paragraph("Document ID: SRS-AUT-2026-V4.2 | Tier-1 Automotive OEM Engineering\nConfidential - High Voltage & Functional Safety Engineering Specification\n")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    current_cat = ""
    for r in BENCHMARK_REQUIREMENTS:
        cat = r["category"]
        if cat != current_cat:
            current_cat = cat
            doc.add_heading(f"Section: {current_cat}", level=1)
        
        # Add requirement
        req_heading = doc.add_heading(f"{r['requirement_id']}: {r['title']}", level=2)
        doc.add_paragraph(f"Mandatory Clause: {r['requirement_text']}")
        
        # Conditions list
        cond_para = doc.add_paragraph()
        cond_para.add_run("Formal Engineering Constraints:\n").bold = True
        for c in r["conditions"]:
            cond_para.add_run(f"  • [{c['condition_id']}] {c['description']} -> {c['parameter']} {c['operator']} {c['threshold']} {c['unit']}\n")
        
        doc.add_paragraph() # Spacer

    doc.save(target_path)
    print(f"  [OK] Generated DOCX specification: {target_path.name}")


def _create_pdf(target_path: Path, title: str, subtitle: str, pages_content: list[list[str]]):
    """Helper to generate multi-page PDF documents with PyMuPDF."""
    doc = pymupdf.open()
    
    for page_idx, lines in enumerate(pages_content, 1):
        page = doc.new_page(width=595, height=842) # A4 size (points)
        
        # Header banner
        rect = pymupdf.Rect(40, 30, 555, 75)
        page.draw_rect(rect, color=(0.1, 0.2, 0.4), fill=(0.93, 0.95, 0.98))
        page.insert_text((50, 50), title, fontsize=12, color=(0.1, 0.2, 0.5))
        page.insert_text((50, 68), subtitle, fontsize=8.5, color=(0.4, 0.4, 0.4))
        
        y = 100
        for line in lines:
            if y > 790:
                break
            if line.startswith("===") or line.startswith("---"):
                page.draw_line((40, y), (555, y), color=(0.7, 0.7, 0.7))
                y += 15
            elif line.startswith("# ") or line.startswith("## "):
                heading_text = line.lstrip("# ")
                page.insert_text((45, y), heading_text, fontsize=11, color=(0.05, 0.15, 0.35))
                y += 20
            elif line.startswith("• ") or line.startswith("  - "):
                page.insert_text((55, y), line, fontsize=9, color=(0.2, 0.2, 0.2))
                y += 15
            elif "Verdict: PASS" in line or "VERIFIED" in line:
                page.insert_text((45, y), line, fontsize=9, color=(0.0, 0.5, 0.1))
                y += 16
            elif "failing" in line or "CONFLICT" in line or "exceeding" in line:
                page.insert_text((45, y), line, fontsize=9, color=(0.7, 0.1, 0.1))
                y += 16
            else:
                # Regular text (split if too long)
                page.insert_text((45, y), line[:110], fontsize=9, color=(0.15, 0.15, 0.15))
                y += 15
                if len(line) > 110:
                    page.insert_text((45, y), line[110:220], fontsize=9, color=(0.15, 0.15, 0.15))
                    y += 15
        
        # Footer
        footer_text = f"Page {page_idx} of {len(pages_content)}  |  TraceAudit Confidential Lab Records  |  ISO 17025 Accredited"
        page.insert_text((150, 815), footer_text, fontsize=7.5, color=(0.5, 0.5, 0.5))
    
    doc.save(target_path)
    doc.close()
    print(f"  [OK] Generated PDF document: {target_path.name} ({len(pages_content)} pages)")


def generate_all_pdf_documents(docs_dir: Path):
    """Generate all 18 PDF technical reports and datasheets."""
    
    # 02_System_Architecture_Interface_Spec.pdf
    _create_pdf(
        docs_dir / "02_System_Architecture_Interface_Spec.pdf",
        "System Architecture & Interface Specification (v3.1)",
        "System Level High-Voltage, Networking, and Diagnostic Pinout Definitions",
        [
            [
                "## 1. High-Voltage Interlock Loop (HVIL) Architecture",
                "The HVIL subsystem monitors continuity of all high-voltage connectors and service disconnect covers.",
                "• Section 3.4: HVIL signal generator is specified for 1 kHz PWM with 5 ms fault detector as described in architecture section 3.4.",
                "• Diagnostic loop impedance is monitored via secondary microcontroller ADC input.",
                "---",
                "## 2. Inverter Field Oriented Control (FOC) Architecture",
                "• Section 5.2 defines the FOC flux weakening vector control block architecture and current loop equations.",
                "• Pulse-width modulation is synthesized via third-harmonic injection space vector generator.",
                "---",
                "## 3. High-Voltage Soft-Start Pre-Charge Circuit",
                "• Section 4.3 defines the pre-charge resistor sizing (50 ohm) and soft-start timing diagram.",
                "• Inrush current profile is limited during contactor sequencing.",
                "---",
                "## 4. Electric Vehicle Grid Interface Architecture",
                "• Section 6.1 specifies the HomePlug GreenPHY architecture and TLS 1.3 cipher suite requirements.",
                "• Smart grid interface aligns with ISO 15118-20 communications controller."
            ],
            [
                "## 5. Thermal Management Controller Interfaces",
                "• Section 7.4 describes water pump LIN diagnostic messages and dry-run fault detection algorithm.",
                "• Pump speed feedback is encoded as 8-bit LIN frame with cavitation alarm flag.",
                "---",
                "## 6. In-Vehicle Networking Socket Buffers",
                "• Section 8.1 defines the static socket buffer allocation tables in AUTOSAR BswM configuration.",
                "• Up to 8 concurrent TCP stream connections are allocated without dynamic memory allocation.",
                "---",
                "## 7. Diagnostic Fault Code Protocol Architecture",
                "• Section 9.2 specifies negative response code handling tables for diagnostic stack.",
                "• Handles NRC 0x12 (subFunctionNotSupported) and NRC 0x22 (conditionsNotCorrect).",
                "---",
                "## 8. Safety Clock Supervisor & Mechanical Shock Profiles",
                "• Section 10.1 defines the clock supervisor hardware architecture and backup oscillator configuration.",
                "• Section 11.3 describes the secure logging event format and flash partition layout.",
                "• Section 12.1 specifies mechanical shock test parameters and fixture mounting details per ISO 16750-3."
            ]
        ]
    )

    # 03_BMS_Cell_Supervisory_ASIC_Datasheet.pdf
    _create_pdf(
        docs_dir / "03_BMS_Cell_Supervisory_ASIC_Datasheet.pdf",
        "BMS-ASIC-8800 16-Channel Battery Monitoring IC Datasheet",
        "Semiconductor Component Specification & Absolute Maximum Ratings",
        [
            [
                "## 1. Absolute Maximum Electrical Ratings",
                "Stresses beyond those listed under Absolute Maximum Ratings may cause permanent damage to the device.",
                "• Parameter: High-Voltage Common-Mode Standoff Voltage (V_HVCM)",
                "• Absolute Maximum Ratings: Maximum continuous common-mode voltage rating is limited to 750.0 V DC. Operating above 750 V DC violates manufacturer warranty and risks junction punch-through.",
                "• Operating Voltage (Cell Inputs): -0.3 V to +6.0 V per channel.",
                "---",
                "## 2. CAN-FD Transceiver Physical Interface",
                "• Parameter: Bus Common-Mode Operating Range (V_CANCM)",
                "• Absolute Maximum Ratings: Maximum continuous common-mode voltage range is -7.0 V to +7.0 V DC. Operation beyond ±7 V causes bit decoding errors.",
                "• Differential input threshold: 0.5 V to 0.9 V.",
                "---",
                "## 3. Cell Balancing Switch Dissipation",
                "• Maximum on-chip balancing switch continuous dissipation: 250 mW per package.",
                "• Balancing current derating curves require external bypass transistors for currents above 100 mA."
            ]
        ]
    )

    # 04_High_Voltage_Contactor_Datasheet.pdf
    _create_pdf(
        docs_dir / "04_High_Voltage_Contactor_Datasheet.pdf",
        "HVC-1000 Heavy-Duty Contactor & Valve Component Datasheet",
        "Electromechanical Component Ratings & Pressure Specifications",
        [
            [
                "## 1. High-Voltage Main Contactor Specifications",
                "• Rated Operating Voltage: 1000 V DC nominal, 1200 V DC peak.",
                "• Continuous Current: 500 A @ 85°C ambient.",
                "• Mechanical switching closing time: 18 ms max; opening time: 8 ms max.",
                "---",
                "## 2. Integrated 3-Way Coolant Actuator Valve",
                "• Component Maximum Pressure Rating: Maximum continuous rated pressure for 3-way coolant valve is 1.80 bar. Pressures above 1.8 bar risk seal deformation.",
                "• Burst pressure rating: 3.20 bar static.",
                "• Media compatibility: 50/50 Water-Ethylene Glycol at -40°C to +90°C."
            ]
        ]
    )

    # 05_Pyro_Fuse_Actuator_Datasheet.pdf
    _create_pdf(
        docs_dir / "05_Pyro_Fuse_Actuator_Datasheet.pdf",
        "PYRO-FAST-800 Pyrotechnic Battery Disconnect Fuse Datasheet",
        "Safety Critical Battery Disconnect Specification",
        [
            [
                "## 1. Pyrotechnic Trigger Characteristics",
                "• Firing Loop Current: 1.75 A minimum firing pulse for 2.0 ms.",
                "• Safe Diagnostic Monitoring Current: <= 10.0 mA continuous (no-fire limit).",
                "• Disconnect reaction time: Total arc extinction achieved in < 1.8 ms at 1000 V / 10 kA short circuit.",
                "---",
                "## 2. Environmental Storage & Temperature Limits",
                "• Operating Temperature: -40.0°C to +105.0°C.",
                "• Hermetic glass-to-metal seal meets helium leakage rate < 1e-8 mbar L/s."
            ]
        ]
    )

    # 06_Traction_Inverter_IGBT_Module_Datasheet.pdf
    _create_pdf(
        docs_dir / "06_Traction_Inverter_IGBT_Module_Datasheet.pdf",
        "PWR-MOD-1200 Dual IGBT & DC-DC Power Stage Datasheet",
        "Power Semiconductor Electrical Characteristics",
        [
            [
                "## 1. DC-DC Converter Secondary Power Stage",
                "• Low-Voltage Output Current Rating:",
                "• Maximum continuous rated LV current output is 180.0 A at 65°C ambient due to inductor thermal saturation limit.",
                "• Peak pulse output current: 220 A for <= 5 seconds.",
                "---",
                "## 2. Main Inverter IGBT Half-Bridge Ratings",
                "• Collector-Emitter Breakdown Voltage Vces: 1200 V.",
                "• Continuous Collector Current Ic: 600 A @ Tc=75°C.",
                "• Thermal resistance junction to heatsink: Rth_j-s = 0.082 K/W."
            ]
        ]
    )

    # 07_Silicon_Carbide_MOSFET_Datasheet.pdf
    _create_pdf(
        docs_dir / "07_Silicon_Carbide_MOSFET_Datasheet.pdf",
        "SiC-FET-1200 High-Speed Silicon Carbide Power Module Datasheet",
        "Wide Bandgap Power Switch Operational Limits",
        [
            [
                "## 1. Absolute Maximum Thermal and Electrical Limits",
                "• Drain-Source Voltage Vds: 1200 V DC.",
                "• Continuous Drain Current Id: 400 A @ Tc=25°C, 280 A @ Tc=100°C.",
                "• Absolute Maximum Ratings: Maximum allowable junction temperature Tj_max is 150.0°C. Continuous operation above 150°C causes irreversible gate oxide degradation.",
                "---",
                "## 2. Switching Characteristics & Gate Drive",
                "• Recommended gate drive voltage: -4 V / +18 V.",
                "• Total Gate Charge Qg: 340 nC."
            ]
        ]
    )

    # 08_BMS_Functional_Safety_Validation_Report.pdf (Multi-page comprehensive report)
    _create_pdf(
        docs_dir / "08_BMS_Functional_Safety_Validation_Report.pdf",
        "BMS & Inverter Functional Safety Validation Test Report",
        "HIL & Dynamometer Empirical Verification per ISO 26262 ASIL-D",
        [
            [
                "# 1. High-Voltage Battery Protection Tests",
                "• Test Case TC-BMS-001 (Overvoltage): Overvoltage injected at 4.255 V; persistent for 100 ms triggered contactor trip in 14.2 ms. Verdict: PASS.",
                "• Test Case TC-BMS-002 (Pack Voltage Span): Validated continuous pack monitoring from 380.0 V DC to 820.0 V DC across full operational span. Verdict: PASS.",
                "• Test Case TC-BMS-003 (Current Sensor Dynamic Range): Current sensor evaluated at discrete discharge test points of +100.0 A, +200.0 A, and +300.0 A with error <= 0.3%. Negative regen sweep and high current (> 300 A) characterization not yet performed.",
                "• Test Case TC-BMS-004 (Balancing Thermal Limits): Thermal dissipation measurement on balancing circuitry: balancing current clamped to 120.0 mA max to prevent PCB hotspot exceeding 95°C. Does not meet 300 mA target."
            ],
            [
                "# 2. Battery Monitoring Diagnostics & Isolation Records",
                "• Section 4.1: Isolation monitoring circuit layout reviewed. Isolation fault response timing test scheduled for Phase 3 validation.",
                "---",
                "# 3. Traction Inverter Safety Shutdown & Discharge Tests",
                "• Test Case TC-INV-011 (Phase Overcurrent): Short-circuit test with DESAT protection: peak current 680 A detected and all gates disabled in 1100 ns (1.1 µs). Verdict: PASS.",
                "• Test Case TC-INV-012 (Active DC-Link Discharge): Active discharge test: DC-link voltage decayed from 800 V to 42.5 V in 3.4 seconds. Verdict: PASS."
            ],
            [
                "# 4. Inverter Dynamometer Mapping & Sensor Bandwidth",
                "• Test Case TC-INV-013 (Efficiency Profile): Dynamometer efficiency test conducted at 4000 rpm from 100 Nm to 250 Nm showed efficiency of 98.1%. High-speed (10000 rpm) and low-torque (50 Nm) mapping remains pending dynamometer test slot.",
                "• Test Case TC-INV-014 (Resolver Angle Tracking): Resolver tracking error measured at +25°C ambient up to 15000 rpm was ±0.14 degrees. High temperature (+105°C) and max speed (20000 rpm) tests scheduled for next quarter.",
                "• Test Case TC-INV-016 (Current Hall Bandwidth): Phase current sensor frequency response measured -3dB cutoff at 160.0 kHz. Bandwidth is insufficient for high-frequency PWM ripple observation."
            ],
            [
                "# 5. Microcontroller Watchdog & Brownout Reset Tests",
                "• Test Case TC-SAF-071 (Window Watchdog): Watchdog timing verification: SPI challenge-response verified at 15.2 ms interval. Forced late response triggered hardware reset in 18.1 ms. Verdict: PASS.",
                "• Test Case TC-SAF-072 (Core Brownout): Brownout injection test: Vcore reduced to 2.94 V triggered PMIC hard RESET in 2.1 µs. Verdict: PASS.",
                "• Test Case TC-SAF-073 (Lockstep Core Error Injection): Lockstep error injection on Core 0 ALU registers triggered safety alarm in 1 clock cycle. Core 1 memory bus fault injection test pending."
            ],
            [
                "# 6. Safety Cross-Check & Fault Reaction Times",
                "• Test Case TC-SAF-074 (ADC Redundant Cross-Check): Cross-check algorithm verified at room temperature with 2.0% injected delta, fault asserted at 52 ms. Cold temperature (-40°C) cross-check pending.",
                "• Test Case TC-SAF-075 (Safe Torque Off FTTI): Fault injection test: measured total elapsed time from overcurrent trigger to STO state transition was 24.5 ms due to filter debounce delay.",
                "• Test Case TC-SAF-076 (Flash Memory ECC Handling): Memory fault injection: double-bit flash error triggered standard exception handler in 450 ns instead of required 100 ns NMI."
            ],
            [
                "# 7. Functional Safety FMEDA & Cybersecurity TARA Calculations",
                "• Quantitative Safety Metrics: FMEDA quantitative calculation report indicates theoretical SPFM of 99.4% and LFM of 91.2%.",
                "• Cybersecurity Risk Assessment: TARA analysis report section 4 evaluates 42 threat vectors and assigns theoretical CAL ratings."
            ]
        ]
    )

    # 09_Environmental_Thermal_Shock_Report.pdf
    _create_pdf(
        docs_dir / "09_Environmental_Thermal_Shock_Report.pdf",
        "Environmental Thermal Chamber Validation Test Report",
        "Climatic, Thermal Shock, and Temperature Sensor Qualification per ISO 16750-4",
        [
            [
                "# 1. Cell Temperature Sensor Chamber Measurements",
                "• Test Case TC-ENV-003: Cell temperature measurement verified across cold and warm chambers from -20.0°C to +70.0°C with accuracy of ±0.6°C. Full low-temp (-40°C) and high-temp (+85°C) qualification pending chamber availability.",
                "• Sensor calibration profile: NTC thermistor polynomial coefficient verification.",
                "---",
                "# 2. Thermal Shock Fast Transition Endurance Test",
                "• Test Case TC-ENV-094: Thermal shock chamber test completed 250 cycles of 500 total cycles with zero solder joint cracking. Remaining 250 cycles currently in progress in chamber #2.",
                "• Chamber transition time: 22 seconds between -40°C basket and +85°C basket."
            ]
        ]
    )

    # 10_Mechanical_Vibration_Shock_Report.pdf
    _create_pdf(
        docs_dir / "10_Mechanical_Vibration_Shock_Report.pdf",
        "Mechanical Dynamics & Enclosure IP Validation Test Report",
        "Vibration Shaker Endurance and Water Ingress per ISO 16750-3 / IEC 60529",
        [
            [
                "# 1. 3-Axis Random Vibration Shaker Test",
                "• Test Case TC-MECH-093: Random vibration test completed on X-axis (8.0 h) and Y-axis (8.0 h) at 2.50 g RMS with zero mechanical failure. Z-axis vibration run pending shaker maintenance.",
                "• Frequency spectrum: 10 Hz to 2000 Hz broadband random excitation profile.",
                "---",
                "# 2. Enclosure Ingress Protection (IP67) Submersion Test",
                "• Test Case TC-MECH-095: Ingress protection test: housing passed IP6X dust test, but water ingress of 4.2 mL observed during 1m submersion test due to connector gasket leakage. Rated as IP65 only."
            ]
        ]
    )

    # 11_EMC_Radiated_Immunity_Test_Report.pdf
    _create_pdf(
        docs_dir / "11_EMC_Radiated_Immunity_Test_Report.pdf",
        "Electromagnetic Compatibility (EMC) Test Report",
        "Anechoic Chamber Radiated Emissions & Immunity per CISPR 25 Class 4",
        [
            [
                "# 1. Radiated Emissions Measurement Sweep",
                "• Test Case TC-EMC-091: Semi-anechoic chamber radiated emissions sweep 150 kHz–2.5 GHz: all frequency peaks remained >= 6.4 dB below CISPR 25 Class 4 limit. Verdict: PASS.",
                "• Polarization: Horizontal and Vertical antenna orientations evaluated.",
                "• Detector modes: Peak, Quasi-Peak, and Average detector sweeps all verified."
            ]
        ]
    )

    # 12_Electrical_Transient_Overvoltage_Report.pdf
    _create_pdf(
        docs_dir / "12_Electrical_Transient_Overvoltage_Report.pdf",
        "Electrical Transient & Supply Voltage Overvoltage Test Report",
        "Pulse Transients per ISO 7637-2 and Jump Start per ISO 16750-2",
        [
            [
                "# 1. ISO 16750-2 Jump Start Supply Overvoltage",
                "• Test Case TC-PWR-092: Jump start simulation: 26.0 V DC applied for 60.0 seconds; no thermal runaway or parametric drift observed. Verdict: PASS.",
                "---",
                "# 2. ISO 7637-2 Fast Transient Immunity (Pulses 3a / 3b)",
                "• Test Case TC-PWR-096: Electrical transient test: application of Pulse 3a (-150 V) caused microcontroller reset (Class C behavior), violating Class A requirement."
            ]
        ]
    )

    # 13_Thermal_Runaway_Venting_Validation_Report.pdf
    _create_pdf(
        docs_dir / "13_Thermal_Runaway_Venting_Validation_Report.pdf",
        "Thermal Management & Fluid Dynamics Validation Report",
        "Empirical Chiller Testing, PTC Response, and Theoretical Simulations",
        [
            [
                "# 1. Battery Pack Coolant Chiller Closed-Loop Regulation",
                "• Test Case TC-THM-041: Thermal bench test: inlet coolant temperature stabilized at 25.4°C under 5.2 kW steady thermal dissipation. Verdict: PASS.",
                "• Test Case TC-THM-042: Compressor speed sweep from 1000 rpm to 8500 rpm verified; maximum observed speed error was 28 rpm. Verdict: PASS.",
                "---",
                "# 2. Cabin Heat Pump Thermal Capacity & EXV Modulation",
                "• Test Case TC-THM-043: Heat pump tested at 0°C and -5°C ambient delivering 5.1 kW and 4.8 kW respectively. Extreme cold chamber test at -15.0°C pending.",
                "• Test Case TC-THM-044: EXV positioning verified at +25°C ambient across full 500 steps. Cold step validation at -30°C scheduled for next test run."
            ],
            [
                "# 3. PTC Cabin Heater Transient Response",
                "• Test Case TC-THM-046: PTC heater step response test: measured 32.4 seconds to reach 90% power (5.4 kW) due to soft-start inrush current limiting.",
                "---",
                "# 4. Analytical Calculations & Simulation Records",
                "• Venting Acoustic Modeling: Acoustic sensor baseline signal characterized in bench chamber. Sensor response demonstrated under generic ultrasonic pulse, but acoustic signature discrimination in vehicle enclosure cannot be confirmed without pack-level calibration.",
                "• Inverter Current THD Model: MATLAB/Simulink power stage simulation predicts current THD of 2.4% under ideal switching parameters.",
                "• DC-DC Surge Clamp Model: SPICE circuit simulation shows clamping TVS diode limits pulse 2a peak to 16.2 V.",
                "• Grid Surge Varistor Model: Transient surge suppression modeled in LTspice with MOV + gas tube surge arrestor.",
                "• Comfort PMV Calculation: CFD thermal cabin comfort modeling demonstrates PMV of +0.2 under simulated 22°C ambient.",
                "• NVRAM Wear Calculation: NVRAM endurance calculation model estimates 125,000 cycles based on Flash memory manufacturer cell fatigue curve.",
                "• Moisture Permeation Model: Conformal coating moisture permeation model calculates insulation resistance > 100 MΩ under 95% RH."
            ]
        ]
    )

    # 14_CAN_FD_Network_Timing_Test_Report.pdf
    _create_pdf(
        docs_dir / "14_CAN_FD_Network_Timing_Test_Report.pdf",
        "CAN-FD Powertrain Bus Timing & Protocol Test Report",
        "Dual Bitrate Characterization, Bus-Off, and E2E Profile 01 per ISO 11898-1/2",
        [
            [
                "# 1. CAN-FD Physical Layer Dual Bitrate Timing",
                "• Test Case TC-NET-051: Oscilloscope bus timing analysis confirmed 500.0 kbps arbitration and 5.00 Mbps data phase with 75.0% sample point. Verdict: PASS.",
                "• Bus propagation delay measured: 142 ns over 25m topology.",
                "---",
                "# 2. CAN Bus-Off Recovery State Machine",
                "• Test Case TC-NET-052: Fault injection bus-off test: node resumed transmission on CAN bus at 94.6 ms following error frame burst. Verdict: PASS.",
                "---",
                "# 3. AUTOSAR E2E Profile 01 Checksum Verification",
                "• Test Case TC-NET-053: E2E Profile 01 verified on BMS status messages (IDs 0x100 to 0x108). Inverter and DC-DC message IDs (0x109 to 0x110) E2E verification pending."
            ]
        ]
    )

    # 15_Automotive_Ethernet_100Base_T1_Report.pdf
    _create_pdf(
        docs_dir / "15_Automotive_Ethernet_100Base_T1_Report.pdf",
        "Automotive 100Base-T1 Physical Layer & SOME/IP Test Report",
        "Signal Quality, BER, and Network Traffic Latency per IEEE 802.3bw",
        [
            [
                "# 1. 100Base-T1 Physical Layer Bit Error Rate",
                "• Test Case TC-ETH-054: Ethernet BER tested over 5 meters cable length showed zero bit errors (BER < 10^-11). Full 15-meter harness length test pending harness fabrication.",
                "---",
                "# 2. SOME/IP Transport Protocol End-to-End Latency",
                "• Test Case TC-ETH-056: Ethernet traffic analysis under 70% network load: measured SOME/IP packet latency was 2.8 ms due to queue buffer contention.",
                "---",
                "# 3. gPTP Clock Synchronization Simulation",
                "• Clock Drift Prediction: Network simulation in OMNeT++ predicts clock drift synchronization within 420 ns."
            ]
        ]
    )

    # 16_UDS_Diagnostics_Security_Access_Report.pdf
    _create_pdf(
        docs_dir / "16_UDS_Diagnostics_Security_Access_Report.pdf",
        "UDS Diagnostic Protocol & Flash Bootloader Validation Report",
        "ISO 14229 Diagnostic Services (0x10, 0x19, 0x27, 0x36) and OTA Rollback",
        [
            [
                "# 1. Diagnostic Session Control (0x10) Server Response Timing",
                "• Test Case TC-UDS-061: UDS Service 0x10 diagnostic timing test: measured P2 server response time across 100 requests was 18.4 ms max. Verdict: PASS.",
                "---",
                "# 2. DTC Freeze Frame Snapshot Storage",
                "• Test Case TC-UDS-062: DTC snapshot validation test: freeze frame written to NVRAM in 12.8 ms with complete mandatory sensor records. Verdict: PASS.",
                "---",
                "# 3. UDS Security Access (0x27) Seed-Key Authentication",
                "• Test Case TC-UDS-063: Level 0x01 Reprogramming security access validated with ECDSA-256. Level 0x03 Engineering calibration access security test pending implementation.",
                "---",
                "# 4. Flash Bootloader Rollback & Security Findings",
                "• Test Case TC-UDS-064: Corrupted image injected into Bank B; bootloader detected CRC mismatch and initiated rollback in 1.4 seconds. Golden image boot verification scheduled.",
                "• Finding SEC-065: Section 3.2: Diagnostic port configuration revealed Service 0x23 ReadMemoryByAddress is configured with open access with no login or authentication required.",
                "• Test Case TC-UDS-066: Flash throughput benchmark: measured write speed was 38.5 kB/s due to internal flash block erase wait states."
            ]
        ]
    )

    # 17_Cybersecurity_HSM_SecOC_Validation_Report.pdf
    _create_pdf(
        docs_dir / "17_Cybersecurity_HSM_SecOC_Validation_Report.pdf",
        "Vehicle Cybersecurity & Hardware Security Module Test Report",
        "ISO 21434, Secure Boot, SecOC, JTAG Inspection, and TRNG Entropy",
        [
            [
                "# 1. Hardware Security Module Secure Boot Benchmarks",
                "• Test Case TC-SEC-081: Secure boot benchmark: RSA-3072 signature verification completed in 112.4 ms with valid public key. Verdict: PASS.",
                "---",
                "# 2. AUTOSAR SecOC Hardware CMAC Acceleration",
                "• Test Case TC-SEC-082: SecOC hardware acceleration benchmark: measured AES-128 CMAC generation time was 28.5 µs per message. Verdict: PASS.",
                "---",
                "# 3. Keystore Hardware Isolation & IDS Rate Limiter",
                "• Test Case TC-SEC-083: AES symmetric root keys verified inside HSM secure storage. Asymmetric ECC private key migration to secure keystore pending firmware v2.1.",
                "• Test Case TC-SEC-084: IDS rate limiter evaluated on CAN 1 (Powertrain) and CAN 2 (Body); blocked injection in 32.0 ms. CAN 3 (Chassis) and CAN 4 (Infotainment) testing pending."
            ],
            [
                "# 4. Hardware Vulnerability Inspection & TRNG Entropy Benchmarks",
                "• Inspection Finding SEC-085: Hardware inspection: JTAG port pins remain accessible with debugging enabled on production sample lot #3 due to unblown e-fuses.",
                "• Test Case TC-SEC-086: NIST SP 800-22 random number test suite: measured TRNG output rate was 140.0 kbps, failing the 500.0 kbps throughput requirement."
            ]
        ]
    )

    # 18_DC_DC_Converter_Efficiency_Test_Report.pdf
    _create_pdf(
        docs_dir / "18_DC_DC_Converter_Efficiency_Test_Report.pdf",
        "DC-DC Converter Electrical & Efficiency Test Report",
        "Galvanic Isolation Hipot, Step Load Dynamics, and UVLO Ramp",
        [
            [
                "# 1. Step Load Transient Response & Hipot Isolation",
                "• Test Case TC-DCDC-021: Transient step load test: 10 A to 200 A transient caused 13.84 V output, recovered within 320 µs. Verdict: PASS.",
                "• Test Case TC-DCDC-022: Hipot test between HV and LV domains applied 3000 V AC rms for 60 s; measured leakage current was 0.38 mA. Verdict: PASS.",
                "---",
                "# 2. Efficiency Mapping Across Load & Voltage Ripple",
                "• Test Case TC-DCDC-023: Efficiency measured at 1000 W (95.2%) and 2000 W (94.8%). Low power (300 W) and full power (3000 W) efficiency points not yet measured.",
                "• Test Case TC-DCDC-024: Output ripple measured at 25°C ambient was 68.0 mVpp. Thermal chamber ripple measurements at -40°C and +85°C pending.",
                "---",
                "# 3. Undervoltage Lockout (UVLO) Shutdown Trip Point",
                "• Test Case TC-DCDC-026: Input voltage ramp test: DC-DC converter continued switching down to 290.0 V DC before tripping, failing the 350.0 V undervoltage threshold."
            ]
        ]
    )

    # 19_OnBoard_Charger_AC_Validation_Report.pdf
    _create_pdf(
        docs_dir / "19_OnBoard_Charger_AC_Validation_Report.pdf",
        "On-Board Charger (OBC) AC Grid & Thermal Test Report",
        "11 kW Power Factor, Control Pilot PWM, Earth Leakage, and Thermal Cutoff",
        [
            [
                "# 1. AC Grid Charging Power & Power Factor",
                "• Test Case TC-OBC-031: Grid test at 400 V AC 3-phase delivered 11.05 kW with measured power factor of 0.992. Verdict: PASS.",
                "• Test Case TC-OBC-032: Control pilot duty cycle sweep from 10.0% to 90.0% measured with maximum error of ±0.22% duty cycle. Verdict: PASS.",
                "---",
                "# 2. Wide-Range AC Input & V2L Bidirectional Discharge",
                "• Test Case TC-OBC-033: Tested at 230 V AC / 50 Hz and 120 V AC / 60 Hz. Low-line voltage limit (85 V) and frequency margin (47 Hz, 63 Hz) tests pending.",
                "• Test Case TC-OBC-034: V2L evaluated with 1.8 kW resistive heater: output 230.4 V, THD 1.8%. Inductive load testing (3.6 kW motor load) remains pending."
            ],
            [
                "# 3. Touch Leakage Current & Thermal Shutdown Limit",
                "• Test Case TC-OBC-035: Earth leakage current test: measured touch leakage current was 5.2 mA rms due to Y-capacitor filter sizing, exceeding 3.5 mA limit.",
                "• Test Case TC-OBC-036: Inlet temperature override test: controller throttled at 95.0°C and did not terminate charging until 108.0°C."
            ]
        ]
    )


def generate_xlsx_compliance_matrix(target_path: Path):
    """Generate 20_Master_Compliance_Verification_Matrix.xlsx tracking all 100 requirements."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Compliance Matrix"
    
    # Headers
    headers = [
        "Requirement ID", "Title", "Category", "Verification Method",
        "Verification Status", "Observed Value / Evidence Excerpt", "Evidence Document Ref", "Verdict"
    ]
    ws.append(headers)
    
    header_fill = PatternFill(start_color="1B365D", end_color="1B365D", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    
    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
    
    for r in BENCHMARK_REQUIREMENTS:
        req_id = r["requirement_id"]
        title = r["title"]
        cat = r["category"]
        exp_status = r["expected_status"]
        
        # Find ground truth details
        gt = next((g for g in BENCHMARK_GROUND_TRUTH if g["requirement_id"] == req_id), None)
        evidence_ref = gt["expected_evidence"][0]["document"] if gt and gt.get("expected_evidence") else "None"
        quote = gt["expected_evidence"][0]["quote"] if gt and gt.get("expected_evidence") else "Record missing"
        
        status_map = {
            "SUPPORTED": ("COMPLETE", "PASS"),
            "PARTIAL": ("IN PROGRESS", "PARTIAL"),
            "CONFLICT": ("COMPLETE", "FAIL"),
            "MISSING": ("NOT STARTED", "MISSING"),
            "UNKNOWN": ("COMPLETE", "REVIEW REQUIRED"),
        }
        v_status, verdict = status_map.get(exp_status, ("NOT STARTED", "MISSING"))
        
        ws.append([
            req_id, title, cat, "Test / Inspection", v_status, quote, evidence_ref, verdict
        ])
    
    # Adjust column widths
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 25
    ws.column_dimensions["D"].width = 18
    ws.column_dimensions["E"].width = 18
    ws.column_dimensions["F"].width = 75
    ws.column_dimensions["G"].width = 35
    ws.column_dimensions["H"].width = 16

    wb.save(target_path)
    print(f"  [OK] Generated XLSX compliance matrix: {target_path.name}")


def generate_readme(target_path: Path):
    """Generate evaluation/complex_benchmark/README.md."""
    content = f"""# TraceAudit AI - 100-Requirement Complex Benchmark

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
"""
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  [OK] Generated README: {target_path.name}")


def main():
    print("=" * 70)
    print("     GENERATING 100-REQUIREMENT COMPLEX AUTOMOTIVE BENCHMARK")
    print("=" * 70)
    
    docs_dir = BENCHMARK_DIR / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. JSON Data
    generate_json_files()
    
    # 2. Specification DOCX
    generate_docx_srs(docs_dir / "01_System_Requirements_Specification_SRS.docx")
    
    # 3. PDF Technical Reports & Datasheets (18 PDFs)
    generate_all_pdf_documents(docs_dir)
    
    # 4. Compliance Matrix XLSX
    generate_xlsx_compliance_matrix(docs_dir / "20_Master_Compliance_Verification_Matrix.xlsx")
    
    # 5. README
    generate_readme(BENCHMARK_DIR / "README.md")
    
    print("\n" + "=" * 70)
    print(" [COMPLETED] 100-Requirement Benchmark Corpus Created Successfully!")
    print("=" * 70)


if __name__ == "__main__":
    main()
