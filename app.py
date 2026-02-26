"""
app.py — Streamlit UI for RICS (with optional Finnhub live-quote overlay)

This is an updated lightweight Streamlit app that integrates the
quotes.fetch_quotes_finnhub function and handles failures robustly.
Designed to slot into your existing rics/ folder. Adjust paths if needed.
"""

import os
import io
import csv
import time
import json
from datetime import datetime

import streamlit as st

# Import local modules (make sure quotes.py and live_overlay.py are on PYTHONPATH)
from quotes import fetch_quotes_finnhub
from live_overlay import apply_price_overrides

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
ACCOUNTS_CSV = os.path.join(DATA_DIR, "accounts_snapshot.csv")


st.set_page_config(page_title="RICS — Demo", layout="wide")
st.title("RICS — Portfolio Snapshot (with optional live quotes)")


@st.cache_data(ttl=300, show_spinner=False)
def load_accounts_csv(path):
    """Return list[dict] of account rows read from CSV (no price overrides)."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            # Basic numeric coercion where helpful
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
    """Create price_overrides dict and separate failures for UI."""
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
    """Ensure market_value and unrealized_gain exist for display after overlay."""
    for r in accounts:
        price = r.get("price") or 0.0
        shares = r.get("shares") or 0.0
        cost = r.get("cost_basis") or 0.0
        r["market_value"] = shares * price
        r["unrealized_gain"] = r["market_value"] - cost
    return accounts


def csv_download_bytes(accounts):
    """Return a bytes buffer for downloading the refreshed CSV (one-off)."""
    if not accounts:
        return None
    output = io.StringIO()
    # Infer fieldnames from first row (preserve original order where possible)
    fieldnames = list(accounts[0].keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for r in accounts:
        # Convert any floats to plain values for CSV
        safe = {k: (("" if v is None else v) if not isinstance(v, float) else f"{v:.6f}") for k, v in r.items()}
        writer.writerow(safe)
    return output.getvalue().encode("utf-8")


# Sidebar controls
st.sidebar.header("Live quotes (optional)")
use_live = st.sidebar.checkbox("Overlay live prices (Finnhub)", value=False)
api_key_input = st.sidebar.text_input("Finnhub API key (leave blank to use FINNHUB_API_KEY env var)", type="password")
refresh = st.sidebar.button("Refresh quotes (cache-bust)")

# Main load
st.sidebar.markdown("---")
st.sidebar.markdown("Data source: local `data/accounts_snapshot.csv` (snapshot).")
accounts = load_accounts_csv(ACCOUNTS_CSV)

tickers = sorted({r.get("ticker", "").strip() for r in accounts if r.get("ticker")})
quote_meta = {}
price_overrides = {}
quote_failures = {}

if use_live and tickers:
    # Determine API key to use
    api_key = api_key_input.strip() or os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        st.sidebar.error("Finnhub API key required (env var or paste in field).")
    else:
        # Cache-busting: if user pressed refresh, clear cache by calling st.cache_data with different args
        # We wrap call in try/except and surface errors without crashing the app.
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

# Apply overrides (in-memory) and recompute derived fields
if price_overrides:
    accounts = apply_price_overrides(accounts, price_overrides)
accounts = recompute_derived_fields(accounts)

# Top-level summary
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

# Accounts table
st.dataframe(accounts, use_container_width=True)

# Quote coverage and failures
with st.expander("Quote coverage (updated vs skipped)", expanded=False):
    st.write("Price overrides applied for these tickers:")
    st.write(list(price_overrides.keys()))
    if quote_failures:
        st.warning(f"{len(quote_failures)} tickers were skipped or returned no usable price.")
        st.json(quote_failures)

# Download refreshed CSV
if st.button("Download refreshed accounts_snapshot.csv (one-off)"):
    b = csv_download_bytes(accounts)
    if b:
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        st.download_button("Click to download", data=b, file_name=f"accounts_snapshot_refreshed_{ts}.csv", mime="text/csv")
    else:
        st.error("No accounts to export.")

st.markdown("---")
st.caption("Note: accounts_snapshot.csv remains the canonical snapshot on disk. This export is one-off and will not overwrite your files.")
