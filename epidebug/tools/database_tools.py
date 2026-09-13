"""
Mock database and reference tools for EpiDebug sandbox.

These tools simulate scientific databases and reference lookups,
returning deterministic data for reproducible evaluation.
"""

from __future__ import annotations

from typing import Any, Optional


def protein_pka_lookup(
    residue: str = "histidine", context: str = "free", case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Look up pKa values for amino acid residues in different contexts."""
    pka_data = {
        "histidine": {"free_amino_acid": 6.04, "in_protein_typical": {"min": 5.5, "max": 8.0, "avg": 6.5},
                      "notes": "His pKa is highly context-dependent. In His-tags, typically 6.0-6.5. "
                               "At pH < pKa, His is protonated (positively charged). "
                               "At pH > pKa, His is neutral and can coordinate metal ions like Ni2+."},
        "cysteine": {"free_amino_acid": 8.37, "in_protein_typical": {"min": 6.0, "max": 11.0, "avg": 8.5}},
        "aspartate": {"free_amino_acid": 3.90, "in_protein_typical": {"min": 2.0, "max": 7.5, "avg": 3.7}},
        "glutamate": {"free_amino_acid": 4.07, "in_protein_typical": {"min": 2.0, "max": 7.0, "avg": 4.2}},
        "lysine": {"free_amino_acid": 10.54, "in_protein_typical": {"min": 8.0, "max": 12.0, "avg": 10.4}},
        "tyrosine": {"free_amino_acid": 10.46, "in_protein_typical": {"min": 9.0, "max": 13.0, "avg": 10.1}},
        "arginine": {"free_amino_acid": 12.48, "in_protein_typical": {"min": 11.0, "max": 13.5, "avg": 12.3}},
    }
    res = residue.lower()
    if res in pka_data:
        return {"residue": residue, **pka_data[res]}
    return {"residue": residue, "error": f"No pKa data for '{residue}'", "available": list(pka_data.keys())}


def buffer_stability_reference(
    buffer_name: str = "Tris-HCl", case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Return stability and storage recommendations for common lab buffers."""
    data = {
        "tris": {
            "full_name": "Tris(hydroxymethyl)aminomethane hydrochloride",
            "pH_range": "7.0-9.0", "pKa_25C": 8.07,
            "temperature_coefficient": "-0.028 pH units/°C",
            "stability_notes": [
                "Significant temperature dependence — always measure pH at working temperature",
                "Susceptible to CO2 absorption from atmosphere, which lowers pH",
                "Should be stored at 4°C in sealed containers",
                "Working solutions should be used within 1 week",
                "Concentrated stocks (1M) are more stable than dilute solutions",
                "Do NOT autoclave Tris with glucose — Maillard reaction occurs",
            ],
            "co2_vulnerability": "HIGH — Tris has poor buffering capacity against carbonic acid. "
                                 "Extended exposure to atmospheric CO2 can drop pH by 1-2 units.",
            "recommended_storage": "4°C, sealed container, use within 1 week for working solutions",
        },
        "phosphate": {
            "full_name": "Sodium phosphate buffer",
            "pH_range": "5.8-8.0", "pKa_25C": 7.20,
            "temperature_coefficient": "-0.005 pH units/°C",
            "stability_notes": [
                "Very stable over time", "Low temperature dependence",
                "Can precipitate with divalent cations (Ca2+, Mg2+)",
                "Not suitable for use with Ni-NTA (competes for Ni binding)",
            ],
            "co2_vulnerability": "LOW",
            "recommended_storage": "Room temperature OK for weeks, 4°C for months",
        },
        "hepes": {
            "full_name": "4-(2-Hydroxyethyl)piperazine-1-ethanesulfonic acid",
            "pH_range": "6.8-8.2", "pKa_25C": 7.48,
            "temperature_coefficient": "-0.014 pH units/°C",
            "stability_notes": [
                "Good biological buffer — minimal metal binding",
                "Stable over time if protected from light",
                "Can generate free radicals under UV light",
            ],
            "co2_vulnerability": "LOW-MEDIUM",
            "recommended_storage": "4°C, protected from light",
        },
    }
    key = buffer_name.lower().replace("-", "").replace(" ", "").replace("hcl", "")
    if key in data:
        return {"buffer": buffer_name, **data[key]}
    return {"buffer": buffer_name, "error": "Buffer not in reference database", "available": list(data.keys())}


def ni_nta_binding_conditions(case_id: Optional[str] = None) -> dict[str, Any]:
    """Return optimal binding conditions for Ni-NTA affinity chromatography."""
    return {
        "resin": "Ni-NTA (Nickel-Nitrilotriacetic acid)",
        "mechanism": "His-tag (6xHis) coordinates Ni2+ ions via imidazole side chains",
        "optimal_conditions": {
            "pH": {"optimal": "7.5-8.0", "acceptable": "7.0-8.5",
                   "critical_note": "Below pH 6.5, histidine becomes protonated and CANNOT coordinate Ni2+. "
                                    "This is the most common cause of binding failure."},
            "NaCl_mM": {"optimal": "300-500", "note": "Reduces non-specific ionic interactions"},
            "imidazole_mM": {"binding": "10-25", "wash": "20-50", "elution": "250-500",
                             "note": "Low imidazole in binding buffer reduces non-specific binding"},
            "temperature": "4°C preferred to reduce proteolysis",
        },
        "common_failure_modes": [
            "pH too low (< 6.5) — His-tag protonated, cannot bind Ni2+",
            "Protein not soluble — target in inclusion bodies",
            "His-tag buried/inaccessible in folded protein",
            "Resin stripped of Ni2+ by chelating agents (EDTA, DTT > 5mM)",
            "Column overloaded — exceeded binding capacity",
        ],
        "troubleshooting": {
            "no_binding": "Check pH of all buffers. Verify His-tag is accessible. Try native vs denaturing.",
            "low_purity": "Increase wash imidazole. Add detergent. Use tandem purification.",
            "low_yield": "Check expression level. Optimize lysis. Verify tag integrity.",
        },
    }


def reaction_database(
    reaction_type: str = "general", reagents: Optional[list[str]] = None,
    case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Look up known reaction conditions, mechanisms, and failure modes."""
    data = {
        "grignard": {
            "reaction": "Grignard Reaction (RMgX + Electrophile)",
            "requirements": ["Anhydrous solvent (THF or Et2O)", "Inert atmosphere (N2 or Ar)",
                             "Dry glassware", "Activated Mg turnings"],
            "critical_sensitivities": {
                "water": "FATAL — even trace moisture will quench RMgX → RH + Mg(OH)X. "
                         "THF is hygroscopic and must be freshly dried or purchased anhydrous.",
                "oxygen": "Oxidizes Grignard reagent. Use Schlenk line or glovebox.",
                "protic_solvents": "Immediately protonate Grignard. Use only aprotic solvents.",
            },
            "common_failures": [
                "Wet solvent — THF absorbs moisture from air rapidly",
                "Mg not activating — surface oxide layer, try I2 or DIBAL-H activation",
                "Coupling (Wurtz) reaction if alkyl halide added too fast",
            ],
        },
        "suzuki": {
            "reaction": "Suzuki-Miyaura Coupling (Ar-X + Ar-B(OH)2 → Ar-Ar)",
            "requirements": ["Pd catalyst (Pd(PPh3)4 or Pd(dppf)Cl2)", "Base (K2CO3, Cs2CO3)",
                             "Inert atmosphere", "Degassed solvent"],
            "critical_sensitivities": {
                "oxygen": "Oxidizes Pd(0) to Pd(II), deactivating catalyst. "
                          "Even small leaks in Schlenk line can cause failure.",
                "moisture": "Generally tolerant of trace moisture (aqueous base used)",
                "catalyst_loading": "Typically 1-5 mol%. Below 0.5 mol% may be too low.",
            },
            "common_failures": [
                "O2 leak oxidizing Pd catalyst — check Schlenk line integrity",
                "Boronic acid protodeboronation in basic conditions",
                "Homocoupling of boronic acid instead of cross-coupling",
            ],
        },
        "sn2": {
            "reaction": "SN2 Nucleophilic Substitution",
            "requirements": ["Good nucleophile", "Primary or secondary substrate",
                             "Polar aprotic solvent preferred"],
            "critical_sensitivities": {
                "steric_hindrance": "SN2 rate decreases dramatically: methyl > primary > secondary. Tertiary substrates do NOT undergo SN2.",
                "base_strength": "Strong bases (t-BuOK, NaH) at elevated temperatures favor E2 elimination over SN2",
                "solvent": "Polar aprotic solvents (DMF, DMSO) favor SN2. Protic solvents slow SN2.",
                "temperature": "High temperature favors E2 (entropy-driven) over SN2",
            },
            "competing_pathways": {
                "E2": "Favored by: strong bulky base, high temperature, tertiary substrate",
                "SN1": "Favored by: polar protic solvent, tertiary/secondary substrate, weak nucleophile",
                "E1": "Favored by: weak base, polar protic solvent, high temperature",
            },
        },
    }
    key = (reaction_type or "general").lower().replace(" ", "").replace("-", "")
    if key in data:
        return data[key]
    return {"reaction_type": reaction_type, "note": "Not in database", "available": list(data.keys())}


def material_properties(
    material: str = "aluminum", case_id: Optional[str] = None,
) -> dict[str, Any]:
    """Look up mechanical and thermal properties of engineering materials."""
    data = {
        "aluminum_6061_t6": {
            "name": "Aluminum 6061-T6",
            "yield_strength_MPa": 276, "ultimate_tensile_MPa": 310,
            "elastic_modulus_GPa": 68.9, "poissons_ratio": 0.33,
            "elongation_percent": 12, "hardness_brinell": 95,
            "density_kg_m3": 2700, "thermal_conductivity_W_mK": 167,
            "fatigue_strength_MPa": 96.5,
            "notes": [
                "Commonly used structural alloy with good machinability",
                "Yield strength can vary ±10% between batches",
                "Stress concentrations at notches can exceed yield locally even when nominal stress is below yield",
                "Kt (stress concentration factor) for sharp notch can be 3-5x",
            ],
        },
        "steel_304": {
            "name": "Stainless Steel 304",
            "yield_strength_MPa": 215, "ultimate_tensile_MPa": 505,
            "elastic_modulus_GPa": 193, "poissons_ratio": 0.29,
            "elongation_percent": 70, "density_kg_m3": 7900,
        },
    }
    key = material.lower().replace(" ", "_").replace("-", "_")
    for k, v in data.items():
        if key in k or k in key:
            return v
    return {"material": material, "error": "Not found", "available": list(data.keys())}


DATABASE_TOOL_REGISTRY: dict[str, callable] = {
    "protein_pka_lookup": protein_pka_lookup,
    "buffer_stability_reference": buffer_stability_reference,
    "ni_nta_binding_conditions": ni_nta_binding_conditions,
    "reaction_database": reaction_database,
    "material_properties": material_properties,
}
