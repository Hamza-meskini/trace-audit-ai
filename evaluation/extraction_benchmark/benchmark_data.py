"""Frozen source and evaluator-only ground truth for the extraction benchmark.

The public PDFs contain natural requirement prose and requirement identifiers.
Atomic condition IDs, logic labels, normalized fields, and scoring metadata live
only in this module / generated ground_truth.json.
"""

from __future__ import annotations

from typing import Any


def c(
    description: str,
    parameter: str | None = None,
    operator: str | None = None,
    threshold: Any = None,
    unit: str | None = None,
    *,
    role: str = "VERIFICATION",
    visual: bool = False,
) -> dict[str, Any]:
    return {
        "description": description,
        "parameter": parameter,
        "operator": operator,
        "threshold": threshold,
        "unit": unit,
        "condition_role": role,
        "mandatory": True,
        "requires_visual_evidence": visual,
    }


def r(
    code: str,
    title: str,
    text: str,
    conditions: list[dict[str, Any]],
    *,
    logic: str = "ALL_OF",
) -> dict[str, Any]:
    if not code.startswith("REQ-"):
        code = f"REQ-{code}"
    assert 2 <= len(conditions) <= 5
    enriched = []
    for index, condition in enumerate(conditions, 1):
        enriched.append({"condition_id": f"{code}-C{index}", **condition})
    ids = [item["condition_id"] for item in enriched]
    if logic == "IF_THEN":
        logic_data = {
            "operator": logic,
            "condition_ids": ids,
            "if_condition_id": ids[0],
            "then_condition_ids": ids[1:],
        }
    else:
        logic_data = {"operator": logic, "condition_ids": ids}
    return {
        "requirement_id": code,
        "title": title,
        "requirement_text": text,
        "logic": logic_data,
        "conditions": enriched,
    }


DOCUMENTS: list[dict[str, Any]] = [
    {
        "filename": "01_Nova_HV_Electrical_Requirements.pdf",
        "title": "Nova High-Voltage Electrical Requirements",
        "domain": "Electrical safety",
        "scanned_page": False,
        "secondary_figure": False,
        "requirements": [
            r("NVA-ELE-001", "Operating voltage window", "The traction battery controller shall remain functional from 350 V DC through 820 V DC.", [c("Functional coverage includes 350 V DC", "operating_voltage_lower", "<=", 350, "V"), c("Functional coverage includes 820 V DC", "operating_voltage_upper", ">=", 820, "V")]),
            r("NVA-ELE-002", "Sleep current", "After the wake line has remained low for 120 seconds, controller supply current shall not exceed 5 mA.", [c("Wake line remains low for at least 120 seconds", "sleep_entry_delay", ">=", 120, "s", role="APPLICABILITY"), c("Controller supply current does not exceed 5 mA", "sleep_current", "<=", 5, "mA")], logic="IF_THEN"),
            r("NVA-ELE-003", "Post-impact isolation", "At 60 seconds after an impact event, the measured isolation resistance divided by the measured bus voltage shall be at least 500 ohm/V.", [c("Isolation measurement occurs 60 seconds after impact", "post_impact_time", "==", 60, "s"), c("Isolation resistance per bus voltage is at least 500 ohm/V", "isolation_ratio", ">=", 500, "ohm/V")]),
            r("NVA-ELE-004", "Contactor opening", "When a validated crash input is received, the positive and negative main contactors shall each reach the open state within 20 ms.", [c("A validated crash input is received", "crash_input_valid", "==", True, None, role="APPLICABILITY"), c("Both positive and negative contactors reach open state", "main_contactors_open", "==", True), c("Opening completes within 20 ms", "contactor_open_latency", "<=", 20, "ms")], logic="IF_THEN"),
            r("NVA-ELE-005", "Precharge behavior", "During each high-voltage start, the DC link shall reach at least 90 percent of pack voltage within 2.0 s without exceeding pack voltage by more than 10 V.", [c("DC link reaches at least 90 percent of pack voltage", "precharge_ratio", ">=", 90, "%"), c("Required level is reached within 2.0 seconds", "precharge_time", "<=", 2.0, "s"), c("DC-link overshoot above pack voltage does not exceed 10 V", "precharge_overshoot", "<=", 10, "V")]),
            r("NVA-ELE-006", "Emergency discharge", "Following an emergency disconnect, the exposed DC bus shall fall below 60 V within 5.0 s and the red high-voltage warning indicator shall illuminate.", [c("Exposed DC bus falls below 60 V", "discharge_voltage", "<", 60, "V", visual=True), c("Voltage limit is achieved within 5.0 seconds", "discharge_time", "<=", 5.0, "s", visual=True), c("Red high-voltage warning indicator illuminates", "hv_warning_red", "==", True, None, visual=True)])
        ],
    },
    {
        "filename": "02_Nova_Thermal_Control_Requirements.pdf",
        "title": "Nova Thermal Control Requirements",
        "domain": "Thermal control",
        "scanned_page": False,
        "secondary_figure": True,
        "requirements": [
            r("NVA-THM-001", "Ambient operating range", "The battery control assembly shall perform all monitoring functions from -30 deg C to +70 deg C ambient.", [c("Monitoring functions operate at -30 deg C", "ambient_temperature_lower", "<=", -30, "deg C"), c("Monitoring functions operate at +70 deg C", "ambient_temperature_upper", ">=", 70, "deg C")]),
            r("NVA-THM-002", "Shutdown band", "The controller shall command thermal shutdown at or above 90 deg C and shall not command thermal shutdown below 85 deg C.", [c("Thermal shutdown is commanded at or above 90 deg C", "shutdown_upper", ">=", 90, "deg C"), c("Thermal shutdown is not commanded below 85 deg C", "shutdown_lower_guard", ">=", 85, "deg C")]),
            r("NVA-THM-003", "Coolant delivery", "While battery heat rejection is 35 kW, coolant flow through the pack shall be at least 8.0 L/min.", [c("Battery heat rejection is 35 kW", "heat_rejection", "==", 35, "kW", role="APPLICABILITY"), c("Pack coolant flow is at least 8.0 L/min", "coolant_flow", ">=", 8.0, "L/min")], logic="IF_THEN"),
            r("NVA-THM-004", "Controlled recovery", "After thermal shutdown, recovery shall be permitted only when temperature has remained below 70 deg C for 60 s, and no latched sensor fault is present.", [c("Temperature is below 70 deg C", "recovery_temperature", "<", 70, "deg C"), c("Temperature remains below the limit for 60 seconds", "recovery_dwell", ">=", 60, "s"), c("No latched temperature-sensor fault is present", "sensor_fault_latched", "==", False)]),
            r("NVA-THM-005", "Cell temperature uniformity", "During a 30-minute 2C discharge, the difference between the hottest and coldest measured cell temperatures shall not exceed 5 deg C.", [c("Discharge rate is 2C", "discharge_rate", "==", 2, "C", role="APPLICABILITY"), c("Discharge duration is at least 30 minutes", "discharge_duration", ">=", 30, "min", role="APPLICABILITY"), c("Maximum cell-temperature difference is 5 deg C or less", "cell_temperature_delta", "<=", 5, "deg C")], logic="IF_THEN"),
            r("NVA-THM-006", "Cooling flow direction", "The installed cooling module shall move air from the blue inlet toward the red outlet, provide at least 420 m3/h, and leave the service clearance unobstructed.", [c("Air moves from the blue inlet toward the red outlet", "airflow_direction", "==", "blue_to_red", None, visual=True), c("Cooling airflow is at least 420 m3/h", "cooling_airflow", ">=", 420, "m3/h", visual=True), c("Service clearance is unobstructed", "service_clearance_open", "==", True, None, visual=True)])
        ],
    },
    {
        "filename": "03_Nova_Network_Diagnostics_Requirements.pdf",
        "title": "Nova Network and Diagnostics Requirements",
        "domain": "Communications",
        "scanned_page": True,
        "secondary_figure": False,
        "requirements": [
            r("NVA-NET-001", "CAN heartbeat", "The controller shall transmit its CAN FD heartbeat at intervals not exceeding 100 ms for a continuous 30-minute operating period.", [c("CAN FD heartbeat interval does not exceed 100 ms", "heartbeat_interval", "<=", 100, "ms"), c("Heartbeat behavior is maintained for at least 30 minutes", "heartbeat_duration", ">=", 30, "min")]),
            r("NVA-NET-002", "Packet loss", "During the 30-minute heartbeat test, no heartbeat frame shall be lost at a bus load of 70 percent.", [c("Heartbeat test duration is 30 minutes", "heartbeat_test_duration", "==", 30, "min", role="APPLICABILITY"), c("CAN bus load is 70 percent", "bus_load", "==", 70, "%", role="APPLICABILITY"), c("Lost heartbeat frame count is zero", "lost_heartbeat_frames", "==", 0, "count")], logic="IF_THEN"),
            r("NVA-NET-003", "Ethernet latency", "For authenticated diagnostic traffic, round-trip latency over 100BASE-T1 shall remain below 12 ms at 95 percent network utilization.", [c("Diagnostic traffic is authenticated", "diagnostic_traffic_authenticated", "==", True, None, role="APPLICABILITY"), c("Transport uses 100BASE-T1", "ethernet_physical_layer", "==", "100BASE-T1", None, role="APPLICABILITY"), c("Network utilization is 95 percent", "network_utilization", "==", 95, "%", role="APPLICABILITY"), c("Diagnostic round-trip latency remains below 12 ms", "diagnostic_round_trip_latency", "<", 12, "ms")], logic="IF_THEN"),
            r("NVA-NET-004", "Diagnostic session timeout", "An inactive extended diagnostic session shall return to the default session after 5 s, clear the temporary unlock state, and record the timeout event.", [c("Extended diagnostic session is inactive", "extended_session_inactive", "==", True, None, role="APPLICABILITY"), c("Session returns to default after 5 seconds", "session_timeout", "==", 5, "s"), c("Temporary unlock state is cleared", "temporary_unlock_cleared", "==", True), c("Timeout event is recorded", "timeout_event_logged", "==", True)], logic="IF_THEN"),
            r("NVA-NET-005", "Bus-off recovery", "Following bus-off detection, the node shall inhibit transmission for at least 1.0 s, recover without an ignition cycle, and increment the persistent bus-off counter.", [c("Bus-off is detected", "bus_off_detected", "==", True, None, role="APPLICABILITY"), c("Transmission is inhibited for at least 1.0 second", "bus_off_inhibit", ">=", 1.0, "s"), c("Node recovers without an ignition cycle", "ignition_cycle_required", "==", False), c("Persistent bus-off counter is incremented", "bus_off_counter_incremented", "==", True)], logic="IF_THEN"),
            r("NVA-NET-006", "Gateway routing", "Messages entering port A with identifier 0x18FF50E5 shall leave port C within 4 ms, retain their payload, and carry the updated alive counter shown in the routing diagram.", [c("A message with identifier 0x18FF50E5 enters port A", "gateway_input", "==", "port_A_0x18FF50E5", None, role="APPLICABILITY", visual=True), c("Message leaves through port C", "gateway_output_port", "==", "C", None, visual=True), c("Routing latency does not exceed 4 ms", "gateway_latency", "<=", 4, "ms", visual=True), c("Message payload is retained", "payload_retained", "==", True, None, visual=True), c("Alive counter is updated", "alive_counter_updated", "==", True, None, visual=True)], logic="IF_THEN")
        ],
    },
    {
        "filename": "04_Nova_Environmental_Mechanical_Requirements.pdf",
        "title": "Nova Environmental and Mechanical Requirements",
        "domain": "Environmental qualification",
        "scanned_page": False,
        "secondary_figure": False,
        "requirements": [
            r("NVA-ENV-001", "Ingress protection", "Connector J17 shall remain IP67 after immersion in 1 m of water for 30 minutes.", [c("Connector J17 is immersed to a depth of 1 m", "immersion_depth", "==", 1, "m", role="APPLICABILITY"), c("Immersion duration is 30 minutes", "immersion_duration", "==", 30, "min", role="APPLICABILITY"), c("Connector J17 retains IP67 protection", "ingress_rating", "==", "IP67")], logic="IF_THEN"),
            r("NVA-ENV-002", "Vibration endurance", "The enclosure shall withstand 30 g RMS random vibration for 8 hours per axis without loss of electrical continuity.", [c("Random vibration level is 30 g RMS", "random_vibration_level", "==", 30, "g RMS", role="APPLICABILITY"), c("Exposure lasts 8 hours per axis", "vibration_duration_per_axis", "==", 8, "h", role="APPLICABILITY"), c("Electrical continuity is not lost", "electrical_continuity_loss", "==", False)], logic="IF_THEN"),
            r("NVA-ENV-003", "Mechanical shock", "Following three 50 g half-sine shocks on each axis, no fastener shall loosen and the enclosure shall show no crack longer than 2 mm.", [c("Three 50 g half-sine shocks are applied on each axis", "mechanical_shock", "==", "3x_50_g_each_axis", None, role="APPLICABILITY"), c("No fastener loosens", "loose_fasteners", "==", 0, "count"), c("No enclosure crack is longer than 2 mm", "maximum_crack_length", "<=", 2, "mm")], logic="IF_THEN"),
            r("NVA-ENV-004", "Salt-mist resistance", "After 96 hours of salt-mist exposure, connector contact resistance shall remain below 15 mOhm, sealing surfaces shall show no red corrosion, and labels shall remain legible.", [c("Salt-mist exposure lasts 96 hours", "salt_mist_duration", "==", 96, "h", role="APPLICABILITY"), c("Contact resistance remains below 15 mOhm", "contact_resistance", "<", 15, "mOhm"), c("Sealing surfaces show no red corrosion", "red_corrosion_present", "==", False), c("Labels remain legible", "labels_legible", "==", True)], logic="IF_THEN"),
            r("NVA-ENV-005", "Mounting integrity", "Each of the four mounting bolts shall be tightened to 22 Nm, marked with torque witness paint, and retain at least 18 Nm after vibration.", [c("All four mounting bolts are included", "mounting_bolt_count", "==", 4, "count"), c("Initial mounting-bolt torque is 22 Nm", "initial_bolt_torque", "==", 22, "Nm"), c("Every mounting bolt has torque witness paint", "witness_marks_present", "==", True), c("Post-vibration residual torque is at least 18 Nm", "residual_bolt_torque", ">=", 18, "Nm")]),
            r("NVA-ENV-006", "Drain orientation", "With the enclosure mounted in vehicle orientation, the drain slot shall face downward, remain outside the gasket boundary, and provide an unobstructed opening of at least 18 mm2.", [c("Enclosure is mounted in vehicle orientation", "vehicle_mounting_orientation", "==", True, None, role="APPLICABILITY", visual=True), c("Drain slot faces downward", "drain_orientation", "==", "downward", None, visual=True), c("Drain slot remains outside the gasket boundary", "drain_outside_gasket", "==", True, None, visual=True), c("Unobstructed drain opening is at least 18 mm2", "drain_open_area", ">=", 18, "mm2", visual=True)], logic="IF_THEN")
        ],
    },
    {
        "filename": "05_Nova_Functional_Safety_Requirements.pdf",
        "title": "Nova Functional Safety Requirements",
        "domain": "Functional safety",
        "scanned_page": False,
        "secondary_figure": False,
        "requirements": [
            r("NVA-FSA-001", "Watchdog reaction", "If the control task misses two consecutive execution windows, the independent watchdog shall reset the controller within 50 ms.", [c("Control task misses two consecutive execution windows", "missed_execution_windows", "==", 2, "count", role="APPLICABILITY"), c("Independent watchdog resets the controller within 50 ms", "watchdog_reset_latency", "<=", 50, "ms")], logic="IF_THEN"),
            r("NVA-FSA-002", "Sensor plausibility", "A disagreement greater than 8 deg C between redundant pack temperature channels shall set a diagnostic fault within 200 ms.", [c("Redundant temperature-channel disagreement exceeds 8 deg C", "temperature_disagreement", ">", 8, "deg C", role="APPLICABILITY"), c("Diagnostic fault is set within 200 ms", "plausibility_fault_latency", "<=", 200, "ms")], logic="IF_THEN"),
            r("NVA-FSA-003", "Torque inhibition", "While a charge connector is latched, commanded propulsion torque shall remain zero and vehicle displacement shall not exceed 150 mm.", [c("Charge connector is latched", "charge_connector_latched", "==", True, None, role="APPLICABILITY"), c("Commanded propulsion torque remains zero", "propulsion_torque", "==", 0, "Nm"), c("Vehicle displacement does not exceed 150 mm", "vehicle_displacement", "<=", 150, "mm")], logic="IF_THEN"),
            r("NVA-FSA-004", "Safe-state persistence", "After a crash latch is set, the controller shall hold both contactors open, suppress automatic restart, and preserve the latch across a 12 V power cycle.", [c("Crash latch is set", "crash_latch_set", "==", True, None, role="APPLICABILITY"), c("Both contactors remain open", "contactors_held_open", "==", True), c("Automatic restart is suppressed", "automatic_restart_suppressed", "==", True), c("Crash latch persists across a 12 V power cycle", "crash_latch_persistent", "==", True)], logic="IF_THEN"),
            r("NVA-FSA-005", "Fault logging", "Each ASIL-D safety reaction shall store the fault code, the first-failure timestamp with 10 ms resolution, and 2 seconds of pre-trigger sensor history.", [c("The reaction is an ASIL-D safety reaction", "safety_reaction_asil", "==", "ASIL-D", None, role="APPLICABILITY"), c("Safety reaction stores its fault code", "fault_code_stored", "==", True), c("First-failure timestamp resolution is 10 ms or better", "timestamp_resolution", "<=", 10, "ms"), c("At least 2 seconds of pre-trigger sensor history is stored", "pretrigger_history", ">=", 2, "s")], logic="IF_THEN"),
            r("NVA-FSA-006", "Redundant shutdown path", "Either the primary safety MCU or the independent hardware comparator shall de-energize the contactor coils within 25 ms, as shown by the alternative paths in the safety diagram.", [c("Primary safety MCU de-energizes the coils within 25 ms", "primary_shutdown_latency", "<=", 25, "ms", visual=True), c("Independent comparator de-energizes the coils within 25 ms", "backup_shutdown_latency", "<=", 25, "ms", visual=True)], logic="ANY_OF")
        ],
    },
    {
        "filename": "06_Nova_Cybersecurity_Requirements.pdf",
        "title": "Nova Cybersecurity Requirements",
        "domain": "Cybersecurity",
        "scanned_page": True,
        "secondary_figure": False,
        "requirements": [
            r("NVA-CYS-001", "Secure boot", "At every startup, the controller shall verify the application signature before executing application code.", [c("Signature verification occurs at every startup", "signature_checked_each_start", "==", True), c("Application code executes only after successful verification", "execution_after_verification", "==", True)]),
            r("NVA-CYS-002", "Authentication lockout", "After five consecutive invalid authentication attempts, diagnostic security access shall be locked for 15 minutes.", [c("Five consecutive invalid authentication attempts occur", "invalid_auth_attempts", "==", 5, "count", role="APPLICABILITY"), c("Security access is locked for at least 15 minutes", "security_lockout", ">=", 15, "min")], logic="IF_THEN"),
            r("NVA-CYS-003", "Update freshness", "The bootloader shall reject a correctly signed software image when its security version is lower than the installed version.", [c("Candidate image has a valid signature but a lower security version", "rollback_candidate", "==", True, None, role="APPLICABILITY"), c("Bootloader rejects the rollback image", "rollback_rejected", "==", True)], logic="IF_THEN"),
            r("NVA-CYS-004", "SecOC verification", "For protected CAN messages, the receiver shall verify freshness and message authentication before releasing the payload, and shall increment the intrusion counter after a failed check.", [c("The CAN message is protected", "protected_can_message", "==", True, None, role="APPLICABILITY"), c("Freshness value is verified", "freshness_verified", "==", True), c("Message authentication is verified before payload release", "mac_verified_before_release", "==", True), c("Failed verification increments the intrusion counter", "intrusion_counter_incremented", "==", True)], logic="IF_THEN"),
            r("NVA-CYS-005", "Key handling", "Production private keys shall be generated inside the hardware security module, shall never be exported in plaintext, and shall be erased when ownership transfer is authorized.", [c("Keys are production private keys", "production_private_key", "==", True, None, role="APPLICABILITY"), c("Production private keys are generated inside the HSM", "keys_generated_in_hsm", "==", True), c("Private keys are never exported in plaintext", "plaintext_key_export", "==", False), c("Keys are erased after authorized ownership transfer", "keys_erased_on_transfer", "==", True)], logic="IF_THEN"),
            r("NVA-CYS-006", "Debug-port state", "In production mode the external debug connector shall be physically unpopulated, the debug fuse shall read disabled, and the secure-state indicator shall appear green as shown in the board view.", [c("Controller is in production mode", "production_mode", "==", True, None, role="APPLICABILITY", visual=True), c("External debug connector is physically unpopulated", "debug_connector_populated", "==", False, None, visual=True), c("Debug fuse reports disabled", "debug_fuse_enabled", "==", False, None, visual=True), c("Secure-state indicator appears green", "secure_indicator", "==", "green", None, visual=True)], logic="IF_THEN")
        ],
    },
    {
        "filename": "07_Nova_Charging_Power_Requirements.pdf",
        "title": "Nova Charging and Power Conversion Requirements",
        "domain": "Charging",
        "scanned_page": False,
        "secondary_figure": True,
        "requirements": [
            r("NVA-CHG-001", "AC input range", "The onboard charger shall deliver rated power from 180 V AC through 264 V AC at 45 Hz through 65 Hz.", [c("Rated power is delivered at 180 V AC", "ac_input_voltage_lower", "<=", 180, "V"), c("Rated power is delivered at 264 V AC", "ac_input_voltage_upper", ">=", 264, "V"), c("Rated power is delivered at 45 Hz", "ac_input_frequency_lower", "<=", 45, "Hz"), c("Rated power is delivered at 65 Hz", "ac_input_frequency_upper", ">=", 65, "Hz")]),
            r("NVA-CHG-002", "Conversion efficiency", "At 400 V battery voltage and 11 kW output, charger efficiency shall be at least 94 percent.", [c("Battery voltage is 400 V", "battery_voltage", "==", 400, "V", role="APPLICABILITY"), c("Charger output power is 11 kW", "charger_output_power", "==", 11, "kW", role="APPLICABILITY"), c("Charger efficiency is at least 94 percent", "charger_efficiency", ">=", 94, "%")], logic="IF_THEN"),
            r("NVA-CHG-003", "Power factor", "At loads from 50 percent to rated output, input power factor shall be at least 0.99.", [c("Charger load is at least 50 percent", "charger_load_lower", ">=", 50, "%", role="APPLICABILITY"), c("Charger load does not exceed rated output", "charger_load_upper", "<=", 100, "%", role="APPLICABILITY"), c("Input power factor is at least 0.99", "input_power_factor", ">=", 0.99, None)], logic="IF_THEN"),
            r("NVA-CHG-004", "Connector temperature", "During a 200 A DC charging session, no connector terminal shall exceed 90 deg C, the left-to-right terminal spread shall remain within 8 deg C, and charging shall continue for 20 minutes.", [c("DC charging current is 200 A", "charging_current", "==", 200, "A", role="APPLICABILITY"), c("Maximum connector terminal temperature is 90 deg C or less", "terminal_temperature", "<=", 90, "deg C"), c("Terminal temperature spread is 8 deg C or less", "terminal_temperature_spread", "<=", 8, "deg C"), c("Charging continues for 20 minutes", "charging_duration", ">=", 20, "min")], logic="IF_THEN"),
            r("NVA-CHG-005", "Isolation-fault response", "When charging isolation falls below 100 kohm, the charger shall stop transferring energy within 100 ms, open the input relays, and report diagnostic CHG-ISO-01.", [c("Charging isolation falls below 100 kohm", "charging_isolation", "<", 100, "kohm", role="APPLICABILITY"), c("Energy transfer stops within 100 ms", "isolation_stop_latency", "<=", 100, "ms"), c("Input relays open", "input_relays_open", "==", True), c("Diagnostic CHG-ISO-01 is reported", "isolation_diagnostic", "==", "CHG-ISO-01")], logic="IF_THEN"),
            r("NVA-CHG-006", "Interlock sequence", "The charge port shall close the proximity switch before energizing the pilot, energize the pilot before closing the power contactors, and display each completed step on the sequence diagram.", [c("Proximity switch closes before pilot energization", "proximity_before_pilot", "==", True, None, visual=True), c("Pilot energizes before power contactors close", "pilot_before_contactors", "==", True, None, visual=True), c("Every completed step is displayed", "sequence_steps_displayed", "==", True, None, visual=True)])
        ],
    },
    {
        "filename": "08_Nova_Manufacturing_Quality_Requirements.pdf",
        "title": "Nova Manufacturing and Quality Requirements",
        "domain": "Manufacturing quality",
        "scanned_page": False,
        "secondary_figure": False,
        "requirements": [
            r("NVA-QLT-001", "Instrument calibration", "Every instrument used for acceptance measurements shall show its serial number and a calibration expiry date later than the test date.", [c("Every acceptance instrument is identified by serial number", "instrument_serial_present", "==", True), c("Calibration expiry is later than the test date", "calibration_valid", "==", True)]),
            r("NVA-QLT-002", "Software traceability", "Each released controller shall record a software part number and a build hash that match the approved release manifest.", [c("Released controller records a software part number", "software_part_number_recorded", "==", True), c("Recorded build hash matches the approved manifest", "build_hash_matches_manifest", "==", True)]),
            r("NVA-QLT-003", "Sampling records", "For every production lot, the inspection record shall identify the lot, state the sample size, and name the sampling plan.", [c("Inspection record identifies the production lot", "production_lot_identified", "==", True), c("Inspection record states the sample size", "sample_size_present", "==", True), c("Inspection record names the sampling plan", "sampling_plan_present", "==", True)]),
            r("NVA-QLT-004", "Weld acceptance", "Every inspected busbar weld shall have an effective diameter of at least 4.5 mm, porosity below 8 percent, and no crack visible at 10x magnification.", [c("Every inspected busbar weld is evaluated", "all_inspected_welds_covered", "==", True, None, role="APPLICABILITY"), c("Effective weld diameter is at least 4.5 mm", "weld_diameter", ">=", 4.5, "mm"), c("Weld porosity is below 8 percent", "weld_porosity", "<", 8, "%"), c("No crack is visible at 10x magnification", "weld_crack_visible", "==", False)], logic="IF_THEN"),
            r("NVA-QLT-005", "Label content", "The product label shall include the hardware revision, software revision, and a machine-readable serial number matching the human-readable serial number.", [c("Product label includes hardware revision", "hardware_revision_present", "==", True), c("Product label includes software revision", "software_revision_present", "==", True), c("Product label includes a machine-readable serial number", "machine_serial_present", "==", True), c("Machine-readable and human-readable serial numbers match", "serial_numbers_match", "==", True)]),
            r("NVA-QLT-006", "Sealant inspection", "The blue sealant bead shall form a continuous closed loop, remain between the two red tolerance boundaries, and show no gap wider than 0.5 mm in the inspection image.", [c("Blue sealant bead forms a continuous closed loop", "sealant_loop_closed", "==", True, None, visual=True), c("Sealant remains between both red tolerance boundaries", "sealant_within_boundaries", "==", True, None, visual=True), c("No sealant gap exceeds 0.5 mm", "sealant_gap", "<=", 0.5, "mm", visual=True)])
        ],
    },
]


def materialize_ground_truth() -> dict[str, Any]:
    requirements: list[dict[str, Any]] = []
    document_rows: list[dict[str, Any]] = []
    for document in DOCUMENTS:
        rows = []
        for position, requirement in enumerate(document["requirements"], 1):
            if position <= 3:
                page = 2
                modality = "scanned_text" if document["scanned_page"] else ("two_column_text" if position > 1 else "body_text")
            elif position <= 5:
                page = 3
                modality = "table"
            else:
                page = 4
                modality = "figure"
            item = {
                **requirement,
                "document": document["filename"],
                "source_page": page,
                "source_modality": modality,
                "category": document["domain"],
            }
            requirements.append(item)
            rows.append(requirement["requirement_id"])
        document_rows.append({
            "filename": document["filename"],
            "title": document["title"],
            "doc_type": "System specification",
            "expected_role": "SPECIFICATION",
            "requirement_ids": rows,
            "expected_tables": 2,
            "expected_figures": 1 + int(document["scanned_page"]) + int(document["secondary_figure"]),
        })
    return {
        "benchmark_id": "traceaudit-complex-extraction-v1",
        "version": "1.1.0",
        "purpose": "Measure document ingestion, requirement discovery, and atomic contract extraction before retrieval or verdict reasoning.",
        "leakage_policy": "Atomic IDs, logic labels, normalized fields, and evaluator annotations must never appear in source PDFs.",
        "documents": document_rows,
        "requirements": requirements,
    }
