from quotes import fetch_quotes_finnhub
import json

# Hardcoded API key (user-provided for debugging)
API_KEY = "d6g84l1r01qt4931tc50d6g84l1r01qt4931tc5g"

tickers = ["AAPL", "MSFT", "GOOGL", "VMFXX", "CASH"]

quotes = fetch_quotes_finnhub(tickers, api_key=API_KEY, timeout=8)

print("=== RAW QUOTES RESPONSE ===")
print(json.dumps(quotes, indent=2))

ok = {
    t: q for t, q in quotes.items()
    if isinstance(q, dict) and "price" in q and q["price"] not in (None, 0, "")
}

bad = {t: q for t, q in quotes.items() if t not in ok}

print("\n=== SUCCESSFUL TICKERS ===")
print(list(ok.keys()))

print("\n=== FAILED / SKIPPED TICKERS ===")
print(json.dumps(bad, indent=2))
