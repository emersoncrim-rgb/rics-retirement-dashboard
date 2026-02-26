"""
tax_irmaa.py — Tax computation and IRMAA impact module for RICS.

Public API:
    compute_taxes(...)          -> dict   # federal + state + total
    compute_magi(...)           -> float  # MAGI for IRMAA
    compute_irmaa_impact(...)   -> dict   # tier, surcharges, annual cost
    simulate_year_tax_effects(...)-> dict # all-in-one for projection rows
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Bracket-based tax computation (progressive)
# ---------------------------------------------------------------------------

def _tax_from_brackets(taxable_amount: float, brackets: list[dict]) -> float:
    """
    Compute tax using progressive brackets.

    Each bracket: {lower, upper (or null), rate}
    """
    if taxable_amount <= 0:
        return 0.0

    tax = 0.0
    for b in brackets:
        lo = b["lower"]
        hi = b["upper"]  # None means unlimited
        rate = b["rate"]

        if taxable_amount <= lo:
            break

        top = taxable_amount if hi is None else min(taxable_amount, hi)
        tax += (top - lo) * rate

    return tax


# ---------------------------------------------------------------------------
# Social Security taxable amount
# ---------------------------------------------------------------------------

def compute_ss_taxable(
    ss_annual: float,
    other_income: float,
    filing_status: str = "mfj",
) -> float:
    """
    Compute the taxable portion of Social Security benefits.

    Uses the IRS provisional-income formula:
      provisional = other_income + 0.5 * ss_annual

    MFJ thresholds: $32,000 (50% tier), $44,000 (85% tier)
    Single thresholds: $25,000 / $34,000
    """
    if filing_status == "mfj":
        base, add = 32_000, 44_000
    else:
        base, add = 25_000, 34_000

    provisional = other_income + 0.5 * ss_annual

    if provisional <= base:
        return 0.0
    elif provisional <= add:
        return min(0.50 * (provisional - base), 0.50 * ss_annual)
    else:
        tier1 = 0.50 * (add - base)
        tier2 = 0.85 * (provisional - add)
        return min(tier1 + tier2, 0.85 * ss_annual)


# ---------------------------------------------------------------------------
# Capital gains / loss netting
# ---------------------------------------------------------------------------

def _net_capital_gains(
    long_term_gains: float,
    short_term_gains: float,
    cap_loss_carryforward: float,
) -> dict:
    """
    Net capital gains against losses and carryforward.

    Returns dict with:
      net_ltcg, net_stcg, loss_used, remaining_carryforward,
      excess_loss_deduction (up to $3,000/yr applied to ordinary income)
    """
    # Apply carryforward: first against ST gains, then LT gains
    remaining_cf = cap_loss_carryforward

    net_stcg = short_term_gains
    if remaining_cf > 0 and net_stcg > 0:
        offset = min(remaining_cf, net_stcg)
        net_stcg -= offset
        remaining_cf -= offset

    net_ltcg = long_term_gains
    if remaining_cf > 0 and net_ltcg > 0:
        offset = min(remaining_cf, net_ltcg)
        net_ltcg -= offset
        remaining_cf -= offset

    # If still carryforward remaining, up to $3,000 offsets ordinary income
    excess_loss_deduction = 0.0
    if remaining_cf > 0:
        excess_loss_deduction = min(remaining_cf, 3_000)
        remaining_cf -= excess_loss_deduction

    loss_used = cap_loss_carryforward - remaining_cf

    return {
        "net_ltcg": max(net_ltcg, 0),
        "net_stcg": max(net_stcg, 0),
        "loss_used": loss_used,
        "excess_loss_deduction": excess_loss_deduction,
        "remaining_carryforward": remaining_cf,
    }


# ---------------------------------------------------------------------------
# compute_taxes — main entry point
# ---------------------------------------------------------------------------

def compute_taxes(
    ordinary_income: float,
    long_term_cap_gains: float = 0.0,
    qualified_dividends: float = 0.0,
    short_term_cap_gains: float = 0.0,
    cap_loss_carryforward: float = 0.0,
    ss_annual: float = 0.0,
    standard_deduction: float = 33_100,
    tax_profile: Optional[dict] = None,
) -> dict:
    """
    Compute federal + state taxes for one year.

    Parameters
    ----------
    ordinary_income : wages, IRA distributions, ordinary dividends, interest
                      (EXCLUDING SS, LTCG, and qualified dividends)
    long_term_cap_gains : realized LTCG from taxable account
    qualified_dividends : dividends qualifying for preferential rates
    short_term_cap_gains : realized STCG (taxed as ordinary)
    cap_loss_carryforward : unused capital losses from prior years
    ss_annual : total Social Security benefits received
    standard_deduction : MFJ + senior bonus deduction
    tax_profile : full tax_profile.json dict (provides bracket tables)

    Returns
    -------
    dict: {federal_ordinary, federal_ltcg, federal_total,
           state_tax, total_tax, effective_rate,
           ss_taxable, cap_gain_netting, agi, taxable_income}
    """
    if tax_profile is None:
        tax_profile = _default_tax_profile()

    # Step 1: Net capital gains against losses / carryforward
    cg = _net_capital_gains(long_term_cap_gains, short_term_cap_gains,
                            cap_loss_carryforward)

    # Step 2: Compute AGI components
    # Net STCG is taxed as ordinary income
    total_ordinary = ordinary_income + cg["net_stcg"]

    # SS taxable amount depends on all other income
    other_income = total_ordinary + cg["net_ltcg"] + qualified_dividends
    ss_taxable = compute_ss_taxable(ss_annual, other_income,
                                     tax_profile.get("filing_status", "mfj"))

    # AGI = ordinary + SS taxable + net LTCG + qualified divs
    # (minus excess loss deduction from carryforward)
    agi = (total_ordinary + ss_taxable + cg["net_ltcg"]
           + qualified_dividends - cg["excess_loss_deduction"])

    # Step 3: Taxable income after standard deduction
    taxable_income = max(agi - standard_deduction, 0)

    # Step 4: Federal tax — split ordinary vs preferential
    # Preferential income (LTCG + qualified divs) stacks on top of ordinary
    preferential_income = cg["net_ltcg"] + qualified_dividends
    ordinary_taxable = max(taxable_income - preferential_income, 0)

    # Tax on ordinary portion
    fed_brackets = tax_profile.get("federal_brackets_mfj_2025", [])
    federal_ordinary = _tax_from_brackets(ordinary_taxable, fed_brackets)

    # Tax on preferential portion (stacked on top of ordinary)
    ltcg_brackets = tax_profile.get("qualified_div_brackets_mfj_2025", [])
    # The preferential income fills brackets starting where ordinary left off
    federal_ltcg = _compute_stacked_preferential_tax(
        ordinary_taxable, preferential_income, ltcg_brackets
    )

    federal_total = federal_ordinary + federal_ltcg

    # Step 5: State tax (Oregon)
    state_tax = _compute_state_tax(agi, ss_taxable, standard_deduction, tax_profile)

    total_tax = federal_total + state_tax

    return {
        "agi": round(agi, 2),
        "ss_taxable": round(ss_taxable, 2),
        "taxable_income": round(taxable_income, 2),
        "ordinary_taxable": round(ordinary_taxable, 2),
        "preferential_income": round(preferential_income, 2),
        "federal_ordinary": round(federal_ordinary, 2),
        "federal_ltcg": round(federal_ltcg, 2),
        "federal_total": round(federal_total, 2),
        "state_tax": round(state_tax, 2),
        "total_tax": round(total_tax, 2),
        "effective_rate": round(total_tax / max(agi, 1), 4),
        "cap_gain_netting": cg,
        "standard_deduction": standard_deduction,
    }


def _compute_stacked_preferential_tax(
    ordinary_taxable: float,
    preferential_income: float,
    ltcg_brackets: list[dict],
) -> float:
    """
    Compute tax on LTCG + qualified dividends at preferential rates.

    The preferential income is "stacked" on top of ordinary taxable income,
    meaning it fills brackets starting at the ordinary_taxable level.
    """
    if preferential_income <= 0:
        return 0.0

    # Total income (ordinary + preferential) determines where we are in brackets
    total_top = ordinary_taxable + preferential_income

    # Tax on total at preferential rates minus tax on ordinary portion alone
    tax_total = _tax_from_brackets(total_top, ltcg_brackets)
    tax_ordinary_only = _tax_from_brackets(ordinary_taxable, ltcg_brackets)

    return max(tax_total - tax_ordinary_only, 0)


# ---------------------------------------------------------------------------
# State tax (Oregon)
# ---------------------------------------------------------------------------

def _compute_state_tax(
    agi: float,
    ss_taxable: float,
    federal_std_ded: float,
    tax_profile: dict,
) -> float:
    """
    Compute Oregon state income tax.

    Oregon specifics:
      - Social Security is fully exempt
      - Has its own standard deduction ($5,010 MFJ 2025)
      - No sales tax
      - Uses federal AGI as starting point, then subtracts SS
    """
    or_config = tax_profile.get("oregon_tax", {})
    if not or_config:
        return 0.0

    # Oregon taxable income: AGI minus SS (exempt) minus OR standard deduction
    or_std_ded = or_config.get("standard_deduction_mfj", 5_010)
    or_taxable = max(agi - ss_taxable - or_std_ded, 0)

    or_brackets = or_config.get("brackets_mfj_2025", [])
    return _tax_from_brackets(or_taxable, or_brackets)


# ---------------------------------------------------------------------------
# MAGI and IRMAA
# ---------------------------------------------------------------------------

def compute_magi(
    agi: float,
    tax_exempt_interest: float = 0.0,
) -> float:
    """
    Compute Modified Adjusted Gross Income for IRMAA purposes.

    MAGI = AGI + tax-exempt interest income
    (For most retirees without muni bonds, MAGI ≈ AGI)
    """
    return agi + tax_exempt_interest


def compute_irmaa_impact(
    magi: float,
    tax_profile: Optional[dict] = None,
    num_people: int = 2,
) -> dict:
    """
    Determine IRMAA tier and compute annual surcharge for Medicare Parts B & D.

    Parameters
    ----------
    magi : MAGI from 2 years prior (lookback rule)
    tax_profile : contains irmaa_thresholds_mfj_2025
    num_people : number of Medicare beneficiaries (2 for couple)

    Returns
    -------
    dict: {tier, magi_lower, magi_upper, part_b_surcharge_annual,
           part_d_surcharge_annual, total_annual_cost, headroom_to_next_tier}
    """
    if tax_profile is None:
        tax_profile = _default_tax_profile()

    tiers = tax_profile.get("irmaa_thresholds_mfj_2025", [])
    if not tiers:
        return {"tier": 0, "total_annual_cost": 0, "headroom_to_next_tier": None}

    matched_tier = 0
    result = {
        "tier": 0,
        "magi_lower": 0,
        "magi_upper": tiers[0].get("magi_upper", 0),
        "part_b_surcharge_annual": 0,
        "part_d_surcharge_annual": 0,
        "total_annual_cost": 0,
        "headroom_to_next_tier": None,
    }

    for i, t in enumerate(tiers):
        lo = t["magi_lower"]
        hi = t["magi_upper"]

        if hi is None:
            # Top tier — applies if MAGI >= lo
            if magi >= lo:
                matched_tier = i
        else:
            if lo <= magi < hi:
                matched_tier = i

    t = tiers[matched_tier]
    part_b = t.get("part_b_surcharge", 0) * num_people
    part_d = t.get("part_d_surcharge", 0) * num_people

    # Headroom: how much MAGI can increase before hitting next tier
    headroom = None
    if matched_tier + 1 < len(tiers):
        next_lo = tiers[matched_tier + 1]["magi_lower"]
        headroom = next_lo - magi

    return {
        "tier": matched_tier,
        "magi": round(magi, 2),
        "magi_lower": t["magi_lower"],
        "magi_upper": t["magi_upper"],
        "part_b_surcharge_annual": round(part_b, 2),
        "part_d_surcharge_annual": round(part_d, 2),
        "total_annual_cost": round(part_b + part_d, 2),
        "headroom_to_next_tier": round(headroom, 2) if headroom is not None else None,
    }


# ---------------------------------------------------------------------------
# simulate_year_tax_effects — all-in-one for projection rows
# ---------------------------------------------------------------------------

def simulate_year_tax_effects(
    projection_row: dict,
    ss_annual: float,
    qualified_divs: float,
    ordinary_divs: float,
    cap_loss_carryforward: float,
    tax_profile: dict,
    taxable_gain_pct: float = 0.02,
    magi_lookback: Optional[float] = None,
) -> dict:
    """
    Compute full tax effects for one projection year.

    Pulls IRA withdrawals from projection_row, estimates realized gains
    from taxable account, computes federal/state taxes, and checks IRMAA.

    Returns dict with: taxes (from compute_taxes), irmaa (from compute_irmaa_impact),
    updated_cap_loss_carryforward
    """
    withdrawals = projection_row.get("withdrawals", {})

    # IRA distributions = ordinary income
    ira_distributions = sum(v for k, v in withdrawals.items()
                            if "IRA" in k.upper())

    # Estimated realized gains from taxable account activity
    taxable_bal = sum(v for k, v in projection_row.get("account_balances", {}).items()
                      if "TAXABLE" in k.upper())
    est_ltcg = taxable_bal * taxable_gain_pct

    # Ordinary income = IRA distributions + ordinary dividends
    ordinary_income = ira_distributions + ordinary_divs

    # Compute taxes
    std_ded = tax_profile.get("effective_standard_deduction", 33_100)
    taxes = compute_taxes(
        ordinary_income=ordinary_income,
        long_term_cap_gains=est_ltcg,
        qualified_dividends=qualified_divs,
        short_term_cap_gains=0,
        cap_loss_carryforward=cap_loss_carryforward,
        ss_annual=ss_annual,
        standard_deduction=std_ded,
        tax_profile=tax_profile,
    )

    # IRMAA check (uses MAGI from 2 years prior; use current year as proxy
    # if no lookback provided)
    magi_for_irmaa = magi_lookback if magi_lookback is not None else taxes["agi"]
    magi = compute_magi(magi_for_irmaa)
    irmaa = compute_irmaa_impact(magi, tax_profile)

    # Update carryforward
    new_cf = taxes["cap_gain_netting"]["remaining_carryforward"]

    return {
        "year": projection_row.get("year"),
        "taxes": taxes,
        "irmaa": irmaa,
        "updated_cap_loss_carryforward": new_cf,
        "total_tax_plus_irmaa": round(taxes["total_tax"] + irmaa["total_annual_cost"], 2),
    }


# ---------------------------------------------------------------------------
# Default tax profile loader
# ---------------------------------------------------------------------------

_CACHED_PROFILE: Optional[dict] = None


def _default_tax_profile() -> dict:
    global _CACHED_PROFILE
    if _CACHED_PROFILE is None:
        path = Path(__file__).parent / "data" / "tax_profile.json"
        if path.exists():
            with open(path) as f:
                _CACHED_PROFILE = json.load(f)
        else:
            _CACHED_PROFILE = {}
    return _CACHED_PROFILE


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from pathlib import Path as P

    sys.path.insert(0, str(P(__file__).parent))
    from ingest import ingest_all, DEFAULT_PATHS
    from deterministic import build_projection_from_ingested

    base = P(sys.argv[1]) if len(sys.argv) > 1 else P(".")
    paths = {k: str(base / v) for k, v in DEFAULT_PATHS.items()}
    ingested = ingest_all(paths)
    tp = ingested["tax_profile"]

    scenarios = build_projection_from_ingested(ingested, horizon=20)
    central = scenarios["central"]

    print("\n" + "=" * 95)
    print("  TAX & IRMAA ANALYSIS — CENTRAL SCENARIO (4.5%)")
    print("=" * 95)
    print(f"\n{'Year':>6} {'Age':>4} {'AGI':>10} {'Fed Tax':>10} {'OR Tax':>9}"
          f" {'Total Tax':>10} {'Eff Rate':>9} {'IRMAA':>8} {'Headroom':>10}")
    print("─" * 95)

    cf = 3_000.0  # initial carryforward
    ss = tp.get("ss_combined_annual", 56_000)
    qual_div = 6_700
    ord_div = 3_300
    prev_agi = tp.get("agi_prior_year", 104_000)
    magi_history = [prev_agi, prev_agi]  # 2-year lookback seed

    for row in central["projection"]:
        effects = simulate_year_tax_effects(
            row, ss, qual_div, ord_div, cf, tp,
            magi_lookback=magi_history[-2] if len(magi_history) >= 2 else None,
        )
        t = effects["taxes"]
        ir = effects["irmaa"]
        cf = effects["updated_cap_loss_carryforward"]
        magi_history.append(t["agi"])

        headroom_str = f"${ir['headroom_to_next_tier']:>8,.0f}" if ir["headroom_to_next_tier"] is not None else "     N/A"
        print(f"{row['year']:>6} {row['age']:>4} ${t['agi']:>9,.0f} ${t['federal_total']:>9,.0f}"
              f" ${t['state_tax']:>8,.0f} ${t['total_tax']:>9,.0f}"
              f"   {t['effective_rate']:>5.1%}  ${ir['total_annual_cost']:>6,.0f}"
              f"  {headroom_str}")

    print()
