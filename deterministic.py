"""
deterministic.py — Deterministic projection engine for RICS.

Public API:
    project_deterministic(...)  -> list[dict]   # year-by-year projection
    project_three_scenarios(...) -> dict         # conservative / central / growth
    compute_agi_projection(...)  -> list[dict]   # AGI + SS taxation per year
    build_projection_from_ingested(ingested) -> dict  # convenience entry point
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# IRS Uniform Lifetime Table (age → divisor) — for RMD calculation
# Ages 72–100+; source: IRS Publication 590-B (2024 updated table)
# ---------------------------------------------------------------------------

UNIFORM_LIFETIME_TABLE = {
    72: 27.4, 73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9,
    78: 22.0, 79: 21.1, 80: 20.2, 81: 19.4, 82: 18.5, 83: 17.7,
    84: 16.8, 85: 16.0, 86: 15.2, 87: 14.4, 88: 13.7, 89: 12.9,
    90: 12.2, 91: 11.5, 92: 10.8, 93: 10.1, 94:  9.5, 95:  8.9,
    96:  8.4, 97:  7.8, 98:  7.3, 99:  6.8, 100: 6.4,
    101: 6.0, 102: 5.6, 103: 5.2, 104: 4.9, 105: 4.6,
}


def _rmd_divisor(age: int) -> float:
    """Look up the Uniform Lifetime Table divisor; cap at age 105."""
    if age < 72:
        return 0.0  # no RMD required
    return UNIFORM_LIFETIME_TABLE.get(age, UNIFORM_LIFETIME_TABLE[min(age, 105)])


# ---------------------------------------------------------------------------
# RMD computation
# ---------------------------------------------------------------------------

def compute_rmd(trad_ira_balance: float, owner_age: int) -> float:
    """
    Compute Required Minimum Distribution for a traditional IRA.

    Uses the Uniform Lifetime Table.  RMD starts at age 73 under SECURE 2.0
    for those born 1951–1959 (our owner is 72 in 2025 → born ~1953).
    """
    if owner_age < 73:
        return 0.0
    divisor = _rmd_divisor(owner_age)
    if divisor <= 0:
        return trad_ira_balance  # full distribution
    return trad_ira_balance / divisor


def compute_inherited_ira_schedule(
    balance: float,
    years_remaining: int,
    growth_rate: float = 0.035,
) -> list[float]:
    """
    Compute annual distributions from an inherited IRA under the 10-year rule.

    Strategy: level real distributions that exhaust the account by deadline.
    We solve for a level withdrawal W such that the account reaches ~0
    at the end of `years_remaining` years, assuming growth_rate on remaining balance.

    Uses annuity-due formula: W = B * r / (1 - (1+r)^-n)  if r > 0
    """
    if years_remaining <= 0:
        return [balance]
    if years_remaining == 1:
        return [balance * (1 + growth_rate)]
    if growth_rate == 0:
        w = balance / years_remaining
        return [w] * years_remaining

    r = growth_rate
    n = years_remaining
    # PMT for ordinary annuity (end-of-year withdrawal)
    w = balance * r * (1 + r) ** n / ((1 + r) ** n - 1)

    schedule = []
    bal = balance
    for _ in range(n):
        bal = bal * (1 + r)
        withdrawal = min(w, bal)
        bal -= withdrawal
        schedule.append(round(withdrawal, 2))

    # Adjust last year for any residual
    if bal > 1.0:
        schedule[-1] += round(bal, 2)

    return schedule


# ---------------------------------------------------------------------------
# Core deterministic projection
# ---------------------------------------------------------------------------

def project_deterministic(
    initial_balances: Dict[str, float],
    annual_withdrawals: Dict[int, Dict[str, float]],
    growth_rates: Dict[str, float],
    horizon: int,
    start_year: int = 2025,
    owner_start_age: int = 72,
    inflation_rate: float = 0.025,
) -> list[dict]:
    """
    Deterministic year-by-year projection.

    Parameters
    ----------
    initial_balances : {account_id: balance} at start of projection
    annual_withdrawals : {year: {account_id: withdrawal_amount}}
        Pre-computed withdrawals per account per year.
        If a year/account is missing, withdrawal = 0.
    growth_rates : {account_id: annual_return}
        Blended return rate per account.
    horizon : number of years to project
    start_year : calendar year of projection start
    owner_start_age : age of primary owner at start_year

    Returns
    -------
    List of dicts, one per year:
      {year, age, account_balances: {id: bal}, total_balance,
       withdrawals: {id: amt}, total_withdrawal, rmd_amount,
       inherited_ira_withdrawal, cumulative_withdrawals}
    """
    balances = dict(initial_balances)
    results = []
    cumulative_withdrawals = 0.0

    for y in range(horizon):
        year = start_year + y
        age = owner_start_age + y

        # Get this year's planned withdrawals
        yr_withdrawals = annual_withdrawals.get(year, {})

        # Grow balances, then withdraw (end-of-year convention)
        new_balances = {}
        actual_withdrawals = {}
        total_withdrawal = 0.0

        for acct_id, bal in balances.items():
            r = growth_rates.get(acct_id, 0.0)
            grown = bal * (1 + r)
            w = yr_withdrawals.get(acct_id, 0.0)
            # Can't withdraw more than available (floor at 0)
            actual_w = min(w, max(grown, 0.0))
            new_bal = grown - actual_w
            new_balances[acct_id] = max(new_bal, 0.0)
            actual_withdrawals[acct_id] = actual_w
            total_withdrawal += actual_w

        cumulative_withdrawals += total_withdrawal

        # Identify RMD and inherited IRA components
        rmd_amount = sum(v for k, v in actual_withdrawals.items()
                         if "RIRA" in k or "rmd" in k.lower())
        inherited_w = sum(v for k, v in actual_withdrawals.items()
                          if "IIRA" in k or "inherited" in k.lower())

        results.append({
            "year": year,
            "age": age,
            "account_balances": dict(new_balances),
            "total_balance": sum(new_balances.values()),
            "withdrawals": actual_withdrawals,
            "total_withdrawal": round(total_withdrawal, 2),
            "rmd_amount": round(rmd_amount, 2),
            "inherited_ira_withdrawal": round(inherited_w, 2),
            "cumulative_withdrawals": round(cumulative_withdrawals, 2),
        })

        balances = new_balances

    return results


# ---------------------------------------------------------------------------
# AGI projection helper
# ---------------------------------------------------------------------------

def compute_agi_projection(
    projection: list[dict],
    ss_annual: float,
    qualified_divs: float,
    ordinary_divs: float,
    tax_profile: dict,
    taxable_gain_pct: float = 0.02,
) -> list[dict]:
    """
    Estimate AGI for each projection year.

    AGI components:
      1. IRA withdrawals (fully ordinary income)
      2. Inherited IRA withdrawals (fully ordinary income)
      3. Social Security taxable portion (up to 85%)
      4. Qualified dividends (taxed at preferential rates but included in AGI)
      5. Ordinary dividends / interest
      6. Estimated taxable realized gains from taxable account
      7. Less: standard deduction (for taxable income, not AGI, but we track both)

    Parameters
    ----------
    taxable_gain_pct : assumed % of taxable account balance realized as gains/year
    """
    ss_base = tax_profile.get("ss_taxation", {}).get("base_amount_mfj", 32000)
    ss_add = tax_profile.get("ss_taxation", {}).get("additional_amount_mfj", 44000)
    std_ded = tax_profile.get("effective_standard_deduction", 33100)

    results = []
    for row in projection:
        # IRA + Inherited withdrawals = ordinary income
        ira_w = sum(v for k, v in row["withdrawals"].items()
                    if "RIRA" in k or "IRA" in k.upper())

        # Estimated realized gains from taxable account
        taxable_bal = sum(v for k, v in row["account_balances"].items()
                          if "TAXABLE" in k.upper())
        est_realized_gains = taxable_bal * taxable_gain_pct

        # Provisional income for SS taxation
        non_ss_income = ira_w + qualified_divs + ordinary_divs + est_realized_gains
        provisional = non_ss_income + (ss_annual * 0.5)

        # SS taxable portion (standard formula)
        if provisional <= ss_base:
            ss_taxable = 0.0
        elif provisional <= ss_add:
            ss_taxable = min(0.50 * (provisional - ss_base), 0.50 * ss_annual)
        else:
            tier1 = 0.50 * (ss_add - ss_base)
            tier2 = 0.85 * (provisional - ss_add)
            ss_taxable = min(tier1 + tier2, 0.85 * ss_annual)

        agi = non_ss_income + ss_taxable
        taxable_income = max(agi - std_ded, 0.0)

        results.append({
            "year": row["year"],
            "age": row["age"],
            "ira_withdrawals": round(ira_w, 2),
            "ss_taxable": round(ss_taxable, 2),
            "qualified_divs": qualified_divs,
            "ordinary_divs": ordinary_divs,
            "est_realized_gains": round(est_realized_gains, 2),
            "agi": round(agi, 2),
            "taxable_income": round(taxable_income, 2),
            "std_deduction": std_ded,
        })

    return results


# ---------------------------------------------------------------------------
# Three-scenario builder
# ---------------------------------------------------------------------------

_SCENARIO_RATES = {
    "conservative": 0.030,
    "central":      0.045,
    "growth":       0.060,
}


def _build_withdrawal_schedule(
    initial_balances: Dict[str, float],
    cashflow: list[dict],
    tax_profile: dict,
    constraints: dict,
    horizon: int,
    start_year: int,
    owner_start_age: int,
    scenario_rate: float,
) -> Dict[int, Dict[str, float]]:
    """
    Build a year-by-year withdrawal schedule that covers expenses.

    Strategy:
      1. Inherited IRA: level distributions over remaining 10-year window
      2. Traditional IRA: max(RMD, needed amount) each year
      3. Taxable: residual needed after IRA + inherited + SS + dividends
    """
    # Cashflow analysis
    annual_expenses = sum(r["amount"] for r in cashflow
                          if r["category"] == "expense" and r["frequency"] == "annual")
    ss_income = sum(r["amount"] for r in cashflow
                    if r.get("subcategory") == "social_security")
    div_income = sum(r["amount"] for r in cashflow
                     if r["category"] == "income" and r.get("subcategory") != "social_security"
                     and r.get("taxable", "") != "n/a")

    # Lumpy expenses by year
    lumpy: Dict[int, float] = {}
    for r in cashflow:
        if "one_time" in r.get("frequency", ""):
            yr = r["year"]
            lumpy[yr] = lumpy.get(yr, 0) + r["amount"]

    # Inherited IRA schedule
    deadline = constraints.get("inherited_ira_deadline_year", 2033)
    iira_ids = [k for k in initial_balances if "IIRA" in k]
    iira_balance = sum(initial_balances.get(k, 0) for k in iira_ids)
    iira_years = max(deadline - start_year, 1)
    iira_schedule = compute_inherited_ira_schedule(
        iira_balance, iira_years, scenario_rate
    )

    # Build year-by-year
    inflation = 0.025
    schedule: Dict[int, Dict[str, float]] = {}
    trad_bal = sum(v for k, v in initial_balances.items() if "RIRA" in k)

    for y in range(horizon):
        year = start_year + y
        age = owner_start_age + y
        infl_factor = (1 + inflation) ** y

        # Expenses this year
        expenses = annual_expenses * infl_factor + lumpy.get(year, 0)

        # Income that doesn't require withdrawal
        passive_income = ss_income * infl_factor + div_income

        # Net needed from portfolio
        net_needed = max(expenses - passive_income, 0)

        yr_w: Dict[str, float] = {}

        # 1. Inherited IRA
        iira_w = 0.0
        if y < len(iira_schedule):
            iira_w = iira_schedule[y]
        for iid in iira_ids:
            yr_w[iid] = iira_w / max(len(iira_ids), 1)

        remaining = max(net_needed - iira_w, 0)

        # 2. Traditional IRA: at least RMD
        rmd = compute_rmd(trad_bal, age)
        trad_w = max(rmd, min(remaining, trad_bal * 0.8))  # don't over-drain
        rira_ids = [k for k in initial_balances if "RIRA" in k]
        for rid in rira_ids:
            yr_w[rid] = trad_w / max(len(rira_ids), 1)

        remaining = max(remaining - trad_w, 0)
        # Approximate IRA balance forward for next year's RMD
        trad_bal = max(trad_bal * (1 + scenario_rate) - trad_w, 0)

        # 3. Taxable: residual
        taxable_ids = [k for k in initial_balances if "TAXABLE" in k]
        if taxable_ids and remaining > 0:
            per_acct = remaining / len(taxable_ids)
            for tid in taxable_ids:
                yr_w[tid] = per_acct

        schedule[year] = yr_w

    return schedule


def project_three_scenarios(
    initial_balances: Dict[str, float],
    cashflow: list[dict],
    tax_profile: dict,
    constraints: dict,
    horizon: int = 20,
    start_year: int = 2025,
    owner_start_age: int = 72,
) -> dict:
    """
    Run conservative, central, and growth deterministic projections.

    Returns dict with keys: 'conservative', 'central', 'growth',
    each containing: projection (list[dict]), agi_projection (list[dict]),
    summary metrics.
    """
    ss_annual = sum(r["amount"] for r in cashflow
                    if r.get("subcategory") == "social_security")
    qual_div = sum(r["amount"] for r in cashflow
                   if r.get("subcategory") == "qualified_dividends")
    ord_div = sum(r["amount"] for r in cashflow
                  if r.get("subcategory") == "ordinary_dividends")

    results = {}

    for scenario_name, base_rate in _SCENARIO_RATES.items():
        # Build per-account growth rates (simplified: same rate for all)
        growth_rates = {acct_id: base_rate for acct_id in initial_balances}

        # Build withdrawal schedule
        withdrawal_schedule = _build_withdrawal_schedule(
            initial_balances, cashflow, tax_profile, constraints,
            horizon, start_year, owner_start_age, base_rate,
        )

        # Run projection
        projection = project_deterministic(
            initial_balances=initial_balances,
            annual_withdrawals=withdrawal_schedule,
            growth_rates=growth_rates,
            horizon=horizon,
            start_year=start_year,
            owner_start_age=owner_start_age,
        )

        # AGI projection
        agi_proj = compute_agi_projection(
            projection, ss_annual, qual_div, ord_div, tax_profile
        )

        # Summary metrics
        final = projection[-1] if projection else {}
        total_withdrawn = final.get("cumulative_withdrawals", 0)

        results[scenario_name] = {
            "rate": base_rate,
            "projection": projection,
            "agi_projection": agi_proj,
            "summary": {
                "end_balance": round(final.get("total_balance", 0), 0),
                "total_withdrawals": round(total_withdrawn, 0),
                "final_year": start_year + horizon - 1,
                "final_age": owner_start_age + horizon - 1,
            },
        }

    return results


# ---------------------------------------------------------------------------
# Convenience: build from ingested data
# ---------------------------------------------------------------------------

def build_projection_from_ingested(ingested: dict, horizon: int = 20) -> dict:
    """
    One-call entry point: takes output of ingest_all, returns three-scenario projection.
    """
    accounts = ingested["accounts"]
    totals = ingested["totals"]

    # Initial balances per account_id
    initial_balances = totals["by_account_id"]

    return project_three_scenarios(
        initial_balances=initial_balances,
        cashflow=ingested["cashflow"],
        tax_profile=ingested["tax_profile"],
        constraints=ingested["constraints"],
        horizon=horizon,
        start_year=2025,
        owner_start_age=ingested["tax_profile"]["ages"][0],
    )


# ---------------------------------------------------------------------------
# CLI / display
# ---------------------------------------------------------------------------

def print_projection_table(scenarios: dict) -> None:
    """Print a compact comparison table for all three scenarios."""
    print("\n" + "=" * 100)
    print("  DETERMINISTIC PROJECTION — 3-SCENARIO COMPARISON")
    print("=" * 100)

    # Header
    print(f"\n{'Year':>6} {'Age':>4}  ", end="")
    for s in ("conservative", "central", "growth"):
        print(f"│ {'Balance':>13} {'Wdraw':>10} {'AGI':>10}  ", end="")
    print(f"│ {'RMD':>9}")
    print("─" * 6 + " " + "─" * 4 + "  " + ("├" + "─" * 38 + "  ") * 3 + "├" + "─" * 10)

    # Use central scenario for year/age/RMD
    central_proj = scenarios["central"]["projection"]
    central_agi = scenarios["central"]["agi_projection"]
    horizon = len(central_proj)

    for i in range(horizon):
        year = central_proj[i]["year"]
        age = central_proj[i]["age"]
        rmd = central_proj[i]["rmd_amount"]

        print(f"{year:>6} {age:>4}  ", end="")
        for s in ("conservative", "central", "growth"):
            bal = scenarios[s]["projection"][i]["total_balance"]
            wdraw = scenarios[s]["projection"][i]["total_withdrawal"]
            agi = scenarios[s]["agi_projection"][i]["agi"]
            print(f"│ ${bal:>12,.0f} ${wdraw:>9,.0f} ${agi:>9,.0f}  ", end="")
        print(f"│ ${rmd:>8,.0f}")

    # Summary
    print("\n── Summary ──")
    for s in ("conservative", "central", "growth"):
        sm = scenarios[s]["summary"]
        rate = scenarios[s]["rate"]
        print(f"  {s:14s} ({rate:.1%}):  End balance ${sm['end_balance']:>12,.0f}  "
              f"  Total withdrawn ${sm['total_withdrawals']:>12,.0f}  "
              f"  Final age {sm['final_age']}")
    print()


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from ingest import ingest_all, DEFAULT_PATHS

    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    paths = {k: str(base / v) for k, v in DEFAULT_PATHS.items()}
    ingested = ingest_all(paths)

    scenarios = build_projection_from_ingested(ingested, horizon=20)
    print_projection_table(scenarios)
