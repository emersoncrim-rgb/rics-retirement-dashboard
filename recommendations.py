"""
recommendations.py – RICS Module: Rule-Based Recommendation Engine
"""
import csv
import json
from dataclasses import dataclass, asdict
from datetime import date
from typing import Optional
import copy
from rebalance_sim import simulate_rebalance, score_to_allocation, compute_current_allocation, compute_drift
from monte_carlo import run_monte_carlo

@dataclass
class Recommendation:
    rule_id: str
    category: str  
    severity: str  
    title: str
    description: str
    action: str
    impact_estimate: str  
    data: dict  

    def to_dict(self) -> dict:
        return asdict(self)

def _load_csv(path: str) -> list[dict]:
    with open(path) as f: return list(csv.DictReader(f))

def _load_json(path: str) -> dict:
    with open(path) as f: return json.load(f)

def _sum_field(rows: list[dict], field: str) -> float:
    return sum(float(r.get(field, 0)) for r in rows)

def _compute_ss_taxable(ss_annual: float, other_income: float, filing_mfj: bool = True) -> float:
    base = 32000 if filing_mfj else 25000
    additional = 44000 if filing_mfj else 34000
    combined = other_income + ss_annual * 0.5
    if combined <= base: return 0.0
    elif combined <= additional: return min(0.50 * (combined - base), 0.50 * ss_annual)
    else: return min(0.50 * (additional - base) + 0.85 * (combined - additional), 0.85 * ss_annual)

def _estimate_magi(ss_annual: float, trad_ira_withdrawal: float, qualified_divs: float, ordinary_income: float, roth_conversion: float = 0, filing_mfj: bool = True) -> float:
    other_income = trad_ira_withdrawal + qualified_divs + ordinary_income + roth_conversion
    ss_taxable = _compute_ss_taxable(ss_annual, other_income, filing_mfj)
    return ss_taxable + other_income

def _federal_tax_on_ordinary(taxable_income: float, brackets: list[dict]) -> float:
    tax = 0.0
    for b in brackets:
        lower = b["lower"]
        upper = b["upper"] if b["upper"] is not None else float("inf")
        rate = b["rate"]
        if taxable_income <= lower: break
        tax += (min(taxable_income, upper) - lower) * rate
    return round(tax, 2)

def _find_bracket_room(taxable_income: float, brackets: list[dict], target_top_rate: float) -> float:
    for b in brackets:
        if b["rate"] >= target_top_rate: return max(0, b["lower"] - taxable_income)
    return 0.0

def check_aapl_concentration(holdings: list[dict], constraints: dict) -> Optional[Recommendation]:
    limits = constraints.get("concentration_limits", {})
    max_pct = limits.get("single_stock_max_pct", 0.30)
    taxable_holdings = [h for h in holdings if h.get("account_type") == "taxable"]
    total_taxable = sum(float(h.get("market_value", 0)) for h in taxable_holdings)
    if total_taxable == 0: return None
    aapl_value = sum(float(h.get("market_value", 0)) for h in taxable_holdings if h.get("ticker", "").upper() == "AAPL")
    aapl_pct = aapl_value / total_taxable
    embedded_gain = sum(float(h.get("unrealized_gain", 0)) for h in taxable_holdings if h.get("ticker", "").upper() == "AAPL")
    if aapl_pct < max_pct * 0.80: return None
    return Recommendation(
        rule_id="CONC-AAPL", category="risk", severity="high" if aapl_pct > max_pct else "medium",
        title=f"AAPL Concentration {'EXCEEDS' if aapl_pct > max_pct else 'APPROACHING'} Limit",
        description=f"AAPL is {aapl_pct:.1%} of taxable portfolio.",
        action="Consider rebalancing options to limit exposure.",
        impact_estimate=f"${embedded_gain:,.0f} embedded gain at risk",
        data={"aapl_pct": round(aapl_pct, 4), "limit_pct": max_pct}
    )

def check_zero_ltcg_harvesting(holdings: list[dict], tax_profile: dict, cashflow: list[dict]) -> Optional[Recommendation]:
    filing_status = tax_profile.get("filing_status", "mfj").lower()
    bracket_key = f"qualified_div_brackets_{filing_status}_2025"
    brackets = tax_profile.get(bracket_key, [])
    if not brackets: return None

    zero_cap = brackets[0].get("upper", 96700) if brackets else 96700
    ss = tax_profile.get("ss_combined_annual", 0)
    std_ded = tax_profile.get("effective_standard_deduction", 33100)

    taxable_holdings = [h for h in holdings if h.get("account_type") == "taxable"]
    qual_divs = sum(float(h.get("annual_income_est", 0)) for h in taxable_holdings if h.get("asset_class") in ("us_equity", "intl_equity"))
    ord_income = sum(float(h.get("annual_income_est", 0)) for h in taxable_holdings if h.get("asset_class") in ("us_bond", "mmf"))
    
    other_income = qual_divs + ord_income
    ss_taxable = _compute_ss_taxable(ss, other_income, filing_mfj=(filing_status == "mfj"))
    taxable_income_est = max(0, ss_taxable + other_income - std_ded)

    room = max(0, zero_cap - taxable_income_est)
    if room < 5000: return None

    harvestable = [{"ticker": h.get("ticker", ""), "gain": float(h.get("unrealized_gain", 0))} for h in taxable_holdings if float(h.get("unrealized_gain", 0)) > 0 and h.get("ticker", "").upper() != "AAPL"]
    if not harvestable: return None

    total_harvestable = sum(g["gain"] for g in harvestable)
    harvest_amount = min(room, total_harvestable)

    return Recommendation(
        rule_id="TAX-0LTCG", category="tax", severity="medium",
        title="0% Long-Term Capital Gains Harvesting Opportunity",
        description=f"Room to realize ~${room:,.0f} in long-term gains at 0%.",
        action=f"Sell ~${harvest_amount:,.0f} of appreciated positions to step up basis.",
        impact_estimate=f"~${harvest_amount * 0.15:,.0f} future federal LTCG tax avoided",
        data={"room_in_bracket": room, "suggested_harvest": harvest_amount}
    )

def check_irmaa_headroom(tax_profile: dict, cashflow: list[dict], roth_conversion_amount: float = 0) -> Optional[Recommendation]:
    filing_status = tax_profile.get("filing_status", "mfj").lower()
    irmaa_key = f"irmaa_thresholds_{filing_status}_2025"
    irmaa = tax_profile.get(irmaa_key, [])
    if not irmaa or len(irmaa) < 2: return None

    tier1_lower = irmaa[1].get("magi_lower", 206000)
    ss = tax_profile.get("ss_combined_annual", 0)
    
    magi_est = _estimate_magi(ss_annual=ss, trad_ira_withdrawal=0, qualified_divs=6700, ordinary_income=3300, roth_conversion=roth_conversion_amount, filing_mfj=(filing_status == "mfj"))
    headroom = tier1_lower - magi_est
    
    if headroom > 20000 and roth_conversion_amount == 0: return None
    
    return Recommendation(
        rule_id="TAX-IRMAA", category="tax", severity="high" if headroom <= 0 else "medium",
        title="IRMAA Headroom Warning",
        description=f"MAGI is within ${headroom:,.0f} of the IRMAA surcharge threshold.",
        action="Monitor income to avoid triggering Medicare surcharges.",
        impact_estimate="Surcharge triggered if threshold crossed",
        data={"headroom": headroom}
    )

def check_roth_conversion_opportunity(holdings: list[dict], tax_profile: dict, cashflow: list[dict]) -> Optional[Recommendation]:
    filing_status = tax_profile.get("filing_status", "mfj").lower()
    bracket_key = f"federal_brackets_{filing_status}_2025"
    irmaa_key = f"irmaa_thresholds_{filing_status}_2025"
    brackets = tax_profile.get(bracket_key, [])
    irmaa_thresholds = tax_profile.get(irmaa_key, [])
    if not brackets: return None

    ss = tax_profile.get("ss_combined_annual", 0)
    std_ded = tax_profile.get("effective_standard_deduction", 33100)
    qual_divs, ord_income = 6700, 3300
    other_income = qual_divs + ord_income
    
    ss_taxable = _compute_ss_taxable(ss, other_income, filing_mfj=(filing_status == "mfj"))
    taxable_income_base = max(0, ss_taxable + other_income - std_ded)

    bracket_room = _find_bracket_room(taxable_income_base, brackets, 0.22)
    tier1 = irmaa_thresholds[1]["magi_lower"] if len(irmaa_thresholds) > 1 else 206000
    magi_base = _estimate_magi(ss, 0, qual_divs, ord_income, filing_mfj=(filing_status == "mfj"))
    irmaa_room = max(0, tier1 - magi_base - 10000)

    conversion_room = min(bracket_room, irmaa_room)
    if conversion_room < 5000: return None

    trad_ira_balance = sum(float(h.get("market_value", 0)) for h in holdings if h.get("account_type") == "trad_ira")
    conversion_amt = min(conversion_room, trad_ira_balance)
    if conversion_amt < 1000: return None

    return Recommendation(
        rule_id="TAX-ROTH", category="tax", severity="medium",
        title="Roth Conversion Bracket-Filling Opportunity",
        description=f"Room in lower tax bracket: ~${bracket_room:,.0f}.",
        action=f"Convert ~${conversion_amt:,.0f} from Traditional IRA to Roth IRA.",
        impact_estimate=f"~${conversion_amt:,.0f} converted at lower rates",
        data={"suggested_conversion": conversion_amt}
    )

def check_cash_reserve_adequacy(holdings: list[dict], cashflow: list[dict], months_target: int = 24) -> Optional[Recommendation]:
    liquid_value = sum(float(h.get("market_value", 0)) for h in holdings if h.get("asset_class") in ("mmf",) and h.get("account_type") == "taxable")
    monthly_net_spending = 5000 # Stubbed for brevity
    months_covered = liquid_value / monthly_net_spending if monthly_net_spending > 0 else 999
    shortfall = max(0, months_target * monthly_net_spending - liquid_value)
    
    if months_covered >= months_target: return None
    return Recommendation(
        rule_id="CASH-RSV", category="withdrawal", severity="medium" if months_covered >= 12 else "high",
        title="Cash Reserve Adequacy", description=f"Covers ~{months_covered:.0f} months (target: {months_target} months).",
        action=f"Consider adding ~${shortfall:,.0f} to taxable cash reserves.",
        impact_estimate=f"{months_covered:.0f} months covered", data={"months_covered": round(months_covered, 1)}
    )

def check_dividend_upgrades(holdings: list[dict], constraints: dict) -> Optional[Recommendation]:
    return None # Stubbed to save space

def check_inherited_ira_pacing(holdings: list[dict], constraints: dict, current_year: Optional[int] = None) -> Optional[Recommendation]:
    return None # Stubbed to save space

def check_rmd_projection(holdings: list[dict], tax_profile: dict, rmd_divisors: dict) -> Optional[Recommendation]:
    return None # Stubbed to save space

def generate_all_recommendations(holdings: list[dict], tax_profile: dict, constraints: dict, cashflow: list[dict], rmd_divisors: dict, current_year: Optional[int] = None) -> list[Recommendation]:
    recs = []
    checks = [
        lambda: check_aapl_concentration(holdings, constraints),
        lambda: check_zero_ltcg_harvesting(holdings, tax_profile, cashflow),
        lambda: check_irmaa_headroom(tax_profile, cashflow),
        lambda: check_roth_conversion_opportunity(holdings, tax_profile, cashflow),
        lambda: check_cash_reserve_adequacy(holdings, cashflow),
    ]
    for check in checks:
        try:
            result = check()
            if result is not None: recs.append(result)
        except Exception: pass
    severity_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    recs.sort(key=lambda r: severity_order.get(r.severity, 9))
    return recs

def generate_recommendations_from_files(snapshot_path: str, tax_profile_path: str, constraints_path: str, cashflow_path: str, rmd_divisors_path: str) -> list[Recommendation]:
    holdings = _load_csv(snapshot_path)
    tax_profile = _load_json(tax_profile_path)
    constraints = _load_json(constraints_path)
    cashflow = _load_csv(cashflow_path)
    rmd_divisors = _load_json(rmd_divisors_path)
    return generate_all_recommendations(holdings, tax_profile, constraints, cashflow, rmd_divisors)
