"""
Plan Engine Orchestrator (Slice 1)

Goal: Provide a single, stable orchestration layer that returns a structured plan output.
This is intentionally a skeleton: cashflows, taxes, RMDs, and Monte Carlo integration come in later slices.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


def _infer_horizon_years(constraints: Dict[str, Any]) -> int:
    # Prefer constraints.monte_carlo.projection_years if present; else default 20.
    mc = constraints.get("monte_carlo", {}) if isinstance(constraints, dict) else {}
    years = mc.get("projection_years", 20)
    try:
        years = int(years)
    except Exception:
        years = 20
    return max(1, years)


def _calc_fed_tax(income: float) -> float:
    brackets = [
        (0, 23200, 0.10),
        (23200, 94300, 0.12),
        (94300, 201050, 0.22),
        (201050, 383900, 0.24),
        (383900, 487450, 0.32),
        (487450, 731200, 0.35),
        (731200, float('inf'), 0.37)
    ]
    tax = 0.0
    for lower, upper, rate in brackets:
        if income > lower:
            tax += (min(income, upper) - lower) * rate
    return tax

def _get_state_tax_info(state: str) -> tuple:
    s = str(state).strip().upper()
    if s in ["OREGON", "OR"]:
        return "OR", 5000.0, [(0, 10000, 0.0475), (10000, 25000, 0.0675), (25000, 125000, 0.0875), (125000, float('inf'), 0.099)]
    elif s in ["CALIFORNIA", "CA"]:
        return "CA", 10000.0, [(0, 20000, 0.01), (20000, 50000, 0.02), (50000, 100000, 0.04), (100000, float('inf'), 0.08)]
    elif s in ["NEW YORK", "NY"]:
        return "NY", 16000.0, [(0, 17000, 0.04), (17000, 24000, 0.045), (24000, float('inf'), 0.06)]
    elif s in ["TEXAS", "TX", "FLORIDA", "FL"]:
        return s[:2] if len(s) > 2 else s, 0.0, [(0, float('inf'), 0.0)]
    return "UNKNOWN", 0.0, [(0, float('inf'), 0.0)]

def _calc_state_tax(income: float, brackets: list) -> float:
    tax = 0.0
    for lower, upper, rate in brackets:
        if income > lower:
            tax += (min(income, upper) - lower) * rate
    return tax


def get_initial_balances(holdings: List[Dict[str, Any]]) -> Dict[str, float]:
    value_keys = ["market_value", "marketValue", "value", "current_value", "currentValue", "mv"]
    type_keys = ["account_type", "type", "accountType"]
    balances = {
        "taxable": 0.0,
        "trad_ira": 0.0,
        "inherited_ira": 0.0,
        "roth_ira": 0.0
    }

    for h in holdings or []:
        v = None
        for k in value_keys:
            if k in h and h[k] not in (None, ""):
                v = h[k]
                break
        if v is None:
            continue
        try:
            val = float(str(v).replace(",", "").replace("$", ""))
        except Exception:
            continue

        acct_type = "taxable"
        for k in type_keys:
            if k in h and h[k]:
                val_str = str(h[k]).lower().strip().replace(" ", "_").replace("-", "_")
                if val_str in balances:
                    acct_type = val_str
                break
        balances[acct_type] += val
    return balances


def step_one_year(balances: Dict[str, float], current_age: int, spending_need: float,
                  rmd_start_age: int, rmd_applies_to: List[str], withdrawal_sequence: List[str],
                  irmaa_enabled: bool, irmaa_threshold: float, assumed_growth_rate: float,
                  ss_income: float = 0.0, state: str = "None", fed_std_deduction: float = 29200.0,
                  ltcg_rate: float = 0.12) -> tuple:
    start_balance = sum(balances.values())
    rmd_required, rmd_withdrawn = 0.0, 0.0
    withdrawals_by_account = {k: 0.0 for k in balances.keys()}

    if current_age >= rmd_start_age:
        for acct in rmd_applies_to:
            if acct in balances and balances[acct] > 0:
                req = min(balances[acct] / 25.0, balances[acct])
                rmd_required += req; balances[acct] -= req; withdrawals_by_account[acct] += req; rmd_withdrawn += req

    remaining_need = max(0.0, spending_need - rmd_withdrawn)
    for acct in withdrawal_sequence:
        if remaining_need <= 0: break
        if acct in balances and balances[acct] > 0:
            take = min(balances[acct], remaining_need)
            balances[acct] -= take; withdrawals_by_account[acct] += take; remaining_need -= take

    withdrawals_total = sum(withdrawals_by_account.values())
    
    ira_withdrawals = withdrawals_by_account.get("trad_ira", 0.0) + withdrawals_by_account.get("inherited_ira", 0.0)
    taxable_withdrawals = withdrawals_by_account.get("taxable", 0.0)
    capital_gains = taxable_withdrawals * 0.40

    other_income = ira_withdrawals + capital_gains
    provisional_income = other_income + 0.5 * ss_income
    taxable_ss = min(0.85 * ss_income, max(0.0, provisional_income - 32000.0) * 0.85)

    magi = ira_withdrawals + taxable_ss + capital_gains

    ordinary_income = ira_withdrawals + taxable_ss
    fed_taxable_income = max(0.0, ordinary_income - fed_std_deduction)
    fed_ordinary_tax = _calc_fed_tax(fed_taxable_income)
    ltcg_tax = capital_gains * ltcg_rate
    federal_est_tax = fed_ordinary_tax + ltcg_tax

    state_code, state_std_deduction, state_brackets = _get_state_tax_info(state)
    state_taxable_income = max(0.0, ordinary_income + capital_gains - state_std_deduction)
    state_est_tax = _calc_state_tax(state_taxable_income, state_brackets)

    est_tax_total = federal_est_tax + state_est_tax


    irmaa_warning = irmaa_enabled and magi > irmaa_threshold
    irmaa_details = f"Estimated MAGI (${magi:,.2f}) exceeds IRMAA safety threshold (${irmaa_threshold:,.2f})" if irmaa_warning else None

    tax_details = {
        "taxable_ss": taxable_ss,
        "capital_gains": capital_gains,
        "ordinary_income": ordinary_income,
        "federal_tax_est": federal_est_tax,
        "state_code": state_code,
        "state_taxable_income": state_taxable_income,
        "state_tax_est": state_est_tax,
        "est_tax_total": est_tax_total
    }


    for acct in balances:
        balances[acct] += balances[acct] * assumed_growth_rate

    end_balance = sum(balances.values())
    return (start_balance, end_balance, withdrawals_total, withdrawals_by_account,
            rmd_required, rmd_withdrawn, magi, est_tax_total, irmaa_warning, irmaa_details, tax_details)


def run_plan(profile: Dict[str, Any], holdings: List[Dict[str, Any]], constraints: Dict[str, Any]) -> Dict[str, Any]:
    """
    Orchestrate the retirement plan calculation.

    Inputs:
      - profile: retiree/tax profile (dict)
      - holdings: list of holdings rows (dicts)
      - constraints: constraints config (dict)

    Returns:
      Dict with:
        - timeline: list of year skeleton entries
        - plan_summary: high-level placeholder metrics
        - warnings: list[str]
        - meta: info about assumptions/versions
    """
    horizon_years = _infer_horizon_years(constraints)

    balances = get_initial_balances(holdings)
    total_val = sum(balances.values())

    withdrawal_sequence = constraints.get("withdrawal_sequence_default", ["taxable", "trad_ira", "inherited_ira", "roth_ira"])
    rmd_start_age = constraints.get("rmd_start_age", 73)
    rmd_applies_to = constraints.get("rmd_applies_to", ["trad_ira"])

    irmaa_config = constraints.get("irmaa_guardrails", {}) if isinstance(constraints, dict) else {}
    irmaa_enabled = irmaa_config.get("enabled", False)
    irmaa_tier1 = irmaa_config.get("tier1_magi_mfj", 206000)
    irmaa_headroom = irmaa_config.get("target_headroom_below_tier1", 10000)
    irmaa_threshold = irmaa_tier1 - irmaa_headroom


    current_age = profile.get("age", 72)
    state = profile.get("state", "None")

    fed_std_deduction = constraints.get("federal_standard_deduction_mfj", 29200.0)
    ltcg_rate = constraints.get("ltcg_effective_rate", 0.12)

    # Deterministic timeline: year-by-year cashflow ledger (with withdrawals + simplified RMD)
    timeline = []
    plan_warnings = []
    current_year = 2026
    assumed_growth_rate = 0.045
    base_spending = constraints.get("placeholder_spending", 100000.0)
    inflation_rate = constraints.get("inflation_rate", 0.025)

    ss_annual = profile.get("social_security_annual", 0.0)
    ss_start_age = profile.get("social_security_start_age", 67)
    cola = profile.get("cola", inflation_rate)
    pension_annual = profile.get("pension_annual", 0.0)
    pension_start_age = profile.get("pension_start_age", 65)

    
    for i in range(1, horizon_years + 1):
        year = current_year + i - 1
        spending_need = base_spending * ((1 + inflation_rate) ** (i - 1))
        income_ss = ss_annual * ((1 + cola) ** (current_age - ss_start_age)) if current_age >= ss_start_age else 0.0
        income_pension = pension_annual if current_age >= pension_start_age else 0.0
        income_total = income_ss + income_pension
        net_spending_need = max(0.0, spending_need - income_total)

        (start_balance, end_balance, withdrawals_total, withdrawals_by_account,
         rmd_required, rmd_withdrawn, magi, est_tax_total, irmaa_warning, irmaa_details, tax_details) = step_one_year(
            balances, current_age, net_spending_need,
            rmd_start_age, rmd_applies_to, withdrawal_sequence,
            irmaa_enabled, irmaa_threshold, assumed_growth_rate,
            ss_income=income_ss, state=state, fed_std_deduction=fed_std_deduction, ltcg_rate=ltcg_rate
        )

        if irmaa_warning:
            plan_warnings.append(f"Year {year}: {irmaa_details}")

        timeline.append({
            "year_index": i,
            "year": year,
            "cashflow": {
                "spending_need": round(spending_need, 2),
                "income_total": round(income_total, 2),
                "income_breakdown": {
                    "ss": round(income_ss, 2),
                    "pension": round(income_pension, 2)
                },
                "net_spending_need": round(net_spending_need, 2),
                "income": 0.0,
                "withdrawals_total": round(withdrawals_total, 2),
                "withdrawals_by_account": {k: round(v, 2) for k, v in withdrawals_by_account.items() if v > 0},
            },
            "tax": {
                "magi": round(magi, 2),
                "est_tax": round(est_tax_total, 2),
                "federal_tax": round(tax_details["federal_tax_est"], 2),
                "state_tax": round(tax_details["state_tax_est"], 2),
                "taxable_ss": round(tax_details["taxable_ss"], 2),
                "capital_gains": round(tax_details["capital_gains"], 2),
                "ordinary_income": round(tax_details["ordinary_income"], 2),
                "state_code": tax_details["state_code"],
                "state_taxable_income": round(tax_details["state_taxable_income"], 2),
                "est_tax_total": round(est_tax_total, 2),

            },
            "irmaa": {
                "warning": irmaa_warning,
                "details": irmaa_details,
            },
            "rmd": {
                "required": round(rmd_required, 2),
                "withdrawn": round(rmd_withdrawn, 2),
                "details": None,
            },
            "portfolio": {
                "start_balance": round(start_balance, 2),
                "end_balance": round(end_balance, 2),
            },
            "notes": [],
        })

        current_age += 1


    plan_summary = {
        "horizon_years": horizon_years,
        "portfolio_value_estimate": round(total_val, 2),
        "profile_loaded": bool(profile),
        "holdings_count": len(holdings or []),
        "advisor_grade_status": "NOT_YET (Slice 1 skeleton)",
    }

    return {
        "timeline": timeline,
        "plan_summary": plan_summary,
        "warnings": plan_warnings,
        "meta": {
            "engine_version": "slice_1_deterministic",
            "assumptions": [
                "Timeline uses deterministic year-by-year cashflow",
                "Assumed constant $100k spending and 4.5% growth",
                "No taxes/RMD/IRMAA computed yet",
                "Portfolio value is best-effort from holdings rows",
                "Capital gains estimated as 40% of taxable withdrawals",
                "State tax uses minimal hardcoded progressive brackets for few states",
            ],
        },
    }
