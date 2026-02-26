"""
mc_sim.py — Monte Carlo simulation engine for RICS.

Public API:
    simulate_monte_carlo(...)   -> MCResults
    summarize_mc_results(...)   -> dict (JSON-friendly)
    run_mc_from_ingested(...)   -> dict (convenience entry point)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MCResults:
    """Container for Monte Carlo simulation outputs."""
    n_sims: int
    horizon: int
    start_year: int

    # Shape: (n_sims, horizon) — total portfolio value at each year-end
    portfolio_paths: np.ndarray

    # Shape: (n_sims, horizon) — annual withdrawal taken
    withdrawal_paths: np.ndarray

    # Shape: (n_sims, horizon) — AGI each year
    agi_paths: np.ndarray

    # Shape: (n_sims, horizon) — boolean: did IRMAA trigger?
    irmaa_trigger_paths: np.ndarray

    # Per-sim terminal values
    terminal_values: np.ndarray     # shape: (n_sims,)
    cumulative_withdrawals: np.ndarray  # shape: (n_sims,)

    # Ruin tracking
    ruin_flags: np.ndarray          # shape: (n_sims,) — True if ruined
    ruin_year: np.ndarray           # shape: (n_sims,) — year of ruin (or horizon+1)

    # Timing
    elapsed_seconds: float = 0.0

    # Asset class names for reference
    asset_classes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Return generation
# ---------------------------------------------------------------------------

def generate_correlated_returns(
    mu: np.ndarray,
    sigma: np.ndarray,
    corr_matrix: np.ndarray,
    n_sims: int,
    horizon: int,
    seed: Optional[int] = None,
) -> np.ndarray:
    """
    Generate correlated annual returns for multiple asset classes.

    Parameters
    ----------
    mu : (n_assets,) expected annual returns
    sigma : (n_assets,) annual standard deviations
    corr_matrix : (n_assets, n_assets) correlation matrix
    n_sims : number of simulations
    horizon : number of years

    Returns
    -------
    np.ndarray shape (n_sims, horizon, n_assets) — annual returns
    """
    rng = np.random.default_rng(seed)
    n_assets = len(mu)

    # Build covariance matrix from correlation + sigma
    cov = np.outer(sigma, sigma) * corr_matrix

    # Draw all samples at once: (n_sims * horizon, n_assets)
    raw = rng.multivariate_normal(mu, cov, size=(n_sims * horizon))

    # Reshape to (n_sims, horizon, n_assets)
    returns = raw.reshape(n_sims, horizon, n_assets)

    return returns


def generate_inflation_series(
    mu_infl: float,
    sigma_infl: float,
    n_sims: int,
    horizon: int,
    seed: Optional[int] = None,
) -> np.ndarray:
    """
    Generate annual inflation rates.

    Returns shape (n_sims, horizon).
    """
    rng = np.random.default_rng(seed)
    # Clamp inflation to [0%, 15%] to avoid absurd scenarios
    raw = rng.normal(mu_infl, sigma_infl, size=(n_sims, horizon))
    return np.clip(raw, 0.0, 0.15)


# ---------------------------------------------------------------------------
# Core simulation loop
# ---------------------------------------------------------------------------

def simulate_monte_carlo(
    initial_balances: Dict[str, float],
    allocation_weights: Dict[str, float],
    return_assumptions: Dict[str, dict],
    cashflow: list[dict],
    tax_profile: dict,
    constraints: dict,
    n_sims: int = 10_000,
    horizon: int = 20,
    start_year: int = 2025,
    owner_start_age: int = 72,
    ruin_threshold: float = 50_000,
    irmaa_threshold: float = 206_000,
    seed: Optional[int] = None,
) -> MCResults:
    """
    Run Monte Carlo simulation of retirement portfolio.

    Uses vectorized return generation and a simplified annual-step model:
    1. Generate correlated returns for all sims × years
    2. For each year: grow portfolio, compute expenses, withdraw, track metrics

    The withdrawal logic is simplified for MC speed: instead of full lot-level
    sequencing (which is O(lots) per sim per year), we use the blended
    withdrawal-from-total approach and estimate AGI analytically.

    Parameters
    ----------
    initial_balances : {account_id: balance}
    allocation_weights : {asset_class: weight} — portfolio-level allocation
    return_assumptions : {asset_class: {mean, std}} from constraints.json
    cashflow : from cashflow_plan.csv
    tax_profile : from tax_profile.json
    constraints : from constraints.json
    ruin_threshold : portfolio below this = "ruin"
    irmaa_threshold : MAGI above this triggers IRMAA
    """
    t0 = time.perf_counter()

    # ── Parse inputs ──
    asset_classes = sorted(allocation_weights.keys())
    n_assets = len(asset_classes)
    weights = np.array([allocation_weights[ac] for ac in asset_classes])
    weights = weights / weights.sum()  # normalize

    mu = np.array([return_assumptions[ac]["mean"] for ac in asset_classes])
    sigma = np.array([return_assumptions[ac]["std"] for ac in asset_classes])

    # Default correlation matrix: moderate equity correlation, low bond-equity
    corr = _build_default_correlation(asset_classes)

    # Inflation parameters
    mc_cfg = constraints.get("monte_carlo", {})
    infl_cfg = mc_cfg.get("inflation", {"mean": 0.025, "std": 0.01})

    # ── Generate all random draws upfront (vectorized) ──
    returns = generate_correlated_returns(mu, sigma, corr, n_sims, horizon, seed)
    inflation = generate_inflation_series(
        infl_cfg["mean"], infl_cfg["std"], n_sims, horizon,
        seed=(seed + 1) if seed is not None else None,
    )

    # ── Cashflow parsing ──
    annual_expenses_base = sum(
        r["amount"] for r in cashflow
        if r["category"] == "expense" and r["frequency"] == "annual"
    )
    ss_income_base = sum(
        r["amount"] for r in cashflow
        if r.get("subcategory") == "social_security"
    )
    div_income = sum(
        r["amount"] for r in cashflow
        if r["category"] == "income" and r.get("subcategory") != "social_security"
        and r.get("taxable", "") != "n/a"
    )

    # Lumpy expenses by year offset
    lumpy_by_offset: Dict[int, float] = {}
    for r in cashflow:
        if "one_time" in r.get("frequency", ""):
            offset = r["year"] - start_year
            if 0 <= offset < horizon:
                lumpy_by_offset[offset] = lumpy_by_offset.get(offset, 0) + r["amount"]

    # ── IRA / inherited IRA parameters ──
    total_portfolio = sum(initial_balances.values())
    ira_frac = sum(v for k, v in initial_balances.items() if "RIRA" in k) / total_portfolio if total_portfolio > 0 else 0
    iira_balance = sum(v for k, v in initial_balances.items() if "IIRA" in k)
    iira_deadline_offset = constraints.get("inherited_ira_deadline_year", 2033) - start_year

    # RMD divisors (simplified inline)
    rmd_divisors = {
        72: 27.4, 73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9,
        78: 22.0, 79: 21.1, 80: 20.2, 81: 19.4, 82: 18.5, 83: 17.7,
        84: 16.8, 85: 16.0, 86: 15.2, 87: 14.4, 88: 13.7, 89: 12.9,
        90: 12.2, 91: 11.5, 92: 10.8, 93: 10.1, 94: 9.5, 95: 8.9,
    }

    # SS taxation helper
    ss_base = tax_profile.get("ss_taxation", {}).get("base_amount_mfj", 32_000)
    ss_add = tax_profile.get("ss_taxation", {}).get("additional_amount_mfj", 44_000)
    std_ded = tax_profile.get("effective_standard_deduction", 33_100)

    # ── Allocate output arrays ──
    portfolio_paths = np.zeros((n_sims, horizon))
    withdrawal_paths = np.zeros((n_sims, horizon))
    agi_paths = np.zeros((n_sims, horizon))
    irmaa_triggers = np.zeros((n_sims, horizon), dtype=bool)
    ruin_flags = np.zeros(n_sims, dtype=bool)
    ruin_year = np.full(n_sims, horizon + 1, dtype=int)

    # ── Simulation loop ──
    # Vectorized across sims for each year
    balances = np.full(n_sims, total_portfolio, dtype=np.float64)
    ira_balances = np.full(n_sims, total_portfolio * ira_frac, dtype=np.float64)
    iira_balances = np.full(n_sims, iira_balance, dtype=np.float64)
    cumul_infl = np.ones(n_sims, dtype=np.float64)

    for y in range(horizon):
        age = owner_start_age + y

        # 1. Compute blended portfolio return for this year
        # returns[:, y, :] is (n_sims, n_assets)
        blended_return = (returns[:, y, :] * weights).sum(axis=1)  # (n_sims,)

        # 2. Grow all balances
        balances *= (1 + blended_return)
        ira_balances *= (1 + blended_return)
        iira_balances *= (1 + blended_return)

        # 3. Cumulative inflation
        cumul_infl *= (1 + inflation[:, y])

        # 4. Compute expenses for this year
        expenses = annual_expenses_base * cumul_infl
        expenses += lumpy_by_offset.get(y, 0.0)  # scalar broadcast

        # 5. Income that doesn't require withdrawal
        ss_income = ss_income_base * cumul_infl
        passive_income = ss_income + div_income  # div_income not inflation-adj

        # 6. Net withdrawal needed
        net_needed = np.maximum(expenses - passive_income, 0)

        # 7. RMD floor (on IRA portion)
        rmd_amount = np.zeros(n_sims)
        if age >= 73:
            divisor = rmd_divisors.get(age, max(27.4 - (age - 72) * 0.9, 1.0))
            rmd_amount = ira_balances / divisor

        # 8. Inherited IRA distribution
        iira_dist = np.zeros(n_sims)
        if y < iira_deadline_offset and iira_deadline_offset > 0:
            years_left = iira_deadline_offset - y
            iira_dist = iira_balances / max(years_left, 1)
        elif y == iira_deadline_offset:
            iira_dist = iira_balances.copy()

        # 9. Total withdrawal = max(net_needed, RMD + iira_dist)
        mandatory = rmd_amount + iira_dist
        total_withdrawal = np.maximum(net_needed, mandatory)

        # Cap at available balance
        total_withdrawal = np.minimum(total_withdrawal, np.maximum(balances, 0))

        # 10. Update balances
        balances -= total_withdrawal
        ira_withdraw = np.minimum(rmd_amount, ira_balances)
        ira_balances -= ira_withdraw
        ira_balances = np.maximum(ira_balances, 0)
        iira_balances -= np.minimum(iira_dist, iira_balances)
        iira_balances = np.maximum(iira_balances, 0)

        # 11. Estimate AGI for IRMAA tracking
        ira_total_dist = ira_withdraw + np.minimum(iira_dist, total_withdrawal)
        other_income = ira_total_dist + div_income
        provisional = other_income + 0.5 * ss_income

        ss_taxable = np.where(
            provisional <= ss_base, 0.0,
            np.where(
                provisional <= ss_add,
                np.minimum(0.5 * (provisional - ss_base), 0.5 * ss_income),
                np.minimum(
                    0.5 * (ss_add - ss_base) + 0.85 * (provisional - ss_add),
                    0.85 * ss_income,
                ),
            ),
        )
        agi = other_income + ss_taxable

        # 12. Store results
        portfolio_paths[:, y] = balances
        withdrawal_paths[:, y] = total_withdrawal
        agi_paths[:, y] = agi
        irmaa_triggers[:, y] = agi > irmaa_threshold

        # 13. Ruin check
        newly_ruined = (balances < ruin_threshold) & ~ruin_flags
        ruin_flags |= newly_ruined
        ruin_year = np.where(newly_ruined, np.minimum(ruin_year, y), ruin_year)

    elapsed = time.perf_counter() - t0

    return MCResults(
        n_sims=n_sims,
        horizon=horizon,
        start_year=start_year,
        portfolio_paths=portfolio_paths,
        withdrawal_paths=withdrawal_paths,
        agi_paths=agi_paths,
        irmaa_trigger_paths=irmaa_triggers,
        terminal_values=balances,
        cumulative_withdrawals=withdrawal_paths.sum(axis=1),
        ruin_flags=ruin_flags,
        ruin_year=ruin_year,
        elapsed_seconds=elapsed,
        asset_classes=asset_classes,
    )


# ---------------------------------------------------------------------------
# Default correlation matrix
# ---------------------------------------------------------------------------

def _build_default_correlation(asset_classes: list[str]) -> np.ndarray:
    """
    Build a reasonable default correlation matrix.

    Assumptions:
      equity ↔ equity: 0.85
      equity ↔ bond: -0.10
      equity ↔ mmf: 0.05
      bond ↔ mmf: 0.20
    """
    n = len(asset_classes)
    corr = np.eye(n)

    type_map = {}
    for i, ac in enumerate(asset_classes):
        if "equity" in ac:
            type_map[i] = "eq"
        elif "bond" in ac:
            type_map[i] = "bond"
        else:
            type_map[i] = "cash"

    for i in range(n):
        for j in range(i + 1, n):
            ti, tj = type_map[i], type_map[j]
            if ti == "eq" and tj == "eq":
                c = 0.85
            elif (ti == "eq" and tj == "bond") or (ti == "bond" and tj == "eq"):
                c = -0.10
            elif (ti == "eq" and tj == "cash") or (ti == "cash" and tj == "eq"):
                c = 0.05
            elif (ti == "bond" and tj == "cash") or (ti == "cash" and tj == "bond"):
                c = 0.20
            else:
                c = 0.30
            corr[i, j] = c
            corr[j, i] = c

    return corr


# ---------------------------------------------------------------------------
# Summarize results
# ---------------------------------------------------------------------------

_PERCENTILES = [5, 10, 25, 50, 75, 90, 95]


def summarize_mc_results(results: MCResults) -> dict:
    """
    Produce a JSON-serializable summary of MC results.

    Returns dict with:
      metadata, percentile_paths, terminal_stats, ruin_stats,
      irmaa_stats, year_by_year_table
    """
    n = results.n_sims
    h = results.horizon

    # Percentile paths: (len(percentiles), horizon)
    pct_paths = np.percentile(results.portfolio_paths, _PERCENTILES, axis=0)
    wdraw_pcts = np.percentile(results.withdrawal_paths, [25, 50, 75], axis=0)
    agi_pcts = np.percentile(results.agi_paths, [25, 50, 75], axis=0)

    # Terminal value stats
    tv = results.terminal_values
    terminal_stats = {
        "mean": float(np.mean(tv)),
        "median": float(np.median(tv)),
        "std": float(np.std(tv)),
        "p5": float(np.percentile(tv, 5)),
        "p10": float(np.percentile(tv, 10)),
        "p25": float(np.percentile(tv, 25)),
        "p75": float(np.percentile(tv, 75)),
        "p90": float(np.percentile(tv, 90)),
        "p95": float(np.percentile(tv, 95)),
    }

    # Ruin statistics
    ruin_count = int(results.ruin_flags.sum())
    ruin_stats = {
        "probability_of_ruin": round(ruin_count / n, 4),
        "ruin_count": ruin_count,
        "median_ruin_year": int(np.median(results.ruin_year[results.ruin_flags]))
                           if ruin_count > 0 else None,
    }

    # IRMAA crossing probability by year
    irmaa_by_year = results.irmaa_trigger_paths.mean(axis=0).tolist()
    irmaa_ever = float((results.irmaa_trigger_paths.any(axis=1)).mean())

    # Year-by-year table
    year_table = []
    for y in range(h):
        year = results.start_year + y
        row = {"year": year, "age": 72 + y}  # owner_start_age assumed 72
        for pi, p in enumerate(_PERCENTILES):
            row[f"balance_p{p}"] = round(float(pct_paths[pi, y]), 0)
        row["withdrawal_p50"] = round(float(wdraw_pcts[1, y]), 0)
        row["agi_p50"] = round(float(agi_pcts[1, y]), 0)
        row["irmaa_prob"] = round(irmaa_by_year[y], 4)
        row["ruin_prob_cumul"] = round(
            float((results.ruin_year <= y).sum() / n), 4
        )
        year_table.append(row)

    return {
        "metadata": {
            "n_sims": n,
            "horizon": h,
            "start_year": results.start_year,
            "elapsed_seconds": round(results.elapsed_seconds, 3),
            "asset_classes": results.asset_classes,
        },
        "terminal_stats": {k: round(v, 0) for k, v in terminal_stats.items()},
        "ruin_stats": ruin_stats,
        "irmaa_stats": {
            "prob_ever_triggered": round(irmaa_ever, 4),
            "prob_by_year": [round(x, 4) for x in irmaa_by_year],
        },
        "cumulative_withdrawal_stats": {
            "mean": round(float(results.cumulative_withdrawals.mean()), 0),
            "median": round(float(np.median(results.cumulative_withdrawals)), 0),
            "p5": round(float(np.percentile(results.cumulative_withdrawals, 5)), 0),
            "p95": round(float(np.percentile(results.cumulative_withdrawals, 95)), 0),
        },
        "year_by_year": year_table,
    }


# ---------------------------------------------------------------------------
# Convenience: run from ingested data
# ---------------------------------------------------------------------------

def run_mc_from_ingested(
    ingested: dict,
    n_sims: int = 10_000,
    horizon: int = 20,
    seed: Optional[int] = None,
) -> dict:
    """
    One-call entry point: ingest_all output → MC summary dict.
    """
    mc_cfg = ingested["constraints"].get("monte_carlo", {})
    return_assumptions = mc_cfg.get("return_assumptions", {
        "us_equity":   {"mean": 0.07, "std": 0.16},
        "intl_equity": {"mean": 0.06, "std": 0.18},
        "us_bond":     {"mean": 0.035, "std": 0.05},
        "mmf":         {"mean": 0.04, "std": 0.005},
    })

    # Derive allocation weights from current holdings
    accounts = ingested["accounts"]
    ac_totals: Dict[str, float] = {}
    total_mv = 0.0
    for r in accounts:
        ac = r["asset_class"]
        mv = r["market_value"]
        ac_totals[ac] = ac_totals.get(ac, 0) + mv
        total_mv += mv

    allocation_weights = {ac: v / total_mv for ac, v in ac_totals.items() if ac in return_assumptions}
    # Normalize
    w_sum = sum(allocation_weights.values())
    if w_sum > 0:
        allocation_weights = {k: v / w_sum for k, v in allocation_weights.items()}

    results = simulate_monte_carlo(
        initial_balances=ingested["totals"]["by_account_id"],
        allocation_weights=allocation_weights,
        return_assumptions=return_assumptions,
        cashflow=ingested["cashflow"],
        tax_profile=ingested["tax_profile"],
        constraints=ingested["constraints"],
        n_sims=n_sims,
        horizon=horizon,
        start_year=2025,
        owner_start_age=ingested["tax_profile"]["ages"][0],
        seed=seed,
    )

    return summarize_mc_results(results)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_mc_summary(summary: dict) -> None:
    meta = summary["metadata"]
    print(f"\n{'=' * 115}")
    print(f"  MONTE CARLO SIMULATION — {meta['n_sims']:,} sims × {meta['horizon']} years"
          f"  ({meta['elapsed_seconds']:.2f}s)")
    print(f"{'=' * 115}")

    # Year-by-year table
    print(f"\n{'Year':>6} {'Age':>4}"
          f" {'P5':>11} {'P10':>11} {'P25':>11} {'P50':>11}"
          f" {'P75':>11} {'P90':>11} {'P95':>11}"
          f" {'Wdraw':>9} {'IRMAA%':>7} {'Ruin%':>6}")
    print("─" * 115)

    for row in summary["year_by_year"]:
        print(f"{row['year']:>6} {row['age']:>4}"
              f" ${row['balance_p5']:>10,.0f}"
              f" ${row['balance_p10']:>10,.0f}"
              f" ${row['balance_p25']:>10,.0f}"
              f" ${row['balance_p50']:>10,.0f}"
              f" ${row['balance_p75']:>10,.0f}"
              f" ${row['balance_p90']:>10,.0f}"
              f" ${row['balance_p95']:>10,.0f}"
              f" ${row['withdrawal_p50']:>8,.0f}"
              f" {row['irmaa_prob']:>6.1%}"
              f" {row['ruin_prob_cumul']:>5.1%}")

    # Terminal stats
    ts = summary["terminal_stats"]
    print(f"\n── Terminal Value (Age {summary['year_by_year'][-1]['age']}) ──")
    print(f"  Median: ${ts['median']:>12,.0f}   Mean: ${ts['mean']:>12,.0f}   Std: ${ts['std']:>12,.0f}")
    print(f"  P5:     ${ts['p5']:>12,.0f}   P25:  ${ts['p25']:>12,.0f}   P75: ${ts['p75']:>12,.0f}   P95: ${ts['p95']:>12,.0f}")

    # Ruin stats
    rs = summary["ruin_stats"]
    print(f"\n── Ruin Probability ──")
    print(f"  P(ruin): {rs['probability_of_ruin']:.2%}  ({rs['ruin_count']} of {meta['n_sims']})")
    if rs["median_ruin_year"] is not None:
        print(f"  Median ruin year offset: {rs['median_ruin_year']}")

    # IRMAA
    ir = summary["irmaa_stats"]
    print(f"\n── IRMAA ──")
    print(f"  P(ever triggered): {ir['prob_ever_triggered']:.1%}")

    # Withdrawals
    cw = summary["cumulative_withdrawal_stats"]
    print(f"\n── Cumulative Withdrawals ──")
    print(f"  Median: ${cw['median']:>12,.0f}   P5: ${cw['p5']:>12,.0f}   P95: ${cw['p95']:>12,.0f}")
    print()


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from ingest import ingest_all, DEFAULT_PATHS

    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    paths = {k: str(base / v) for k, v in DEFAULT_PATHS.items()}
    ingested = ingest_all(paths)

    # Run with N=10,000
    summary = run_mc_from_ingested(ingested, n_sims=10_000, horizon=20, seed=42)
    print_mc_summary(summary)
