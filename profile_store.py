# profile_store.py
"""
profile_store.py — Adapter for reading, validating, and saving profile data.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Tuple

DATA_DIR = Path(__file__).parent / "data"
DEFAULT_TAX_PATH = DATA_DIR / "tax_profile.json"
DEFAULT_CONST_PATH = DATA_DIR / "constraints.json"

TAX_KEYS = {
    "filing_status", "tax_year", "state", "ages", "agi_prior_year",
    "cap_loss_carryforward", "standard_deduction_base",
    "standard_deduction_senior_bonus_each", "effective_standard_deduction",
    "ss_combined_annual", "federal_brackets_mfj_2025",
    "qualified_div_brackets_mfj_2025", "ss_taxation",
    "irmaa_thresholds_mfj_2025", "irmaa_note", "oregon_tax"
}

CONSTRAINT_KEYS = {
    "withdrawal_sequence_default", "inherited_ira_deadline_year",
    "inherited_ira_balance_start", "irmaa_guardrails", "rmd_start_age",
    "rmd_table", "rmd_applies_to", "rmd_note", "concentration_limits",
    "monte_carlo", "aggressiveness_score", "planning_horizon"
}


def load_profile(tax_profile_path: Path | str = DEFAULT_TAX_PATH, 
                 constraints_path: Path | str = DEFAULT_CONST_PATH) -> Dict[str, Any]:
    """
    Load a merged view of the user's financial profile.
    
    Keys sourced from tax_profile.json:
        filing_status, tax_year, state, ages, agi_prior_year, ss_combined_annual,
        cap_loss_carryforward, standard_deduction_*, brackets, etc.
        
    Keys sourced from constraints.json:
        withdrawal_sequence_default, inherited_ira_deadline_year, rmd_*, 
        monte_carlo, aggressiveness_score, planning_horizon, etc.
    """
    tax_profile_path = Path(tax_profile_path)
    constraints_path = Path(constraints_path)
    
    tax_data = {}
    if tax_profile_path.exists():
        with open(tax_profile_path, "r", encoding="utf-8") as f:
            tax_data = json.load(f)
            
    const_data = {}
    if constraints_path.exists():
        with open(constraints_path, "r", encoding="utf-8") as f:
            const_data = json.load(f)

    # Merge constraints over tax_data 
    return {**tax_data, **const_data}


def validate_profile(profile: Dict[str, Any]) -> List[str]:
    """
    Validate basic profile constraints: types, ranges, enums.
    Returns a list of human-readable error messages. An empty list means valid.
    """
    errors = []
    
    if "ages" in profile:
        ages = profile["ages"]
        if not isinstance(ages, list) or not all(isinstance(a, (int, float)) and a >= 0 for a in ages):
            errors.append("Ages must be a list of non-negative numbers.")
            
    if "filing_status" in profile:
        valid_statuses = {"single", "mfj", "mfs", "hoh", "qw"}
        if profile["filing_status"] not in valid_statuses:
            errors.append(f"Invalid filing_status: {profile['filing_status']}. Must be one of {valid_statuses}.")
            
    if "ss_combined_annual" in profile:
        val = profile["ss_combined_annual"]
        if not isinstance(val, (int, float)) or val < 0:
            errors.append("ss_combined_annual must be a non-negative number.")
            
    if "aggressiveness_score" in profile:
        agg = profile["aggressiveness_score"]
        if isinstance(agg, dict) and "current_target" in agg:
            target = agg["current_target"]
            if not isinstance(target, (int, float)) or target < 0 or target > 100:
                errors.append("aggressiveness_score.current_target must be between 0 and 100.")
                
    for key in ["agi_prior_year", "standard_deduction_base"]:
        if key in profile:
            val = profile[key]
            if not isinstance(val, (int, float)) or val < 0:
                errors.append(f"{key} must be a non-negative number.")
                
    return errors


def save_profile(patch: Dict[str, Any],
                 tax_profile_path: Path | str = DEFAULT_TAX_PATH,
                 constraints_path: Path | str = DEFAULT_CONST_PATH) -> Tuple[Dict[str, Any], List[str]]:
    """
    Patch the profile store with new values.
    
    Returns: (patched_merged_profile, list_of_errors)
        
    If validation fails, the save is aborted and changes are not written to disk.
    Preserves unknown keys and delegates writes to the proper JSON file.
    """
    tax_profile_path = Path(tax_profile_path)
    constraints_path = Path(constraints_path)
    
    current_profile = load_profile(tax_profile_path, constraints_path)
    merged = {**current_profile, **patch}
    
    errors = validate_profile(merged)
    if errors:
        return current_profile, errors

    tax_data = {}
    if tax_profile_path.exists():
        with open(tax_profile_path, "r", encoding="utf-8") as f:
            tax_data = json.load(f)
            
    const_data = {}
    if constraints_path.exists():
        with open(constraints_path, "r", encoding="utf-8") as f:
            const_data = json.load(f)

    for k, v in patch.items():
        if k in tax_data:
            tax_data[k] = v
        elif k in const_data:
            const_data[k] = v
        elif k in CONSTRAINT_KEYS:
            const_data[k] = v
        else:
            tax_data[k] = v

    tax_profile_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tax_profile_path, "w", encoding="utf-8") as f:
        json.dump(tax_data, f, indent=2)
        
    constraints_path.parent.mkdir(parents=True, exist_ok=True)
    with open(constraints_path, "w", encoding="utf-8") as f:
        json.dump(const_data, f, indent=2)

    return merged, []
