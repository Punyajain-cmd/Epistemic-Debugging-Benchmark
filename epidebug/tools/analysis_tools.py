"""
Mock analysis and computation tools for EpiDebug sandbox.

These tools simulate data analysis capabilities available to researchers.
"""

from __future__ import annotations

from typing import Any, Optional


def statistical_analyzer(
    data: Optional[list[float]] = None,
    test_type: str = "t_test",
    control: Optional[list[float]] = None,
    case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Run basic statistical tests on experimental data."""
    if data is None:
        data = []
    if not data:
        return {"error": "No data provided", "test_type": test_type}

    n = len(data)
    mean = sum(data) / n if n > 0 else 0
    variance = sum((x - mean) ** 2 for x in data) / max(n - 1, 1)
    std = variance ** 0.5

    result: dict[str, Any] = {
        "test_type": test_type,
        "n": n, "mean": round(mean, 4), "std": round(std, 4),
        "min": round(min(data), 4), "max": round(max(data), 4),
    }

    if control and test_type == "t_test":
        c_mean = sum(control) / len(control)
        c_std = (sum((x - c_mean) ** 2 for x in control) / max(len(control) - 1, 1)) ** 0.5
        # Simplified t-test (Welch's approximation)
        se = ((std**2 / n) + (c_std**2 / len(control))) ** 0.5
        t_stat = (mean - c_mean) / se if se > 0 else 0
        result.update({
            "control_mean": round(c_mean, 4), "control_std": round(c_std, 4),
            "t_statistic": round(t_stat, 3),
            "p_value_approx": round(max(0.001, min(1.0, 2.0 / (1.0 + abs(t_stat) ** 2))), 4),
            "significant": abs(t_stat) > 2.0,
        })

    return result


def spectrum_analyzer(
    spectrum_id: str = "default",
    spectrum_type: str = "NMR",
    case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Analyze NMR or mass spectrometry data. Returns peak positions and assignments."""
    case_data = {
        "RF-004": {
            "NMR": {
                "type": "1H NMR", "solvent": "CDCl3", "frequency_MHz": 400,
                "peaks": [
                    {"ppm": 7.25, "multiplicity": "s", "integration": 1.0, "assignment": "CHCl3 (solvent)"},
                    {"ppm": 3.65, "multiplicity": "t", "integration": 2.0, "assignment": "Unknown — NOT expected product"},
                    {"ppm": 1.55, "multiplicity": "broad s", "integration": "variable", "assignment": "Water peak — indicates wet sample/solvent"},
                ],
                "missing_peaks": [
                    {"expected_ppm": "2.1-2.3", "assignment": "Ketone alpha-CH2 of expected product"},
                    {"expected_ppm": "6.8-7.2", "assignment": "Aromatic protons of expected product"},
                ],
                "assessment": "Product peaks absent. Water peak prominent. Spectrum consistent with "
                              "protonated starting material, not Grignard addition product.",
            },
            "MS": {
                "type": "ESI-MS", "mode": "positive",
                "peaks": [
                    {"mz": 137.06, "relative_intensity": 100, "assignment": "Protonated starting material [M+H]+"},
                    {"mz": 159.04, "relative_intensity": 45, "assignment": "[M+Na]+"},
                ],
                "expected_product_mz": 227.13,
                "assessment": "Expected product mass (m/z 227.13) NOT observed. "
                              "Only starting material detected.",
            },
        },
        "FH-003": {
            "NMR": {
                "type": "1H NMR", "solvent": "CDCl3", "frequency_MHz": 400,
                "peaks": [
                    {"ppm": 5.82, "multiplicity": "ddt", "integration": 1.0, "assignment": "Vinyl CH (alkene product)"},
                    {"ppm": 5.02, "multiplicity": "dd", "integration": 1.0, "assignment": "=CH2 terminal (Z)"},
                    {"ppm": 4.95, "multiplicity": "dd", "integration": 1.0, "assignment": "=CH2 terminal (E)"},
                    {"ppm": 2.05, "multiplicity": "m", "integration": 2.0, "assignment": "Allylic CH2"},
                ],
                "missing_peaks": [
                    {"expected_ppm": "3.3-3.5", "assignment": "C-O CH2 of expected SN2 product"},
                ],
                "assessment": "Spectrum shows ALKENE product (elimination E2), "
                              "NOT the expected ether (SN2 substitution).",
            },
        },
    }
    if case_id and case_id in case_data:
        spec = case_data[case_id].get(spectrum_type, {})
        if spec:
            return {"spectrum_id": spectrum_id, **spec}
    return {"spectrum_id": spectrum_id, "type": spectrum_type, "peaks": [], "assessment": "No case-specific data."}


def calibration_checker(
    instrument: str = "spectrometer",
    last_calibration: str = "unknown",
    case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Check calibration status of a lab instrument."""
    case_data = {
        "IN-001": {
            "instrument": "NanoDrop One",
            "last_calibration": "2024-06-15",
            "calibration_status": "VALID",
            "notes": "Factory calibration within spec. However, pedestal cleanliness is USER responsibility. "
                     "Residue on pedestal causes baseline offset that calibration does not correct.",
            "recommendation": "Clean pedestal with lint-free wipe + DI water before each session. "
                              "Always verify blank reads < 0.04 AU.",
        },
        "IN-005": {
            "instrument": "Tektronix TDS2024",
            "last_calibration": "2024-01-10",
            "calibration_status": "VALID",
            "sample_rate": "100 MS/s maximum",
            "bandwidth": "200 MHz",
            "nyquist_frequency": "50 MHz",
            "notes": "Calibration is valid for amplitude and timebase. However, sample rate determines "
                     "the maximum frequency that can be correctly digitized (Nyquist = Fs/2). "
                     "Signals above Nyquist will alias to a lower frequency.",
        },
        "IN-007": {
            "instrument": "BD FACSCanto II",
            "last_calibration": "2024-11-20",
            "calibration_status": "OVERDUE",
            "recommended_interval": "Daily with CS&T beads",
            "notes": "Last CS&T bead run was 3 days ago. Laser alignment drift can occur between "
                     "calibrations, causing peak broadening and splitting artifacts.",
            "recommendation": "Run CS&T beads before each acquisition session.",
        },
    }
    if case_id and case_id in case_data:
        return case_data[case_id]
    return {"instrument": instrument, "last_calibration": last_calibration,
            "calibration_status": "UNKNOWN", "notes": "No calibration data available."}


def oscilloscope_analyzer(
    signal_frequency_MHz: float = 10.0,
    sample_rate_MS: float = 100.0,
    case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Analyze oscilloscope signal capture for aliasing and sampling issues."""
    nyquist = sample_rate_MS / 2.0
    is_aliased = signal_frequency_MHz > nyquist

    if is_aliased:
        # Calculate aliased frequency
        folded = signal_frequency_MHz
        while folded > nyquist:
            folded = abs(sample_rate_MS - folded)
            if folded > nyquist:
                folded = abs(folded - sample_rate_MS)
        apparent_freq = abs(signal_frequency_MHz - sample_rate_MS)
        if apparent_freq > nyquist:
            apparent_freq = sample_rate_MS - apparent_freq

        return {
            "signal_frequency_MHz": signal_frequency_MHz,
            "sample_rate_MS_s": sample_rate_MS,
            "nyquist_frequency_MHz": nyquist,
            "WARNING": "ALIASING DETECTED",
            "apparent_frequency_MHz": round(apparent_freq, 2),
            "explanation": f"Signal at {signal_frequency_MHz} MHz exceeds Nyquist limit of {nyquist} MHz. "
                           f"The oscilloscope will display an aliased signal at ~{round(apparent_freq, 2)} MHz.",
            "fix": f"Increase sample rate to at least {2.5 * signal_frequency_MHz:.0f} MS/s "
                   f"(2.5× signal frequency for reliable measurement).",
        }
    return {
        "signal_frequency_MHz": signal_frequency_MHz,
        "sample_rate_MS_s": sample_rate_MS,
        "nyquist_frequency_MHz": nyquist,
        "status": "OK — signal below Nyquist limit",
        "samples_per_cycle": round(sample_rate_MS / signal_frequency_MHz, 1),
    }


def trace_log_analyzer(
    service: str = "checkout",
    window_minutes: int = 30,
    case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Inspect distributed traces, host clocks, and service metrics for software failures."""
    if case_id == "IN-009":
        return {
            "service": service,
            "window_minutes": window_minutes,
            "trace_summary": {
                "dashboard_p95_ms": 1840,
                "synthetic_probe_p95_ms": 172,
                "negative_span_fraction": 0.18,
                "spans_with_future_start_timestamps": 0.31,
            },
            "host_clock_offsets_ms": {
                "checkout-canary-1": 3,
                "checkout-canary-2": -5,
                "checkout-canary-3": 1240,
                "payments-1": 2,
                "inventory-1": -4,
            },
            "system_metrics": {
                "cpu_percent": 42,
                "db_p95_ms": 38,
                "error_rate_percent": 0.04,
                "queue_depth": 0,
            },
            "log_findings": [
                "checkout-canary-3 reports 'chronyd inactive' after AMI bake",
                "application monotonic timer logs show local handler duration 118-156 ms",
                "collector received spans out of timestamp order from checkout-canary-3",
            ],
            "assessment": (
                "The apparent latency regression is a telemetry artifact caused by host "
                "clock skew on checkout-canary-3, not a real service slowdown."
            ),
        }
    return {
        "service": service,
        "window_minutes": window_minutes,
        "trace_summary": {},
        "assessment": "No case-specific trace data available.",
    }


def machine_failure_analyzer(
    machine: str = "cnc_mill",
    tool_id: str = "unknown",
    case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Analyze machine-process telemetry such as spindle load, vibration, and tool wear."""
    if case_id == "RF-009":
        return {
            "machine": machine,
            "tool_id": tool_id,
            "spindle_load_percent": [34, 36, 39, 45, 52, 61, 68],
            "vibration_rms_g": [0.18, 0.19, 0.22, 0.29, 0.38, 0.51, 0.62],
            "tool_life": {
                "recorded_cutting_hours": 18.4,
                "recommended_cutting_hours": 8.0,
                "flank_wear_mm": 0.34,
                "replacement_threshold_mm": 0.20,
            },
            "metrology": {
                "slot_width_mm": [6.02, 6.01, 5.99, 5.95, 5.91, 5.88],
                "surface_roughness_ra_um": [0.9, 1.1, 1.6, 2.4, 3.8, 4.9],
            },
            "assessment": (
                "Cutting force and vibration drift track tool age. The end mill is past "
                "tool-life and shows flank wear above the replacement threshold."
            ),
        }
    return {
        "machine": machine,
        "tool_id": tool_id,
        "assessment": "No case-specific machine data available.",
    }


def vision_dataset_auditor(
    dataset_id: str = "concrete_cracks",
    split_strategy: str = "random_patch",
    case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Audit image datasets for leakage, shortcut features, and split validity."""
    if case_id == "FH-009":
        return {
            "dataset_id": dataset_id,
            "split_strategy": split_strategy,
            "random_patch_split": {
                "validation_accuracy": 0.982,
                "validation_f1": 0.978,
                "near_duplicate_val_patches_percent": 41.0,
            },
            "site_holdout_split": {
                "validation_accuracy": 0.614,
                "validation_f1": 0.52,
                "false_positive_rate": 0.38,
            },
            "shortcut_indicators": [
                "Grad-CAM highlights painted grid marks and aggregate texture more than "
                "crack pixels",
                "Adjacent patches from the same wall appear in both train and validation sets",
                "Positive examples are concentrated in two structures photographed under "
                "different lighting",
            ],
            "assessment": (
                "The original validation measured patch/site leakage and background shortcuts, "
                "not robust crack detection."
            ),
        }
    return {
        "dataset_id": dataset_id,
        "split_strategy": split_strategy,
        "assessment": "No case-specific dataset audit available.",
    }


def clinical_sample_checker(
    sample_id: str = "batch",
    analyte: str = "potassium",
    case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Check clinical specimen quality indicators and common pre-analytical artifacts."""
    if case_id == "RF-010":
        return {
            "sample_id": sample_id,
            "analyte": analyte,
            "specimen_quality": {
                "hemolysis_index": [18, 22, 91, 105, 118, 96],
                "acceptable_hemolysis_index": "<20 for potassium interpretation",
                "centrifugation_delay_minutes": [24, 28, 72, 81, 88, 76],
                "transport": "pneumatic_tube for affected samples",
            },
            "paired_recollection": {
                "affected_sample_potassium_mmol_L": [6.2, 6.5, 6.8],
                "gentle_recollection_potassium_mmol_L": [4.2, 4.1, 4.3],
                "ecg_changes": "none",
            },
            "interference_notes": [
                "RBC lysis releases intracellular potassium into plasma",
                "High LDH and AST track the hemolyzed specimens",
                "Repeat non-hemolyzed draws normalize without clinical intervention",
            ],
            "assessment": (
                "The elevated potassium values are most consistent with hemolyzed samples "
                "causing pseudohyperkalemia."
            ),
        }
    return {
        "sample_id": sample_id,
        "analyte": analyte,
        "assessment": "No case-specific sample-quality data available.",
    }


ANALYSIS_TOOL_REGISTRY: dict[str, callable] = {
    "statistical_analyzer": statistical_analyzer,
    "spectrum_analyzer": spectrum_analyzer,
    "calibration_checker": calibration_checker,
    "oscilloscope_analyzer": oscilloscope_analyzer,
    "trace_log_analyzer": trace_log_analyzer,
    "machine_failure_analyzer": machine_failure_analyzer,
    "vision_dataset_auditor": vision_dataset_auditor,
    "clinical_sample_checker": clinical_sample_checker,
}
