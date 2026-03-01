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
    total_val = 0.0
    for h in holdings or []:
        v = None
        for k in value_keys:
            if k in h and h[k] not in (None, ""):
                v = h[k]
                break
        if v is None:
            continue
        try:
            total_val += float(str(v).replace(",", "").replace("$", ""))
        except Exception:
            continue

    # Deterministic timeline: year-by-year cashflow ledger
    timeline = []
    current_balance = total_val
    current_year = 2026
    assumed_growth_rate = 0.045
    placeholder_spending = 100000.0

    for i in range(1, horizon_years + 1):
        year = current_year + i - 1
        start_balance = current_balance

        spending_need = placeholder_spending
        withdrawals_total = min(spending_need, start_balance)

        growth = (start_balance - withdrawals_total) * assumed_growth_rate
        end_balance = start_balance - withdrawals_total + growth

        current_balance = end_balance

        timeline.append({
            "year_index": i,
            "year": year,
            "cashflow": {
                "spending_need": round(spending_need, 2),
                "income": 0.0,
                "withdrawals": round(withdrawals_total, 2),
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
                "required": None,
                "details": None,
            },
            "portfolio": {
                "start_balance": round(start_balance, 2),
                "end_balance": round(end_balance, 2),
            },
            "notes": [],
        })

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
