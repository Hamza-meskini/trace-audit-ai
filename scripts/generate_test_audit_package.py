"""Generate a complete, realistic 4-document test audit package for TraceAudit AI.

Produces:
1. test_audit_package/01_System_Requirements_Specification_BMS.pdf (10 complex requirements)
2. test_audit_package/02_HV_Electrical_and_Safety_Test_Report.pdf (Waveforms, timing, insulation)
3. test_audit_package/03_Thermal_Chamber_and_Environmental_Log.pdf (Telemetry, delta T conflict, range gap)
4. test_audit_package/04_Physical_Inspection_and_Enclosure_Report.pdf (Visual label photo, creepage diagram, IP67)
"""

import os
from pathlib import Path
import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pymupdf as fitz

OUT_DIR = Path(__file__).resolve().parent.parent / "test_audit_package"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Helper Functions for PDF Drawing ─────────────────────────────────────────

def create_base_page(doc, doc_title: str, doc_code: str, page_num: int, total_pages: int):
    """Creates a standard 595x842 (A4) page with headers and footers."""
    page = doc.new_page(width=595, height=842)

    # Header Top Bar
    page.draw_rect(fitz.Rect(40, 25, 555, 60), color=fitz.utils.getColor("gray70"), width=0.5)
    page.draw_rect(fitz.Rect(40, 25, 555, 60), fill=fitz.utils.getColor("gray95"))
    page.insert_text((50, 42), "APEX EV MOBILITY SYSTEMS — ENGINEERING DIVISION", fontsize=9, fontname="helv", color=(0.2, 0.2, 0.2))
    page.insert_text((50, 53), f"{doc_title} | Doc ID: {doc_code} | Rev 2.4", fontsize=8, fontname="helv", color=(0.4, 0.4, 0.4))
    page.insert_text((475, 47), "CONFIDENTIAL", fontsize=8, fontname="helv", color=(0.8, 0.1, 0.1))

    # Footer
    page.draw_line(fitz.Point(40, 805), fitz.Point(555, 805), color=fitz.utils.getColor("gray80"), width=0.5)
    page.insert_text((40, 818), "TraceAudit AI Benchmark Package — ISO 26262 / FMVSS 305 Compliance Evaluation", fontsize=7.5, fontname="helv", color=(0.5, 0.5, 0.5))
    page.insert_text((495, 818), f"Page {page_num} of {total_pages}", fontsize=8, fontname="helv", color=(0.3, 0.3, 0.3))

    return page


def draw_section_heading(page, y: float, title: str):
    page.draw_rect(fitz.Rect(40, y, 555, y + 20), fill=(0.08, 0.25, 0.45))
    page.insert_text((48, y + 14), title, fontsize=10.5, fontname="helv", color=(1, 1, 1))
    return y + 28


def draw_table(page, y: float, headers: list[str], rows: list[list[str]], col_widths: list[float]):
    table_w = sum(col_widths)
    curr_y = y

    # Header Row
    page.draw_rect(fitz.Rect(40, curr_y, 40 + table_w, curr_y + 18), fill=(0.92, 0.94, 0.96), color=(0.7, 0.7, 0.7), width=0.5)
    x = 45
    for i, h in enumerate(headers):
        page.insert_text((x, curr_y + 13), h, fontsize=8.5, fontname="helv", color=(0.1, 0.1, 0.1))
        x += col_widths[i]
    curr_y += 18

    # Data Rows
    for r_idx, row in enumerate(rows):
        bg = (1, 1, 1) if r_idx % 2 == 0 else (0.98, 0.98, 0.99)
        page.draw_rect(fitz.Rect(40, curr_y, 40 + table_w, curr_y + 16), fill=bg, color=(0.85, 0.85, 0.85), width=0.5)
        x = 45
        for c_idx, cell in enumerate(row):
            is_fail = "NON-CONFORMING" in cell or "FAIL" in cell or "7.4" in cell
            col = (0.8, 0.1, 0.1) if is_fail else (0.2, 0.2, 0.2)
            font = "helv"
            page.insert_text((x, curr_y + 12), cell, fontsize=8, fontname=font, color=col)
            x += col_widths[c_idx]
        curr_y += 16

    return curr_y + 8


# ── 1. Document 1: System Requirements Specification (SRS) ───────────────────

def build_srs_document():
    pdf_path = OUT_DIR / "01_System_Requirements_Specification_BMS.pdf"
    doc = fitz.open()

    # ── Page 1: Overview & Section 1 (High-Voltage Safety & Protection) ──
    p1 = create_base_page(doc, "System Requirements Specification (SRS)", "SRS-BMS-2026-X800", 1, 3)

    # Title Block
    p1.insert_text((40, 85), "Modular Battery Management System (BMS-X800)", fontsize=16, fontname="helv", color=(0.08, 0.25, 0.45))
    p1.insert_text((40, 100), "Safety, Environmental, and Multimodal Verification Requirements Matrix", fontsize=10, fontname="helv", color=(0.4, 0.4, 0.4))

    meta_rows = [
        ["Product Category:", "Automotive High-Voltage Energy Storage", "Target Standard:", "ISO 26262:2018 / FMVSS 305"],
        ["System Architecture:", "800V DC Nominal / 12 Cell Modules", "Integrity Level:", "ASIL C / D Safety Critical"],
        ["Author:", "Apex EV Safety & Systems Architecture Team", "Approval Status:", "Released for Verification (Q3)"],
    ]
    draw_table(p1, 115, ["Property", "Specification Value", "Property", "Specification Value"], meta_rows, [100, 160, 95, 160])

    y = draw_section_heading(p1, 195, "Section 1: High-Voltage Safety & Electrical Protection Requirements")

    # REQ-BMS-001
    p1.draw_rect(fitz.Rect(40, y, 555, y + 78), color=(0.7, 0.8, 0.9), fill=(0.97, 0.98, 1.0), width=0.8)
    p1.insert_text((48, y + 16), "REQ-BMS-001: High-Voltage Bus Active Discharge Timing", fontsize=9.5, fontname="helv", color=(0.08, 0.25, 0.45))
    p1.insert_text((430, y + 16), "[Category: Electrical / Safety] [ASIL D]", fontsize=8, fontname="helv", color=(0.4, 0.4, 0.4))
    req1_desc = (
        "Following an emergency shutdown trigger or vehicle collision event signal, the BMS active discharge circuit\n"
        "shall rapidly de-energize the high-voltage DC bus. The DC bus voltage (V_bus) shall drop from its nominal\n"
        "operating potential (400.0 V) to below 60.0 V within a maximum allowable duration of 5.00 seconds,\n"
        "in strict compliance with FMVSS 305 Section S5.4.3.4 and ISO 6469-3."
    )
    for idx, line in enumerate(req1_desc.split("\n")):
        p1.insert_text((48, y + 30 + idx * 11), line, fontsize=8.2, fontname="helv", color=(0.15, 0.15, 0.15))
    y += 86

    # REQ-BMS-002
    p1.draw_rect(fitz.Rect(40, y, 555, y + 78), color=(0.7, 0.8, 0.9), fill=(0.97, 0.98, 1.0), width=0.8)
    p1.insert_text((48, y + 16), "REQ-BMS-002: Galvanic Isolation Resistance Monitoring", fontsize=9.5, fontname="helv", color=(0.08, 0.25, 0.45))
    p1.insert_text((440, y + 16), "[Category: Electrical] [ASIL C]", fontsize=8, fontname="helv", color=(0.4, 0.4, 0.4))
    req2_desc = (
        "Under all nominal DC operating conditions, the galvanic isolation resistance between the isolated high-voltage\n"
        "conductors (both positive rail DC+ and negative rail DC-) and the vehicle chassis electrical ground shall\n"
        "remain at or above 500.0 Ohm/V (total resistance >= 200 kOhm for 400V system). If isolation falls below\n"
        "this threshold, an electrical fault warning shall be raised within 1.0 second."
    )
    for idx, line in enumerate(req2_desc.split("\n")):
        p1.insert_text((48, y + 30 + idx * 11), line, fontsize=8.2, fontname="helv", color=(0.15, 0.15, 0.15))
    y += 86

    # REQ-BMS-003
    p1.draw_rect(fitz.Rect(40, y, 555, y + 78), color=(0.7, 0.8, 0.9), fill=(0.97, 0.98, 1.0), width=0.8)
    p1.insert_text((48, y + 16), "REQ-BMS-003: Over-Voltage Cell Contactor Disconnect Reaction Time", fontsize=9.5, fontname="helv", color=(0.08, 0.25, 0.45))
    p1.insert_text((430, y + 16), "[Category: Electrical / Protection] [ASIL D]", fontsize=8, fontname="helv", color=(0.4, 0.4, 0.4))
    req3_desc = (
        "If any individual cell sensing channel detects a terminal voltage exceeding 4.250 V for longer than 2.0 ms,\n"
        "the BMS hardware safety supervisor shall de-energize the main contactor coil driver. Total disconnection time\n"
        "from fault threshold detection to physical contactor air-gap separation shall not exceed 20.0 ms\n"
        "to prevent irreversible thermal and electrolytic cell degradation."
    )
    for idx, line in enumerate(req3_desc.split("\n")):
        p1.insert_text((48, y + 30 + idx * 11), line, fontsize=8.2, fontname="helv", color=(0.15, 0.15, 0.15))

    # ── Page 2: Section 2 (Thermal Management & Environmental Requirements) ──
    p2 = create_base_page(doc, "System Requirements Specification (SRS)", "SRS-BMS-2026-X800", 2, 3)
    y2 = draw_section_heading(p2, 75, "Section 2: Thermal Management & Environmental Operational Limits")

    # REQ-BMS-004
    p2.draw_rect(fitz.Rect(40, y2, 555, y2 + 82), color=(0.7, 0.8, 0.9), fill=(0.97, 0.98, 1.0), width=0.8)
    p2.insert_text((48, y2 + 16), "REQ-BMS-004: Fast-Charge Cell-to-Cell Temperature Uniformity", fontsize=9.5, fontname="helv", color=(0.08, 0.25, 0.45))
    p2.insert_text((420, y2 + 16), "[Category: Thermal Management] [ASIL B]", fontsize=8, fontname="helv", color=(0.4, 0.4, 0.4))
    req4_desc = (
        "During maximum continuous 3C DC fast-charging (from 10% to 80% State-of-Charge at peak 120 kW input),\n"
        "the active liquid cooling subsystem shall maintain thermal equilibrium across all module cells.\n"
        "The maximum temperature differential (Delta T) between any two cell temperature monitoring sensors\n"
        "throughout the pack shall not exceed 5.0 deg C at any point during the charging cycle."
    )
    for idx, line in enumerate(req4_desc.split("\n")):
        p2.insert_text((48, y2 + 30 + idx * 11), line, fontsize=8.2, fontname="helv", color=(0.15, 0.15, 0.15))
    y2 += 90

    # REQ-BMS-005
    p2.draw_rect(fitz.Rect(40, y2, 555, y2 + 82), color=(0.7, 0.8, 0.9), fill=(0.97, 0.98, 1.0), width=0.8)
    p2.insert_text((48, y2 + 16), "REQ-BMS-005: Extreme Cold Ambient Operational Temperature Range", fontsize=9.5, fontname="helv", color=(0.08, 0.25, 0.45))
    p2.insert_text((435, y2 + 16), "[Category: Environmental] [ASIL B]", fontsize=8, fontname="helv", color=(0.4, 0.4, 0.4))
    req5_desc = (
        "The battery system and control electronics shall maintain continuous operational telemetry, pre-charge\n"
        "capability, and safety monitoring across the full ambient climatic operating range from -40.0 deg C\n"
        "to +85.0 deg C. The system shall successfully execute pre-heating protocols and start communication\n"
        "without hardware fault or watchdog reset at -40.0 deg C cold-soak equilibrium."
    )
    for idx, line in enumerate(req5_desc.split("\n")):
        p2.insert_text((48, y2 + 30 + idx * 11), line, fontsize=8.2, fontname="helv", color=(0.15, 0.15, 0.15))
    y2 += 90

    # REQ-BMS-006
    p2.draw_rect(fitz.Rect(40, y2, 555, y2 + 82), color=(0.7, 0.8, 0.9), fill=(0.97, 0.98, 1.0), width=0.8)
    p2.insert_text((48, y2 + 16), "REQ-BMS-006: Thermal Runaway Warning Broadcast Latency", fontsize=9.5, fontname="helv", color=(0.08, 0.25, 0.45))
    p2.insert_text((430, y2 + 16), "[Category: Thermal / Safety] [ASIL D]", fontsize=8, fontname="helv", color=(0.4, 0.4, 0.4))
    req6_desc = (
        "Upon detection of venting gas (via combustible sensor) or localized temperature gradient exceeding 1.0 deg C/s,\n"
        "the BMS shall transmit an emergency thermal runaway warning frame (CAN-FD ID 0x018) to the vehicle master\n"
        "controller. The total latency from sensor threshold trip to bus frame transmission shall be strictly\n"
        "less than 100 ms to ensure sufficient egress time for vehicle occupants."
    )
    for idx, line in enumerate(req6_desc.split("\n")):
        p2.insert_text((48, y2 + 30 + idx * 11), line, fontsize=8.2, fontname="helv", color=(0.15, 0.15, 0.15))

    # ── Page 3: Section 3 (Physical, Creepage, IP67 & Crash Safety) ──
    p3 = create_base_page(doc, "System Requirements Specification (SRS)", "SRS-BMS-2026-X800", 3, 3)
    y3 = draw_section_heading(p3, 75, "Section 3: Mechanical Enclosure, Visual Safety & Crash Protection")

    # REQ-BMS-007
    p3.draw_rect(fitz.Rect(40, y3, 555, y3 + 78), color=(0.7, 0.8, 0.9), fill=(0.97, 0.98, 1.0), width=0.8)
    p3.insert_text((48, y3 + 16), "REQ-BMS-007: High-Voltage Enclosure Hazard Warning Labeling", fontsize=9.5, fontname="helv", color=(0.08, 0.25, 0.45))
    p3.insert_text((420, y3 + 16), "[Category: Mechanical / Visual] [Visual]", fontsize=8, fontname="helv", color=(0.4, 0.4, 0.4))
    req7_desc = (
        "The exterior surface of the battery top cover shall bear a permanent, weather-resistant ISO 7010-W012\n"
        "high-voltage hazard warning triangle symbol. The warning symbol shall have an equilateral triangle height of\n"
        "at least 40.0 mm and shall be positioned directly adjacent to the Manual Service Disconnect (MSD) port.\n"
        "Compliance shall be verified by physical photographic measurement and dimensional inspection."
    )
    for idx, line in enumerate(req7_desc.split("\n")):
        p3.insert_text((48, y3 + 30 + idx * 11), line, fontsize=8.2, fontname="helv", color=(0.15, 0.15, 0.15))
    y3 += 86

    # REQ-BMS-008
    p3.draw_rect(fitz.Rect(40, y3, 555, y3 + 78), color=(0.7, 0.8, 0.9), fill=(0.97, 0.98, 1.0), width=0.8)
    p3.insert_text((48, y3 + 16), "REQ-BMS-008: High-Voltage Terminal Creepage Distance to Chassis", fontsize=9.5, fontname="helv", color=(0.08, 0.25, 0.45))
    p3.insert_text((410, y3 + 16), "[Category: Physical / Clearance] [Visual]", fontsize=8, fontname="helv", color=(0.4, 0.4, 0.4))
    req8_desc = (
        "The shortest physical creepage path along the surface of insulating barriers between any uninsulated live\n"
        "high-voltage terminal busbar and the grounded metal enclosure housing shall be at least 8.00 mm.\n"
        "Compliance shall be verified through optical CMM cross-sectional dimensional verification under ISO 6469-3\n"
        "Pollution Degree 2 environmental conditions."
    )
    for idx, line in enumerate(req8_desc.split("\n")):
        p3.insert_text((48, y3 + 30 + idx * 11), line, fontsize=8.2, fontname="helv", color=(0.15, 0.15, 0.15))
    y3 += 86

    # REQ-BMS-009
    p3.draw_rect(fitz.Rect(40, y3, 555, y3 + 78), color=(0.7, 0.8, 0.9), fill=(0.97, 0.98, 1.0), width=0.8)
    p3.insert_text((48, y3 + 16), "REQ-BMS-009: Submersion Ingress Protection (IP67)", fontsize=9.5, fontname="helv", color=(0.08, 0.25, 0.45))
    p3.insert_text((410, y3 + 16), "[Category: Ingress Protection] [ASIL C]", fontsize=8, fontname="helv", color=(0.4, 0.4, 0.4))
    req9_desc = (
        "The fully sealed battery enclosure assembly shall satisfy IP67 protection per ISO 20653. When submerged\n"
        "under a hydrostatic water column depth of 1.00 meter for a continuous duration of 30.0 minutes,\n"
        "there shall be exactly 0.0 mL of liquid water ingress into the internal high-voltage chamber,\n"
        "and internal cavity pressure shall remain hermetically stabilized."
    )
    for idx, line in enumerate(req9_desc.split("\n")):
        p3.insert_text((48, y3 + 30 + idx * 11), line, fontsize=8.2, fontname="helv", color=(0.15, 0.15, 0.15))
    y3 += 86

    # REQ-BMS-010
    p3.draw_rect(fitz.Rect(40, y3, 555, y3 + 78), color=(0.7, 0.8, 0.9), fill=(0.97, 0.98, 1.0), width=0.8)
    p3.insert_text((48, y3 + 16), "REQ-BMS-010: Pyrotechnic Disconnect Trigger on Crash Deceleration", fontsize=9.5, fontname="helv", color=(0.08, 0.25, 0.45))
    p3.insert_text((410, y3 + 16), "[Category: Mechanical / Crash] [ASIL D]", fontsize=8, fontname="helv", color=(0.4, 0.4, 0.4))
    req10_desc = (
        "In the event of a severe vehicle collision where deceleration along the longitudinal X-axis exceeds 15.0 g\n"
        "for a duration greater than 10.0 ms, the BMS crash sensing subsystem shall issue a pyrotechnic ignition\n"
        "pulse to detonate the pyrofuse switch within 5.0 ms of acceleration threshold confirmation,\n"
        "physically severing the battery traction power circuit."
    )
    for idx, line in enumerate(req10_desc.split("\n")):
        p3.insert_text((48, y3 + 30 + idx * 11), line, fontsize=8.2, fontname="helv", color=(0.15, 0.15, 0.15))

    doc.save(str(pdf_path))
    doc.close()
    print(f"Generated: {pdf_path}")


# ── 2. Document 2: HV Electrical and Safety Test Report ───────────────────────

def build_hv_electrical_report():
    pdf_path = OUT_DIR / "02_HV_Electrical_and_Safety_Test_Report.pdf"
    doc = fitz.open()

    # ── Page 1: Cover & Equipment Table ──
    p1 = create_base_page(doc, "High-Voltage Electrical & Safety Test Report", "TR-ELEC-2026-441", 1, 4)
    p1.insert_text((40, 85), "High-Voltage Electrical & Discharge Test Report", fontsize=15, fontname="helv", color=(0.08, 0.25, 0.45))
    p1.insert_text((40, 100), "Active Discharge, Isolation Resistance, and Over-Voltage Protection Verification", fontsize=9.5, fontname="helv", color=(0.4, 0.4, 0.4))

    meta = [
        ["Test Facility:", "Apex Power Electronics Lab (A2LA Accredited)", "Test Dates:", "2026-08-12 to 2026-08-15"],
        ["Unit Under Test:", "BMS-X800 Pack Controller Subsystem (SN: 800-042)", "Operating Temp:", "+23.4 deg C (+/- 1.5 deg C)"],
        ["Lead Test Engineer:", "Dr. Markus Vance, PE (Chief HV Engineer)", "Test Standard:", "FMVSS 305 / ISO 6469-3:2021"],
    ]
    draw_table(p1, 115, ["Test Parameter", "Details", "Test Parameter", "Details"], meta, [105, 155, 95, 160])

    y = draw_section_heading(p1, 195, "1.0 Calibrated Test Instrumentation & Equipment Setup")
    equip_rows = [
        ["Yokogawa DL850", "ScopeCoder High-Speed Recorder", "SN-994102", "Calibrated: 2026-03", "PASS"],
        ["Fluke 1555", "10 kV Insulation Resistance MegOhmMeter", "SN-441290", "Calibrated: 2026-01", "PASS"],
        ["Chroma 62150H-1000", "Programmable DC Power Supply (1000V/15kW)", "SN-882194", "Calibrated: 2026-04", "PASS"],
        ["Tektronix P5205A", "High-Voltage Differential Probe (100 MHz)", "SN-112049", "Calibrated: 2026-02", "PASS"],
    ]
    draw_table(p1, y, ["Equipment", "Function Description", "Serial No.", "Calibration", "Status"], equip_rows, [95, 190, 75, 100, 55])

    # ── Page 2: Section 2 (Active Discharge Timing Test with Matplotlib Plot) ──
    p2 = create_base_page(doc, "High-Voltage Electrical & Safety Test Report", "TR-ELEC-2026-441", 2, 4)
    y2 = draw_section_heading(p2, 75, "2.0 High-Voltage Bus Active Discharge Timing (REQ-BMS-001)")

    p2.insert_text((40, y2 + 10), "Test Description: Nominal 400.0 V DC bus energized. Crash/E-Stop signal injected at t = 0.00 s.", fontsize=8.5, fontname="helv")
    p2.insert_text((40, y2 + 22), "Requirement REQ-BMS-001 Criterion: Bus voltage must drop < 60.0 V within 5.00 s of shutdown trigger.", fontsize=8.5, fontname="helv", color=(0.1, 0.4, 0.1))

    # Generate Discharge Matplotlib Plot
    fig, ax = plt.subplots(figsize=(6.2, 3.0), dpi=180)
    t = np.linspace(0, 5.5, 300)
    v = 400.0 * np.exp(-t / 1.51)
    ax.plot(t, v, color="#1f77b4", linewidth=2.2, label="Measured DC Bus Voltage (V_bus)")
    ax.axhline(60.0, color="#d62728", linestyle="--", linewidth=1.5, label="Safe Voltage Threshold: 60.0 V (REQ-BMS-001)")
    ax.axvline(5.0, color="#7f7f7f", linestyle=":", linewidth=1.2, label="Max Allowable Time: 5.00 s")

    # Annotate measured value
    t_meas = 3.42
    v_meas = 400.0 * np.exp(-t_meas / 1.51) # ~41.8 V
    ax.plot([t_meas], [v_meas], marker="o", markersize=7, color="#2ca02c")
    ax.annotate(f"Measured: {v_meas:.1f} V at {t_meas:.2f} s\n(Well below 60.0 V limit)",
                xy=(t_meas, v_meas), xytext=(t_meas + 0.3, v_meas + 75),
                arrowprops=dict(facecolor="#2ca02c", shrink=0.08, width=1.5, headwidth=6),
                fontsize=8.5, fontweight="bold", color="#1a6e1a",
                bbox=dict(boxstyle="round,pad=0.3", fc="#eafaf1", ec="#2ca02c", lw=1))

    ax.set_title("Oscilloscope Capture: High-Voltage DC Bus Active Discharge Curve", fontsize=9.5, fontweight="bold", pad=8)
    ax.set_xlabel("Elapsed Time Post-Shutdown Trigger (seconds)", fontsize=8.5)
    ax.set_ylabel("DC Bus Voltage (Volts)", fontsize=8.5)
    ax.set_xlim(0, 5.5)
    ax.set_ylim(0, 440)
    ax.grid(True, linestyle="--", alpha=0.6)
    ax.legend(loc="upper right", fontsize=7.5)
    plt.tight_layout()

    img_buf = io.BytesIO()
    plt.savefig(img_buf, format="png")
    plt.close(fig)
    img_buf.seek(0)
    p2.insert_image(fitz.Rect(40, y2 + 32, 555, y2 + 250), stream=img_buf.read())

    # Discharge Table
    y2_table = y2 + 260
    p2.insert_text((40, y2_table), "Table 2.1: Active Discharge Timing Measurement Log (5 Consecutive Runs)", fontsize=9, fontname="helv", color=(0.1, 0.1, 0.1))
    dis_rows = [
        ["Discharge Run 1", "400.0 V", "3.42 seconds", "41.8 V", "Pass (< 60.0 V in < 5.0 s)"],
        ["Discharge Run 2", "400.0 V", "3.38 seconds", "42.5 V", "Pass (< 60.0 V in < 5.0 s)"],
        ["Discharge Run 3", "400.0 V", "3.45 seconds", "40.9 V", "Pass (< 60.0 V in < 5.0 s)"],
        ["Discharge Run 4", "400.0 V", "3.40 seconds", "42.1 V", "Pass (< 60.0 V in < 5.0 s)"],
        ["Discharge Run 5", "400.0 V", "3.41 seconds", "41.6 V", "Pass (< 60.0 V in < 5.0 s)"],
    ]
    draw_table(p2, y2_table + 10, ["Test Cycle", "Initial Voltage", "Time to < 60V", "Recorded Voltage", "Evaluation Status"], dis_rows, [100, 95, 100, 105, 115])

    # ── Page 3: Section 3 (Galvanic Isolation Resistance Measurement) ──
    p3 = create_base_page(doc, "High-Voltage Electrical & Safety Test Report", "TR-ELEC-2026-441", 3, 4)
    y3 = draw_section_heading(p3, 75, "3.0 Galvanic Isolation Resistance Verification (REQ-BMS-002)")

    p3.insert_text((40, y3 + 10), "Test Description: Megohmmeter DC test voltage applied across high-voltage conductors to vehicle ground.", fontsize=8.5, fontname="helv")
    p3.insert_text((40, y3 + 22), "Requirement REQ-BMS-002 Criterion: Isolation resistance must be >= 500.0 Ohm/V on nominal 400V bus.", fontsize=8.5, fontname="helv", color=(0.1, 0.4, 0.1))

    iso_rows = [
        ["HV Bus (+) to Chassis Ground", "1,000 V DC", "568 kOhm", "1,420 Ohm/V", ">= 500.0 Ohm/V", "PASS"],
        ["HV Bus (-) to Chassis Ground", "1,000 V DC", "552 kOhm", "1,380 Ohm/V", ">= 500.0 Ohm/V", "PASS"],
        ["Post-Thermal Soak HV(+) to Ground", "1,000 V DC", "512 kOhm", "1,280 Ohm/V", ">= 500.0 Ohm/V", "PASS"],
        ["Post-Thermal Soak HV(-) to Ground", "1,000 V DC", "496 kOhm", "1,240 Ohm/V", ">= 500.0 Ohm/V", "PASS"],
    ]
    y3_iso = draw_table(p3, y3 + 35, ["Measurement Location", "Test Voltage", "Measured Res.", "Normalized Iso.", "Required Limit", "Result"], iso_rows, [140, 75, 75, 85, 85, 55])

    p3.insert_text((40, y3_iso + 15), "Section 3.2 Engineering Conclusion on Galvanic Isolation:", fontsize=9, fontname="helv", color=(0.08, 0.25, 0.45))
    iso_conclusion = (
        "The minimum measured isolation resistance was 1,380 Ohm/V on the negative rail and 1,420 Ohm/V on the\n"
        "positive rail under room temperature ambient conditions, and 1,240 Ohm/V following thermal environmental stress.\n"
        "All values significantly exceed the mandatory 500.0 Ohm/V threshold established in REQ-BMS-002 and FMVSS 305."
    )
    for idx, line in enumerate(iso_conclusion.split("\n")):
        p3.insert_text((40, y3_iso + 28 + idx * 12), line, fontsize=8.2, fontname="helv", color=(0.2, 0.2, 0.2))

    # ── Page 4: Section 4 (Cell Over-Voltage Contactor Reaction Time) ──
    p4 = create_base_page(doc, "High-Voltage Electrical & Safety Test Report", "TR-ELEC-2026-441", 4, 4)
    y4 = draw_section_heading(p4, 75, "4.0 Cell Over-Voltage Contactor Disconnect Timing (REQ-BMS-003)")

    p4.insert_text((40, y4 + 10), "Test Description: Fast step voltage injected at Cell Channel 04 rising from 3.80V to 4.28V (fault trigger > 4.250 V).", fontsize=8.5, fontname="helv")
    p4.insert_text((40, y4 + 22), "Requirement REQ-BMS-003 Criterion: Contactor coil de-energization and air gap open within <= 20.0 ms.", fontsize=8.5, fontname="helv", color=(0.1, 0.4, 0.1))

    # High-Speed Timing Plot
    fig2, ax2 = plt.subplots(figsize=(6.2, 2.8), dpi=180)
    t2 = np.linspace(-5, 25, 400)
    # Cell voltage step
    v_cell = np.where(t2 < 0, 3.80, 4.28)
    # Contactor state (1 = closed, 0 = open with bounce at 14.6ms)
    contactor_state = np.where(t2 < 14.6, 1.0, 0.0)

    ax2.plot(t2, v_cell, color="#9467bd", linewidth=2, label="Cell 04 Voltage (Fault injected at t = 0)")
    ax2.plot(t2, contactor_state * 2 + 2, color="#ff7f0e", linewidth=2.2, label="Main Contactor Status (1=Closed, 0=Open)")
    ax2.axvline(0.0, color="gray", linestyle=":")
    ax2.axvline(14.6, color="#2ca02c", linestyle="--", linewidth=1.5, label="Contactor Disconnected: 14.6 ms")
    ax2.axvline(20.0, color="#d62728", linestyle="--", linewidth=1.5, label="Max Allowable Disconnect: 20.0 ms")

    ax2.annotate("Contactor Open\nat 14.6 ms (< 20.0 ms)", xy=(14.6, 2.0), xytext=(16.0, 2.8),
                 arrowprops=dict(facecolor="#2ca02c", shrink=0.08, width=1.5, headwidth=5),
                 fontsize=8, fontweight="bold", color="#1a6e1a")

    ax2.set_title("Waveform 4.1: High-Speed Contactor Opening Profile Upon Over-Voltage Detection", fontsize=9, fontweight="bold")
    ax2.set_xlabel("Time (milliseconds)", fontsize=8)
    ax2.set_ylabel("Signal Level / Voltage", fontsize=8)
    ax2.set_xlim(-4, 24)
    ax2.set_ylim(1.5, 4.6)
    ax2.grid(True, linestyle="--", alpha=0.5)
    ax2.legend(loc="lower right", fontsize=7.2)
    plt.tight_layout()

    img_buf2 = io.BytesIO()
    plt.savefig(img_buf2, format="png")
    plt.close(fig2)
    img_buf2.seek(0)
    p4.insert_image(fitz.Rect(40, y4 + 32, 555, y4 + 230), stream=img_buf2.read())

    # Contactor Log Table
    y4_tab = y4 + 240
    p4.insert_text((40, y4_tab), "Table 4.1: Contactor Disconnect Timing Summary Across Over-Voltage Injection Tests", fontsize=9, fontname="helv")
    ov_rows = [
        ["Fault Cycle 1", "4.280 V", "2.1 ms", "12.5 ms", "14.6 ms", "PASS (<= 20.0 ms)"],
        ["Fault Cycle 2", "4.295 V", "1.9 ms", "12.8 ms", "14.7 ms", "PASS (<= 20.0 ms)"],
        ["Fault Cycle 3", "4.300 V", "2.0 ms", "12.4 ms", "14.4 ms", "PASS (<= 20.0 ms)"],
        ["Fault Cycle 4", "4.275 V", "2.2 ms", "12.7 ms", "14.9 ms", "PASS (<= 20.0 ms)"],
        ["Fault Cycle 5", "4.285 V", "2.0 ms", "12.5 ms", "14.5 ms", "PASS (<= 20.0 ms)"],
    ]
    draw_table(p4, y4_tab + 10, ["Cycle", "Injected Voltage", "Detection Delay", "Mechanical Opening", "Total Disconnect", "Compliance"], ov_rows, [70, 90, 85, 95, 90, 85])

    doc.save(str(pdf_path))
    doc.close()
    print(f"Generated: {pdf_path}")


# ── 3. Document 3: Thermal Chamber & Environmental Log ───────────────────────

def build_thermal_log_report():
    pdf_path = OUT_DIR / "03_Thermal_Chamber_and_Environmental_Log.pdf"
    doc = fitz.open()

    # ── Page 1: Cover & Chamber Specification ──
    p1 = create_base_page(doc, "Climatic Chamber & Thermal Telemetry Log", "TR-THERM-2026-883", 1, 4)
    p1.insert_text((40, 85), "Thermal Chamber Validation & Telemetry Log", fontsize=15, fontname="helv", color=(0.08, 0.25, 0.45))
    p1.insert_text((40, 100), "Fast-Charging Uniformity, Climatic Range, and Thermal Runaway Alert Verification", fontsize=9.5, fontname="helv", color=(0.4, 0.4, 0.4))

    meta = [
        ["Test Laboratory:", "Weiss Environmental Testing Center (Stuttgart)", "Test Chamber:", "Weiss ClimeEvent C/1000/70/3"],
        ["Coolant Fluid:", "Ethylene Glycol / Water 50:50 Premix", "Flow Rate:", "12.5 Liters/min @ 3.0 bar pressure"],
        ["Battery Pack:", "BMS-X800 Pack SN: 800-042 (800V Architecture)", "Test Protocol:", "ECE-R100 / ISO 26262 Thermal Safety"],
    ]
    draw_table(p1, 115, ["Facility Item", "Specification Parameter", "Facility Item", "Specification Parameter"], meta, [95, 165, 95, 160])

    p1.insert_text((40, 205), "Executive Test Summary & Scope:", fontsize=10, fontname="helv", color=(0.08, 0.25, 0.45))
    exec_text = (
        "This engineering test report documents verification of the thermal management subsystem for the BMS-X800.\n"
        "Testing includes: (1) 3C DC fast-charge cell temperature gradient monitoring, (2) Extreme ambient climatic\n"
        "temperature qualification (-40°C to +85°C), and (3) Gas venting / thermal runaway CAN-FD broadcast latency.\n"
        "Detailed sensor telemetry plots and non-conformance reports are appended below."
    )
    for idx, line in enumerate(exec_text.split("\n")):
        p1.insert_text((40, 222 + idx * 13), line, fontsize=8.5, fontname="helv", color=(0.2, 0.2, 0.2))

    # ── Page 2: Section 1 (Fast-Charge Delta T: INTENTIONAL CONFLICT) ──
    p2 = create_base_page(doc, "Climatic Chamber & Thermal Telemetry Log", "TR-THERM-2026-883", 2, 4)
    y2 = draw_section_heading(p2, 75, "1.0 Fast-Charge Cell-to-Cell Temperature Uniformity (REQ-BMS-004)")

    p2.insert_text((40, y2 + 10), "Test Protocol: 3C DC Fast Charging (120 kW constant current phase from 10% to 80% SoC).", fontsize=8.5, fontname="helv")
    p2.insert_text((40, y2 + 22), "Requirement REQ-BMS-004 Criterion: Maximum cell-to-cell temperature differential Delta T <= 5.0 deg C.", fontsize=8.5, fontname="helv", color=(0.1, 0.4, 0.1))

    # Matplotlib Thermocouple Chart Showing the 7.4 C Conflict
    fig3, ax3 = plt.subplots(figsize=(6.2, 3.0), dpi=180)
    time_min = np.linspace(0, 30, 200)
    # Module 1 (coolest near inlet)
    tc1 = 23.0 + 13.8 * (1 - np.exp(-time_min / 9.0)) # reaches 36.8 C
    # Module 4 (hottest near center/stagnant coolant loop)
    tc4 = 23.0 + 21.2 * (1 - np.exp(-time_min / 8.5)) # reaches 44.2 C
    tc2 = 23.0 + 15.5 * (1 - np.exp(-time_min / 8.8))
    tc3 = 23.0 + 18.0 * (1 - np.exp(-time_min / 8.6))

    ax3.plot(time_min, tc1, color="#1f77b4", linewidth=2.0, label="Module 1 (TC-01 Inlet): 36.8 deg C")
    ax3.plot(time_min, tc2, color="#2ca02c", linestyle="--", linewidth=1.5, label="Module 2 (TC-03): 38.5 deg C")
    ax3.plot(time_min, tc3, color="#ff7f0e", linestyle="--", linewidth=1.5, label="Module 3 (TC-06): 41.0 deg C")
    ax3.plot(time_min, tc4, color="#d62728", linewidth=2.2, label="Module 4 (TC-08 Stagnant Zone): 44.2 deg C")

    # Annotation of Delta T = 7.4 C
    ax3.annotate("NON-CONFORMANCE DETECTED:\nDelta T = 7.4 deg C (Exceeds <= 5.0 deg C limit)",
                 xy=(28, 44.2), xytext=(8, 41.5),
                 arrowprops=dict(facecolor="#d62728", shrink=0.08, width=1.8, headwidth=6),
                 fontsize=8.5, fontweight="bold", color="#a30000",
                 bbox=dict(boxstyle="round,pad=0.3", fc="#fee8e8", ec="#d62728", lw=1.2))

    ax3.set_title("Chart 1.1: Fast-Charge Thermocouple Telemetry (3C Rate / 120 kW)", fontsize=9.5, fontweight="bold")
    ax3.set_xlabel("Elapsed Fast-Charging Time (minutes)", fontsize=8.5)
    ax3.set_ylabel("Cell Temperature (deg C)", fontsize=8.5)
    ax3.set_xlim(0, 30)
    ax3.set_ylim(20, 48)
    ax3.grid(True, linestyle="--", alpha=0.5)
    ax3.legend(loc="lower right", fontsize=7.2)
    plt.tight_layout()

    img_buf3 = io.BytesIO()
    plt.savefig(img_buf3, format="png")
    plt.close(fig3)
    img_buf3.seek(0)
    p2.insert_image(fitz.Rect(40, y2 + 32, 555, y2 + 250), stream=img_buf3.read())

    # Thermocouple Table
    y2_tab = y2 + 260
    p2.insert_text((40, y2_tab), "Table 1.2: End-of-Charge Thermocouple Peak Temperature Matrix (80% SoC)", fontsize=9, fontname="helv", color=(0.1, 0.1, 0.1))
    tc_rows = [
        ["Module 1 (Inlet)", "TC-01", "36.8 deg C", "+13.8 deg C", "Baseline reference (coolest module)"],
        ["Module 2", "TC-03", "38.5 deg C", "+15.5 deg C", "Nominal coolant heat uptake"],
        ["Module 3", "TC-06", "41.0 deg C", "+18.0 deg C", "Acceptable thermal spread"],
        ["Module 4 (Hotspot)", "TC-08", "44.2 deg C", "+21.2 deg C", "NON-CONFORMING: Delta T = 7.4 deg C (Threshold <= 5.0 deg C)"],
    ]
    draw_table(p2, y2_tab + 10, ["Battery Module", "Sensor ID", "Peak Temp", "Rise from 23C", "Compliance Evaluation"], tc_rows, [100, 65, 80, 85, 185])

    # ── Page 3: Section 2 (Extreme Ambient Range: INTENTIONAL PARTIAL GAP) ──
    p3 = create_base_page(doc, "Climatic Chamber & Thermal Telemetry Log", "TR-THERM-2026-883", 3, 4)
    y3 = draw_section_heading(p3, 75, "2.0 Extreme Climatic Temperature Range Validation (REQ-BMS-005)")

    p3.insert_text((40, y3 + 10), "Test Protocol: 48-hour climatic profile soak and functional checkout.", fontsize=8.5, fontname="helv")
    p3.insert_text((40, y3 + 22), "Requirement REQ-BMS-005 Criterion: Full functionality across -40.0 deg C to +85.0 deg C range.", fontsize=8.5, fontname="helv", color=(0.1, 0.4, 0.1))

    env_rows = [
        ["High-Temperature Soak", "+85.0 deg C", "12 hours", "Normal CAN-FD telemetry", "PASS"],
        ["Upper Operating Plateau", "+70.0 deg C", "12 hours", "Pre-charge functional", "PASS"],
        ["Room Ambient Baseline", "+23.0 deg C", "8 hours", "All cell channels calibrated", "PASS"],
        ["Intermediate Cold Dwell", "-20.0 deg C", "12 hours", "Boot successful, monitoring active", "PASS"],
        ["Extreme Sub-Zero Soak", "-40.0 deg C", "0 hours (NOT RUN)", "NOT EVALUATED (Chamber limit)", "TEST GAP / PARTIAL"],
    ]
    y3_tab = draw_table(p3, y3 + 35, ["Environmental Condition", "Chamber Temp", "Dwell Time", "Observed Functional State", "Evaluation"], env_rows, [125, 80, 90, 140, 80])

    p3.insert_text((40, y3_tab + 15), "Section 2.3 Environmental Test Deviation & Traceability Note:", fontsize=9, fontname="helv", color=(0.8, 0.1, 0.1))
    deviation_note = (
        "DEVIATION REPORT DEV-ENV-2026-088:\n"
        "During the environmental test execution, the secondary cryogenic cascade compressor experienced low pressure,\n"
        "limiting minimum chamber air temperature to -20.0 deg C. While continuous operation was successfully demonstrated\n"
        "from -20.0 deg C up to +85.0 deg C, the mandatory extreme cold qualification at -40.0 deg C required by REQ-BMS-005\n"
        "was NOT completed. Evidence for REQ-BMS-005 is therefore PARTIAL and requires additional validation."
    )
    for idx, line in enumerate(deviation_note.split("\n")):
        p3.insert_text((40, y3_tab + 28 + idx * 12), line, fontsize=8.2, fontname="helv", color=(0.3, 0.1, 0.1))

    # ── Page 4: Section 3 (Thermal Runaway Warning Signal Latency) ──
    p4 = create_base_page(doc, "Climatic Chamber & Thermal Telemetry Log", "TR-THERM-2026-883", 4, 4)
    y4 = draw_section_heading(p4, 75, "3.0 Thermal Runaway Warning Broadcast Latency (REQ-BMS-006)")

    p4.insert_text((40, y4 + 10), "Test Protocol: Gas sensor injection trigger (simulated cell vent gas at Module 6).", fontsize=8.5, fontname="helv")
    p4.insert_text((40, y4 + 22), "Requirement REQ-BMS-006 Criterion: CAN-FD warning broadcast latency must be strictly < 100 ms.", fontsize=8.5, fontname="helv", color=(0.1, 0.4, 0.1))

    tr_rows = [
        ["Event Trial 1 (Module 6 Vent)", "Gas Sensor Trip", "CAN-FD ID 0x018", "48 milliseconds", "PASS (< 100 ms)"],
        ["Event Trial 2 (Module 2 Vent)", "Gas Sensor Trip", "CAN-FD ID 0x018", "46 milliseconds", "PASS (< 100 ms)"],
        ["Event Trial 3 (Thermal Spike >1C/s)", "dT/dt Threshold", "CAN-FD ID 0x018", "52 milliseconds", "PASS (< 100 ms)"],
        ["Event Trial 4 (Thermal Spike >1C/s)", "dT/dt Threshold", "CAN-FD ID 0x018", "49 milliseconds", "PASS (< 100 ms)"],
    ]
    y4_tab = draw_table(p4, y4 + 35, ["Test Trial", "Trigger Mechanism", "Transmitted Frame", "Measured Latency", "Evaluation"], tr_rows, [130, 95, 95, 95, 100])

    p4.insert_text((40, y4_tab + 15), "Section 3.1 Runaway Latency Conclusion:", fontsize=9, fontname="helv", color=(0.08, 0.25, 0.45))
    tr_conclusion = (
        "Across all four simulated thermal runaway venting and rapid temperature spike trials, the maximum recorded\n"
        "latency from sensor trigger to vehicle CAN-FD bus frame broadcast was 52 ms, with an average of 48.7 ms.\n"
        "All values satisfy the REQ-BMS-006 maximum latency limit of 100 ms, confirming compliance."
    )
    for idx, line in enumerate(tr_conclusion.split("\n")):
        p4.insert_text((40, y4_tab + 28 + idx * 12), line, fontsize=8.2, fontname="helv", color=(0.2, 0.2, 0.2))

    doc.save(str(pdf_path))
    doc.close()
    print(f"Generated: {pdf_path}")


# ── 4. Document 4: Physical Inspection and Enclosure Report ──────────────────

def build_physical_inspection_report():
    pdf_path = OUT_DIR / "04_Physical_Inspection_and_Enclosure_Report.pdf"
    doc = fitz.open()

    # ── Page 1: Cover & Metrology Overview ──
    p1 = create_base_page(doc, "Physical Inspection & Enclosure Safety Report", "TR-MECH-2026-219", 1, 4)
    p1.insert_text((40, 85), "Physical Inspection & Mechanical Safety Report", fontsize=15, fontname="helv", color=(0.08, 0.25, 0.45))
    p1.insert_text((40, 100), "Visual Warning Labels, Optical Creepage Metrology, and IP67 Enclosure Integrity", fontsize=9.5, fontname="helv", color=(0.4, 0.4, 0.4))

    meta = [
        ["Testing Inspection Body:", "Hexagon Metrology & Safety Certification Lab", "Test Date:", "2026-08-20"],
        ["Inspection Standards:", "ISO 7010 / ISO 6469-3 / ISO 20653 (IP67)", "Measurement Tool:", "Hexagon Optiv Reference CMM"],
        ["Enclosure Assembly:", "BMS-X800 Aluminum Die-Cast Housing (IP67)", "Sample Unit:", "Serial Number: BMS-800-042"],
    ]
    draw_table(p1, 115, ["Inspection Parameter", "Details", "Inspection Parameter", "Details"], meta, [110, 150, 100, 155])

    p1.insert_text((40, 205), "1.0 Scope of Physical & Dimensional Verification:", fontsize=10, fontname="helv", color=(0.08, 0.25, 0.45))
    scope_text = (
        "This physical inspection report evaluates the mechanical and visual compliance of the BMS-X800 enclosure.\n"
        "Three distinct physical safety criteria were evaluated:\n"
        "1. Visual photographic measurement of the high-voltage warning label (ISO 7010-W012).\n"
        "2. Optical coordinate measuring machine (CMM) verification of the HV terminal electrical creepage distance.\n"
        "3. Hydrostatic water submersion tank test to certify IP67 ingress protection."
    )
    for idx, line in enumerate(scope_text.split("\n")):
        p1.insert_text((40, 222 + idx * 13), line, fontsize=8.5, fontname="helv", color=(0.2, 0.2, 0.2))

    # ── Page 2: Section 1 (Visual Warning Label Photograph: REQ-BMS-007) ──
    p2 = create_base_page(doc, "Physical Inspection & Enclosure Safety Report", "TR-MECH-2026-219", 2, 4)
    y2 = draw_section_heading(p2, 75, "2.0 High-Voltage Hazard Warning Labeling Inspection (REQ-BMS-007)")

    p2.insert_text((40, y2 + 10), "Requirement REQ-BMS-007 Criterion: Permanent ISO 7010-W012 symbol height >= 40.0 mm adjacent to MSD port.", fontsize=8.5, fontname="helv", color=(0.1, 0.4, 0.1))

    # Generate Synthetic High-Resolution Warning Label Photograph using Pillow
    img = Image.new("RGB", (700, 320), color=(55, 60, 68)) # Slate gray enclosure background
    draw = ImageDraw.Draw(img)

    # Battery Lid Top Texture & Fasteners
    draw.rectangle([20, 20, 680, 300], outline=(80, 85, 95), width=3)
    for bolt_pos in [(35, 35), (665, 35), (35, 285), (665, 285), (350, 35), (350, 285)]:
        draw.ellipse([bolt_pos[0]-8, bolt_pos[1]-8, bolt_pos[0]+8, bolt_pos[1]+8], fill=(90, 95, 105), outline=(30, 30, 35))

    # Manual Service Disconnect (MSD) Orange Receptacle
    draw.rectangle([80, 70, 260, 250], fill=(235, 95, 20), outline=(180, 60, 10), width=3)
    draw.rectangle([110, 100, 230, 220], fill=(40, 40, 45), outline=(20, 20, 20))
    draw.text((120, 150), "MANUAL SERVICE\nDISCONNECT", fill=(255, 255, 255))

    # ISO 7010-W012 Yellow Warning Triangle
    triangle_center = (420, 160)
    side = 160
    # Equilateral points
    pt1 = (triangle_center[0], triangle_center[1] - 80)
    pt2 = (triangle_center[0] - 80, triangle_center[1] + 65)
    pt3 = (triangle_center[0] + 80, triangle_center[1] + 65)
    draw.polygon([pt1, pt2, pt3], fill=(255, 215, 0), outline=(0, 0, 0), width=5)

    # Inner Lightning Bolt Symbol (Black)
    bolt_pts = [
        (425, 105), (405, 145), (422, 145), (402, 185),
        (438, 148), (420, 148), (435, 110)
    ]
    draw.polygon(bolt_pts, fill=(0, 0, 0))

    # Measurement Caliper / Ruler Overlay Callout
    # Vertical dimension line next to triangle
    ruler_x = 525
    draw.line([(ruler_x, pt1[1]), (ruler_x, pt2[1])], fill=(0, 255, 255), width=2)
    draw.line([(ruler_x - 10, pt1[1]), (ruler_x + 10, pt1[1])], fill=(0, 255, 255), width=2)
    draw.line([(ruler_x - 10, pt2[1]), (ruler_x + 10, pt2[1])], fill=(0, 255, 255), width=2)
    draw.text((ruler_x + 15, 140), "Measured Height:\nh = 45.0 mm\n(Limit: >= 40.0 mm)\nSTATUS: PASS", fill=(0, 255, 255))

    label_buf = io.BytesIO()
    img.save(label_buf, format="PNG")
    label_buf.seek(0)
    p2.insert_image(fitz.Rect(40, y2 + 30, 555, y2 + 230), stream=label_buf.read())

    # Label Table
    y2_tab = y2 + 240
    p2.insert_text((40, y2_tab), "Photograph Fig 1.1: Physical Inspection of Battery Enclosure Warning Label Callout", fontsize=9, fontname="helv", color=(0.1, 0.1, 0.1))
    lbl_rows = [
        ["Warning Symbol Standard", "ISO 7010-W012 (Hazardous Voltage Triangle)", "Conforming (Yellow/Black)", "PASS"],
        ["Measured Symbol Height (h)", "45.0 mm (+/- 0.2 mm calibration)", "Requirement >= 40.0 mm", "PASS (Exceeds by 5.0 mm)"],
        ["Location on Enclosure", "Top lid, directly adjacent to MSD connector", "Within 150 mm of port", "PASS"],
        ["Label Material & Durability", "UV-cured polycarbonate overlay, permanent adhesive", "UL 969 / ISO 6469 compliant", "PASS"],
    ]
    draw_table(p2, y2_tab + 10, ["Inspection Criterion", "Observed Physical Property", "Specification Threshold", "Evaluation Status"], lbl_rows, [125, 165, 125, 100])

    # ── Page 3: Section 2 (Optical Creepage Distance Measurement: REQ-BMS-008) ──
    p3 = create_base_page(doc, "Physical Inspection & Enclosure Safety Report", "TR-MECH-2026-219", 3, 4)
    y3 = draw_section_heading(p3, 75, "3.0 High-Voltage Terminal Creepage Distance Verification (REQ-BMS-008)")

    p3.insert_text((40, y3 + 10), "Requirement REQ-BMS-008 Criterion: Creepage distance across insulating rib to chassis >= 8.00 mm.", fontsize=8.5, fontname="helv", color=(0.1, 0.4, 0.1))

    # Matplotlib Creepage Distance Diagram
    fig4, ax4 = plt.subplots(figsize=(6.2, 2.8), dpi=180)
    # Draw cross section schematic
    # Grounded chassis
    ax4.fill_between([0, 2], [0, 0], [4, 4], color="#708090", label="Grounded Aluminum Chassis Wall")
    # Insulator base
    ax4.fill_between([2, 10], [0, 0], [1.5, 1.5], color="#f4a460", label="PBT-GF30 Insulating Barrier Base")
    # Insulating rib
    ax4.fill_between([5, 6.5], [1.5, 1.5], [4.5, 4.5], color="#cd853f", label="Insulating Surface Rib (Creepage Extender)")
    # HV Terminal Busbar
    ax4.fill_between([9.5, 11], [1.5, 1.5], [5.0, 5.0], color="#b8860b", label="HV DC+ Busbar Terminal (400V Potential)")

    # Creepage path line (dashed red along surface)
    creep_x = [2.0, 5.0, 5.0, 6.5, 6.5, 9.5]
    creep_y = [1.5, 1.5, 4.5, 4.5, 1.5, 1.5]
    ax4.plot(creep_x, creep_y, color="#e41a1c", linewidth=2.5, linestyle=":", label="Creepage Surface Path: d = 8.65 mm")

    ax4.annotate("Measured Surface Creepage Path:\nd = 8.65 mm (Pass >= 8.00 mm)",
                 xy=(5.75, 4.5), xytext=(3.0, 5.5),
                 arrowprops=dict(facecolor="#2ca02c", shrink=0.08, width=1.5, headwidth=5),
                 fontsize=8.5, fontweight="bold", color="#1a6e1a",
                 bbox=dict(boxstyle="round,pad=0.3", fc="#eafaf1", ec="#2ca02c", lw=1))

    ax4.set_title("Diagram 2.1: Optical CMM Metrology Cross-Section of High-Voltage Terminal Barrier", fontsize=9, fontweight="bold")
    ax4.set_xlim(-0.5, 12)
    ax4.set_ylim(-0.5, 6.5)
    ax4.set_aspect("equal")
    ax4.axis("off")
    ax4.legend(loc="lower center", bbox_to_anchor=(0.5, -0.25), ncol=2, fontsize=7.2)
    plt.tight_layout()

    img_buf4 = io.BytesIO()
    plt.savefig(img_buf4, format="png")
    plt.close(fig4)
    img_buf4.seek(0)
    p3.insert_image(fitz.Rect(40, y3 + 25, 555, y3 + 225), stream=img_buf4.read())

    y3_tab = y3 + 235
    p3.insert_text((40, y3_tab), "Table 2.1: Optical CMM Surface Distance Measurements Across 4 Pack Terminals", fontsize=9, fontname="helv")
    creep_rows = [
        ["HV DC+ Terminal to Chassis Wall", "8.65 mm", ">= 8.00 mm", "PASS (+0.65 mm margin)"],
        ["HV DC- Terminal to Chassis Wall", "8.72 mm", ">= 8.00 mm", "PASS (+0.72 mm margin)"],
        ["Auxiliary Pre-Charge to Chassis", "9.10 mm", ">= 8.00 mm", "PASS (+1.10 mm margin)"],
        ["Fast-Charge Contactor Terminal", "8.58 mm", ">= 8.00 mm", "PASS (+0.58 mm margin)"],
    ]
    draw_table(p3, y3_tab + 10, ["Terminal Inspection Location", "Measured Creepage Path", "Required Clearance", "Evaluation Status"], creep_rows, [145, 120, 115, 135])

    # ── Page 4: Section 3 (Submersion IP67 Protection Certificate: REQ-BMS-009) ──
    p4 = create_base_page(doc, "Physical Inspection & Enclosure Safety Report", "TR-MECH-2026-219", 4, 4)
    y4 = draw_section_heading(p4, 75, "4.0 Hydrostatic Tank Submersion Ingress Test — IP67 (REQ-BMS-009)")

    p4.insert_text((40, y4 + 10), "Requirement REQ-BMS-009 Criterion: Zero water ingress after 1.00 m water submersion for 30.0 minutes.", fontsize=8.5, fontname="helv", color=(0.1, 0.4, 0.1))

    cert_rows = [
        ["Test Facility & Tank:", "Hexagon Hydrostatic Environmental Tank #2", "Water Depth:", "1.00 meter (head to top lid)"],
        ["Submersion Duration:", "30.0 minutes continuous soak", "Water Temperature:", "18.5 deg C (+/- 1.0 deg C)"],
        ["Post-Test Seal Inspection:", "0.0 mL water ingress (completely dry)", "Internal Chamber Pressure:", "25.2 kPa differential maintained"],
    ]
    y4_tab1 = draw_table(p4, y4 + 30, ["Test Condition", "Recorded Observation", "Test Condition", "Recorded Observation"], cert_rows, [110, 150, 110, 145])

    p4.draw_rect(fitz.Rect(40, y4_tab1 + 10, 555, y4_tab1 + 90), color=(0.2, 0.6, 0.2), fill=(0.95, 0.99, 0.95), width=1.0)
    p4.insert_text((48, y4_tab1 + 28), "OFFICIAL INGRESS PROTECTION CERTIFICATION (IPX7 / IP67):", fontsize=9.5, fontname="helv", color=(0.1, 0.5, 0.1))
    cert_text = (
        "This certifies that the BMS-X800 Enclosure Unit SN: 800-042 was fully submerged under 1.00 meter\n"
        "of demineralized water for 30.0 minutes in accordance with ISO 20653 Section 8.3.7.\n"
        "Upon completion of test, the enclosure was exterior-dried and opened in an ESD cleanroom.\n"
        "Visual and hygrometer inspection verified 0.0 mL ingress with all internal desiccants and seal gaskets intact.\n"
        "COMPLIANCE EVALUATION: PASS — Meets REQ-BMS-009 requirements in full."
    )
    for idx, line in enumerate(cert_text.split("\n")):
        p4.insert_text((48, y4_tab1 + 42 + idx * 11), line, fontsize=8.0, fontname="helv", color=(0.15, 0.35, 0.15))

    p4.insert_text((40, y4_tab1 + 115), "Section 4.1 Missing Scope & Crash Shock Acceleration Notice:", fontsize=9, fontname="helv", color=(0.8, 0.1, 0.1))
    omission_text = (
        "NOTICE REGARDING REQ-BMS-010 (Crash Acceleration & Pyrotechnic Disconnect):\n"
        "Please note that dynamic longitudinal crash sled testing (accelerations > 15.0 g / 10 ms) and pyrotechnic\n"
        "switch firing verification are NOT covered by this inspection report. Sled testing requires specialized\n"
        "dynamic impact facility certification and is scheduled under a separate test campaign (Document TR-CRASH-2026-X)."
    )
    for idx, line in enumerate(omission_text.split("\n")):
        p4.insert_text((40, y4_tab1 + 128 + idx * 11), line, fontsize=8.2, fontname="helv", color=(0.4, 0.1, 0.1))

    doc.save(str(pdf_path))
    doc.close()
    print(f"Generated: {pdf_path}")


if __name__ == "__main__":
    print("Building Test Audit Package...")
    build_srs_document()
    build_hv_electrical_report()
    build_thermal_log_report()
    build_physical_inspection_report()
    print("All 4 test documents generated successfully in test_audit_package/")
