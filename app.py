"""
app.py - RICS Streamlit Application
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
    import cloud_sync
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
from profile_store import load_profile, save_profile
from holdings_store import load_holdings, validate_holdings, save_holdings
from live_overlay import apply_price_overrides
from quotes import fetch_quotes_finnhub
from trades_store import load_trades, validate_trade, append_trade
from trade_apply import apply_trades_to_snapshot
from trades_apply_state import compute_new_trades
from sector_prefs_store import load_sector_preferences, save_sector_preferences
import settings_store
import advisor_brain

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

def _show_sector_prefs_summary(prefs=None):
    try:
        if prefs is None:
            profile = load_profile(TAX_PROFILE_PATH, CONSTRAINTS_PATH)
            prefs = load_sector_preferences(profile)
        liked_list = prefs.get("liked_sectors", []) or []
        avoided_list = prefs.get("avoided_sectors", []) or []
        liked = ", ".join(liked_list) if liked_list else "--"
        avoided = ", ".join(avoided_list) if avoided_list else "--"
        tilt = prefs.get("tilt_strength", 0)
        st.caption(
            f"**Sector Preferences** -- Tilt Strength: {tilt} | "
            f"Liked: {liked} | Avoided: {avoided}"
        )
    except Exception:
        st.caption("No preferences set.")

def _setup_price_sidebar():
    st.subheader("📡 Price Mode")
    mode = st.session_state.get("price_mode", "Snapshot (CSV prices)")
    api_key = st.session_state.get("finnhub_key", "")
    if mode.startswith("Live") and api_key:
        st.write("🟢 Live prices active")
    else:
        st.write("⚪ Using saved prices")
    with st.container():
        new_key = st.text_input("Finnhub API key", value=api_key, type="password")
        new_mode = st.radio("Default valuation mode", ("Snapshot (CSV prices)", "Live (Finnhub)"), index=1 if mode.startswith("Live") else 0)
        
        st.markdown("---")
        st.subheader("🧠 Advisor AI")
        gemini_key = st.session_state.get("gemini_key", settings_store.get_setting("gemini_api_key", ""))
        new_gemini_key = st.text_input("Gemini API Key (For Market Briefings)", value=gemini_key, type="password")
        
        if st.button("Save Settings"):
            settings_store.set_setting("finnhub_api_key", new_key)
            settings_store.set_setting("price_mode", "live" if new_mode.startswith("Live") else "disk")
            settings_store.set_setting("gemini_api_key", new_gemini_key)
            st.session_state["finnhub_key"] = new_key
            st.session_state["price_mode"] = new_mode
            st.session_state["gemini_key"] = new_gemini_key
            st.rerun()

def _setup_holdings_editor():
    with st.container():
        st.subheader("📁 Manual Holdings Editor")
        st.markdown("Edit your portfolio snapshot below. We've separated your holdings by account to make it easier to read and update.")
        try:
            rows, fieldnames = load_holdings(SNAPSHOT_PATH)
        except Exception as e:
            st.error(f"Failed to load holdings: {e}")
            return
            
        # Group by account
        accounts_dict = {}
        for r in rows:
            acct = r.get("account_label", "Unknown Account")
            if acct not in accounts_dict:
                accounts_dict[acct] = []
            accounts_dict[acct].append(r)
            
        all_edited_rows = []
        
        # Display a separate editor for each account
        for acct, acct_rows in accounts_dict.items():
            with st.container(border=True):
                st.markdown(f"<h4 style='color: #4CAF50; margin-bottom: 10px;'>🏦 {acct}</h4>", unsafe_allow_html=True)
                # Create a unique key for each editor to prevent Streamlit collisions
                safe_key = f"editor_{acct.replace(' ', '_')}"
                edited_group = st.data_editor(acct_rows, num_rows="dynamic", key=safe_key, use_container_width=True)
                all_edited_rows.extend(edited_group)
            st.markdown("<br>", unsafe_allow_html=True)
            
        # Make the save button a massive, unmissable target
        if st.button("Save All Holdings", type="primary", use_container_width=True):
            errors = validate_holdings(all_edited_rows)
            if errors:
                for err in errors: st.error(f"• {err}")
            else:
                ok, save_errors = save_holdings(SNAPSHOT_PATH, all_edited_rows, fieldnames)
                if ok:
                    st.success("Saved successfully!")
                    st.rerun()
                else:
                    for err in save_errors: st.error(f"• {err}")

def _setup_trades_sidebar():
    with st.container():
        st.subheader("🧾 Trade Log & Entry")
        applied_count = settings_store.get_setting("trades_applied_count", 0)
        try:
            trade_rows, _ = load_trades(TRADE_LOG_PATH)
        except Exception:
            trade_rows = []
        st.caption(f"Applied trades: {applied_count} / {len(trade_rows)}")
        st.markdown("Log a new trade below.")
        with st.form("trade_form", clear_on_submit=True):
            trade_date = st.date_input("Date")
            account_id = st.text_input("Account ID")
            action = st.selectbox("Action", ["buy", "sell", "withdraw"])
            ticker = st.text_input("Ticker")
            shares = st.number_input("Shares", min_value=0.0, step=1.0)
            price = st.number_input("Price", min_value=0.0, step=1.0)
            notes = st.text_input("Notes")
            if st.form_submit_button("Add Trade"):
                trade = {
                    "trade_date": str(trade_date), "account_id": account_id.strip(),
                    "action": action, "ticker": ticker.strip().upper(),
                    "shares": str(shares), "price": str(price),
                    "total_amount": str(shares * price) if shares and price else "0",
                    "cost_basis_per_share": "", "realized_gain": "", "term": "", "notes": notes.strip()
                }
                ok, errors = append_trade(TRADE_LOG_PATH, trade)
                if ok:
                    st.success("Trade added!")
                    st.rerun()
                else:
                    for err in errors: st.error(f"• {err}")
        st.markdown("### Recent Trades")
        try:
            if trade_rows:
                recent = trade_rows[-5:]
                recent.reverse()
                st.dataframe([r.__dict__ if hasattr(r, '__dict__') else r for r in recent], use_container_width=True)
            else:
                st.info("No trades found.")
        except Exception as e:
            st.error(f"Failed to load trades: {e}")
        st.markdown("---")
        if st.button("Apply Trades to Snapshot"):
            try:
                snap_rows, fieldnames = load_holdings(SNAPSHOT_PATH)
                trade_dicts = [r.__dict__ if hasattr(r, "__dict__") else r for r in trade_rows]
                new_trades, total_count = compute_new_trades(trade_dicts, applied_count)
                if not new_trades:
                    st.info("No new trades to apply.")
                else:
                    new_snap, errors = apply_trades_to_snapshot(snap_rows, new_trades)
                    if errors:
                        for e in errors: st.error(f"• {e}")
                    else:
                        ok, save_errors = save_holdings(SNAPSHOT_PATH, new_snap, fieldnames)
                        if ok:
                            settings_store.set_setting("trades_applied_count", total_count)
                            st.success("Trades applied successfully!")
                            st.rerun()
                        else:
                            for e in save_errors: st.error(f"• {e}")
            except Exception as e:
                st.error(f"Failed to apply trades: {e}")

def _setup_profile_editor():
    profile = load_profile(TAX_PROFILE_PATH, CONSTRAINTS_PATH)
    with st.container():
        st.subheader("👤 Personal Profile")
        st.markdown("Update your key financial details here.")
        patch = {}
        if "filing_status" in profile:
            valid_statuses = ["single", "mfj", "mfs", "hoh", "qw"]
            current_fs = profile["filing_status"] if profile["filing_status"] in valid_statuses else "single"
            fs = st.selectbox("Filing status", valid_statuses, index=valid_statuses.index(current_fs))
            if fs != profile["filing_status"]: patch["filing_status"] = fs
        if "ages" in profile and isinstance(profile["ages"], list) and len(profile["ages"]) >= 2:
            col1, col2 = st.columns(2)
            age1 = col1.number_input("Age (Person 1)", value=int(profile["ages"][0]), step=1)
            age2 = col2.number_input("Age (Person 2)", value=int(profile["ages"][1]), step=1)
            if age1 != profile["ages"][0] or age2 != profile["ages"][1]: patch["ages"] = [age1, age2]
        if "ss_combined_annual" in profile:
            ss = st.number_input("Annual Social Security", value=float(profile["ss_combined_annual"]), step=1000.0)
            if ss != profile["ss_combined_annual"]: patch["ss_combined_annual"] = ss
        if "agi_prior_year" in profile:
            agi = st.number_input("Prior-year AGI", value=float(profile["agi_prior_year"]), step=1000.0)
            if agi != profile["agi_prior_year"]: patch["agi_prior_year"] = agi
        if "rmd_start_age" in profile:
            rmd = st.number_input("RMD Start Age", value=int(profile["rmd_start_age"]), step=1)
            if rmd != profile["rmd_start_age"]: patch["rmd_start_age"] = rmd
        if "aggressiveness_score" in profile and isinstance(profile["aggressiveness_score"], dict) and "current_target" in profile["aggressiveness_score"]:
            curr_agg = profile["aggressiveness_score"]["current_target"]
            agg = st.slider("Risk Target (0=Conservative, 100=Aggressive)", 0, 100, int(curr_agg))
            if agg != curr_agg:
                new_agg = profile["aggressiveness_score"].copy()
                new_agg["current_target"] = agg
                patch["aggressiveness_score"] = new_agg
        st.markdown("")
        if st.button("Save Profile"):
            if patch:
                merged, errors = save_profile(patch, TAX_PROFILE_PATH, CONSTRAINTS_PATH)
                if errors:
                    for err in errors: st.error(f"• {err}")
                else:
                    st.success("Profile saved successfully!")
                    st.rerun()
            else:
                st.info("No changes to save.")

def _setup_sector_prefs_sidebar():
    profile = load_profile(TAX_PROFILE_PATH, CONSTRAINTS_PATH)
    prefs = load_sector_preferences(profile)
    with st.container():
        st.subheader("🏷️ Sector Preferences")
        st.markdown("Set your sector tilts.")
        liked_str = ", ".join(prefs["liked_sectors"])
        avoided_str = ", ".join(prefs["avoided_sectors"])
        with st.form("sector_prefs_form"):
            new_liked = st.text_input("Liked Sectors (comma-separated)", value=liked_str)
            new_avoided = st.text_input("Avoided Sectors (comma-separated)", value=avoided_str)
            new_tilt = st.slider("Tilt Strength", 0, 5, int(prefs["tilt_strength"]))
            if st.form_submit_button("Save Preferences"):
                new_prefs = {
                    "liked_sectors": [x.strip() for x in new_liked.split(",") if x.strip()],
                    "avoided_sectors": [x.strip() for x in new_avoided.split(",") if x.strip()],
                    "tilt_strength": new_tilt,
                }
                try:
                    save_sector_preferences(new_prefs, TAX_PROFILE_PATH, CONSTRAINTS_PATH)
                    st.success("Sector preferences saved!")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

if HAS_STREAMLIT:
    @st.cache_data(ttl=120, show_spinner="Fetching live quotes ...")
    def _cached_quotes(tickers_tuple, api_key):
        return fetch_quotes_finnhub(list(tickers_tuple), api_key=api_key)
else:
    def _cached_quotes(tickers_tuple, api_key):
        return fetch_quotes_finnhub(list(tickers_tuple), api_key=api_key)

def load_holdings_with_mode():
    holdings = load_csv_rows(SNAPSHOT_PATH)
    default_mode = "Snapshot (CSV prices)" if settings_store.get_setting("price_mode", "disk") == "disk" else "Live (Finnhub)"
    mode = st.session_state.get("price_mode", default_mode)
    api_key = st.session_state.get("finnhub_key", "")
    if not mode.startswith("Live") or not api_key: return holdings
    tickers = sorted({h["ticker"] for h in holdings if h.get("ticker")})
    try:
        raw_quotes = _cached_quotes(tuple(tickers), api_key)
    except Exception as exc:
        st.sidebar.error(f"Quote fetch failed: {exc}")
        return holdings
    price_overrides = {}
    for tkr, payload in (raw_quotes or {}).items():
        if isinstance(payload, dict):
            if "price" in payload and payload["price"] is not None:
                price_overrides[tkr] = payload["price"]
        else:
            price_overrides[tkr] = payload
    return apply_price_overrides(holdings, price_overrides)

# ══════════════════════════════════════════════════════════════════════════════
# TAB IMPLEMENTATIONS
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=3600, show_spinner=False)
def get_previous_closes(tickers_tuple):
    import yfinance as yf
    prev_closes = {}
    for t in tickers_tuple:
        try:
            fi = yf.Ticker(t).fast_info
            if fi.previous_close: prev_closes[t] = fi.previous_close
        except: pass
    return prev_closes

def compute_daily_changes(holdings):
    tickers = tuple(set([h.get("ticker", "").upper() for h in holdings if h.get("ticker") and h.get("asset_class") != "mmf"]))
    prev_closes = get_previous_closes(tickers)
    
    port_curr = 0.0
    port_prev = 0.0
    acct_changes = {}
    
    for h in holdings:
        t = h.get("ticker", "").upper()
        acct = h.get("account_label", "Unknown Account")
        shares = float(h.get("shares", 0) or 0)
        curr_price = float(h.get("price", 0) or 0)
        
        val_curr = shares * curr_price
        
        if h.get("asset_class") == "mmf":
            val_prev = val_curr
        else:
            prev_p = prev_closes.get(t)
            val_prev = shares * prev_p if prev_p else val_curr
            
        port_curr += val_curr
        port_prev += val_prev
        
        if acct not in acct_changes:
            acct_changes[acct] = {"curr": 0.0, "prev": 0.0}
        acct_changes[acct]["curr"] += val_curr
        acct_changes[acct]["prev"] += val_prev
        
    port_change_dlr = port_curr - port_prev
    port_change_pct = (port_change_dlr / port_prev) if port_prev > 0 else 0.0
    
    acct_results = {}
    for acct, vals in acct_changes.items():
        c_dlr = vals["curr"] - vals["prev"]
        c_pct = (c_dlr / vals["prev"]) if vals["prev"] > 0 else 0.0
        acct_results[acct] = {"dlr": c_dlr, "pct": c_pct}
        
    return port_change_dlr, port_change_pct, acct_results

def tab_your_plan():
    # Pull total portfolio value directly to the top
    holdings = load_holdings_with_mode()
    total_portfolio = sum(float(h.get("market_value", 0)) for h in holdings) if holdings else 0.0
    port_change_dlr, port_change_pct, _ = compute_daily_changes(holdings)
    
    c_color = "#4CAF50" if port_change_dlr >= 0 else "#f44336"
    c_sign = "+" if port_change_dlr >= 0 else ""
    
    st.markdown(f"""
    <div style="padding-top: 0px; padding-bottom: 20px;">
        <h1 style="margin-bottom: 0px; font-size: 2.5em; font-weight: 600;">My Retirement Dashboard</h1>
        <p style="font-size: 1.1em; color: #a0aab5; margin-top: 5px;">Here is your financial snapshot for today.</p>
    </div>
    <div style="background-color: #1e2632; padding: 40px; border-radius: 12px; border: 1px solid #2b3543; text-align: center; margin-bottom: 25px; box-shadow: 0px 4px 15px rgba(0,0,0,0.2);">
        <p style="font-size: 1.3em; color: #a0aab5; margin-bottom: 0px; font-weight: 500; text-transform: uppercase; letter-spacing: 1px;">Total Portfolio Value</p>
        <h1 style="font-size: 4.5em; margin-top: 5px; margin-bottom: 5px; color: #ffffff; font-weight: 700;">${total_portfolio:,.0f}</h1>
        <p style="font-size: 1.4em; font-weight: 600; color: {c_color}; margin-top: 0px;">{c_sign}${port_change_dlr:,.0f} ({c_sign}{port_change_pct:.2%}) Today</p>
    </div>
    """, unsafe_allow_html=True)
    
    # --- TODAY's MARKET BRIEFING (AI INFERENCE) ---
    gemini_key = st.session_state.get("gemini_key", settings_store.get_setting("gemini_api_key", ""))
    
    st.subheader("📰 Today's Market Briefing")
    if not gemini_key:
        st.info("👋 Want to see personalized daily news and trends for your portfolio? Add your Gemini API key in the 'Settings & Data' tab.")
    else:
        with st.spinner("Your advisor is reviewing today's news and market trends..."):
            # We use an expander or container to hold the generated content
            briefing_cards = advisor_brain.generate_advisor_briefing(holdings, gemini_key)
            
            if not briefing_cards:
                st.success("✅ The markets are quiet today. Your portfolio is holding steady, and there is no major news requiring your attention.")
            else:
                for card in briefing_cards:
                    with st.container(border=True):
                        sev = card.get("severity", "low")
                        title = card.get("title", "Market Update")
                        note = card.get("advisor_note", "")
                        ticker = card.get("ticker", "")
                        
                        if sev == "high":
                            st.error(f"🚨 **{ticker}**: {title}")
                        elif sev == "medium":
                            st.warning(f"💡 **{ticker}**: {title}")
                        else:
                            st.success(f"📈 **{ticker}**: {title}")
                            
                        st.markdown(f"<p style='font-size: 1.1em;'><em>Advisor Note:</em> {note}</p>", unsafe_allow_html=True)
    
    st.markdown("<br>", unsafe_allow_html=True)

    # --- AUTOMATED NEWS FEED ---
    st.subheader("🔗 Latest Headlines For Your Holdings")
    st.write("Recent articles directly linked to your portfolio, updated automatically.")
    
    with st.spinner("Fetching latest articles..."):
        try:
            latest_news = advisor_brain.get_latest_news(holdings, limit=5)
            if latest_news:
                for item in latest_news:
                    ticker = item.get("related_ticker", "")
                    title = item.get("parsed_title", "News Article")
                    link = item.get("parsed_link", "#")
                    publisher = item.get("parsed_publisher", "Financial News")
                    
                    # Clean, broker-style UI with links that force open in a NEW tab (_blank)
                    with st.container(border=True):
                        st.markdown(f"<h5 style='margin-bottom: 2px;'><a href='{link}' target='_blank' style='text-decoration: none; color: #4DA8DA;'>{title}</a></h5>", unsafe_allow_html=True)
                        st.markdown(f"<p style='font-size: 0.9em; color: #888; margin-top: 0px;'><strong>{ticker}</strong> &nbsp;•&nbsp; Source: {publisher}</p>", unsafe_allow_html=True)
            else:
                st.info("No recent news found for your specific holdings.")
        except Exception as e:
            st.caption("News feed temporarily unavailable.")
            
    st.markdown("<br>", unsafe_allow_html=True)
    
    cashflow = []
    recs = []
    try:
        cashflow = load_csv_rows(CASHFLOW_PATH)
        tax_profile = load_json(TAX_PROFILE_PATH)
        constraints = load_json(CONSTRAINTS_PATH)
        rmd_divisors = load_json(RMD_DIVISORS_PATH)
        recs = generate_all_recommendations(holdings, tax_profile, constraints, cashflow, rmd_divisors)
    except Exception:
        pass

    # Clear, plain English status banner
    high_sev = sum(1 for r in recs if getattr(r, "severity", "") == "high")
    med_sev = sum(1 for r in recs if getattr(r, "severity", "") == "medium")
    if high_sev > 0:
        st.error("⚠️ **Action Recommended:** We found some important items to review.")
    elif med_sev > 0:
        st.warning("👀 **Small adjustments recommended:** Things look okay, but there are a few minor ideas.")
    else:
        st.success("✅ **You are on track!** No urgent actions needed. Have a great day!")

    income_items = [c for c in cashflow if c.get("category") == "income"]
    expense_items = [c for c in cashflow if c.get("category") == "expense" and c.get("frequency") == "annual"]
    total_income = sum(float(i.get("amount", 0) or 0) for i in income_items)
    total_exp = sum(float(e.get("amount", 0) or 0) for e in expense_items)
    surplus = total_income - total_exp
    
    st.divider()
    st.subheader("Your Cash Flow for the Year")
    col1, col2, col3 = st.columns(3)
    col1.metric("Projected Income", f"${total_income:,.0f}")
    col2.metric("Estimated Expenses", f"${total_exp:,.0f}")
    col3.metric("Surplus / Shortfall", f"${surplus:,.0f}")
    st.divider()

    def set_nav(section_name):
        st.session_state["nav_section"] = section_name

    st.subheader("Suggested Next Steps")
    if recs:
        for idx, rec in enumerate(recs[:3], start=1):
            title = getattr(rec, "title", f"Recommendation {idx}")
            desc = getattr(rec, "description", "")
            sev = getattr(rec, "severity", "info")
            
            # Create a distinct bordered card for each step
            with st.container(border=True):
                st.markdown(f"### {idx}. {title}")
                if desc: 
                    st.markdown(f"<p style='font-size: 1.1em; margin-bottom: 10px;'>{desc}</p>", unsafe_allow_html=True)
                
                col_btn, col_space = st.columns([1, 3])
                with col_btn:
                    # Make the button visually distinct
                    btn_type = "primary" if sev in ["high", "medium"] else "secondary"
                    st.button("Review Idea", key=f"plan_review_{idx}", type=btn_type, use_container_width=True, on_click=set_nav, args=("Tax & Planning Ideas",))
    else:
        st.write("You have no pending tasks.")
        
    st.markdown("**Quick Links**")
    q1, q2, q3, q4 = st.columns(4)
    with q1:
        st.button("View Portfolio", use_container_width=True, on_click=set_nav, args=("My Accounts & Holdings",))
    with q2:
        st.button("Adjust My Risk", use_container_width=True, on_click=set_nav, args=("Adjust My Risk",))
    with q3:
        st.button("Tax Details", use_container_width=True, on_click=set_nav, args=("Tax & Planning Ideas",))
    with q4:
        st.button("Settings", use_container_width=True, on_click=set_nav, args=("Settings & Data",))

def _show_sector_exposure_panel(prefs=None):
    with st.expander("Sector Exposure (Top 10)", expanded=False):
        try:
            holdings = load_holdings_with_mode()
            if not holdings:
                st.info("No holdings found.")
                return
            if prefs is None:
                try:
                    profile = load_profile(TAX_PROFILE_PATH, CONSTRAINTS_PATH)
                    prefs = load_sector_preferences(profile)
                except Exception:
                    prefs = {"liked_sectors": [], "avoided_sectors": []}
            liked = [s.strip().lower() for s in prefs.get("liked_sectors", [])]
            avoided = [s.strip().lower() for s in prefs.get("avoided_sectors", [])]
            sectors = {}
            total_value = 0.0
            for h in holdings:
                sec_raw = h.get("sector", "Unknown")
                sec = str(sec_raw).strip().title() if sec_raw else "Unknown"
                try: mv = float(h.get("market_value") or 0.0)
                except ValueError: mv = 0.0
                sectors[sec] = sectors.get(sec, 0.0) + mv
                total_value += mv
            if total_value <= 0:
                st.info("Total portfolio value is zero.")
                return
            sorted_sectors = sorted(sectors.items(), key=lambda x: x[1], reverse=True)[:10]
            data = []
            chart_data = {}
            for sec, val in sorted_sectors:
                pct = val / total_value
                sec_lower = sec.lower()
                tag = "Liked" if sec_lower in liked else ("Avoided" if sec_lower in avoided else "Ambient")
                data.append({"Sector": sec, "Value": f"${val:,.0f}", "Pct": f"{pct:.1%}", "Tag": tag})
                chart_data[sec] = pct * 100
            st.dataframe(data, use_container_width=True)
            st.bar_chart(chart_data)
        except Exception as e:
            st.caption(f"Could not load sector exposure: {e}")

def tab_portfolio_overview():
    st.header("Portfolio Overview")
    holdings = load_holdings_with_mode()

    if not holdings:
        st.info("No holdings found. Please add accounts or import a broker CSV to see your overview.")
        return

    accounts = {}
    for h in holdings:
        acct = h["account_label"]
        mv = float(h.get("market_value", 0))
        accounts.setdefault(acct, {"type": h.get("account_type", ""), "value": 0, "positions": 0})
        accounts[acct]["value"] += mv
        accounts[acct]["positions"] += 1

    total_portfolio = sum(a["value"] for a in accounts.values())
    st.metric("Total Portfolio Value", f"${total_portfolio:,.0f}")

    if len(accounts) > 0:
        _, _, acct_results = compute_daily_changes(holdings)
        cols = st.columns(len(accounts))
        for col, (name, info) in zip(cols, accounts.items()):
            acct_c = acct_results.get(name, {"dlr": 0, "pct": 0})
            c_sign = "+" if acct_c["dlr"] >= 0 else ""
            c_str = f"{c_sign}${acct_c['dlr']:,.0f} ({c_sign}{acct_c['pct']:.2%}) Today"
            col.metric(name, f"${info['value']:,.0f}", delta=c_str)

    st.subheader("Asset Allocation")
    alloc = compute_current_allocation(holdings)
    alloc_data = [{"Asset Class": k.replace("_", " ").title(), "Pct": round(v * 100, 1)} for k, v in alloc.items()]
    
    try:
        import plotly.express as px
        import pandas as pd
        df = pd.DataFrame(alloc_data)
        df = df[df['Pct'] > 0] # Hide empty buckets for a cleaner look
        
        fig = px.pie(
            df, 
            values='Pct', 
            names='Asset Class', 
            hole=0.45, # Creates the modern donut look
            color_discrete_sequence=px.colors.sequential.Blues_r
        )
        fig.update_traces(
            textposition='inside', 
            textinfo='percent+label',
            hovertemplate="%{label}: %{value}%<extra></extra>",
            textfont_size=14
        )
        fig.update_layout(
            showlegend=False,
            margin=dict(t=10, b=10, l=10, r=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)"
        )
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        # Fallback to a highly readable horizontal bar chart if Plotly isn't installed
        import altair as alt
        import pandas as pd
        df = pd.DataFrame(alloc_data)
        chart = alt.Chart(df).mark_bar().encode(
            x=alt.X('Pct:Q', title='Percentage (%)'),
            y=alt.Y('Asset Class:N', sort='-x', title=''),
            color=alt.Color('Asset Class:N', legend=None),
            tooltip=['Asset Class', 'Pct']
        ).properties(height=300)
        st.altair_chart(chart, use_container_width=True)

    st.subheader("Holdings by Account")
    
    # Group holdings by account
    accounts_dict = {}
    for h in holdings:
        acct = h.get("account_label", "Unknown Account")
        if acct not in accounts_dict:
            accounts_dict[acct] = []
        accounts_dict[acct].append(h)
        
    for acct, acct_holdings in accounts_dict.items():
        acct_total_val = sum(float(h.get("market_value", 0) or 0) for h in acct_holdings)
        
        acct_c = acct_results.get(acct, {"dlr": 0, "pct": 0})
        c_color = "#4CAF50" if acct_c["dlr"] >= 0 else "#f44336"
        c_sign = "+" if acct_c["dlr"] >= 0 else ""
        
        # Wrap each account in a distinct, bordered card
        with st.container(border=True):
            st.markdown(f"<h3 style='color: #4CAF50; margin-bottom: 0px;'>🏦 {acct}</h3>", unsafe_allow_html=True)
            st.markdown(f"<p style='font-size: 1.2em; font-weight: bold; margin-top: 0px;'>Account Total: ${acct_total_val:,.0f} <span style='color: {c_color}; font-size: 0.85em; margin-left: 12px;'>{c_sign}${acct_c['dlr']:,.0f} ({c_sign}{acct_c['pct']:.2%})</span></p>", unsafe_allow_html=True)
            st.divider()
            
            formatted_data = []
            for h in acct_holdings:
                try: shares = float(h.get("shares", 0) or 0)
                except ValueError: shares = 0.0
                try: price = float(h.get("price", 0) or 0)
                except ValueError: price = 0.0
                try: mv = float(h.get("market_value", 0) or 0)
                except ValueError: mv = 0.0
                try: ug = float(h.get("unrealized_gain", 0) or 0)
                except ValueError: ug = 0.0
                try: inc = float(h.get("annual_income_est", 0) or 0)
                except ValueError: inc = 0.0
                
                shares_str = f"{shares:,.3f}".rstrip('0').rstrip('.')
                
                formatted_data.append({
                    "Ticker": str(h.get("ticker", "")).upper(),
                    "Asset Class": str(h.get("asset_class", "")).replace("_", " ").title(),
                    "Shares": shares_str,
                    "Price": f"${price:,.2f}",
                    "Market Value": f"${mv:,.2f}",
                    "Unrealized Gain": f"${ug:,.2f}",
                    "Est. Income": f"${inc:,.2f}"
                })
                
            st.dataframe(formatted_data, use_container_width=True)
            
def tab_cashflow():
    st.header("Cash Flow Plan")
    try:
        cashflow = load_csv_rows(CASHFLOW_PATH)
    except FileNotFoundError:
        st.info("Cashflow file missing.")
        return
    income = [c for c in cashflow if c["category"] == "income"]
    expenses = [c for c in cashflow if c["category"] == "expense"]
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Income Sources")
        for item in income: st.write(f"**{item['subcategory']}**: ${float(item['amount']):,.0f}/yr")
        st.metric("Total Annual Income", f"${sum(float(i['amount']) for i in income):,.0f}")
    with col2:
        st.subheader("Annual Expenses")
        annual_exp = [e for e in expenses if e.get("frequency") == "annual"]
        for item in annual_exp: st.write(f"**{item['subcategory']}**: ${float(item['amount']):,.0f}/yr")
        st.metric("Total Annual Expenses", f"${sum(float(e['amount']) for e in annual_exp):,.0f}")
    st.subheader("Lumpy / One-Time Expenses")
    lumpy = [e for e in expenses if "one_time" in e.get("frequency", "")]
    for item in lumpy: st.write(f"**{item['year']} - {item['subcategory']}**: ${float(item['amount']):,.0f}")

def tab_tax_dashboard():
    st.header("Your Tax Picture")
    st.write("A simple view of your tax profile to help you make smart, tax-efficient withdrawal decisions.")
    try:
        tp = load_json(TAX_PROFILE_PATH)
    except FileNotFoundError:
        st.info("We need a little more information. Please update your Profile in the Settings & Data tab.")
        return
        
    status_map = {
        "single": "Single", 
        "mfj": "Married Filing Jointly", 
        "mfs": "Married Filing Separately", 
        "hoh": "Head of Household", 
        "qw": "Qualifying Widow(er)"
    }
    fs = status_map.get(tp.get("filing_status", "single"), "Single")
    
    ages = tp.get("ages", [0, 0])
    age_str = f"{ages[0]}" if len(ages) == 1 else f"{ages[0]} and {ages[1]}"
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Filing Status", fs)
    col2.metric("Ages", age_str)
    col3.metric("Social Security", f"${tp.get('ss_combined_annual', 0):,.0f}/yr")
    
    st.divider()
    
    st.subheader("Your Tax-Free Buffer")
    deduction = tp.get('effective_standard_deduction', 0)
    st.write(f"Because of your age and filing status, the first **${deduction:,.0f}** of your income this year is completely tax-free (this is your Standard Deduction).")
    st.caption(f"*Math Breakdown:* ${tp.get('standard_deduction_base', 0):,.0f} Base + Additional Senior Bonuses = ${deduction:,.0f}")

def tab_broker_import():
    st.header("Broker Import")
    st.write("Upload a CSV export from Fidelity, Schwab, Vanguard, or any broker and normalize it into the RICS format.")
    uploaded = st.file_uploader("Upload broker CSV", type=["csv", "txt"])
    broker = st.selectbox("Broker format", ["auto", "fidelity", "schwab", "vanguard", "generic"])
    snapshot_date = st.date_input("Snapshot date")
    acct_type_override = st.selectbox("Account type override (optional)", [None, "taxable", "trad_ira", "roth_ira", "inherited_ira"])
    if uploaded is not None:
        csv_text = uploaded.read().decode("utf-8")
        rows = parse_broker_csv(csv_text, broker=broker, snapshot_date=str(snapshot_date), account_type_override=acct_type_override)
        st.success(f"Parsed {len(rows)} holdings")
        if rows:
            st.subheader("Imported Holdings")
            st.dataframe([r.to_dict() for r in rows], use_container_width=True)
            if st.button("Merge into current snapshot"):
                existing = load_accounts_snapshot(str(SNAPSHOT_PATH))
                merged = merge_holdings(existing, rows)
                csv_out = holdings_to_csv(merged)
                st.download_button("Download merged snapshot CSV", csv_out, file_name="accounts_snapshot_merged.csv", mime="text/csv")
                st.info(f"Merged: {len(merged)} total holdings ({len(rows)} imported, {len(existing)} existing)")
            csv_out = holdings_to_csv(rows)
            st.download_button("Download imported holdings CSV", csv_out, file_name="broker_import.csv", mime="text/csv")

def tab_dividend_analysis():
    st.header("Your Income Stream")
    st.write("A breakdown of the passive income generated by your portfolio's dividends and interest.")
    
    holdings = load_holdings_with_mode()
    try: constraints = load_json(CONSTRAINTS_PATH)
    except FileNotFoundError: constraints = {}
    
    # Attempt to auto-load expenses so he doesn't have to type them
    try:
        cashflow = load_csv_rows(CASHFLOW_PATH)
        expense_items = [c for c in cashflow if c.get("category") == "expense" and c.get("frequency") == "annual"]
        auto_expenses = sum(float(e.get("amount", 0) or 0) for e in expense_items)
    except Exception:
        auto_expenses = 90000

    with st.expander("Adjust Annual Expenses"):
        annual_expenses = st.number_input("Estimated Annual Expenses ($)", value=int(auto_expenses) if auto_expenses > 0 else 90000, step=5000)
    
    result = analyze_holdings(holdings, annual_expenses=annual_expenses)
    
    st.divider()
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Annual Income", f"${getattr(result, 'total_annual_income', 0.0) or 0.0:,.0f}")
    col2.metric("Portfolio Yield", f"{getattr(result, 'weighted_avg_yield', 0.0) or 0.0:.2%}")
    col3.metric("5-Year Projected", f"${getattr(result, 'projected_5y_income', 0.0) or 0.0:,.0f}")
    
    # Visual Progress Bar for Coverage
    coverage = getattr(result, 'income_coverage_ratio', 0.0) or 0.0
    st.markdown("### Expense Coverage")
    st.write(f"Your portfolio income covers **{coverage:.1%}** of your estimated annual expenses.")
    
    progress_val = min(coverage, 1.0)
    st.progress(progress_val)
    
    if coverage >= 1.0:
        st.success("🎉 Incredible! Your dividends and interest completely cover your living expenses.")
    elif coverage >= 0.5:
        st.info("👍 You have a strong income floor! Your portfolio covers over half your expenses.")
    else:
        st.warning("💡 Your income covers a portion of your expenses. You may need to rely on Social Security or cash reserves to bridge the gap.")

    st.divider()
    st.subheader("Income Boost Ideas")
    st.write("Here are some simple adjustments you could make to safely increase your annual income.")
    
    opps = find_upgrade_opportunities(holdings, constraints)
    if opps:
        for opp in opps[:5]:  # Show top 5 to prevent overwhelming him
            cur_ticker = getattr(opp, 'current_ticker', '')
            sug_ticker = getattr(opp, 'suggested_ticker', '')
            inc_boost = getattr(opp, 'income_increase', 0.0)
            rationale = getattr(opp, 'rationale', '')
            
            st.info(f"**Swap {cur_ticker} for {sug_ticker}**")
            st.write(f"Moving your money from {cur_ticker} into {sug_ticker} could increase your income by **${inc_boost:,.0f}/year**.")
            st.caption(f"*Advisor Note:* {rationale}")
    else: 
        st.success("✅ Your portfolio is currently highly optimized for income. No obvious upgrades found.")

def tab_rebalance_simulator():
    st.header("Adjust My Risk")
    holdings = load_holdings_with_mode()
    try: constraints = load_json(CONSTRAINTS_PATH)
    except FileNotFoundError: constraints = {}
    try:
        profile = load_profile(TAX_PROFILE_PATH, CONSTRAINTS_PATH)
        prefs = load_sector_preferences(profile)
    except Exception: prefs = None

    st.subheader("How would you like your money to work for you?")
    goal = st.radio(
        "Select your main goal:",
        options=["Protect my cash (Conservative)", "Balanced Income & Growth (Moderate)", "Maximize Growth (Aggressive)"],
        index=1,
        help="This automatically sets the ideal mix of stocks, bonds, and cash."
    )
    
    score_map = {
        "Protect my cash (Conservative)": 20,
        "Balanced Income & Growth (Moderate)": 50,
        "Maximize Growth (Aggressive)": 80
    }
    score = score_map[goal]
    auto_target = score_to_allocation(score)
    
    with st.expander("Advanced: Fine-tune exact percentages"):
        col1, col2, col3, col4 = st.columns(4)
        us_eq = col1.number_input("US Equity %", 0, 100, int(auto_target.us_equity * 100))
        intl_eq = col2.number_input("Intl Equity %", 0, 100, int(auto_target.intl_equity * 100))
        bonds = col3.number_input("US Bonds %", 0, 100, int(auto_target.us_bond * 100))
        cash = col4.number_input("Cash/MMF %", 0, 100, int(auto_target.mmf * 100))
        band = st.slider("Rebalance band tolerance", 0.01, 0.20, 0.05, 0.01)
        
    target = AllocationTarget(us_eq / 100, intl_eq / 100, bonds / 100, cash / 100)
    
    if st.button("See Suggested Adjustments", type="primary"): 
        result = simulate_rebalance(holdings, target, constraints=constraints, rebalance_band=band, sector_prefs=prefs)
        st.divider()
        st.subheader("Impact of Adjustments")
        st.write("If you followed these recommendations, here is the impact on your portfolio:")
        col1, col2, col3 = st.columns(3)
        col1.metric("Portfolio Value", f"${result.total_portfolio_value:,.0f}")
        col2.metric("Estimated Tax Cost", f"${result.total_tax_cost:,.0f}")
        col3.metric("Turnover", f"{result.net_turnover_pct:.1%}")
        
        if result.trades:
            st.subheader(f"Advisor Recommendations ({len(result.trades)} Actionable Steps)")
            st.write("Here are the specific moves to align your portfolio with your selected goal:")
            
            for t in result.trades:
                action_str = "Sell" if t.action.lower() == "sell" else "Buy"
                
                if t.action.lower() == "sell":
                    reasoning = f"Selling this position locks in recent value and reduces your exposure in {t.ticker}, helping shift the portfolio toward your target."
                else:
                    reasoning = f"Adding to {t.ticker} builds up your target allocation securely within your {t.account_type}."
                    
                # Split into two clean lines to avoid any string parsing errors
                st.info(f"**{action_str} {t.shares:,.1f} shares of {t.ticker}** (approx. ${t.trade_value:,.0f}) in your {t.account_type}.")
                st.caption(f"*Why?* {reasoning}")
            
            with st.expander("View Trade Details Table"):
                trades_display = [{"Account": t.account_id, "Type": t.account_type, "Action": t.action.upper(), "Ticker": t.ticker, "Shares": f"{t.shares:,.1f}", "Value": f"${t.trade_value:,.0f}"} for t in result.trades]
                st.dataframe(trades_display, use_container_width=True)
        else:
            st.success("✅ Your portfolio is already perfectly aligned with this goal! No trades needed.")

def tab_recommendations():
    st.header("Advisor Alerts & Ideas")
    st.write("Smart, automated suggestions to protect your wealth and lower your taxes.")
    
    try:
        tax_profile = load_json(TAX_PROFILE_PATH)
        constraints = load_json(CONSTRAINTS_PATH)
        cashflow = load_csv_rows(CASHFLOW_PATH)
        rmd_divisors = load_json(RMD_DIVISORS_PATH)
    except FileNotFoundError:
        st.warning("We are waiting for your profile data to generate ideas. Please complete your profile in Settings.")
        return

    holdings = load_holdings_with_mode()
    recs = generate_all_recommendations(holdings, tax_profile, constraints, cashflow, rmd_divisors)
    
    if not recs:
        st.success("✅ Your financial plan looks solid! We have no new alerts or recommendations for you right now.")
        return

    st.markdown(f"#### We found **{len(recs)}** ideas for you to review:")
    st.markdown("<br>", unsafe_allow_html=True)
    
    for rec in recs:
        # Use visually distinct borders and colors instead of hidden expanders
        with st.container(border=True):
            if rec.severity == "high":
                st.error(f"🚨 **URGENT: {rec.title}**")
                callout_func = st.error
            elif rec.severity == "medium":
                st.warning(f"💡 **OPPORTUNITY: {rec.title}**")
                callout_func = st.warning
            else:
                st.success(f"✅ **IDEA: {rec.title}**")
                callout_func = st.success
                
            st.markdown(f"<p style='font-size: 1.15em; margin-top: 10px;'>{rec.description}</p>", unsafe_allow_html=True)
            
            if getattr(rec, 'impact_estimate', ''):
                st.markdown(f"**Estimated Impact:** <span style='color: #4CAF50; font-size: 1.1em;'>{rec.impact_estimate}</span>", unsafe_allow_html=True)
            
            st.markdown("<br>", unsafe_allow_html=True)
            callout_func(f"**👉 Suggested Action:** {rec.action}")

def tab_big_purchase_check():
    st.header("Can I Afford This?")
    st.write("Planning a trip, a new car, or a home renovation? Enter the estimated cost below to see how it impacts your financial plan.")
    
    with st.container():
        st.markdown("### Let's check the numbers")
        col1, col2 = st.columns([2, 1])
        with col1:
            purchase_name = st.text_input("What are you planning to buy?", placeholder="e.g., River Cruise in Europe")
        with col2:
            purchase_amount = st.number_input("Estimated Cost ($)", min_value=0, step=1000, value=15000)
    
    if st.button("Check My Plan", type="primary"):
        if purchase_amount <= 0:
            st.info("Please enter a valid amount above $0.")
            return
            
        # Gather necessary data
        holdings = load_holdings_with_mode()
        total_portfolio = sum(float(h.get("market_value", 0)) for h in holdings) if holdings else 0.0
        
        try:
            cashflow = load_csv_rows(CASHFLOW_PATH)
            income_items = [c for c in cashflow if c.get("category") == "income"]
            expense_items = [c for c in cashflow if c.get("category") == "expense" and c.get("frequency") == "annual"]
            total_income = sum(float(i.get("amount", 0) or 0) for i in income_items)
            total_exp = sum(float(e.get("amount", 0) or 0) for e in expense_items)
            surplus = total_income - total_exp
        except Exception:
            surplus = 0
            
        # Get Liquid Cash reserves
        alloc = compute_current_allocation(holdings) if holdings else {}
        cash_value = alloc.get("mmf", 0.0) * total_portfolio
        
        st.divider()
        st.subheader(f"Results for: {purchase_name if purchase_name else 'Your Purchase'}")
        
        # Logic for Advisor Response
        if purchase_amount <= surplus:
            st.success("🟢 **Yes, absolutely!**")
            st.write(f"Your expected annual cash surplus of **${surplus:,.0f}** easily covers this **${purchase_amount:,.0f}** expense without needing to touch your investments.")
            st.write("**Advisor Note:** Enjoy it! You have built a great safety net, and this fits perfectly within your budget.")
            
        elif purchase_amount <= (surplus + cash_value):
            st.warning("🟡 **Yes, using your cash reserves.**")
            shortfall = purchase_amount - surplus
            st.write(f"Your annual surplus is **${surplus:,.0f}**, so you will need to pull **${shortfall:,.0f}** from your cash/money market reserves.")
            st.write("**Advisor Note:** This is a safe move. It doesn't require selling any stocks or bonds, meaning it won't trigger unexpected capital gains taxes or alter your risk profile.")
            
        else:
            st.error("🔴 **Caution: This requires selling investments.**")
            investments_to_sell = purchase_amount - surplus - cash_value
            st.write(f"To afford this, you would use up your surplus and cash reserves, plus you'd need to sell about **${investments_to_sell:,.0f}** of your invested assets.")
            
            if total_portfolio > 0 and (purchase_amount / total_portfolio) > 0.05:
                st.write("**Advisor Note:** This represents a significant withdrawal (more than 5% of your total portfolio). Consider if this purchase can be split across multiple tax years to minimize the tax hit.")
            else:
                st.write("**Advisor Note:** Before moving forward, check the 'Tax & Planning Ideas' section. We want to be smart about *which* accounts to withdraw this money from so we don't accidentally bump you into a higher tax bracket.")

def main():
    cloud_sync.run_auto_sync() # Silently handle cloud backups
    if len(sys.argv) > 1 and sys.argv[1] == "--baseline": return
    st.set_page_config(page_title="Retirement Dashboard", page_icon="🏦", layout="wide")
    
    if "finnhub_key" not in st.session_state:
        stored_key = settings_store.get_setting("finnhub_api_key", "")
        st.session_state["finnhub_key"] = os.environ.get("FINNHUB_API_KEY", stored_key)
    if "price_mode" not in st.session_state:
        saved_mode = settings_store.get_setting("price_mode", "disk")
        st.session_state["price_mode"] = "Live (Finnhub)" if saved_mode == "live" else "Snapshot (CSV prices)"
    if "onboarding_dismissed" not in st.session_state:
        st.session_state["onboarding_dismissed"] = bool(settings_store.get_setting("finnhub_api_key", ""))

    if not st.session_state["onboarding_dismissed"]:
        st.title("Enable live prices")
        new_key = st.text_input("Finnhub API key", type="password")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Save and enable"):
                settings_store.set_setting("finnhub_api_key", new_key)
                settings_store.set_setting("price_mode", "live")
                st.session_state["finnhub_key"] = new_key
                st.session_state["price_mode"] = "Live (Finnhub)"
                st.session_state["onboarding_dismissed"] = True; st.rerun()
        with col2:
            if st.button("Skip"):
                st.session_state["onboarding_dismissed"] = True; st.rerun()
        return

    
    
    # ── CLEAN NAVIGATION ───────────────────────────────────────────
    st.sidebar.markdown("## Main Menu")
    section = st.sidebar.radio(
        "Main Menu", 
        ["Home Dashboard", "Can I Afford This?", "My Accounts & Holdings", "Adjust My Risk", "Trade Log", "Tax & Planning Ideas", "Settings & Data"], 
        key="nav_section",
        label_visibility="collapsed"
    )
    
    # ── PAGE ROUTING ───────────────────────────────────────────────
    if section == "Home Dashboard": 
        tab_your_plan()
        
    elif section == "Can I Afford This?":
        tab_big_purchase_check()
        
    elif section == "My Accounts & Holdings":
        st.header("Your Portfolio & Income")
        tab1, tab2 = st.tabs(["Overview", "Dividend Analysis"])
        with tab1: tab_portfolio_overview()
        with tab2: tab_dividend_analysis()
        
    elif section == "Adjust My Risk":
        tab_rebalance_simulator()
        
    elif section == "Tax & Planning Ideas":
        st.header("Planning & Taxes")
        tab1, tab2, tab3 = st.tabs(["Recommendations", "Tax Dashboard", "Cash Flow Plan"])
        with tab1: tab_recommendations()
        with tab2: tab_tax_dashboard()
        with tab3: tab_cashflow()
        
    elif section == "Trade Log":
        st.header("Trade Log & Entry")
        st.write("Record your buys, sells, and withdrawals to keep your portfolio up to date.")
        _setup_trades_sidebar()

    elif section == "Settings & Data":
        st.header("Settings & Data Management")
        st.write("Manage your profile, update holdings, and configure application settings here.")
        st.markdown("<br>", unsafe_allow_html=True)
        
        tab_prof, tab_hold, tab_sys = st.tabs([
            "👤 Profile & Preferences", 
            "📁 Holdings & Import", 
            "⚙️ System Settings"
        ])
        
        with tab_prof:
            _setup_profile_editor()
            st.divider()
            _setup_sector_prefs_sidebar()
            
        with tab_hold:
            tab_broker_import()
            st.divider()
            _setup_holdings_editor()
            
        with tab_sys:
            _setup_price_sidebar()

if __name__ == "__main__":
    main()
