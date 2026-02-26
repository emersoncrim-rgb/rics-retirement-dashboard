"""
trip_simulator.py — "Can I afford this trip?" analysis for RICS.

Public API:
    trip_impact(cost, year, ingested, ...) -> dict
    compare_funding_options(cost, year, ingested, ...) -> dict
"""

from __future__ import annotations

import copy
from typing import Dict, List, Literal, Optional

import numpy as np

# ---------------------------------------------------------------------------
# Imports from RICS modules
# ---------------------------------------------------------------------------
from ingest import ingest_all, DEFAULT_PATHS, compute_today_summary
from deterministic import (
    project_three_scenarios,
    build_projection_from_ingested,
)
from tax_irmaa import (
    compute_taxes,
    compute_magi,
    compute_irmaa_impact,
    compute_ss_taxable,
)
from mc_sim import run_mc_from_ingested

# ---------------------------------------------------------------------------
# Funding options
# ---------------------------------------------------------------------------

FundingSource = Literal["cash", "inherited_ira", "taxable", "trad_ira", "optimal"]

_FUNDING_SOURCES: list[FundingSource] = ["cash", "inherited_ira", "taxable", "trad_ira"]


# ---------------------------------------------------------------------------
# Immediate tax impact per funding source
# ---------------------------------------------------------------------------

def _estimate_tax_impact(
    cost: float,
    source: FundingSource,
    tax_profile: dict,
    baseline_agi: float,
    cap_loss_carryforward: float = 3_000,
    taxable_gain_pct: float = 0.60,
) -> dict:
    """
    Estimate the tax cost of funding `cost` from a specific source.

    Returns dict: {source, gross_cost, tax_cost, net_cost,
                   agi_delta, magi_after, irmaa_impact_after}
    """
    std_ded = tax_profile.get("effective_standard_deduction", 33_100)
    ss = tax_profile.get("ss_combined_annual", 56_000)

    agi_delta = 0.0
    tax_cost = 0.0
    note = ""

    if source == "cash":
        # No new taxable event — already-taxed money
        agi_delta = 0.0
        tax_cost = 0.0
        note = "No tax impact; funded from after-tax cash/MMF"

    elif source == "inherited_ira":
        # Full amount is ordinary income
        agi_delta = cost
        # Marginal tax: approximate by computing delta in tax
        tax_before = _quick_tax(baseline_agi, tax_profile)
        tax_after = _quick_tax(baseline_agi + cost, tax_profile)
        tax_cost = tax_after - tax_before
        note = f"Full ${cost:,.0f} is ordinary income (inherited IRA distribution)"

    elif source == "taxable":
        # Only the gain portion is taxable; assume long-term
        est_gain = cost * taxable_gain_pct
        net_gain = max(est_gain - cap_loss_carryforward, 0)
        # LTCG taxed at preferential rate
        ltcg_rate = _estimate_ltcg_rate(baseline_agi, std_ded, tax_profile)
        tax_cost = net_gain * ltcg_rate
        agi_delta = est_gain  # gain adds to AGI even if taxed preferentially
        note = (f"Est. gain ${est_gain:,.0f} ({taxable_gain_pct:.0%} of proceeds); "
                f"net after carryforward ${net_gain:,.0f}; LTCG rate {ltcg_rate:.0%}")

    elif source == "trad_ira":
        # Full amount is ordinary income (same as inherited)
        agi_delta = cost
        tax_before = _quick_tax(baseline_agi, tax_profile)
        tax_after = _quick_tax(baseline_agi + cost, tax_profile)
        tax_cost = tax_after - tax_before
        note = f"Full ${cost:,.0f} is ordinary income (IRA distribution)"

    # IRMAA impact
    magi_before = baseline_agi  # simplified: MAGI ≈ AGI for this couple
    magi_after = magi_before + agi_delta
    irmaa_before = compute_irmaa_impact(magi_before, tax_profile, num_people=2)
    irmaa_after = compute_irmaa_impact(magi_after, tax_profile, num_people=2)
    irmaa_delta = irmaa_after["total_annual_cost"] - irmaa_before["total_annual_cost"]

    return {
        "source": source,
        "gross_cost": round(cost, 2),
        "tax_cost": round(tax_cost, 2),
        "irmaa_delta_annual": round(irmaa_delta, 2),
        "net_cost": round(cost + tax_cost + irmaa_delta, 2),
        "agi_delta": round(agi_delta, 2),
        "magi_after": round(magi_after, 2),
        "irmaa_tier_before": irmaa_before["tier"],
        "irmaa_tier_after": irmaa_after["tier"],
        "irmaa_headroom_after": irmaa_after.get("headroom_to_next_tier"),
        "note": note,
    }


def _quick_tax(agi: float, tax_profile: dict) -> float:
    """Quick federal + state tax estimate from AGI."""
    std_ded = tax_profile.get("effective_standard_deduction", 33_100)
    taxable = max(agi - std_ded, 0)
    # Federal
    fed = 0.0
    for b in tax_profile.get("federal_brackets_mfj_2025", []):
        lo, hi, rate = b["lower"], b["upper"], b["rate"]
        if taxable <= lo:
            break
        top = taxable if hi is None else min(taxable, hi)
        fed += (top - lo) * rate
    # Oregon (simplified — subtract SS taxable from AGI for OR)
    or_cfg = tax_profile.get("oregon_tax", {})
    ss = tax_profile.get("ss_combined_annual", 56_000)
    # Estimate SS taxable portion
    ss_taxable = min(0.85 * ss, max(0, agi - 32_000) * 0.5)
    or_taxable = max(agi - ss_taxable - or_cfg.get("standard_deduction_mfj", 5010), 0)
    state = 0.0
    for b in or_cfg.get("brackets_mfj_2025", []):
        lo, hi, rate = b["lower"], b["upper"], b["rate"]
        if or_taxable <= lo:
            break
        top = or_taxable if hi is None else min(or_taxable, hi)
        state += (top - lo) * rate
    return fed + state


def _estimate_ltcg_rate(baseline_agi: float, std_ded: float, tax_profile: dict) -> float:
    """Estimate marginal LTCG rate based on where baseline AGI falls."""
    taxable = max(baseline_agi - std_ded, 0)
    brackets = tax_profile.get("qualified_div_brackets_mfj_2025", [])
    for b in brackets:
        hi = b["upper"]
        if hi is None or taxable < hi:
            return b["rate"]
    return 0.20


# ---------------------------------------------------------------------------
# Deterministic delta
# ---------------------------------------------------------------------------

def _deterministic_delta(
    ingested: dict,
    cost: float,
    year: int,
    source: FundingSource,
    horizon: int = 20,
) -> dict:
    """
    Run 3-scenario deterministic projection with and without the trip.

    Returns per-scenario: {end_balance_base, end_balance_trip, delta, pct_delta}
    """
    # Base projection
    base = build_projection_from_ingested(ingested, horizon=horizon)

    # Modify cashflow to include trip as one-time expense
    ingested_trip = copy.deepcopy(ingested)
    ingested_trip["cashflow"].append({
        "year": year,
        "category": "expense",
        "subcategory": "trip_scenario",
        "amount": cost,
        "frequency": "one_time",
        "inflation_adj": False,
        "taxable": "n/a",
        "notes": f"Scenario: ${cost:,.0f} trip funded from {source}",
    })

    trip = build_projection_from_ingested(ingested_trip, horizon=horizon)

    results = {}
    for scenario in ("conservative", "central", "growth"):
        base_end = base[scenario]["summary"]["end_balance"]
        trip_end = trip[scenario]["summary"]["end_balance"]
        delta = trip_end - base_end

        # Year-by-year delta for the first 5 years post-trip
        trip_year_offset = year - 2025
        yearly_deltas = []
        for i in range(min(5, horizon - trip_year_offset)):
            idx = trip_year_offset + i
            if idx < horizon:
                b = base[scenario]["projection"][idx]["total_balance"]
                t = trip[scenario]["projection"][idx]["total_balance"]
                yearly_deltas.append({
                    "year": 2025 + idx,
                    "balance_base": round(b, 0),
                    "balance_trip": round(t, 0),
                    "delta": round(t - b, 0),
                })

        results[scenario] = {
            "rate": base[scenario]["rate"],
            "end_balance_base": round(base_end, 0),
            "end_balance_trip": round(trip_end, 0),
            "delta_20yr": round(delta, 0),
            "pct_delta": round(delta / base_end * 100, 2) if base_end else 0,
            "yearly_deltas": yearly_deltas,
        }

    return results


# ---------------------------------------------------------------------------
# MC delta (optional)
# ---------------------------------------------------------------------------

def _mc_delta(
    ingested: dict,
    cost: float,
    year: int,
    n_sims: int = 1_000,
    horizon: int = 20,
    seed: int = 42,
) -> dict:
    """
    Run MC with and without trip, return deltas in ruin probability and IRMAA.
    """
    base_mc = run_mc_from_ingested(ingested, n_sims=n_sims, horizon=horizon, seed=seed)

    ingested_trip = copy.deepcopy(ingested)
    ingested_trip["cashflow"].append({
        "year": year,
        "category": "expense",
        "subcategory": "trip_scenario",
        "amount": cost,
        "frequency": "one_time",
        "inflation_adj": False,
        "taxable": "n/a",
        "notes": f"MC scenario: trip",
    })

    trip_mc = run_mc_from_ingested(ingested_trip, n_sims=n_sims, horizon=horizon, seed=seed)

    return {
        "n_sims": n_sims,
        "base_ruin_prob": base_mc["ruin_stats"]["probability_of_ruin"],
        "trip_ruin_prob": trip_mc["ruin_stats"]["probability_of_ruin"],
        "ruin_delta": round(
            trip_mc["ruin_stats"]["probability_of_ruin"]
            - base_mc["ruin_stats"]["probability_of_ruin"], 4
        ),
        "base_irmaa_ever": base_mc["irmaa_stats"]["prob_ever_triggered"],
        "trip_irmaa_ever": trip_mc["irmaa_stats"]["prob_ever_triggered"],
        "irmaa_delta": round(
            trip_mc["irmaa_stats"]["prob_ever_triggered"]
            - base_mc["irmaa_stats"]["prob_ever_triggered"], 4
        ),
        "base_median_terminal": base_mc["terminal_stats"]["median"],
        "trip_median_terminal": trip_mc["terminal_stats"]["median"],
        "terminal_delta": round(
            trip_mc["terminal_stats"]["median"]
            - base_mc["terminal_stats"]["median"], 0
        ),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def trip_impact(
    cost: float,
    year: int = 2025,
    source: FundingSource = "optimal",
    ingested: Optional[dict] = None,
    run_mc: bool = True,
    mc_n: int = 1_000,
    mc_seed: int = 42,
    horizon: int = 20,
    baseline_agi: Optional[float] = None,
) -> dict:
    """
    Full trip impact analysis.

    Parameters
    ----------
    cost : trip cost in dollars
    year : year the trip expense occurs
    source : funding source, or "optimal" to compare all options
    ingested : output of ingest_all() (loaded if None)
    run_mc : whether to run Monte Carlo delta analysis
    mc_n : number of MC sims (1000 is fast enough for delta estimation)

    Returns
    -------
    dict with: funding_analysis (per-source tax impact),
               deterministic_delta, mc_delta (if run_mc),
               recommendation
    """
    if ingested is None:
        ingested = ingest_all()

    tp = ingested["tax_profile"]
    if baseline_agi is None:
        baseline_agi = tp.get("agi_prior_year", 104_000)

    # ── Step 1: Tax impact for each funding source ──
    sources = _FUNDING_SOURCES if source == "optimal" else [source]

    funding_analysis = {}
    for s in sources:
        # Adjust gain pct based on source
        gain_pct = 0.60 if s == "taxable" else 0.0
        fa = _estimate_tax_impact(cost, s, tp, baseline_agi,
                                   cap_loss_carryforward=tp.get("cap_loss_carryforward", 3_000),
                                   taxable_gain_pct=gain_pct)
        funding_analysis[s] = fa

    # ── Step 2: Deterministic delta (use cheapest source) ──
    best_source = min(funding_analysis.values(), key=lambda x: x["net_cost"])["source"]
    det_delta = _deterministic_delta(ingested, cost, year, best_source, horizon)

    # ── Step 3: MC delta (optional) ──
    mc_delta = None
    if run_mc:
        mc_delta = _mc_delta(ingested, cost, year, mc_n, horizon, mc_seed)

    # ── Step 4: Recommendation ──
    sorted_options = sorted(funding_analysis.values(), key=lambda x: x["net_cost"])
    recommendation = _build_recommendation(cost, sorted_options, det_delta, mc_delta)

    return {
        "trip_cost": cost,
        "trip_year": year,
        "funding_analysis": funding_analysis,
        "best_source": best_source,
        "deterministic_delta": det_delta,
        "mc_delta": mc_delta,
        "recommendation": recommendation,
    }


def compare_funding_options(
    cost: float,
    year: int = 2025,
    ingested: Optional[dict] = None,
    baseline_agi: Optional[float] = None,
) -> dict:
    """Quick comparison of funding options — tax impact only, no projections."""
    if ingested is None:
        ingested = ingest_all()
    tp = ingested["tax_profile"]
    if baseline_agi is None:
        baseline_agi = tp.get("agi_prior_year", 104_000)

    results = {}
    for s in _FUNDING_SOURCES:
        gain_pct = 0.60 if s == "taxable" else 0.0
        results[s] = _estimate_tax_impact(cost, s, tp, baseline_agi,
                                           cap_loss_carryforward=tp.get("cap_loss_carryforward", 3_000),
                                           taxable_gain_pct=gain_pct)
    return results


# ---------------------------------------------------------------------------
# Recommendation builder
# ---------------------------------------------------------------------------

def _build_recommendation(
    cost: float,
    sorted_options: list[dict],
    det_delta: dict,
    mc_delta: Optional[dict],
) -> dict:
    best = sorted_options[0]
    worst = sorted_options[-1]

    savings = worst["net_cost"] - best["net_cost"]
    central_delta = det_delta["central"]["delta_20yr"]
    central_pct = det_delta["central"]["pct_delta"]

    # Verdict
    if mc_delta and mc_delta["ruin_delta"] > 0.02:
        verdict = "CAUTION"
        reason = (f"Trip increases ruin probability by {mc_delta['ruin_delta']:.1%}. "
                  f"Consider a smaller budget or deferring.")
    elif abs(central_pct) < 1.0:
        verdict = "GO — MINIMAL IMPACT"
        reason = (f"20-year portfolio impact is only {central_pct:.2f}% "
                  f"(${abs(central_delta):,.0f}) under central scenario.")
    elif abs(central_pct) < 3.0:
        verdict = "GO — MANAGEABLE"
        reason = (f"20-year impact of {central_pct:.2f}% is noticeable but manageable "
                  f"for a ${sum(v['gross_cost'] for v in sorted_options[:1]):,.0f} experience.")
    else:
        verdict = "CONSIDER ALTERNATIVES"
        reason = (f"20-year impact of {central_pct:.2f}% is significant. "
                  f"Consider phasing the cost over multiple years.")

    return {
        "verdict": verdict,
        "reason": reason,
        "best_funding": best["source"],
        "best_net_cost": best["net_cost"],
        "worst_funding": worst["source"],
        "worst_net_cost": worst["net_cost"],
        "savings_vs_worst": round(savings, 2),
        "irmaa_safe": best["irmaa_tier_after"] == 0,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_trip_analysis(result: dict) -> None:
    print(f"\n{'=' * 95}")
    print(f"  TRIP IMPACT ANALYSIS — ${result['trip_cost']:,.0f} in {result['trip_year']}")
    print(f"{'=' * 95}")

    # Funding comparison
    print(f"\n── Funding Options ──")
    print(f"  {'Source':>15} {'Gross':>10} {'Tax Cost':>10} {'IRMAA Δ':>9}"
          f" {'Net Cost':>11} {'AGI Δ':>9} {'IRMAA':>6}  Note")
    print("  " + "─" * 90)
    for s in ("cash", "inherited_ira", "taxable", "trad_ira"):
        if s not in result["funding_analysis"]:
            continue
        fa = result["funding_analysis"][s]
        tier_str = f"T{fa['irmaa_tier_after']}" if fa["irmaa_tier_after"] > 0 else "safe"
        print(f"  {fa['source']:>15} ${fa['gross_cost']:>9,.0f} ${fa['tax_cost']:>9,.0f}"
              f" ${fa['irmaa_delta_annual']:>8,.0f} ${fa['net_cost']:>10,.0f}"
              f" ${fa['agi_delta']:>8,.0f} {tier_str:>6}  {fa['note'][:50]}")

    # Best source
    rec = result["recommendation"]
    print(f"\n  ★ Best: {rec['best_funding']} at ${rec['best_net_cost']:,.0f}"
          f"  (saves ${rec['savings_vs_worst']:,.0f} vs {rec['worst_funding']})")

    # Deterministic delta
    print(f"\n── 20-Year Portfolio Impact ──")
    for s in ("conservative", "central", "growth"):
        d = result["deterministic_delta"][s]
        print(f"  {s:14s} ({d['rate']:.1%}): "
              f"${d['end_balance_base']:>12,.0f} → ${d['end_balance_trip']:>12,.0f}"
              f"  Δ ${d['delta_20yr']:>10,.0f}  ({d['pct_delta']:+.2f}%)")

    # MC delta
    if result["mc_delta"]:
        mc = result["mc_delta"]
        print(f"\n── Monte Carlo Delta ({mc['n_sims']:,} sims) ──")
        print(f"  Ruin prob:   {mc['base_ruin_prob']:.2%} → {mc['trip_ruin_prob']:.2%}"
              f"  (Δ {mc['ruin_delta']:+.2%})")
        print(f"  IRMAA ever:  {mc['base_irmaa_ever']:.1%} → {mc['trip_irmaa_ever']:.1%}"
              f"  (Δ {mc['irmaa_delta']:+.1%})")
        print(f"  Median terminal: ${mc['base_median_terminal']:>10,.0f}"
              f" → ${mc['trip_median_terminal']:>10,.0f}"
              f"  (Δ ${mc['terminal_delta']:>+10,.0f})")

    # Verdict
    print(f"\n  ┌─────────────────────────────────────────────┐")
    print(f"  │  {rec['verdict']:^43s}  │")
    print(f"  └─────────────────────────────────────────────┘")
    print(f"  {rec['reason']}")
    print()


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))

    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    paths = {k: str(base / v) for k, v in DEFAULT_PATHS.items()}
    ingested = ingest_all(paths)

    # Example 1: $15k trip funded optimally
    print("\n" + "▓" * 95)
    print("  EXAMPLE 1: $15,000 trip — compare all funding options")
    print("▓" * 95)
    result = trip_impact(15_000, 2026, "optimal", ingested, run_mc=True, mc_n=1_000, mc_seed=42)
    _print_trip_analysis(result)

    # Example 2: $50k trip (larger)
    print("\n" + "▓" * 95)
    print("  EXAMPLE 2: $50,000 bucket-list trip")
    print("▓" * 95)
    result2 = trip_impact(50_000, 2026, "optimal", ingested, run_mc=True, mc_n=1_000, mc_seed=42)
    _print_trip_analysis(result2)
