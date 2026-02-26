"""
rmd.py — RMD computation and inherited IRA distribution scheduling for RICS.

Public API:
    load_rmd_divisors(json_path)                -> dict[int, float]
    compute_rmd_amount(year, birthdate, balance, divisors) -> dict
    generate_inherited_ira_schedule(balance, must_distribute_by, ...) -> dict
    project_rmd_series(start_year, start_age, initial_balance, ...) -> list[dict]
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Dict, List, Literal, Optional


# ---------------------------------------------------------------------------
# Divisor table loading
# ---------------------------------------------------------------------------

def load_rmd_divisors(json_path: str | Path | None = None) -> Dict[int, float]:
    """
    Load the Uniform Lifetime Table from JSON.

    Returns dict mapping age (int) → divisor (float).
    Falls back to embedded table if no path given.
    """
    if json_path is not None:
        p = Path(json_path)
        if p.exists():
            with open(p) as f:
                data = json.load(f)
            return {int(k): float(v) for k, v in data["divisors"].items()}

    # Embedded fallback (IRS Pub 590-B 2024 revision)
    return {
        72: 27.4, 73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9,
        78: 22.0, 79: 21.1, 80: 20.2, 81: 19.4, 82: 18.5, 83: 17.7,
        84: 16.8, 85: 16.0, 86: 15.2, 87: 14.4, 88: 13.7, 89: 12.9,
        90: 12.2, 91: 11.5, 92: 10.8, 93: 10.1, 94:  9.5, 95:  8.9,
        96:  8.4, 97:  7.8, 98:  7.3, 99:  6.8, 100: 6.4,
        101: 6.0, 102: 5.6, 103: 5.2, 104: 4.9, 105: 4.6,
        106: 4.2, 107: 3.9, 108: 3.7, 109: 3.4, 110: 3.1,
        111: 2.9, 112: 2.6, 113: 2.4, 114: 2.1, 115: 1.9,
        116: 1.7, 117: 1.5, 118: 1.4, 119: 1.2, 120: 1.0,
    }


def _get_divisor(age: int, divisors: Dict[int, float]) -> float:
    """Look up divisor for age; extrapolate if beyond table range."""
    if age in divisors:
        return divisors[age]

    max_age = max(divisors.keys())
    if age > max_age:
        return max(divisors[max_age], 1.0)  # floor at 1.0

    min_age = min(divisors.keys())
    if age < min_age:
        return 0.0  # no RMD below table start

    return 0.0


# ---------------------------------------------------------------------------
# RMD computation
# ---------------------------------------------------------------------------

def compute_rmd_amount(
    year: int,
    birthdate: str | date,
    account_balance_prior_year_end: float,
    divisors: Optional[Dict[int, float]] = None,
    rmd_start_age: int = 73,
) -> dict:
    """
    Compute the Required Minimum Distribution for a traditional IRA.

    Parameters
    ----------
    year : the distribution year
    birthdate : owner's date of birth (str 'YYYY-MM-DD' or date)
    account_balance_prior_year_end : IRA balance as of Dec 31 of prior year
    divisors : {age: divisor} from load_rmd_divisors()
    rmd_start_age : 73 for birth years 1951-1959 (SECURE 2.0)

    Returns
    -------
    dict: {year, age, balance_basis, divisor, rmd_amount, rmd_pct,
           rmd_required (bool), note}
    """
    if divisors is None:
        divisors = load_rmd_divisors()

    if isinstance(birthdate, str):
        birthdate = date.fromisoformat(birthdate)

    # Age attained in the distribution year
    age = year - birthdate.year
    if (birthdate.month, birthdate.day) > (12, 31):
        age -= 1  # hasn't had birthday yet this year (edge case)

    # Check if RMD is required
    if age < rmd_start_age:
        return {
            "year": year,
            "age": age,
            "balance_basis": account_balance_prior_year_end,
            "divisor": None,
            "rmd_amount": 0.0,
            "rmd_pct": 0.0,
            "rmd_required": False,
            "note": f"No RMD required: age {age} < start age {rmd_start_age}",
        }

    if account_balance_prior_year_end <= 0:
        return {
            "year": year, "age": age,
            "balance_basis": 0, "divisor": None,
            "rmd_amount": 0.0, "rmd_pct": 0.0,
            "rmd_required": True,
            "note": "Zero balance — no distribution needed",
        }

    divisor = _get_divisor(age, divisors)
    if divisor <= 0:
        divisor = 1.0  # safety: full distribution

    rmd = account_balance_prior_year_end / divisor

    return {
        "year": year,
        "age": age,
        "balance_basis": round(account_balance_prior_year_end, 2),
        "divisor": divisor,
        "rmd_amount": round(rmd, 2),
        "rmd_pct": round(rmd / account_balance_prior_year_end * 100, 2),
        "rmd_required": True,
        "note": f"Uniform Lifetime Table divisor {divisor} at age {age}",
    }


# ---------------------------------------------------------------------------
# Multi-year RMD projection
# ---------------------------------------------------------------------------

def project_rmd_series(
    start_year: int,
    birthdate: str | date,
    initial_balance: float,
    growth_rate: float = 0.045,
    horizon: int = 20,
    divisors: Optional[Dict[int, float]] = None,
    rmd_start_age: int = 73,
    extra_withdrawal: float = 0.0,
) -> list[dict]:
    """
    Project RMDs forward, growing the balance and taking distributions.

    Each year: balance grows, RMD is taken (plus any extra_withdrawal),
    end balance carries forward.

    Returns list of dicts per year with balance, RMD, and post-withdrawal balance.
    """
    if divisors is None:
        divisors = load_rmd_divisors()

    results = []
    balance = initial_balance

    for y in range(horizon):
        year = start_year + y

        # RMD is based on prior year-end balance
        rmd_info = compute_rmd_amount(year, birthdate, balance, divisors, rmd_start_age)

        rmd_taken = rmd_info["rmd_amount"]
        total_withdrawal = rmd_taken + extra_withdrawal

        # Grow balance, then withdraw
        grown_balance = balance * (1 + growth_rate)
        actual_withdrawal = min(total_withdrawal, max(grown_balance, 0))
        end_balance = max(grown_balance - actual_withdrawal, 0)

        results.append({
            **rmd_info,
            "start_balance": round(balance, 2),
            "grown_balance": round(grown_balance, 2),
            "total_withdrawal": round(actual_withdrawal, 2),
            "end_balance": round(end_balance, 2),
        })

        balance = end_balance

    return results


# ---------------------------------------------------------------------------
# Inherited IRA schedule generation
# ---------------------------------------------------------------------------

Strategy = Literal["even", "front_load", "back_load"]


def generate_inherited_ira_schedule(
    current_balance: float,
    must_distribute_by_year: int,
    current_year: int = 2025,
    growth_rate: float = 0.035,
    strategy: Strategy = "even",
) -> Dict[int, float]:
    """
    Generate a year-by-year distribution schedule for an inherited IRA
    subject to the 10-year rule.

    Parameters
    ----------
    current_balance : current inherited IRA balance
    must_distribute_by_year : year by which balance must reach 0
    current_year : first year of distributions
    growth_rate : assumed annual return on remaining balance
    strategy : 'even' | 'front_load' | 'back_load'
        even       — level real distributions (annuity)
        front_load — 2× weight on first half of years
        back_load  — 2× weight on second half of years

    Returns
    -------
    Dict[year, distribution_amount] — all amounts > 0,
    sum approximately equals balance + accumulated growth.
    """
    n = must_distribute_by_year - current_year + 1
    if n <= 0:
        return {current_year: current_balance}
    if current_balance <= 0:
        return {current_year + i: 0.0 for i in range(max(n, 1))}

    # Generate raw weights based on strategy
    weights = _generate_weights(n, strategy)

    # Simulate forward: distribute according to weights, growing remaining balance
    schedule = _simulate_weighted_schedule(current_balance, n, current_year,
                                           growth_rate, weights)
    return schedule


def _generate_weights(n: int, strategy: Strategy) -> list[float]:
    """Generate raw distribution weights for n years."""
    if strategy == "even":
        return [1.0] * n

    elif strategy == "front_load":
        # First half gets 2× weight
        mid = (n + 1) // 2
        weights = [2.0] * mid + [1.0] * (n - mid)
        return weights

    elif strategy == "back_load":
        # Second half gets 2× weight
        mid = n // 2
        weights = [1.0] * mid + [2.0] * (n - mid)
        return weights

    else:
        raise ValueError(f"Unknown strategy: {strategy}. Use 'even', 'front_load', or 'back_load'.")


def _simulate_weighted_schedule(
    balance: float,
    n: int,
    start_year: int,
    growth_rate: float,
    weights: list[float],
) -> Dict[int, float]:
    """
    Forward-simulate distributions using relative weights.

    We first compute a target distribution ratio per year, then simulate
    forward with growth to produce actual dollar amounts.

    The approach:
    1. Normalize weights to fractions
    2. For each year: grow balance, compute target withdrawal as
       (remaining_weight_fraction / total_remaining_weight) × available_balance
    3. The last year takes whatever remains
    """
    total_weight = sum(weights)
    schedule: Dict[int, float] = {}
    remaining_weight = total_weight

    for i in range(n):
        year = start_year + i

        # Grow balance
        balance = balance * (1 + growth_rate)

        if i == n - 1:
            # Last year: take everything remaining
            withdrawal = max(balance, 0)
        else:
            # Fraction of remaining weight this year represents
            frac = weights[i] / remaining_weight if remaining_weight > 0 else 1.0
            withdrawal = balance * frac

        withdrawal = min(withdrawal, max(balance, 0))
        balance = max(balance - withdrawal, 0)
        remaining_weight -= weights[i]

        schedule[year] = round(withdrawal, 2)

    return schedule


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_rmd_series(series: list[dict]) -> None:
    print(f"\n{'Year':>6} {'Age':>4} {'Start Bal':>12} {'Divisor':>8}"
          f" {'RMD':>10} {'RMD%':>6} {'End Bal':>12}")
    print("─" * 70)
    for r in series:
        div_str = f"{r['divisor']:>8.1f}" if r['divisor'] else "     N/A"
        print(f"{r['year']:>6} {r['age']:>4} ${r['start_balance']:>11,.0f}"
              f" {div_str} ${r['rmd_amount']:>9,.0f}"
              f" {r['rmd_pct']:>5.1f}% ${r['end_balance']:>11,.0f}")


def _print_inherited_schedule(schedule: Dict[int, float], label: str) -> None:
    print(f"\n  {label}:")
    total = 0
    for year, amt in sorted(schedule.items()):
        total += amt
        print(f"    {year}: ${amt:>10,.2f}  (cumulative: ${total:>10,.2f})")
    print(f"    Total distributed: ${total:>10,.2f}")


if __name__ == "__main__":
    import sys

    divisors = load_rmd_divisors(str(Path(__file__).parent / "data" / "rmd_divisors.json"))

    print("\n" + "=" * 70)
    print("  RMD PROJECTION — Traditional IRA ($1,000,000)")
    print("=" * 70)

    series = project_rmd_series(
        start_year=2025,
        birthdate="1953-03-15",
        initial_balance=1_000_000,
        growth_rate=0.045,
        horizon=20,
        divisors=divisors,
    )
    _print_rmd_series(series)

    print("\n" + "=" * 70)
    print("  INHERITED IRA SCHEDULES — $85,000, distribute by 2033")
    print("=" * 70)

    for strat in ("even", "front_load", "back_load"):
        schedule = generate_inherited_ira_schedule(
            current_balance=85_000,
            must_distribute_by_year=2033,
            current_year=2025,
            growth_rate=0.035,
            strategy=strat,
        )
        _print_inherited_schedule(schedule, strat.upper())

    print()
