"""
dividend_analyzer.py – RICS Module: Dividend Income Analysis & Projections

Analyzes dividend income across all accounts, projects future income with
growth assumptions, identifies qualified vs ordinary treatment, and finds
upgrade opportunities (low-yield → higher-yield swaps within constraints).
"""

import csv
import json
from dataclasses import dataclass, asdict
from typing import Optional


# ── Dividend growth rate assumptions by asset class ───────────────────────────
DEFAULT_DIV_GROWTH = {
    "us_equity": 0.06,      # ~6% annual dividend growth
    "intl_equity": 0.03,    # ~3%
    "us_bond": 0.00,        # bond coupons don't grow
    "mmf": 0.00,            # money market yields float
}

# Well-known dividend growers
DIVIDEND_ARISTOCRATS = {
    "JNJ", "PG", "KO", "PEP", "MMM", "ABT", "ABBV", "MCD",
    "WMT", "CL", "SYY", "BDX", "EMR", "GPC", "SHW", "ECL",
    "ITW", "ADP", "HRL", "CTAS", "ROP", "CINF", "AFL", "BEN",
}

# High-quality dividend ETFs for upgrade suggestions
DIVIDEND_ETFS = {
    "VYM":  {"yield": 0.029, "asset_class": "us_equity", "label": "Vanguard High Dividend Yield"},
    "SCHD": {"yield": 0.035, "asset_class": "us_equity", "label": "Schwab US Dividend Equity"},
    "DGRO": {"yield": 0.024, "asset_class": "us_equity", "label": "iShares Core Dividend Growth"},
    "VIG":  {"yield": 0.018, "asset_class": "us_equity", "label": "Vanguard Dividend Appreciation"},
    "VYMI": {"yield": 0.045, "asset_class": "intl_equity", "label": "Vanguard Intl High Div Yield"},
    "IDV":  {"yield": 0.060, "asset_class": "intl_equity", "label": "iShares Intl Select Dividend"},
}


@dataclass
class DividendSummary:
    """Per-holding dividend analysis."""
    account_id: str
    account_type: str
    ticker: str
    asset_class: str
    market_value: float
    current_yield: float
    annual_income: float
    is_qualified: bool
    growth_rate: float
    projected_income_5y: float
    projected_income_10y: float
    tax_treatment: str  # "tax-deferred", "tax-free", "taxable-qualified", "taxable-ordinary"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PortfolioIncomeSummary:
    """Aggregate portfolio income analysis."""
    total_annual_income: float
    taxable_qualified_income: float
    taxable_ordinary_income: float
    tax_deferred_income: float
    tax_free_income: float
    weighted_avg_yield: float
    total_market_value: float
    income_coverage_ratio: float  # annual income / annual expenses
    projected_5y_income: float
    projected_10y_income: float
    holdings: list[DividendSummary]


@dataclass
class UpgradeOpportunity:
    """A potential dividend upgrade swap."""
    account_id: str
    account_type: str
    current_ticker: str
    current_yield: float
    current_income: float
    suggested_ticker: str
    suggested_yield: float
    projected_income: float
    income_increase: float
    rationale: str
    feasible: bool  # True if no concentration/tax constraints block it


def classify_tax_treatment(account_type: str, is_qualified: bool) -> str:
    """Determine tax treatment of dividends based on account type and qualification."""
    if account_type == "roth_ira":
        return "tax-free"
    if account_type in ("trad_ira", "inherited_ira", "employer_plan"):
        return "tax-deferred"
    # taxable account
    return "taxable-qualified" if is_qualified else "taxable-ordinary"


def project_income(current_income: float, growth_rate: float, years: int) -> float:
    """Project dividend income forward using compound growth."""
    return round(current_income * (1 + growth_rate) ** years, 2)


def analyze_holdings(
    holdings: list[dict],
    div_growth_overrides: Optional[dict] = None,
    annual_expenses: float = 90000,
) -> PortfolioIncomeSummary:
    """
    Analyze dividend income for a list of holdings.

    Parameters
    ----------
    holdings : list[dict]
        Rows from accounts_snapshot (dicts with standard fields)
    div_growth_overrides : dict, optional
        Override growth rates by asset class
    annual_expenses : float
        Annual spending for coverage ratio calculation

    Returns
    -------
    PortfolioIncomeSummary
    """
    growth_rates = {**DEFAULT_DIV_GROWTH, **(div_growth_overrides or {})}
    summaries = []

    total_income = 0
    taxable_qual = 0
    taxable_ord = 0
    tax_deferred = 0
    tax_free = 0
    total_value = 0

    for h in holdings:
        ticker = h.get("ticker", "")
        acct_type = h.get("account_type", "taxable")
        asset_class = h.get("asset_class", "us_equity")
        market_value = float(h.get("market_value", 0))
        div_yield = float(h.get("qualified_div_yield", 0))
        annual_est = float(h.get("annual_income_est", 0))

        if annual_est == 0 and div_yield > 0:
            annual_est = round(market_value * div_yield, 2)
        elif annual_est > 0 and div_yield == 0 and market_value > 0:
            div_yield = round(annual_est / market_value, 4)

        # Qualify as qualified dividend if yield field is named "qualified_div_yield"
        is_qual = div_yield > 0 and asset_class in ("us_equity", "intl_equity")
        tax_treatment = classify_tax_treatment(acct_type, is_qual)

        gr = growth_rates.get(asset_class, 0.0)
        proj_5y = project_income(annual_est, gr, 5)
        proj_10y = project_income(annual_est, gr, 10)

        summary = DividendSummary(
            account_id=h.get("account_id", ""),
            account_type=acct_type,
            ticker=ticker,
            asset_class=asset_class,
            market_value=market_value,
            current_yield=div_yield,
            annual_income=annual_est,
            is_qualified=is_qual,
            growth_rate=gr,
            projected_income_5y=proj_5y,
            projected_income_10y=proj_10y,
            tax_treatment=tax_treatment,
        )
        summaries.append(summary)
        total_income += annual_est
        total_value += market_value

        if tax_treatment == "taxable-qualified":
            taxable_qual += annual_est
        elif tax_treatment == "taxable-ordinary":
            taxable_ord += annual_est
        elif tax_treatment == "tax-deferred":
            tax_deferred += annual_est
        elif tax_treatment == "tax-free":
            tax_free += annual_est

    wavg_yield = round(total_income / total_value, 4) if total_value > 0 else 0.0
    coverage = round(total_income / annual_expenses, 4) if annual_expenses > 0 else 0.0

    proj_5y_total = sum(s.projected_income_5y for s in summaries)
    proj_10y_total = sum(s.projected_income_10y for s in summaries)

    return PortfolioIncomeSummary(
        total_annual_income=round(total_income, 2),
        taxable_qualified_income=round(taxable_qual, 2),
        taxable_ordinary_income=round(taxable_ord, 2),
        tax_deferred_income=round(tax_deferred, 2),
        tax_free_income=round(tax_free, 2),
        weighted_avg_yield=wavg_yield,
        total_market_value=round(total_value, 2),
        income_coverage_ratio=coverage,
        projected_5y_income=round(proj_5y_total, 2),
        projected_10y_income=round(proj_10y_total, 2),
        holdings=summaries,
    )


def find_upgrade_opportunities(
    holdings: list[dict],
    constraints: Optional[dict] = None,
) -> list[UpgradeOpportunity]:
    """
    Identify positions that could be swapped for higher-yield alternatives.

    Only suggests upgrades in tax-advantaged accounts (no capital gains tax)
    or for positions with minimal embedded gains in taxable accounts.
    """
    opportunities = []
    constr = constraints or {}
    aapl_flag = constr.get("concentration_limits", {}).get("aapl_flag", {})

    for h in holdings:
        ticker = h.get("ticker", "")
        acct_type = h.get("account_type", "taxable")
        asset_class = h.get("asset_class", "us_equity")
        market_value = float(h.get("market_value", 0))
        div_yield = float(h.get("qualified_div_yield", 0))
        annual_est = float(h.get("annual_income_est", 0))
        unrealized = float(h.get("unrealized_gain", 0))

        if market_value < 5000:
            continue

        # Find better alternatives in same asset class
        for etf_ticker, etf_info in DIVIDEND_ETFS.items():
            if etf_ticker == ticker:
                continue
            if etf_info["asset_class"] != asset_class:
                continue
            if etf_info["yield"] <= div_yield + 0.005:  # Need meaningful improvement
                continue

            new_income = round(market_value * etf_info["yield"], 2)
            increase = round(new_income - annual_est, 2)

            # Feasibility checks
            feasible = True
            rationale_parts = []

            if acct_type == "taxable" and unrealized > 1000:
                feasible = False
                rationale_parts.append(f"${unrealized:,.0f} embedded gain in taxable account")

            if ticker.upper() == aapl_flag.get("ticker", "").upper():
                feasible = False
                rationale_parts.append("AAPL concentration constraint")

            if acct_type in ("trad_ira", "inherited_ira", "roth_ira"):
                rationale_parts.append("tax-advantaged account — no capital gains on swap")

            rationale = "; ".join(rationale_parts) if rationale_parts else "eligible for swap"

            opportunities.append(UpgradeOpportunity(
                account_id=h.get("account_id", ""),
                account_type=acct_type,
                current_ticker=ticker,
                current_yield=div_yield,
                current_income=annual_est,
                suggested_ticker=etf_ticker,
                suggested_yield=etf_info["yield"],
                projected_income=new_income,
                income_increase=increase,
                rationale=rationale,
                feasible=feasible,
            ))

    # Sort by income increase descending, feasible first
    opportunities.sort(key=lambda x: (-x.feasible, -x.income_increase))
    return opportunities


def load_holdings_from_csv(csv_path: str) -> list[dict]:
    """Load holdings from accounts_snapshot CSV as list of dicts."""
    with open(csv_path) as f:
        return list(csv.DictReader(f))
