"""
app.py – RICS Streamlit Application

Main entry point for the Retirement Income & Cash-flow Simulator.
Run with: streamlit run app.py

Tabs:
  1. Portfolio Overview       (accounts snapshot summary)
  2. Cash Flow Plan           (income/expenses/lumpy items)
  3. Tax Dashboard            (brackets, SS taxation, IRMAA)
  4. Broker Import        [NEW] – Import & normalize brokerage CSVs
  5. Dividend Analysis    [NEW] – Income analysis, projections, upgrades
  6. Rebalance Simulator  [NEW] – What-if rebalancing with tax impact
  7. Recommendations      [NEW] – Rule-based actionable planning flags
"""

import csv
import json
import io
import os
import sys
from pathlib import Path

# ── Streamlit import with graceful fallback ──────────────────────────────────
try:
    import streamlit as st
    HAS_STREAMLIT = True
except ImportError:
    HAS_STREAMLIT = False

# ── Module imports ───────────────────────────────────────────────────────────
from broker_import import (
    parse_broker_csv, holdings_to_csv, load_accounts_snapshot,
    merge_holdings, HoldingRow, detect_broker,
)
from dividend_analyzer import (
    analyze_holdings, find_upgrade_opportunities, load_holdings_from_csv,
)
from rebalance_sim import (
    score_to_allocation, compute_current_allocation, compute_drift,
    simulate_rebalance, AllocationTarget,
)
from recommendations import (
    generate_all_recommendations, generate_recommendations_from_files,
)

# ── Data paths ───────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
SNAPSHOT_PATH = DATA_DIR / "accounts_snapshot.csv"
CASHFLOW_PATH = DATA_DIR / "cashflow_plan.csv"
TAX_PROFILE_PATH = DATA_DIR / "tax_profile.json"
CONSTRAINTS_PATH = DATA_DIR / "constraints.json"
RMD_DIVISORS_PATH = DATA_DIR / "rmd_divisors.json"
TRADE_LOG_PATH = DATA_DIR / "trade_log.csv"


def load_json(path):
    with open(path) as f:
        return json.load(f)

def load_csv_rows(path):
    with open(path) as f:
        return list(csv.DictReader(f))


# ══════════════════════════════════════════════════════════════════════════════
# TAB IMPLEMENTATIONS
# ══════════════════════════════════════════════════════════════════════════════

def tab_portfolio_overview():
    """Tab 1: Portfolio snapshot summary."""
    st.header("Portfolio Overview")
    holdings = load_csv_rows(SNAPSHOT_PATH)

    # Account totals
    accounts = {}
    for h in holdings:
        acct = h["account_label"]
        mv = float(h["market_value"])
        accounts.setdefault(acct, {"type": h["account_type"], "value": 0, "positions": 0})
        accounts[acct]["value"] += mv
        accounts[acct]["positions"] += 1

    total_portfolio = sum(a["value"] for a in accounts.values())
    st.metric("Total Portfolio Value", f"${total_portfolio:,.0f}")

    cols = st.columns(len(accounts))
    for col, (name, info) in zip(cols, accounts.items()):
        col.metric(name, f"${info['value']:,.0f}",
                   delta=f"{info['value']/total_portfolio:.1%} of total")

    # Allocation pie
    st.subheader("Asset Allocation")
    alloc = compute_current_allocation(holdings)
    alloc_data = [{"Asset Class": k.replace("_", " ").title(), "Pct": round(v * 100, 1)}
                  for k, v in alloc.items()]
    st.bar_chart(data={d["Asset Class"]: d["Pct"] for d in alloc_data})

    # Holdings table
    st.subheader("All Holdings")
    display_cols = ["account_label", "ticker", "asset_class", "shares", "price",
                    "market_value", "unrealized_gain", "annual_income_est"]
    table_data = [{c: h.get(c, "") for c in display_cols} for h in holdings]
    st.dataframe(table_data, use_container_width=True)


def tab_cashflow():
    """Tab 2: Cash flow plan."""
    st.header("Cash Flow Plan")
    cashflow = load_csv_rows(CASHFLOW_PATH)

    income = [c for c in cashflow if c["category"] == "income"]
    expenses = [c for c in cashflow if c["category"] == "expense"]

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Income Sources")
        for item in income:
            st.write(f"**{item['subcategory']}**: ${float(item['amount']):,.0f}/yr")
        total_income = sum(float(i["amount"]) for i in income)
        st.metric("Total Annual Income", f"${total_income:,.0f}")

    with col2:
        st.subheader("Annual Expenses")
        annual_exp = [e for e in expenses if e.get("frequency") == "annual"]
        for item in annual_exp:
            st.write(f"**{item['subcategory']}**: ${float(item['amount']):,.0f}/yr")
        total_exp = sum(float(e["amount"]) for e in annual_exp)
        st.metric("Total Annual Expenses", f"${total_exp:,.0f}")

    st.subheader("Lumpy / One-Time Expenses")
    lumpy = [e for e in expenses if "one_time" in e.get("frequency", "")]
    for item in lumpy:
        st.write(f"**{item['year']} – {item['subcategory']}**: ${float(item['amount']):,.0f}")


def tab_tax_dashboard():
    """Tab 3: Tax profile summary."""
    st.header("Tax Dashboard")
    tp = load_json(TAX_PROFILE_PATH)

    col1, col2, col3 = st.columns(3)
    col1.metric("Filing Status", tp["filing_status"].upper())
    col2.metric("Ages", f"{tp['ages'][0]} / {tp['ages'][1]}")
    col3.metric("Social Security", f"${tp['ss_combined_annual']:,.0f}/yr")

    st.subheader("Standard Deduction")
    st.write(f"Base: ${tp['standard_deduction_base']:,.0f} + "
             f"Senior bonus: 2 × ${tp['standard_deduction_senior_bonus_each']:,.0f} = "
             f"**${tp['effective_standard_deduction']:,.0f}**")

    st.subheader("Federal Brackets (MFJ 2025)")
    bracket_data = []
    for b in tp["federal_brackets_mfj_2025"]:
        upper = f"${b['upper']:,.0f}" if b['upper'] else "∞"
        bracket_data.append({
            "Range": f"${b['lower']:,.0f} – {upper}",
            "Rate": f"{b['rate']:.0%}"
        })
    st.table(bracket_data)

    st.subheader("IRMAA Thresholds (MFJ 2025)")
    irmaa_data = []
    for tier in tp["irmaa_thresholds_mfj_2025"]:
        upper = f"${tier['magi_upper']:,.0f}" if tier['magi_upper'] else "∞"
        irmaa_data.append({
            "MAGI Range": f"${tier['magi_lower']:,.0f} – {upper}",
            "Part B Surcharge": f"${tier['part_b_surcharge']:,.0f}",
            "Part D Surcharge": f"${tier['part_d_surcharge']:,.0f}",
        })
    st.table(irmaa_data)


def tab_broker_import():
    """Tab 4: Broker CSV import and normalization."""
    st.header("Broker Import")
    st.write("Upload a CSV export from Fidelity, Schwab, Vanguard, or any broker "
             "and normalize it into the RICS format.")

    uploaded = st.file_uploader("Upload broker CSV", type=["csv", "txt"])
    broker = st.selectbox("Broker format", ["auto", "fidelity", "schwab", "vanguard", "generic"])
    snapshot_date = st.date_input("Snapshot date")
    acct_type_override = st.selectbox(
        "Account type override (optional)",
        [None, "taxable", "trad_ira", "roth_ira", "inherited_ira"],
    )

    if uploaded is not None:
        csv_text = uploaded.read().decode("utf-8")
        rows = parse_broker_csv(
            csv_text,
            broker=broker,
            snapshot_date=str(snapshot_date),
            account_type_override=acct_type_override,
        )

        st.success(f"Parsed {len(rows)} holdings")

        if rows:
            st.subheader("Imported Holdings")
            display = [r.to_dict() for r in rows]
            st.dataframe(display, use_container_width=True)

            # Merge option
            if st.button("Merge into current snapshot"):
                existing = load_accounts_snapshot(str(SNAPSHOT_PATH))
                merged = merge_holdings(existing, rows)
                csv_out = holdings_to_csv(merged)
                st.download_button(
                    "Download merged snapshot CSV",
                    csv_out,
                    file_name="accounts_snapshot_merged.csv",
                    mime="text/csv",
                )
                st.info(f"Merged: {len(merged)} total holdings "
                        f"({len(rows)} imported, {len(existing)} existing)")

            # Standalone download
            csv_out = holdings_to_csv(rows)
            st.download_button(
                "Download imported holdings CSV",
                csv_out,
                file_name="broker_import.csv",
                mime="text/csv",
            )


def tab_dividend_analysis():
    """Tab 5: Dividend income analysis and projections."""
    st.header("Dividend Analysis")
    holdings = load_csv_rows(SNAPSHOT_PATH)
    constraints = load_json(CONSTRAINTS_PATH)

    annual_expenses = st.number_input("Annual expenses for coverage ratio",
                                       value=90000, step=5000)
    result = analyze_holdings(holdings, annual_expenses=annual_expenses)

    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Annual Income", f"${result.total_annual_income:,.0f}")
    col2.metric("Weighted Avg Yield", f"{result.weighted_avg_yield:.2%}")
    col3.metric("Income Coverage", f"{result.income_coverage_ratio:.1%}")
    col4.metric("5-Year Projected", f"${result.projected_5y_income:,.0f}")

    # Income by tax treatment
    st.subheader("Income by Tax Treatment")
    tax_data = {
        "Taxable – Qualified": result.taxable_qualified_income,
        "Taxable – Ordinary": result.taxable_ordinary_income,
        "Tax-Deferred (IRA)": result.tax_deferred_income,
        "Tax-Free (Roth)": result.tax_free_income,
    }
    st.bar_chart(tax_data)

    # Holdings detail
    st.subheader("Holding-Level Income")
    detail = []
    for h in result.holdings:
        detail.append({
            "Account": h.account_id,
            "Ticker": h.ticker,
            "Value": f"${h.market_value:,.0f}",
            "Yield": f"{h.current_yield:.2%}",
            "Income": f"${h.annual_income:,.0f}",
            "5Y Proj": f"${h.projected_income_5y:,.0f}",
            "10Y Proj": f"${h.projected_income_10y:,.0f}",
            "Tax Treatment": h.tax_treatment,
        })
    st.dataframe(detail, use_container_width=True)

    # Upgrade opportunities
    st.subheader("Dividend Upgrade Opportunities")
    opps = find_upgrade_opportunities(holdings, constraints)
    if opps:
        for opp in opps[:10]:
            icon = "✅" if opp.feasible else "⚠️"
            st.write(
                f"{icon} **{opp.current_ticker}** ({opp.current_yield:.2%}) → "
                f"**{opp.suggested_ticker}** ({opp.suggested_yield:.2%}) | "
                f"+${opp.income_increase:,.0f}/yr | _{opp.rationale}_"
            )
    else:
        st.info("No upgrade opportunities found.")


def tab_rebalance_simulator():
    """Tab 6: Rebalance simulator with what-if scenarios."""
    st.header("Rebalance Simulator")
    holdings = load_csv_rows(SNAPSHOT_PATH)
    constraints = load_json(CONSTRAINTS_PATH)

    # Controls
    st.subheader("Target Allocation")
    score = st.slider("Aggressiveness Score (0=conservative, 100=aggressive)",
                       0, 100, 45)
    auto_target = score_to_allocation(score)

    col1, col2, col3, col4 = st.columns(4)
    us_eq = col1.number_input("US Equity %", 0, 100, int(auto_target.us_equity * 100))
    intl_eq = col2.number_input("Intl Equity %", 0, 100, int(auto_target.intl_equity * 100))
    bonds = col3.number_input("US Bonds %", 0, 100, int(auto_target.us_bond * 100))
    cash = col4.number_input("Cash/MMF %", 0, 100, int(auto_target.mmf * 100))

    target = AllocationTarget(us_eq / 100, intl_eq / 100, bonds / 100, cash / 100)
    if not target.validate():
        st.warning(f"Allocations sum to {us_eq + intl_eq + bonds + cash}% (should be 100%)")

    band = st.slider("Rebalance band tolerance", 0.01, 0.20, 0.05, 0.01)

    if st.button("Run Simulation") or True:  # Auto-run
        result = simulate_rebalance(holdings, target, constraints=constraints,
                                     rebalance_band=band)

        # Summary
        st.subheader("Results")
        col1, col2, col3 = st.columns(3)
        col1.metric("Portfolio Value", f"${result.total_portfolio_value:,.0f}")
        col2.metric("Estimated Tax Cost", f"${result.total_tax_cost:,.0f}")
        col3.metric("Turnover", f"{result.net_turnover_pct:.1%}")

        # Drift
        st.subheader("Current vs Target Allocation")
        drift_data = []
        for ac in ["us_equity", "intl_equity", "us_bond", "mmf"]:
            drift_data.append({
                "Asset Class": ac.replace("_", " ").title(),
                "Current": f"{result.current_allocation.get(ac, 0):.1%}",
                "Target": f"{result.target_allocation.get(ac, 0):.1%}",
                "Drift": f"{result.drift.get(ac, 0):+.1%}",
            })
        st.table(drift_data)

        # Proposed trades
        if result.trades:
            st.subheader(f"Proposed Trades ({len(result.trades)})")
            trades_display = []
            for t in result.trades:
                trades_display.append({
                    "Account": t.account_id,
                    "Type": t.account_type,
                    "Action": t.action.upper(),
                    "Ticker": t.ticker,
                    "Shares": f"{t.shares:,.1f}",
                    "Value": f"${t.trade_value:,.0f}",
                    "Est. Tax": f"${t.estimated_tax:,.0f}",
                    "Rationale": t.rationale,
                })
            st.dataframe(trades_display, use_container_width=True)

        # Blocked trades
        if result.blocked_trades:
            st.subheader(f"Blocked Trades ({len(result.blocked_trades)})")
            for t in result.blocked_trades:
                st.warning(f"**{t.ticker}** ({t.account_id}): {t.block_reason}")


def tab_recommendations():
    """Tab 7: Rule-based recommendations engine."""
    st.header("Recommendations")
    st.write("Actionable planning opportunities based on your complete financial picture.")

    holdings = load_csv_rows(SNAPSHOT_PATH)
    tax_profile = load_json(TAX_PROFILE_PATH)
    constraints = load_json(CONSTRAINTS_PATH)
    cashflow = load_csv_rows(CASHFLOW_PATH)
    rmd_divisors = load_json(RMD_DIVISORS_PATH)

    recs = generate_all_recommendations(
        holdings, tax_profile, constraints, cashflow, rmd_divisors
    )

    if not recs:
        st.success("No actionable recommendations at this time.")
        return

    # Severity legend
    severity_icons = {"high": "🔴", "medium": "🟡", "low": "🟢", "info": "ℹ️"}
    category_icons = {
        "tax": "💰", "risk": "⚠️", "income": "📈",
        "withdrawal": "🏦", "compliance": "📋", "info": "ℹ️",
    }

    st.write(f"**{len(recs)} recommendations found** — sorted by priority")

    for rec in recs:
        sev_icon = severity_icons.get(rec.severity, "❓")
        cat_icon = category_icons.get(rec.category, "")
        with st.expander(f"{sev_icon} {cat_icon} **{rec.title}** — _{rec.impact_estimate}_",
                          expanded=(rec.severity in ("high", "medium"))):
            st.write(rec.description)
            st.write(f"**Suggested Action:** {rec.action}")

            # Show key data points
            if rec.data:
                with st.container():
                    st.caption("Supporting Data")
                    # Show top-level numeric data
                    data_items = {k: v for k, v in rec.data.items()
                                   if isinstance(v, (int, float, str)) and k != "error"}
                    if data_items:
                        data_cols = st.columns(min(4, len(data_items)))
                        for col, (k, v) in zip(data_cols, list(data_items.items())[:4]):
                            label = k.replace("_", " ").title()
                            if isinstance(v, float) and v > 100:
                                col.metric(label, f"${v:,.0f}")
                            elif isinstance(v, float) and v <= 1:
                                col.metric(label, f"{v:.1%}")
                            else:
                                col.metric(label, str(v))

    # Export
    recs_json = json.dumps([r.to_dict() for r in recs], indent=2)
    st.download_button("Download Recommendations (JSON)", recs_json,
                       file_name="rics_recommendations.json", mime="application/json")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    if not HAS_STREAMLIT:
        print("Streamlit not installed. Install with: pip install streamlit")
        print("Running quick self-test instead...\n")

        # Self-test mode: run recommendations engine and print summary
        recs = generate_recommendations_from_files(
            str(SNAPSHOT_PATH), str(TAX_PROFILE_PATH), str(CONSTRAINTS_PATH),
            str(CASHFLOW_PATH), str(RMD_DIVISORS_PATH),
        )
        print(f"RICS Recommendations Engine — {len(recs)} findings:\n")
        for rec in recs:
            print(f"  [{rec.severity.upper():6s}] {rec.title}")
            print(f"           {rec.impact_estimate}")
            print()
        return

    st.set_page_config(
        page_title="RICS – Retirement Income & Cash-flow Simulator",
        page_icon="📊",
        layout="wide",
    )
    st.title("📊 RICS – Retirement Income & Cash-flow Simulator")

    tabs = st.tabs([
        "Portfolio Overview",
        "Cash Flow Plan",
        "Tax Dashboard",
        "Broker Import",
        "Dividend Analysis",
        "Rebalance Simulator",
        "Recommendations",
    ])

    with tabs[0]:
        tab_portfolio_overview()
    with tabs[1]:
        tab_cashflow()
    with tabs[2]:
        tab_tax_dashboard()
    with tabs[3]:
        tab_broker_import()
    with tabs[4]:
        tab_dividend_analysis()
    with tabs[5]:
        tab_rebalance_simulator()
    with tabs[6]:
        tab_recommendations()


if __name__ == "__main__":
    main()
