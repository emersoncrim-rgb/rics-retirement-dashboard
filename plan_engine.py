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

    # Skeleton timeline: years indexed from 1..horizon_years (we'll replace with calendar years later)
    timeline = []
    for i in range(1, horizon_years + 1):
        timeline.append({
            "year_index": i,
            "cashflow": {
                "spending_need": None,
                "income": None,
                "withdrawals": None,
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
                "start_balance": None,
                "end_balance": None,
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
            "engine_version": "slice_1",
            "assumptions": [
                "Timeline is a skeleton only",
                "No withdrawals/taxes/RMD/IRMAA computed yet",
                "Portfolio value is best-effort from holdings rows",
            ],
        },
    }
