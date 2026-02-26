"""
app.py — Streamlit dashboard for the Retirement Income Control System (RICS).

Run:  streamlit run app.py
"""

import sys
import json
import copy
import tempfile
import shutil
from pathlib import Path

import os
import time

import numpy as np
import streamlit as st
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# ── Ensure project root is on path ──
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Live quotes (optional)
from quotes import fetch_quotes_finnhub
from live_overlay import apply_price_overrides

from ingest import ingest_all, compute_today_summary, DEFAULT_PATHS
from risk import (
    risk_report, score_components, map_holdings_to_buckets,
    compute_aggressiveness_score, describe_posture,
)
from deterministic import build_projection_from_ingested
from tax_irmaa import simulate_year_tax_effects
from rmd import load_rmd_divisors, compute_rmd_amount, project_rmd_series, generate_inherited_ira_schedule
from mc_sim import run_mc_from_ingested
from trip_simulator import trip_impact, compare_funding_options

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="RICS — Retirement Income Control System",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Sidebar: Data Loading
# ---------------------------------------------------------------------------

st.sidebar.title("📂 Data Source")
use_sample = st.sidebar.checkbox("Use sample data", value=True)
st.sidebar.divider()
st.sidebar.subheader("📈 Live Quotes (Optional)")
st.sidebar.subheader("🗂️ Data mode")
data_mode = st.sidebar.radio(
    "Use which prices for calculations?",
    ["Disk snapshot (last exported)", "Live overlay (current quotes)"],
    index=0,
    help="Disk snapshot uses data/ files as-is. Live overlay fetches quotes and applies them in-memory.",
)

use_live_quotes = st.sidebar.checkbox(
    "Overlay live prices (Finnhub)",
    value=False,
    help="Fetch current prices from Finnhub and overlay them in-memory. Snapshot file is unchanged.",
)
api_key_input = st.sidebar.text_input(
    "Finnhub API key (optional)",
    type="password",
    help="Leave blank to use FINNHUB_API_KEY environment variable.",
)
refresh_quotes = st.sidebar.button("Refresh quotes")


data_dir = PROJECT_ROOT / "data"

if not use_sample:
    st.sidebar.markdown("**Upload your files:**")
    uploaded_accounts = st.sidebar.file_uploader("accounts_snapshot.csv", type="csv")
    uploaded_trades = st.sidebar.file_uploader("trade_log.csv", type="csv")
    uploaded_cashflow = st.sidebar.file_uploader("cashflow_plan.csv", type="csv")
    uploaded_tax = st.sidebar.file_uploader("tax_profile.json", type="json")
    uploaded_constraints = st.sidebar.file_uploader("constraints.json", type="json")

    if all([uploaded_accounts, uploaded_trades, uploaded_cashflow, uploaded_tax, uploaded_constraints]):
        tmp = Path(tempfile.mkdtemp())
        for name, f in [("accounts_snapshot.csv", uploaded_accounts),
                         ("trade_log.csv", uploaded_trades),
                         ("cashflow_plan.csv", uploaded_cashflow),
                         ("tax_profile.json", uploaded_tax),
                         ("constraints.json", uploaded_constraints)]:
            (tmp / name).write_bytes(f.read())
        data_dir = tmp
    else:
        st.sidebar.warning("Upload all 5 files, or check 'Use sample data'")
        st.stop()


# ── Load data ──
# ── Load data ──
@st.cache_data(ttl=300, show_spinner=False)
def load_data(data_path: str, want_live: bool, api_key: str, refresh_nonce: int):
    # refresh_nonce exists only to bust Streamlit cache when user clicks Refresh quotes
    paths = {k: str(Path(data_path) / v.split("/")[-1]) for k, v in DEFAULT_PATHS.items()}

    # Always ingest from disk first (baseline / last export)
    ingested_disk = ingest_all(paths)
    snapshot_accounts = copy.deepcopy(ingested_disk["accounts"])  # for comparison UI

    # Prepare live copy (starts identical to disk)
    ingested_live = copy.deepcopy(ingested_disk)

    quote_meta = {"enabled": False, "fetched": 0, "updated": 0, "asof_epoch": None}
    quote_failures = {}
    price_overrides = {}

    if want_live:
        api_key_eff = (api_key or "").strip() or os.environ.get("FINNHUB_API_KEY", "")
        if not api_key_eff:
            quote_failures["__config__"] = {"error": "API key missing (set FINNHUB_API_KEY or paste in sidebar)."}
        else:
            tickers = sorted({r.get("ticker", "").strip() for r in ingested_disk["accounts"] if r.get("ticker")})
            try:
                quotes = fetch_quotes_finnhub(tickers, api_key=api_key_eff, timeout=8)
            except Exception as exc:
                quotes = {}
                quote_failures["__fetch__"] = {"error": str(exc)}

            for t, q in (quotes or {}).items():
                if isinstance(q, dict) and "price" in q and q["price"] not in (None, 0, ""):
                    try:
                        price_overrides[t] = float(q["price"])
                    except Exception as exc:
                        quote_failures[t] = {"error": f"bad price: {exc}", "value": q}
                else:
                    quote_failures[t] = q

            if price_overrides:
                apply_price_overrides(ingested_live["accounts"], price_overrides)

            quote_meta = {
                "enabled": True,
                "fetched": len(quotes or {}),
                "updated": len(price_overrides),
                "asof_epoch": int(time.time()),
            }

    # Precompute summaries + risk for both modes (fast; keeps tabs snappy)
    summary_disk = compute_today_summary(ingested_disk)
    rr_disk = risk_report(ingested_disk["accounts"])
    sc_disk = score_components(ingested_disk["accounts"])

    summary_live = compute_today_summary(ingested_live) if want_live else summary_disk
    rr_live = risk_report(ingested_live["accounts"]) if want_live else rr_disk
    sc_live = score_components(ingested_live["accounts"]) if want_live else sc_disk

    return (
        ingested_disk, summary_disk, rr_disk, sc_disk,
        ingested_live, summary_live, rr_live, sc_live,
        quote_meta, quote_failures, price_overrides, snapshot_accounts
    )



(
    ingested_disk, summary_disk, rr_disk, sc_disk,
    ingested_live, summary_live, rr_live, sc_live,
    quote_meta, quote_failures, price_overrides, snapshot_accounts
) = load_data(
    str(data_dir),
    use_live_quotes,
    api_key_input,
    int(refresh_quotes),
)

# Choose which dataset drives calculations across tabs
use_live_for_calcs = (data_mode == "Live overlay (current quotes)")
ingested = ingested_live if use_live_for_calcs else ingested_disk
summary = summary_live if use_live_for_calcs else summary_disk
rr = rr_live if use_live_for_calcs else rr_disk
sc = sc_live if use_live_for_calcs else sc_disk

# Flags
if ingested["flags"]:
    st.sidebar.error(f"⚠ {len(ingested['flags'])} validation flag(s)")
    for f in ingested["flags"]:
        st.sidebar.caption(f"• {f}")


# Live quotes status
if quote_meta.get("enabled"):
    st.sidebar.success(f"Live quotes: {quote_meta.get('updated',0)}/{quote_meta.get('fetched',0)} tickers updated")
    if quote_meta.get("asof_epoch"):
        st.sidebar.caption("As of: " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(quote_meta["asof_epoch"])))
    with st.sidebar.expander("Quote coverage (details)", expanded=False):
        st.write("Updated tickers:", sorted(price_overrides.keys()))
        if quote_failures:
            st.write("Skipped/failed tickers:")
            st.json(quote_failures)
else:
    if use_live_quotes and quote_failures:
        st.sidebar.error("Live quotes enabled but could not apply prices.")
        with st.sidebar.expander("Details", expanded=False):
            st.json(quote_failures)

# ═══════════════════════════════════════════════════════════════════════════
# TAB LAYOUT
# ═══════════════════════════════════════════════════════════════════════════

tab_today, tab_risk, tab_determ, tab_mc, tab_trip = st.tabs([
    "📋 Today", "⚖️ Risk & Allocation", "📈 Deterministic", "🎲 Monte Carlo", "✈️ Trip Simulator"
])


# ═══════════════════════════════════════════════════════════════════════════
# TAB 1: TODAY SUMMARY
# ═══════════════════════════════════════════════════════════════════════════

with tab_today:
    st.header("Portfolio Snapshot")

    # Top-level metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Portfolio", f"${summary['total_portfolio']:,.0f}")
    c2.metric("Equity", f"{summary['equity_pct']:.1%}")
    c3.metric("Cash Reserve", f"{summary['cash_reserve_months']} mo")
    c4.metric("Score", f"{rr['aggressiveness_score']}/100",
              delta=rr["posture"]["label"])

    st.divider()

    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("Account Totals")
        for aid, val in summary["account_totals"].items():
            pct = val / summary["total_portfolio"] * 100
            st.markdown(f"**{aid}**: ${val:,.0f} ({pct:.1f}%)")

        st.subheader("Annual Income")
        inc = summary["income_summary"]
        st.markdown(f"Social Security: **${inc['social_security']:,.0f}**")
        st.markdown(f"Qualified Dividends: **${inc['qualified_dividends']:,.0f}**")
        st.markdown(f"Ordinary Dividends: **${inc['ordinary_dividends']:,.0f}**")
        st.markdown(f"**Total: ${inc['total_income']:,.0f}**/yr")

        st.subheader("Expenses")
        st.markdown(f"Annual recurring: **${summary['annual_expenses']:,.0f}**")
        st.markdown(f"Monthly: **${summary['monthly_expense']:,.0f}**")

    with col_right:
        st.subheader("Top Holdings")
        for h in summary["top5_holdings"]:
            bar_len = int(h["pct"] * 40)
            st.markdown(f"`{h['ticker']:6s}` ${h['value']:>10,.0f} ({h['pct']:.1%}) {'█' * bar_len}")

st.subheader("Snapshot vs Live Prices")
if quote_meta.get("enabled"):
    snap_by_t = {}
    for r in snapshot_accounts:
        t = (r.get("ticker") or "").strip()
        if not t:
            continue
        snap_by_t.setdefault(t, {"ticker": t, "snapshot_price": r.get("price"), "snapshot_value": 0.0})
        try:
            snap_by_t[t]["snapshot_value"] += float(r.get("market_value", 0) or 0)
        except Exception:
            pass

    live_by_t = {}
    for r in ingested["accounts"]:
        t = (r.get("ticker") or "").strip()
        if not t:
            continue
        live_by_t.setdefault(t, {"ticker": t, "live_price": r.get("price"), "live_value": 0.0})
        try:
            live_by_t[t]["live_value"] += float(r.get("market_value", 0) or 0)
        except Exception:
            pass

    rows = []
    for t in sorted(set(snap_by_t) | set(live_by_t)):
        sp = snap_by_t.get(t, {}).get("snapshot_price")
        lp = live_by_t.get(t, {}).get("live_price")
        sv = snap_by_t.get(t, {}).get("snapshot_value", 0.0)
        lv = live_by_t.get(t, {}).get("live_value", 0.0)
        pct = None
        try:
            if sp not in (None, 0, "") and lp not in (None, 0, ""):
                pct = float(lp) / float(sp) - 1.0
        except Exception:
            pct = None
        rows.append({
            "Ticker": t,
            "Snapshot Price": f"${float(sp):,.2f}" if sp not in (None, "") else "—",
            "Live Price": f"${float(lp):,.2f}" if lp not in (None, "") else "—",
            "Δ %": f"{pct:+.2%}" if pct is not None else "—",
            "Snapshot Value": f"${sv:,.0f}",
            "Live Value": f"${lv:,.0f}",
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)
else:
    st.caption("Enable live quotes in the sidebar to compare snapshot vs current prices.")

        st.subheader("Validation")
        if not ingested["flags"]:
            st.success("✅ All validations passed")
        else:
            for f in ingested["flags"]:
                st.warning(f)


# ═══════════════════════════════════════════════════════════════════════════
# TAB 2: RISK & ALLOCATION
# ═══════════════════════════════════════════════════════════════════════════

with tab_risk:
    st.header("Risk Posture & Asset Allocation")

    # ── Two-axis control ──
    st.subheader("Portfolio Controls")
    ctrl_left, ctrl_right = st.columns(2)

    with ctrl_left:
        risk_level = st.select_slider(
            "Risk Dial",
            options=["Preserve", "Balanced", "Growth", "Aggressive"],
            value="Balanced",
            help="Target equity exposure level"
        )
        risk_map = {"Preserve": (0.30, 25), "Balanced": (0.45, 50),
                    "Growth": (0.60, 65), "Aggressive": (0.75, 80)}
        target_eq, target_score = risk_map[risk_level]
        st.caption(f"Target equity: {target_eq:.0%} | Target score: ~{target_score}")

    with ctrl_right:
        income_tilt = st.select_slider(
            "Income Tilt",
            options=["Low Yield", "Medium", "High Yield"],
            value="Medium",
            help="Preference for dividend/income-producing holdings"
        )
        tilt_map = {"Low Yield": 0.0, "Medium": 0.5, "High Yield": 1.0}
        st.caption(f"Yield preference: {tilt_map[income_tilt]:.0%}")

    st.divider()

    col_a, col_b = st.columns([1, 1])

    with col_a:
        # Allocation pie chart
        st.subheader("Asset Allocation")
        buckets = rr["buckets"]
        labels = []
        sizes = []
        colors_map = {"equity": "#4C72B0", "dividend_equity": "#55A868",
                      "bond": "#C44E52", "cash": "#8172B3"}
        colors = []
        for k, v in buckets.items():
            if v > 0.001:
                labels.append(f"{k} ({v:.1%})")
                sizes.append(v)
                colors.append(colors_map.get(k, "#CCB974"))

        fig1, ax1 = plt.subplots(figsize=(5, 5))
        wedges, texts, autotexts = ax1.pie(
            sizes, labels=labels, colors=colors, autopct="%1.1f%%",
            startangle=90, pctdistance=0.75,
            wedgeprops=dict(width=0.4, edgecolor="white"),
        )
        for t in autotexts:
            t.set_fontsize(9)
        ax1.set_title("Portfolio Buckets", fontsize=12, fontweight="bold")
        st.pyplot(fig1)
        plt.close(fig1)

    with col_b:
        # Score components
        st.subheader(f"Aggressiveness Score: {rr['aggressiveness_score']}")
        posture = rr["posture"]
        st.markdown(f"**{posture['label']}**")
        st.caption(posture["justification"])

        st.markdown("**Score Components:**")
        for comp_name, comp_val in sc["components"].items():
            bar_dir = "🟢" if comp_val < 0 else "🟠" if comp_val < 10 else "🔴"
            st.markdown(f"{bar_dir} {comp_name}: **{comp_val:+.1f}**")

        st.markdown("**Concentration:**")
        st.markdown(f"Top-1: **{rr['top1_pct']:.1%}** | Top-5: **{rr['top5_pct']:.1%}** | Tech: **{rr['tech_pct']:.1%}**")


# ═══════════════════════════════════════════════════════════════════════════
# TAB 3: DETERMINISTIC PROJECTIONS
# ═══════════════════════════════════════════════════════════════════════════

with tab_determ:
    st.header("Deterministic Projections")

    horizon = st.slider("Projection Horizon (years)", 5, 30, 20, key="det_horizon")

        def run_deterministic(ing, h: int):
        return build_projection_from_ingested(ing, horizon=h)

    if st.button("Run Deterministic Projections", type="primary", key="run_det"):
        with st.spinner("Running 3-scenario projections..."):
            scenarios = run_deterministic(ingested, horizon)

        # Summary metrics
        c1, c2, c3 = st.columns(3)
        for col, name in zip([c1, c2, c3], ["conservative", "central", "growth"]):
            sm = scenarios[name]["summary"]
            col.metric(
                f"{name.title()} ({scenarios[name]['rate']:.1%})",
                f"${sm['end_balance']:,.0f}",
                f"Age {sm['final_age']}"
            )

        # Chart
        fig2, ax2 = plt.subplots(figsize=(10, 5))
        colors_det = {"conservative": "#C44E52", "central": "#4C72B0", "growth": "#55A868"}

        for name in ("conservative", "central", "growth"):
            proj = scenarios[name]["projection"]
            years = [r["year"] for r in proj]
            balances = [r["total_balance"] for r in proj]
            ax2.plot(years, balances, label=f"{name.title()} ({scenarios[name]['rate']:.1%})",
                     color=colors_det[name], linewidth=2)

        ax2.set_xlabel("Year")
        ax2.set_ylabel("Portfolio Balance ($)")
        ax2.set_title("Portfolio Balance — 3 Scenarios")
        ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x/1e6:.1f}M"))
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        st.pyplot(fig2)
        plt.close(fig2)

        # Table
        st.subheader("Year-by-Year Detail (Central)")
        central_data = []
        tp = ingested["tax_profile"]
        cf = tp.get("cap_loss_carryforward", 3_000)
        for row in scenarios["central"]["projection"]:
            effects = simulate_year_tax_effects(
                row, 56_000, 6_700, 3_300, cf, tp
            )
            cf = effects["updated_cap_loss_carryforward"]
            central_data.append({
                "Year": row["year"],
                "Age": row["age"],
                "Balance": f"${row['total_balance']:,.0f}",
                "Withdrawal": f"${row['total_withdrawal']:,.0f}",
                "RMD": f"${row['rmd_amount']:,.0f}",
                "AGI": f"${effects['taxes']['agi']:,.0f}",
                "Fed Tax": f"${effects['taxes']['federal_total']:,.0f}",
                "Eff Rate": f"{effects['taxes']['effective_rate']:.1%}",
                "IRMAA Headroom": f"${effects['irmaa']['headroom_to_next_tier']:,.0f}"
                    if effects['irmaa']['headroom_to_next_tier'] else "N/A",
            })
        st.dataframe(central_data, use_container_width=True, hide_index=True)

        # RMD detail
        st.subheader("RMD Projection")
        divisors = load_rmd_divisors(str(data_dir / "rmd_divisors.json"))
        rmd_series = project_rmd_series(2025, "1953-03-15", 1_000_000,
                                         growth_rate=0.045, horizon=horizon, divisors=divisors)
        rmd_data = [{"Year": r["year"], "Age": r["age"],
                     "IRA Balance": f"${r['start_balance']:,.0f}",
                     "RMD": f"${r['rmd_amount']:,.0f}",
                     "RMD %": f"{r['rmd_pct']:.1f}%"}
                    for r in rmd_series]
        st.dataframe(rmd_data, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════
# TAB 4: MONTE CARLO
# ═══════════════════════════════════════════════════════════════════════════

with tab_mc:
    st.header("Monte Carlo Simulation")

    mc_col1, mc_col2, mc_col3 = st.columns(3)
    mc_n = mc_col1.number_input("Simulations", 100, 50_000, 10_000, step=1000, key="mc_n")
    mc_horizon = mc_col2.number_input("Horizon (years)", 5, 30, 20, key="mc_h")
    mc_seed = mc_col3.number_input("Random Seed", 1, 99999, 42, key="mc_seed")

    if st.button("Run Monte Carlo", type="primary", key="run_mc"):
        with st.spinner(f"Running {mc_n:,} simulations..."):
            mc_summary = run_mc_from_ingested(ingested, n_sims=mc_n,
                                               horizon=mc_horizon, seed=mc_seed)

        meta = mc_summary["metadata"]
        st.success(f"✅ {meta['n_sims']:,} sims × {meta['horizon']} years in {meta['elapsed_seconds']:.2f}s")

        # Key metrics
        m1, m2, m3, m4 = st.columns(4)
        ts = mc_summary["terminal_stats"]
        rs = mc_summary["ruin_stats"]
        ir = mc_summary["irmaa_stats"]
        m1.metric("Median Terminal", f"${ts['median']:,.0f}")
        m2.metric("P(Ruin)", f"{rs['probability_of_ruin']:.2%}")
        m3.metric("P5 Terminal", f"${ts['p5']:,.0f}")
        m4.metric("P(IRMAA ever)", f"{ir['prob_ever_triggered']:.1%}")

        # Fan chart
        st.subheader("Portfolio Balance Fan Chart")
        fig3, ax3 = plt.subplots(figsize=(10, 5))

        yt = mc_summary["year_by_year"]
        years = [r["year"] for r in yt]

        # Fill bands
        bands = [
            ("balance_p5", "balance_p95", "#4C72B0", 0.15, "5th–95th"),
            ("balance_p10", "balance_p90", "#4C72B0", 0.20, "10th–90th"),
            ("balance_p25", "balance_p75", "#4C72B0", 0.30, "25th–75th"),
        ]
        for lo_key, hi_key, color, alpha, label in bands:
            lo = [r[lo_key] for r in yt]
            hi = [r[hi_key] for r in yt]
            ax3.fill_between(years, lo, hi, color=color, alpha=alpha, label=label)

        # Median line
        median = [r["balance_p50"] for r in yt]
        ax3.plot(years, median, color="#C44E52", linewidth=2, label="Median")

        ax3.set_xlabel("Year")
        ax3.set_ylabel("Portfolio Balance ($)")
        ax3.set_title("Monte Carlo Fan Chart")
        ax3.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x/1e6:.1f}M"))
        ax3.legend(loc="upper left")
        ax3.grid(True, alpha=0.3)
        st.pyplot(fig3)
        plt.close(fig3)

        # IRMAA probability by year
        st.subheader("IRMAA Trigger Probability by Year")
        fig4, ax4 = plt.subplots(figsize=(10, 3))
        irmaa_probs = [r["irmaa_prob"] * 100 for r in yt]
        ax4.bar(years, irmaa_probs, color="#C44E52", alpha=0.7)
        ax4.set_ylabel("P(IRMAA trigger) %")
        ax4.set_xlabel("Year")
        ax4.set_title("Annual IRMAA Crossing Probability")
        ax4.grid(True, alpha=0.3, axis="y")
        st.pyplot(fig4)
        plt.close(fig4)

        # Percentile table
        st.subheader("Percentile Table")
        table_data = []
        for r in yt:
            table_data.append({
                "Year": r["year"], "Age": r["age"],
                "P5": f"${r['balance_p5']:,.0f}",
                "P25": f"${r['balance_p25']:,.0f}",
                "P50": f"${r['balance_p50']:,.0f}",
                "P75": f"${r['balance_p75']:,.0f}",
                "P95": f"${r['balance_p95']:,.0f}",
                "Withdrawal": f"${r['withdrawal_p50']:,.0f}",
                "IRMAA %": f"{r['irmaa_prob']:.1%}",
                "Ruin %": f"{r['ruin_prob_cumul']:.2%}",
            })
        st.dataframe(table_data, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════
# TAB 5: TRIP SIMULATOR
# ═══════════════════════════════════════════════════════════════════════════

with tab_trip:
    st.header("✈️ Can I Afford This Trip?")

    trip_col1, trip_col2, trip_col3 = st.columns(3)
    trip_cost = trip_col1.number_input("Trip Cost ($)", 1_000, 200_000, 15_000, step=1_000)
    trip_year = trip_col2.number_input("Year", 2025, 2045, 2026)
    trip_mc_enabled = trip_col3.checkbox("Include Monte Carlo", value=True)

    if st.button("Analyze Trip", type="primary", key="run_trip"):
        with st.spinner("Analyzing funding options..."):
            result = trip_impact(
                trip_cost, trip_year, "optimal", ingested,
                run_mc=trip_mc_enabled, mc_n=1_000, mc_seed=42,
            )

        # Verdict banner
        rec = result["recommendation"]
        verdict_colors = {
            "GO — MINIMAL IMPACT": "🟢",
            "GO — MANAGEABLE": "🟡",
            "CAUTION": "🔴",
            "CONSIDER ALTERNATIVES": "🟠",
        }
        icon = verdict_colors.get(rec["verdict"], "⚪")
        st.markdown(f"## {icon} {rec['verdict']}")
        st.markdown(rec["reason"])

        st.divider()

        # Funding comparison
        st.subheader("Funding Options Comparison")
        fund_data = []
        for s in ("cash", "inherited_ira", "taxable", "trad_ira"):
            if s not in result["funding_analysis"]:
                continue
            fa = result["funding_analysis"][s]
            is_best = "★" if s == result["best_source"] else ""
            fund_data.append({
                "": is_best,
                "Source": s,
                "Gross Cost": f"${fa['gross_cost']:,.0f}",
                "Tax Cost": f"${fa['tax_cost']:,.0f}",
                "IRMAA Δ": f"${fa['irmaa_delta_annual']:,.0f}",
                "Net Cost": f"${fa['net_cost']:,.0f}",
                "AGI Impact": f"+${fa['agi_delta']:,.0f}",
                "IRMAA Safe": "✅" if fa["irmaa_tier_after"] == 0 else f"⚠ Tier {fa['irmaa_tier_after']}",
            })
        st.dataframe(fund_data, use_container_width=True, hide_index=True)

        st.caption(f"★ Best: **{rec['best_funding']}** saves "
                   f"**${rec['savings_vs_worst']:,.0f}** vs {rec['worst_funding']}")

        # Deterministic delta
        st.subheader("20-Year Portfolio Impact")
        det = result["deterministic_delta"]
        det_data = []
        for s in ("conservative", "central", "growth"):
            d = det[s]
            det_data.append({
                "Scenario": f"{s.title()} ({d['rate']:.1%})",
                "Base End": f"${d['end_balance_base']:,.0f}",
                "With Trip": f"${d['end_balance_trip']:,.0f}",
                "Delta": f"${d['delta_20yr']:,.0f}",
                "% Impact": f"{d['pct_delta']:.2f}%",
            })
        st.dataframe(det_data, use_container_width=True, hide_index=True)

        # Delta chart
        fig5, ax5 = plt.subplots(figsize=(8, 4))
        scenario_names = [f"{s.title()}" for s in ("conservative", "central", "growth")]
        base_vals = [det[s]["end_balance_base"] for s in ("conservative", "central", "growth")]
        trip_vals = [det[s]["end_balance_trip"] for s in ("conservative", "central", "growth")]

        x = np.arange(len(scenario_names))
        width = 0.35
        ax5.bar(x - width/2, [v/1e6 for v in base_vals], width, label="Without Trip", color="#4C72B0")
        ax5.bar(x + width/2, [v/1e6 for v in trip_vals], width, label="With Trip", color="#C44E52", alpha=0.8)
        ax5.set_ylabel("End Balance ($M)")
        ax5.set_title(f"${trip_cost:,.0f} Trip Impact on 20-Year Balance")
        ax5.set_xticks(x)
        ax5.set_xticklabels(scenario_names)
        ax5.legend()
        ax5.grid(True, alpha=0.3, axis="y")
        st.pyplot(fig5)
        plt.close(fig5)

        # MC delta
        if result["mc_delta"]:
            st.subheader("Monte Carlo Impact")
            mc = result["mc_delta"]
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("Ruin Δ", f"{mc['ruin_delta']:+.2%}",
                       delta=None if mc['ruin_delta'] == 0 else f"{mc['trip_ruin_prob']:.2%} total")
            mc2.metric("IRMAA Δ", f"{mc['irmaa_delta']:+.1%}",
                       delta=None if mc['irmaa_delta'] == 0 else f"{mc['trip_irmaa_ever']:.1%} total")
            mc3.metric("Median Terminal Δ", f"${mc['terminal_delta']:+,.0f}")


# ═══════════════════════════════════════════════════════════════════════════
# Footer
# ═══════════════════════════════════════════════════════════════════════════

st.sidebar.divider()
st.sidebar.caption("RICS v0.1 — Local-first, privacy-first retirement planning.")
st.sidebar.caption("No data leaves your machine. No broker APIs.")
st.sidebar.caption("Live quotes are optional and fetched only when enabled.")
