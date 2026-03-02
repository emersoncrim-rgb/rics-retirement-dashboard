"""
recommendations.py – RICS Module: Rule-Based Recommendation Engine

Flags actionable planning opportunities by combining outputs from all other
modules.  Each rule returns a Recommendation dataclass with severity, category,
description, and suggested action.

Rules implemented:
  1. AAPL concentration warning
  2. 0% LTCG bracket harvesting opportunity
  3. IRMAA headroom warning
  4. Roth conversion bracket-filling
  5. Cash reserve adequacy
  6. Dividend upgrade opportunities
  7. Inherited IRA 10-year deadline pacing
  8. RMD projection check
"""

import csv
import json
from dataclasses import dataclass, asdict
from datetime import date
from typing import Optional


@dataclass
class Recommendation:
    """A single planning recommendation."""
    rule_id: str
    category: str  # "tax", "risk", "income", "withdrawal", "compliance"
    severity: str  # "high", "medium", "low", "info"
    title: str
    description: str
    action: str
    impact_estimate: str  # e.g., "$2,400 tax savings" or "reduces IRMAA risk"
    data: dict  # Supporting numbers for the UI

    def to_dict(self) -> dict:
        return asdict(self)


# ── Helper functions ──────────────────────────────────────────────────────────

def _load_csv(path: str) -> list[dict]:
    with open(path) as f:
        return list(csv.DictReader(f))


def _load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _sum_field(rows: list[dict], field: str) -> float:
    return sum(float(r.get(field, 0)) for r in rows)


def _compute_ss_taxable(ss_annual: float, other_income: float, filing_mfj: bool = True) -> float:
    """Estimate taxable portion of Social Security (simplified)."""
    base = 32000 if filing_mfj else 25000
    additional = 44000 if filing_mfj else 34000
    combined = other_income + ss_annual * 0.5

    if combined <= base:
        return 0.0
    elif combined <= additional:
        return min(0.50 * (combined - base), 0.50 * ss_annual)
    else:
        lower_tier = 0.50 * (additional - base)
        upper_tier = 0.85 * (combined - additional)
        return min(lower_tier + upper_tier, 0.85 * ss_annual)


def _estimate_magi(
    ss_annual: float,
    trad_ira_withdrawal: float,
    qualified_divs: float,
    ordinary_income: float,
    roth_conversion: float = 0,
) -> float:
    """Estimate MAGI for IRMAA purposes."""
    other_income = trad_ira_withdrawal + qualified_divs + ordinary_income + roth_conversion
    ss_taxable = _compute_ss_taxable(ss_annual, other_income)
    return ss_taxable + other_income


def _federal_tax_on_ordinary(taxable_income: float, brackets: list[dict]) -> float:
    """Compute federal tax on ordinary income using bracket schedule."""
    tax = 0.0
    for b in brackets:
        lower = b["lower"]
        upper = b["upper"] if b["upper"] is not None else float("inf")
        rate = b["rate"]
        if taxable_income <= lower:
            break
        bracket_income = min(taxable_income, upper) - lower
        tax += bracket_income * rate
    return round(tax, 2)


def _find_bracket_room(taxable_income: float, brackets: list[dict], target_top_rate: float) -> float:
    """Find how much more income fits below target_top_rate bracket."""
    for b in brackets:
        if b["rate"] >= target_top_rate:
            return max(0, b["lower"] - taxable_income)
    return 0.0


# ── Rule implementations ─────────────────────────────────────────────────────

def check_aapl_concentration(
    holdings: list[dict],
    constraints: dict,
) -> Optional[Recommendation]:
    """Rule 1: Flag if AAPL exceeds concentration limit in taxable accounts."""
    limits = constraints.get("concentration_limits", {})
    max_pct = limits.get("single_stock_max_pct", 0.30)
    aapl_info = limits.get("aapl_flag", {})

    taxable_holdings = [h for h in holdings if h.get("account_type") == "taxable"]
    total_taxable = sum(float(h.get("market_value", 0)) for h in taxable_holdings)
    if total_taxable == 0:
        return None

    aapl_value = sum(
        float(h.get("market_value", 0))
        for h in taxable_holdings
        if h.get("ticker", "").upper() == "AAPL"
    )
    aapl_pct = aapl_value / total_taxable
    embedded_gain = sum(
        float(h.get("unrealized_gain", 0))
        for h in taxable_holdings
        if h.get("ticker", "").upper() == "AAPL"
    )

    if aapl_pct < max_pct * 0.80:  # Well below limit
        return None

    severity = "high" if aapl_pct > max_pct else "medium"
    status = "EXCEEDS" if aapl_pct > max_pct else "APPROACHING"

    return Recommendation(
        rule_id="CONC-AAPL",
        category="risk",
        severity=severity,
        title=f"AAPL Concentration {status} Limit",
        description=(
            f"AAPL is {aapl_pct:.1%} of taxable portfolio (limit: {max_pct:.0%}). "
            f"Embedded gain: ${embedded_gain:,.0f}. "
            f"Direct selling triggers significant capital gains tax."
        ),
        action=(
            "Consider: (a) donate appreciated shares to charity/DAF, "
            "(b) use harvested losses to offset a partial trim, "
            "(c) write covered calls to generate income while capping upside, "
            "(d) let new contributions to other holdings dilute the concentration."
        ),
        impact_estimate=f"${embedded_gain:,.0f} embedded gain at risk",
        data={
            "aapl_pct": round(aapl_pct, 4),
            "limit_pct": max_pct,
            "aapl_value": aapl_value,
            "embedded_gain": embedded_gain,
            "total_taxable": total_taxable,
        },
    )


def check_zero_ltcg_harvesting(
    holdings: list[dict],
    tax_profile: dict,
    cashflow: list[dict],
) -> Optional[Recommendation]:
    """Rule 2: Flag 0% LTCG bracket harvesting opportunity."""
    brackets = tax_profile.get("qualified_div_brackets_mfj_2025", [])
    if not brackets:
        return None

    zero_cap = brackets[0].get("upper", 96700) if brackets else 96700

    # Estimate current taxable income
    ss = tax_profile.get("ss_combined_annual", 0)
    std_ded = tax_profile.get("effective_standard_deduction", 33100)

    # Qualified dividends from taxable account
    taxable_holdings = [h for h in holdings if h.get("account_type") == "taxable"]
    qual_divs = sum(float(h.get("annual_income_est", 0)) for h in taxable_holdings
                     if h.get("asset_class") in ("us_equity", "intl_equity"))
    ord_income = sum(float(h.get("annual_income_est", 0)) for h in taxable_holdings
                      if h.get("asset_class") in ("us_bond", "mmf"))

    other_income = qual_divs + ord_income
    ss_taxable = _compute_ss_taxable(ss, other_income)
    taxable_income_est = max(0, ss_taxable + other_income - std_ded)

    # Room in 0% LTCG bracket
    room = max(0, zero_cap - taxable_income_est)

    if room < 5000:
        return None

    # Find long-term gains available to harvest
    harvestable = []
    for h in taxable_holdings:
        ug = float(h.get("unrealized_gain", 0))
        ticker = h.get("ticker", "")
        if ug > 0 and ticker.upper() != "AAPL":  # Exclude AAPL per constraints
            harvestable.append({"ticker": ticker, "gain": ug})

    if not harvestable:
        return None

    total_harvestable = sum(g["gain"] for g in harvestable)
    harvest_amount = min(room, total_harvestable)

    return Recommendation(
        rule_id="TAX-0LTCG",
        category="tax",
        severity="medium",
        title="0% Long-Term Capital Gains Harvesting Opportunity",
        description=(
            f"Estimated taxable income: ~${taxable_income_est:,.0f}. "
            f"0% LTCG bracket ceiling: ${zero_cap:,.0f}. "
            f"Room to realize ~${room:,.0f} in long-term gains at 0% federal tax rate. "
            f"Available harvestable gains: ${total_harvestable:,.0f}."
        ),
        action=(
            f"Sell ~${harvest_amount:,.0f} of appreciated positions (excluding AAPL) and "
            f"immediately repurchase to step up cost basis. Saves future LTCG tax. "
            f"Note: Oregon taxes capital gains as ordinary income (~8.75%)."
        ),
        impact_estimate=f"~${harvest_amount * 0.15:,.0f} future federal LTCG tax avoided",
        data={
            "taxable_income_est": taxable_income_est,
            "zero_ltcg_ceiling": zero_cap,
            "room_in_bracket": room,
            "harvestable_gains": total_harvestable,
            "suggested_harvest": harvest_amount,
            "candidates": harvestable[:5],
        },
    )


def check_irmaa_headroom(
    tax_profile: dict,
    cashflow: list[dict],
    roth_conversion_amount: float = 0,
) -> Optional[Recommendation]:
    """Rule 3: Warn if MAGI is approaching IRMAA Tier 1 threshold."""
    irmaa = tax_profile.get("irmaa_thresholds_mfj_2025", [])
    if not irmaa or len(irmaa) < 2:
        return None

    tier1_lower = irmaa[1].get("magi_lower", 206000)  # First surcharge tier
    target_headroom = 10000  # Stay $10k below

    ss = tax_profile.get("ss_combined_annual", 0)
    prior_agi = tax_profile.get("agi_prior_year", 104000)

    # Estimate current-year MAGI (used for IRMAA 2 years hence)
    magi_est = _estimate_magi(
        ss_annual=ss,
        trad_ira_withdrawal=0,  # Base case: no IRA withdrawals yet
        qualified_divs=6700,    # From cashflow
        ordinary_income=3300,
        roth_conversion=roth_conversion_amount,
    )

    headroom = tier1_lower - magi_est
    surcharge_per_person = irmaa[1].get("part_b_surcharge", 838.8)
    surcharge_couple = surcharge_per_person * 2 + irmaa[1].get("part_d_surcharge", 154.8) * 2

    if headroom > target_headroom * 2:
        severity = "info"
    elif headroom > target_headroom:
        severity = "low"
    elif headroom > 0:
        severity = "medium"
    else:
        severity = "high"

    if severity == "info" and roth_conversion_amount == 0:
        return None  # Not worth flagging if plenty of room

    return Recommendation(
        rule_id="TAX-IRMAA",
        category="tax",
        severity=severity,
        title="IRMAA Headroom Warning",
        description=(
            f"Estimated MAGI: ~${magi_est:,.0f}. "
            f"IRMAA Tier 1 threshold: ${tier1_lower:,.0f}. "
            f"Headroom: ${headroom:,.0f}. "
            f"Crossing triggers ~${surcharge_couple:,.0f}/yr in Medicare surcharges for both spouses."
        ),
        action=(
            "Monitor total income including Roth conversions, IRA withdrawals, and "
            "capital gain realizations. Consider timing large transactions across "
            "tax years to stay below threshold."
        ),
        impact_estimate=f"${surcharge_couple:,.0f}/yr surcharge if breached",
        data={
            "magi_estimate": magi_est,
            "tier1_threshold": tier1_lower,
            "headroom": headroom,
            "surcharge_annual_couple": surcharge_couple,
            "roth_conversion_included": roth_conversion_amount,
        },
    )


def check_roth_conversion_opportunity(
    holdings: list[dict],
    tax_profile: dict,
    cashflow: list[dict],
) -> Optional[Recommendation]:
    """Rule 4: Find room to fill up a low tax bracket with Roth conversions."""
    brackets = tax_profile.get("federal_brackets_mfj_2025", [])
    irmaa_thresholds = tax_profile.get("irmaa_thresholds_mfj_2025", [])
    if not brackets:
        return None

    ss = tax_profile.get("ss_combined_annual", 0)
    std_ded = tax_profile.get("effective_standard_deduction", 33100)
    qual_divs = 6700
    ord_income = 3300

    other_income = qual_divs + ord_income
    ss_taxable = _compute_ss_taxable(ss, other_income)
    taxable_income_base = max(0, ss_taxable + other_income - std_ded)

    # Find room in 12% bracket (don't push into 22%)
    target_rate = 0.22
    bracket_room = _find_bracket_room(taxable_income_base, brackets, target_rate)

    # Also check IRMAA ceiling
    tier1 = irmaa_thresholds[1]["magi_lower"] if len(irmaa_thresholds) > 1 else 206000
    magi_base = _estimate_magi(ss, 0, qual_divs, ord_income)
    irmaa_room = max(0, tier1 - magi_base - 10000)  # Keep $10k buffer

    # Conversion amount is the smaller of bracket room and IRMAA room
    conversion_room = min(bracket_room, irmaa_room)

    if conversion_room < 5000:
        return None

    # Trad IRA balance
    trad_ira_balance = sum(
        float(h.get("market_value", 0))
        for h in holdings
        if h.get("account_type") == "trad_ira"
    )

    conversion_amt = min(conversion_room, trad_ira_balance)
    if conversion_amt < 1000:
        return None

    tax_cost = _federal_tax_on_ordinary(taxable_income_base + conversion_amt, brackets) - \
               _federal_tax_on_ordinary(taxable_income_base, brackets)
    # Oregon state tax on conversion
    or_brackets = tax_profile.get("oregon_tax", {}).get("brackets_mfj_2025", [])
    or_tax = 0
    if or_brackets:
        or_base = max(0, ss_taxable + other_income - tax_profile.get("oregon_tax", {}).get("standard_deduction_mfj", 5010))
        or_tax = _federal_tax_on_ordinary(or_base + conversion_amt, or_brackets) - \
                 _federal_tax_on_ordinary(or_base, or_brackets)

    total_tax = round(tax_cost + or_tax, 2)

    return Recommendation(
        rule_id="TAX-ROTH",
        category="tax",
        severity="medium",
        title="Roth Conversion Bracket-Filling Opportunity",
        description=(
            f"Taxable income before conversion: ~${taxable_income_base:,.0f}. "
            f"Room in 12% bracket: ~${bracket_room:,.0f}. "
            f"IRMAA-safe room: ~${irmaa_room:,.0f}. "
            f"Suggested conversion: ~${conversion_amt:,.0f} from Traditional IRA "
            f"(balance: ${trad_ira_balance:,.0f})."
        ),
        action=(
            f"Convert ~${conversion_amt:,.0f} from Traditional IRA to Roth IRA. "
            f"Estimated tax cost: ~${total_tax:,.0f} (fed + OR). "
            f"This reduces future RMDs and creates tax-free growth. "
            f"Repeat annually while in low bracket."
        ),
        impact_estimate=f"~${conversion_amt:,.0f} converted at ~{total_tax / conversion_amt:.1%} blended rate" if conversion_amt > 0 else "N/A",
        data={
            "taxable_income_base": taxable_income_base,
            "bracket_room_12pct": bracket_room,
            "irmaa_safe_room": irmaa_room,
            "suggested_conversion": conversion_amt,
            "federal_tax": tax_cost,
            "state_tax": or_tax,
            "total_tax": total_tax,
            "trad_ira_balance": trad_ira_balance,
        },
    )


def check_cash_reserve_adequacy(
    holdings: list[dict],
    cashflow: list[dict],
    months_target: int = 24,
) -> Optional[Recommendation]:
    """Rule 5: Check if liquid reserves cover target months of spending."""
    # Sum MMF and short-term bond across all accounts
    liquid_value = sum(
        float(h.get("market_value", 0))
        for h in holdings
        if h.get("asset_class") in ("mmf",) and h.get("account_type") == "taxable"
    )

    # Annual expenses from cashflow
    annual_expenses = sum(
        float(c.get("amount", 0))
        for c in cashflow
        if c.get("category") == "expense" and c.get("frequency") == "annual"
    )
    # Annual income
    ss_income = sum(
        float(c.get("amount", 0))
        for c in cashflow
        if c.get("subcategory") == "social_security"
    )

    monthly_net_spending = max(0, (annual_expenses - ss_income) / 12)
    months_covered = liquid_value / monthly_net_spending if monthly_net_spending > 0 else 999
    shortfall = max(0, months_target * monthly_net_spending - liquid_value)

    if months_covered >= months_target:
        severity = "info"
    elif months_covered >= months_target * 0.75:
        severity = "low"
    elif months_covered >= 12:
        severity = "medium"
    else:
        severity = "high"

    return Recommendation(
        rule_id="CASH-RSV",
        category="withdrawal",
        severity=severity,
        title="Cash Reserve Adequacy",
        description=(
            f"Taxable liquid reserves (MMF): ${liquid_value:,.0f}. "
            f"Monthly net spending gap: ~${monthly_net_spending:,.0f}. "
            f"Covers ~{months_covered:.0f} months (target: {months_target} months)."
        ),
        action=(
            f"{'Adequate reserves.' if months_covered >= months_target else ''} "
            f"{'Consider adding ~$' + f'{shortfall:,.0f} to taxable cash reserves.' if shortfall > 0 else ''} "
            f"Refill from IRA distributions or bond maturities as needed."
        ).strip(),
        impact_estimate=f"{months_covered:.0f} months covered",
        data={
            "liquid_value": liquid_value,
            "monthly_net_spending": round(monthly_net_spending, 2),
            "months_covered": round(months_covered, 1),
            "months_target": months_target,
            "shortfall": round(shortfall, 2),
        },
    )


def check_dividend_upgrades(
    holdings: list[dict],
    constraints: dict,
) -> Optional[Recommendation]:
    """Rule 6: Flag dividend upgrade opportunities in tax-advantaged accounts."""
    # Import inline to avoid circular deps at module level
    from dividend_analyzer import find_upgrade_opportunities

    opps = find_upgrade_opportunities(holdings, constraints)
    feasible = [o for o in opps if o.feasible]

    if not feasible:
        return None

    total_increase = sum(o.income_increase for o in feasible)
    top3 = feasible[:3]

    details = "; ".join(
        f"{o.current_ticker}→{o.suggested_ticker} (+${o.income_increase:,.0f}/yr)"
        for o in top3
    )

    return Recommendation(
        rule_id="INC-DIVUP",
        category="income",
        severity="low",
        title="Dividend Upgrade Opportunities",
        description=(
            f"Found {len(feasible)} feasible dividend upgrade swaps in tax-advantaged accounts. "
            f"Top opportunities: {details}."
        ),
        action=(
            f"Swap low-yield holdings for higher-yield alternatives in IRA/Roth accounts "
            f"(no tax impact). Potential additional income: ~${total_increase:,.0f}/yr."
        ),
        impact_estimate=f"+${total_increase:,.0f}/yr income",
        data={
            "num_opportunities": len(feasible),
            "total_income_increase": round(total_increase, 2),
            "top_swaps": [asdict(o) for o in top3],
        },
    )


def check_inherited_ira_pacing(
    holdings: list[dict],
    constraints: dict,
    current_year: Optional[int] = None,
) -> Optional[Recommendation]:
    """Rule 7: Check if inherited IRA distribution is on pace for 10-year deadline."""
    deadline_year = constraints.get("inherited_ira_deadline_year", 2033)
    start_balance = constraints.get("inherited_ira_balance_start", 85000)
    year = current_year or date.today().year

    years_remaining = max(1, deadline_year - year)
    current_balance = sum(
        float(h.get("market_value", 0))
        for h in holdings
        if h.get("account_type") == "inherited_ira"
    )

    if current_balance == 0:
        return None

    annual_target = current_balance / years_remaining
    drawn_so_far = start_balance - current_balance  # Approximate

    # Is it on pace?
    elapsed_years = max(1, 10 - years_remaining)
    expected_drawn = start_balance * (elapsed_years / 10)
    pace_pct = drawn_so_far / expected_drawn if expected_drawn > 0 else 1.0

    if pace_pct >= 0.9:
        severity = "info"
    elif pace_pct >= 0.6:
        severity = "low"
    else:
        severity = "medium"

    return Recommendation(
        rule_id="WITH-IIRA",
        category="withdrawal",
        severity=severity,
        title="Inherited IRA 10-Year Deadline Pacing",
        description=(
            f"Inherited IRA balance: ${current_balance:,.0f}. "
            f"Deadline: end of {deadline_year} ({years_remaining} years remaining). "
            f"Even annual distribution: ~${annual_target:,.0f}/yr. "
            f"Drawn to date: ~${drawn_so_far:,.0f} ({pace_pct:.0%} of expected pace)."
        ),
        action=(
            f"Distribute ~${annual_target:,.0f}/yr to deplete by {deadline_year}. "
            f"Consider front-loading in low-income years or spreading evenly "
            f"to manage tax bracket impact."
        ),
        impact_estimate=f"~${annual_target:,.0f}/yr needed",
        data={
            "current_balance": current_balance,
            "deadline_year": deadline_year,
            "years_remaining": years_remaining,
            "annual_target": round(annual_target, 2),
            "drawn_so_far": round(drawn_so_far, 2),
            "pace_pct": round(pace_pct, 2),
        },
    )


def check_rmd_projection(
    holdings: list[dict],
    tax_profile: dict,
    rmd_divisors: dict,
) -> Optional[Recommendation]:
    """Rule 8: Project RMD for current or approaching RMD age."""
    ages = tax_profile.get("ages", [72, 70])
    owner_age = max(ages)  # Primary IRA owner
    rmd_start = 73  # SECURE 2.0 for birth years 1951-1959

    divisors = rmd_divisors.get("divisors", {})

    trad_ira_balance = sum(
        float(h.get("market_value", 0))
        for h in holdings
        if h.get("account_type") == "trad_ira"
    )

    if trad_ira_balance == 0:
        return None

    if owner_age < rmd_start - 1:
        return None  # Not relevant yet

    # Current or next year's RMD
    rmd_age = max(owner_age, rmd_start)
    divisor = float(divisors.get(str(rmd_age), 26.5))
    rmd_amount = round(trad_ira_balance / divisor, 2)

    # Project next 5 years
    projections = []
    balance = trad_ira_balance
    for y in range(5):
        age = rmd_age + y
        div = float(divisors.get(str(age), max(1.0, 27 - age * 0.2)))
        yr_rmd = round(balance / div, 2)
        projections.append({"age": age, "divisor": div, "rmd": yr_rmd, "pre_balance": round(balance, 2)})
        balance -= yr_rmd
        balance *= 1.05  # Assume 5% growth

    return Recommendation(
        rule_id="COMP-RMD",
        category="compliance",
        severity="medium" if owner_age >= rmd_start else "low",
        title=f"RMD Projection (Age {rmd_age})",
        description=(
            f"Traditional IRA balance: ${trad_ira_balance:,.0f}. "
            f"Age {rmd_age} divisor: {divisor}. "
            f"Estimated RMD: ${rmd_amount:,.0f}. "
            f"This counts as ordinary income for tax purposes."
        ),
        action=(
            f"Ensure ${rmd_amount:,.0f} is distributed by Dec 31. "
            f"Consider satisfying RMD early in year; excess can fund Roth conversions "
            f"or be redirected as QCD (Qualified Charitable Distribution) up to $105,000."
        ),
        impact_estimate=f"${rmd_amount:,.0f} required distribution",
        data={
            "trad_ira_balance": trad_ira_balance,
            "owner_age": owner_age,
            "rmd_age": rmd_age,
            "divisor": divisor,
            "rmd_amount": rmd_amount,
            "five_year_projection": projections,
        },
    )


# ── Main engine ───────────────────────────────────────────────────────────────

def generate_all_recommendations(
    holdings: list[dict],
    tax_profile: dict,
    constraints: dict,
    cashflow: list[dict],
    rmd_divisors: dict,
    current_year: Optional[int] = None,
) -> list[Recommendation]:
    """
    Run all recommendation rules and return sorted list.

    Parameters
    ----------
    holdings : list[dict]
        From accounts_snapshot.csv
    tax_profile : dict
        From tax_profile.json
    constraints : dict
        From constraints.json
    cashflow : list[dict]
        From cashflow_plan.csv
    rmd_divisors : dict
        From rmd_divisors.json
    current_year : int, optional
        Override for testing

    Returns
    -------
    list[Recommendation] sorted by severity (high→low)
    """
    recs = []

    # Run each rule, collect non-None results
    checks = [
        lambda: check_aapl_concentration(holdings, constraints),
        lambda: check_zero_ltcg_harvesting(holdings, tax_profile, cashflow),
        lambda: check_irmaa_headroom(tax_profile, cashflow),
        lambda: check_roth_conversion_opportunity(holdings, tax_profile, cashflow),
        lambda: check_cash_reserve_adequacy(holdings, cashflow),
        lambda: check_dividend_upgrades(holdings, constraints),
        lambda: check_inherited_ira_pacing(holdings, constraints, current_year),
        lambda: check_rmd_projection(holdings, tax_profile, rmd_divisors),
    ]

    for check in checks:
        try:
            result = check()
            if result is not None:
                recs.append(result)
        except Exception as e:
            # Don't let one rule crash the whole engine
            recs.append(Recommendation(
                rule_id="ERR",
                category="info",
                severity="info",
                title="Rule Error",
                description=f"A recommendation rule encountered an error: {str(e)}",
                action="Review data inputs.",
                impact_estimate="N/A",
                data={"error": str(e)},
            ))

    severity_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    recs.sort(key=lambda r: severity_order.get(r.severity, 9))
    return recs


def generate_recommendations_from_files(
    snapshot_path: str,
    tax_profile_path: str,
    constraints_path: str,
    cashflow_path: str,
    rmd_divisors_path: str,
) -> list[Recommendation]:
    """Convenience: load files and run all rules."""
    holdings = _load_csv(snapshot_path)
    tax_profile = _load_json(tax_profile_path)
    constraints = _load_json(constraints_path)
    cashflow = _load_csv(cashflow_path)
    rmd_divisors = _load_json(rmd_divisors_path)
    return generate_all_recommendations(holdings, tax_profile, constraints, cashflow, rmd_divisors)
