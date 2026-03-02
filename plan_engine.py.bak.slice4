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

    # Simple portfolio value estimate: try common fields
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

    total_val = sum(balances.values())

    withdrawal_sequence = constraints.get("withdrawal_sequence_default", ["taxable", "trad_ira", "inherited_ira", "roth_ira"])
    rmd_start_age = constraints.get("rmd_start_age", 73)
    rmd_applies_to = constraints.get("rmd_applies_to", ["trad_ira"])

    current_age = profile.get("age", 72)

    # Deterministic timeline: year-by-year cashflow ledger (with withdrawals + simplified RMD)
    timeline = []
    current_year = 2026
    assumed_growth_rate = 0.045
    placeholder_spending = 100000.0

    for i in range(1, horizon_years + 1):
        year = current_year + i - 1
        start_balance = sum(balances.values())

        spending_need = placeholder_spending
        rmd_required = 0.0
        rmd_withdrawn = 0.0
        withdrawals_by_account = {k: 0.0 for k in balances.keys()}

        # Simplified RMD: if age >= start age, apply to configured account types using divisor=25
        if current_age >= rmd_start_age:
            for acct in rmd_applies_to:
                if acct in balances and balances[acct] > 0:
                    req = min(balances[acct] / 25.0, balances[acct])
                    rmd_required += req
                    balances[acct] -= req
                    withdrawals_by_account[acct] += req
                    rmd_withdrawn += req

        # Remaining spending need after RMD withdrawals
        remaining_need = max(0.0, spending_need - rmd_withdrawn)
        for acct in withdrawal_sequence:
            if remaining_need <= 0:
                break
            if acct in balances and balances[acct] > 0:
                take = min(balances[acct], remaining_need)
                balances[acct] -= take
                withdrawals_by_account[acct] += take
                remaining_need -= take

        withdrawals_total = sum(withdrawals_by_account.values())

        # Grow remaining balances (simple deterministic growth)
        for acct in balances:
            balances[acct] += balances[acct] * assumed_growth_rate

        end_balance = sum(balances.values())

        timeline.append({
            "year_index": i,
            "year": year,
            "cashflow": {
                "spending_need": round(spending_need, 2),
                "income": 0.0,
                "withdrawals_total": round(withdrawals_total, 2),
                "withdrawals_by_account": {k: round(v, 2) for k, v in withdrawals_by_account.items() if v > 0},
            },
            "tax": {
                "magi": None,
                "federal_tax": None,
                "state_tax": None,
            },
            "irmaa": {
                "warning": False,
                "details": None,
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
        "warnings": [],
        "meta": {
            "engine_version": "slice_1_deterministic",
            "assumptions": [
                "Timeline uses deterministic year-by-year cashflow",
                "Assumed constant $100k spending and 4.5% growth",
                "No taxes/RMD/IRMAA computed yet",
                "Portfolio value is best-effort from holdings rows",
            ],
        },
    }
