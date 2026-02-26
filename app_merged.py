"""
app.py — Merged Streamlit UI for RICS with live-quote overlay

This app restores the multi-tab UI (Today, Risk, Deterministic, Monte Carlo, Trip)
and integrates the Finnhub live-quote overlay in the ingestion step so all modules
use the updated prices. It expects your original modules to exist in the same folder:
ingest.py, risk.py, deterministic.py, mc_sim.py, trip_simulator.py, tax_irmaa.py, rmd.py, withdrawals.py.

Place this file in your rics/ project root (replacing the lightweight demo app if needed).
"""

import os
import io
import csv
import time
import json
from datetime import datetime

import streamlit as st

# Local modules (these should be present in your rics/ folder)
try:
    from ingest import ingest_all, load_accounts  # ingest_all(paths, price_overrides=None)
except Exception:
    # fallback names if your ingest API differs; we'll implement a small wrapper later
    ingest_all = None

try:
    from quotes import fetch_quotes_finnhub
except Exception:
    fetch_quotes_finnhub = None

try:
    from live_overlay import apply_price_overrides
except Exception:
    apply_price_overrides = None

# Import analysis modules (may raise if not present; handled later)
_mods = {}
for name in ("risk", "deterministic", "mc_sim", "trip_simulator", "tax_irmaa", "rmd", "withdrawals"):
    try:
        _mods[name] = __import__(name)
    except Exception:
        _mods[name] = None

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
ACCOUNTS_CSV = os.path.join(DATA_DIR, "accounts_snapshot.csv")

st.set_page_config(page_title="RICS — Retirement Income Control System", layout="wide")
st.title("RICS — Retirement Income Control System")


@st.cache_data(ttl=300, show_spinner=False)
def read_accounts_snapshot(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            r_parsed = dict(r)
            for k in ("shares", "price", "market_value", "cost_basis"):
                if k in r_parsed and r_parsed[k] != "":
                    try:
                        r_parsed[k] = float(r_parsed[k])
                    except Exception:
                        pass
            rows.append(r_parsed)
    return rows


def build_price_overrides_from_quotes(quotes):
    price_overrides = {}
    quote_failures = {}
    for t, q in (quotes or {}).items():
        if not isinstance(q, dict):
            quote_failures[t] = {"error": "invalid response", "value": q}
            continue
        if "price" in q and q["price"] not in (None, 0, ""):
            try:
                price_overrides[t] = float(q["price"])
            except Exception as exc:
                quote_failures[t] = {"error": f"bad price value: {exc}", "value": q}
        else:
            quote_failures[t] = q
    return price_overrides, quote_failures


def recompute_derived_fields(accounts):
    for r in accounts:
        price = r.get("price") or 0.0
        shares = r.get("shares") or 0.0
        cost = r.get("cost_basis") or 0.0
        r["market_value"] = shares * price
        r["unrealized_gain"] = r["market_value"] - cost
    return accounts


def csv_download_bytes(accounts):
    if not accounts:
        return None
    output = io.StringIO()
    fieldnames = list(accounts[0].keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for r in accounts:
        safe = {}
        for k, v in r.items():
            if v is None:
                safe[k] = ""
            elif isinstance(v, float):
                safe[k] = f"{v:.6f}"
            else:
                safe[k] = str(v)
        writer.writerow(safe)
    return output.getvalue().encode("utf-8")


# Sidebar: live quotes controls
st.sidebar.header("Live quotes (optional)")
use_live = st.sidebar.checkbox("Overlay live prices (Finnhub)", value=False)
api_key_input = st.sidebar.text_input("Finnhub API key (leave blank to use FINNHUB_API_KEY env var)", type="password")
refresh = st.sidebar.button("Refresh quotes")


# Load snapshot
accounts_snapshot = read_accounts_snapshot(ACCOUNTS_CSV)
tickers = sorted({r.get("ticker", "").strip() for r in accounts_snapshot if r.get("ticker")})

# If user wants live quotes, fetch and create price_overrides
price_overrides = {}
quote_failures = {}
quote_meta = {}

if use_live and tickers and fetch_quotes_finnhub is not None:
    api_key = api_key_input.strip() or os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        st.sidebar.error("Finnhub API key required (env var or paste in field).")
    else:
        try:
            quotes = fetch_quotes_finnhub(tickers, api_key=api_key, timeout=8)
            price_overrides, quote_failures = build_price_overrides_from_quotes(quotes)
            quote_meta["asof"] = int(time.time())
            quote_meta["fetched_count"] = len(quotes)
            quote_meta["updated_count"] = len(price_overrides)
        except Exception as exc:
            st.sidebar.error(f"Error fetching quotes: {exc}")
            quotes = {}
else:
    quotes = {}


# If ingest_all exists and accepts overrides, use it; otherwise apply overlay locally
ingested = None
ingest_paths = {"data_dir": DATA_DIR}

if ingest_all is not None:
    # Try to call ingest_all with price_overrides if supported
    try:
        # ingest_all may accept positional args; try keyword first
        try:
            ingested = ingest_all(ingest_paths, price_overrides=price_overrides or None)
        except TypeError:
            # fallback: call without overrides and then apply overlay if available
            ingested = ingest_all(ingest_paths)
            if price_overrides and apply_price_overrides is not None:
                ingested_accounts = ingested.get("accounts") if isinstance(ingested, dict) else None
                if ingested_accounts is None:
                    # try to load accounts_snapshot directly
                    accounts_snapshot = apply_price_overrides(accounts_snapshot, price_overrides)
                else:
                    apply_price_overrides(ingested_accounts, price_overrides)
    except Exception as exc:
        st.sidebar.error(f"Error in ingest_all: {exc}")
        ingested = None
else:
    # No ingest_all found: apply overlay to snapshot rows
    if price_overrides and apply_price_overrides is not None:
        accounts_snapshot = apply_price_overrides(accounts_snapshot, price_overrides)
    ingested = {"accounts": accounts_snapshot, "summary": {}}

# Ensure derived fields present
accounts = ingested.get("accounts") if isinstance(ingested, dict) else accounts_snapshot
accounts = recompute_derived_fields(accounts)


# Top-level header and summary metrics
st.header("Overview")
col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    st.subheader("Accounts snapshot")
    st.write(f"Rows: {len(accounts)} — Tickers found: {len(tickers)}")
with col2:
    if quote_meta:
        st.metric("Quotes fetched", quote_meta.get("fetched_count", 0))
with col3:
    if quote_meta:
        st.metric("Tickers updated", quote_meta.get("updated_count", 0))

# Tabs: Today, Risk, Deterministic, Monte Carlo, Trip Simulator
tabs = st.tabs(["Today", "Risk", "Deterministic", "Monte Carlo", "Trip Simulator"])

# --- TODAY tab: account overview, cashflow, expenses
with tabs[0]:
    st.subheader("Today — snapshot & cashflow")
    st.dataframe(accounts, use_container_width=True)
    # Attempt to show cashflow plan if available in data/
    cashflow_path = os.path.join(DATA_DIR, "cashflow_plan.csv")
    if os.path.exists(cashflow_path):
        try:
            import csv as _csv
            rows = []
            with open(cashflow_path, newline='', encoding='utf-8') as f:
                r = _csv.DictReader(f)
                for row in r:
                    rows.append(row)
            st.write("Cashflow plan")
            st.dataframe(rows)
        except Exception as exc:
            st.warning(f"Could not load cashflow_plan.csv: {exc}")

# --- RISK tab
with tabs[1]:
    st.subheader("Risk report")
    if _mods.get("risk") is not None:
        try:
            rr = _mods["risk"].risk_report(accounts)
            st.json(rr)
        except Exception as exc:
            st.error(f"risk_report failed: {exc}")
    else:
        st.info("risk.py not available; install or place it in the rics folder.")

# --- DETERMINISTIC tab
with tabs[2]:
    st.subheader("Deterministic projections")
    if _mods.get("deterministic") is not None:
        try:
            proj = _mods["deterministic"].build_projection_from_ingested(ingested, horizon=20)
            st.dataframe(proj)
        except Exception as exc:
            st.error(f"deterministic projection failed: {exc}")
    else:
        st.info("deterministic.py not available; install or place it in the rics folder.")

# --- MONTE CARLO tab
with tabs[3]:
    st.subheader("Monte Carlo simulations")
    if _mods.get("mc_sim") is not None:
        try:
            mc = _mods["mc_sim"].run_mc_from_ingested(ingested, n_sims=10000, horizon=20, seed=42)
            # show summary stats if present
            if isinstance(mc, dict) and "ruin_stats" in mc:
                st.json(mc["ruin_stats"])
            else:
                st.write(mc)
        except Exception as exc:
            st.error(f"mc_sim failed: {exc}")
    else:
        st.info("mc_sim.py not available; install or place it in the rics folder.")

# --- TRIP SIMULATOR tab
with tabs[4]:
    st.subheader("Trip simulator (one-off expense)")
    if _mods.get("trip_simulator") is not None:
        try:
            # Simple UI to run a quick trip impact analysis
            cost = st.number_input("Trip cost ($)", value=25000, step=1000)
            year = st.number_input("Year", value=datetime.utcnow().year, step=1)
            if st.button("Analyze trip impact"):
                trip = _mods["trip_simulator"].trip_impact(cost, year, "optimal", ingested)
                st.json(trip)
        except Exception as exc:
            st.error(f"trip_simulator failed: {exc}")
    else:
        st.info("trip_simulator.py not available; install or place it in the rics folder.")

# Quote coverage expander
with st.expander("Quote coverage (updated vs skipped)", expanded=False):
    st.write("Price overrides applied for these tickers:")
    st.write(list(price_overrides.keys()))
    if quote_failures:
        st.warning(f"{len(quote_failures)} tickers were skipped or returned no usable price.")
        st.json(quote_failures)

# CSV export
if st.button("Download refreshed accounts_snapshot.csv (one-off)"):
    b = csv_download_bytes(accounts)
    if b:
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        st.download_button("Click to download", data=b, file_name=f"accounts_snapshot_refreshed_{ts}.csv", mime="text/csv")
    else:
        st.error("No accounts to export.")

st.caption("Note: accounts_snapshot.csv remains the canonical snapshot on disk. This export is one-off and will not overwrite your files.")