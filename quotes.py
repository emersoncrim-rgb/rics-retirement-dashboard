"""quotes.py — Live price provider for RICS (Finnhub)

This module fetches latest quote prices from Finnhub.
It prefers using `requests` (if available) because requests uses certifi
automatically. If `requests` is not installed, it falls back to urllib + certifi
to create an SSL context that validates TLS certificates.

Usage:
    from quotes import fetch_quotes_finnhub
    quotes = fetch_quotes_finnhub(["AAPL","MSFT"], api_key="...")
"""

import json
import os
import time
from urllib.parse import urlencode
from urllib.request import urlopen, Request

# Optional imports
try:
    import requests  # type: ignore
except Exception:
    requests = None  # fallback to urllib

# Prefer certifi for a robust CA bundle
try:
    import certifi  # type: ignore
    import ssl  # type: ignore
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    certifi = None
    _SSL_CTX = None


def _fetch_with_requests(symbol, api_key, timeout=10):
    """Fetch single symbol using requests. Returns parsed JSON or None."""
    base = "https://finnhub.io/api/v1/quote"
    resp = requests.get(base, params={"symbol": symbol, "token": api_key}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _fetch_with_urllib(symbol, api_key, timeout=10):
    """Fetch single symbol using urllib with a certifi-backed SSL context if available."""
    url = "https://finnhub.io/api/v1/quote?" + urlencode({"symbol": symbol, "token": api_key})
    req = Request(url, headers={"User-Agent": "rics-live/0.1"})
    if _SSL_CTX is not None:
        with urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
            data = json.loads(r.read().decode("utf-8"))
    else:
        # Last-resort: rely on system SSL (may fail on some macOS installs)
        with urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    return data


def fetch_quotes_finnhub(symbols, api_key=None, timeout=10):
    """Fetch latest quote prices from Finnhub for the provided iterable of symbols.
    Returns:
        dict[str, dict] -> {
            "AAPL": {"price": 187.12, "asof_epoch": 1700000000, "source": "finnhub"},
            ...
        }
    Notes:
      - Requires a Finnhub API key (either via `api_key` or env var FINNHUB_API_KEY).
      - Uses requests (if installed) or urllib + certifi to avoid certificate issues.
    """
    if api_key is None:
        api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        raise ValueError("Finnhub API key not provided. Set FINNHUB_API_KEY or pass api_key argument.")


    results = {}
    # normalize and dedupe input
    unique = sorted({str(s).strip() for s in symbols if s and str(s).strip()})

    for sym in unique:
        try:
            if requests is not None:
                data = _fetch_with_requests(sym, api_key, timeout=timeout)
            else:
                data = _fetch_with_urllib(sym, api_key, timeout=timeout)
        except Exception as exc:
            # Logically skip this symbol on failure (caller can inspect missing tickers)
            # Avoid raising to keep UI resilient; return only successful symbols.
            # You can change this to `raise` if you prefer strict behavior.
            # For debugging, attach the error to the results entry.
            results[sym] = {"error": str(exc)}
            continue

        # Finnhub 'c' field is the current price; 't' is epoch timestamp
        price = data.get("c")
        if price is None or price == 0:
            # No usable price; surface whatever response for debugging
            results[sym] = {"raw": data}
            continue

        results[sym] = {
            "price": float(price),
            "asof_epoch": int(data.get("t", time.time())),
            "source": "finnhub",
        }

    return results
