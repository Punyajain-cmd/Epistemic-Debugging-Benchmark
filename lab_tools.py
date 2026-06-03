"""
Mock lab instrument tools that return realistic pre-recorded data.

Each function simulates a real lab instrument and returns deterministic results.
When case_id is provided, returns case-specific data; otherwise returns generic data.
"""

from __future__ import annotations

import math
from typing import Any, Optional


def ph_calculator(
    solution: str = "Tris-HCl",
    temperature: float = 25.0,
    co2_exposure_hours: float = 0.0,
    concentration_mM: float = 50.0,
    case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Calculate pH of a buffer solution, accounting for temperature and CO2 exposure."""
    buffer_ph = {
        "tris-hcl": 8.0, "tris": 8.0, "phosphate": 7.4, "pbs": 7.4,
        "hepes": 7.5, "mes": 6.1, "mops": 7.2, "bicarbonate": 8.3,
    }
    key = solution.lower().replace(" ", "").replace("-", "").replace("hcl", "")
    base_ph = buffer_ph.get(key, 7.0)
    temp_correction = -0.028 * (temperature - 25.0) if "tris" in solution.lower() else -0.005 * (temperature - 25.0)
    co2_correction = 0.0
    if co2_exposure_hours > 0:
        max_drop = 2.0 if "tris" in solution.lower() else 0.8
        co2_correction = -max_drop * (1 - math.exp(-0.005 * co2_exposure_hours))
    final_ph = round(base_ph + temp_correction + co2_correction, 2)
    return {
        "solution": solution, "concentration_mM": concentration_mM,
        "temperature_C": temperature, "co2_exposure_hours": co2_exposure_hours,
        "initial_ph": base_ph, "temperature_correction": round(temp_correction, 3),
        "co2_correction": round(co2_correction, 3), "calculated_ph": final_ph,
        "warning": "Significant pH drift detected!" if abs(co2_correction) > 0.5 else None,
    }


def spectrophotometer(
    sample: str = "unknown", wavelength: str = "280", case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Read absorbance at a given wavelength. Simulates baseline drift and pedestal artifacts."""
    case_data = {
        "IN-001": {
            "280": {"absorbance": 2.85, "baseline_absorbance": 0.45,
                    "warning": "Very high — verify blank. Pedestal may have residue."},
            "260": {"absorbance": 3.12, "baseline_absorbance": 0.52,
                    "warning": "Near saturation. 260/280 ratio unreliable at high OD."},
        },
    }
    if case_id and case_id in case_data:
        wl = case_data[case_id].get(wavelength, {})
        if wl:
            return {"sample": sample, "wavelength_nm": int(wavelength), **wl}
    return {"sample": sample, "wavelength_nm": int(wavelength), "absorbance": 0.45, "baseline_absorbance": 0.002}


def gel_imager(
    gel_id: str = "default", gel_type: str = "SDS-PAGE", case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Capture and analyze gel image. Returns band positions, intensities, and observations."""
    case_data = {
        "RF-001": {
            "gel_type": "SDS-PAGE 12%", "stain": "Coomassie Blue R-250",
            "lanes": {
                "M": {"label": "Marker", "bands_kDa": [250, 150, 100, 75, 50, 37, 25, 20, 15, 10]},
                "1": {"label": "Total lysate", "bands_kDa": [75, 55, 42, 37, 25],
                      "intensities": ["strong", "strong", "medium", "medium", "weak"]},
                "2": {"label": "Flow-through", "bands_kDa": [75, 55, 42, 37, 25],
                      "intensities": ["strong", "very_strong", "medium", "medium", "weak"],
                      "notes": "Heavy band at 55 kDa — target NOT binding to column"},
                "3": {"label": "Wash", "bands_kDa": [55, 42], "intensities": ["medium", "weak"]},
                "4": {"label": "Elution", "bands_kDa": [70, 55, 42, 30],
                      "intensities": ["weak", "very_weak", "weak", "weak"],
                      "notes": "Very little protein eluted, multiple contaminants"},
            },
            "assessment": "Target (55 kDa) predominantly in flow-through. Low column binding.",
        },
        "RF-002": {
            "gel_type": "Agarose 1.5%", "stain": "SYBR Safe",
            "lanes": {
                "M": {"label": "1 kb ladder", "bands_bp": [10000, 8000, 6000, 5000, 4000, 3000, 2000, 1500, 1000, 500]},
                "1": {"label": "PCR (batch A primers)", "bands_bp": [], "notes": "No bands — reaction failed"},
                "2": {"label": "PCR repeat (batch A)", "bands_bp": [], "notes": "No bands confirmed"},
                "3": {"label": "Positive control (fresh primers)", "bands_bp": [850],
                      "intensities": ["strong"], "notes": "Expected 850 bp, clear"},
                "4": {"label": "No-template control", "bands_bp": [], "notes": "Clean"},
            },
            "assessment": "Batch A primers failed. Fresh primers work. Primer-specific issue.",
        },
    }
    if case_id and case_id in case_data:
        return {"gel_id": gel_id, **case_data[case_id]}
    return {"gel_id": gel_id, "gel_type": gel_type, "lanes": {}, "assessment": "Standard gel."}


def pcr_thermocycler(
    protocol_id: str = "default", case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Retrieve PCR run parameters and machine performance data."""
    case_data = {
        "RF-002": {
            "protocol": {
                "initial_denat": {"temp_C": 95, "duration_s": 180}, "cycles": 35,
                "denat": {"temp_C": 95, "duration_s": 30},
                "anneal": {"temp_C": 58, "duration_s": 30},
                "extend": {"temp_C": 72, "duration_s": 60},
                "final_extend": {"temp_C": 72, "duration_s": 300},
            },
            "machine": "Bio-Rad T100", "lid_temp_C": 105, "status": "completed_normally",
            "notes": "All targets reached. No errors. Machine OK.",
        },
    }
    if case_id and case_id in case_data:
        return {"protocol_id": protocol_id, **case_data[case_id]}
    return {"protocol_id": protocol_id, "status": "completed_normally", "cycles": 30}


def cell_counter(
    sample_id: str = "default", method: str = "trypan_blue", case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Count cells and assess viability via trypan blue or automated counter."""
    case_data = {
        "PL-001": {
            "total_cells_per_mL": 2.1e4, "viable_cells_per_mL": 1.8e4,
            "viability_percent": 85.7, "morphology": "Normal but extremely sparse",
            "expected_cells_per_mL": 5.0e8,
            "note": "Density ~4 orders of magnitude below expected for overnight culture.",
        },
    }
    if case_id and case_id in case_data:
        return {"sample_id": sample_id, "method": method, **case_data[case_id]}
    return {"sample_id": sample_id, "method": method, "total_cells_per_mL": 1.2e6,
            "viability_percent": 91.7, "morphology": "Normal"}


LAB_TOOL_REGISTRY: dict[str, callable] = {
    "ph_calculator": ph_calculator,
    "spectrophotometer": spectrophotometer,
    "gel_imager": gel_imager,
    "pcr_thermocycler": pcr_thermocycler,
    "cell_counter": cell_counter,
}
