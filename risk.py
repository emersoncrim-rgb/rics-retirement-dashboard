"""
risk.py — Aggressiveness scoring and asset-class bucket mapping for RICS.

Public API:
    map_holdings_to_buckets(accounts) -> dict
    compute_aggressiveness_score(accounts) -> float
    describe_posture(score) -> dict
    risk_report(accounts) -> dict   # convenience wrapper
"""

from __future__ import annotations
from typing import Dict, List

# ---------------------------------------------------------------------------
# Constants: ticker → sector classification
# ---------------------------------------------------------------------------

# Tickers classified as "tech" for concentration scoring.
# Extend as needed; keeps it simple and auditable for a local system.
TECH_TICKERS = {
    "AAPL", "MSFT", "NVDA", "AVGO", "GOOG", "GOOGL", "AMZN", "META",
    "TSM", "AMD", "INTC", "CRM", "ADBE", "ORCL", "CSCO", "QCOM",
    "ANET", "NOW", "INTU", "MU", "LRCX", "KLAC", "SNPS", "CDNS",
    "QQQ", "VGT", "XLK", "SMH",
}

# Tickers that are "dividend equity" — high-yield equity ETFs or
# individual stocks with qualified_div_yield > threshold.
DIVIDEND_EQUITY_ETFS = {
    "VYM", "SCHD", "DVY", "HDV", "SPYD", "VIG", "DGRO", "NOBL",
}

# Asset classes from accounts_snapshot.csv → bucket mapping
_ASSET_CLASS_TO_BUCKET = {
    "us_equity":   "equity",
    "intl_equity": "equity",
    "us_bond":     "bond",
    "intl_bond":   "bond",
    "mmf":         "cash",
    "cash":        "cash",
    "reit":        "equity",   # REITs count as equity risk
    "other":       "equity",   # conservative: treat unknown as risky
}

# Threshold: if a stock's qualified_div_yield >= this, classify as
# dividend_equity instead of plain equity (only for individual stocks)
_DIV_YIELD_THRESHOLD = 0.025


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# map_holdings_to_buckets
# ---------------------------------------------------------------------------

def map_holdings_to_buckets(accounts: list[dict]) -> Dict[str, float]:
    """
    Map each holding to one of four buckets and return portfolio-weight dict.

    Buckets: equity, dividend_equity, bond, cash

    Classification rules (in priority order):
      1. asset_class in (mmf, cash)           → cash
      2. asset_class in (us_bond, intl_bond)  → bond
      3. ticker in DIVIDEND_EQUITY_ETFS       → dividend_equity
      4. asset_class is equity AND yield ≥ 2.5% → dividend_equity
      5. remaining equity                     → equity
    """
    buckets = {"equity": 0.0, "dividend_equity": 0.0, "bond": 0.0, "cash": 0.0}
    total = sum(r["market_value"] for r in accounts)
    if total == 0:
        return buckets

    for r in accounts:
        mv = r["market_value"]
        ac = r.get("asset_class", "other")
        ticker = r.get("ticker", "")
        div_yield = r.get("qualified_div_yield", 0.0) or 0.0

        base_bucket = _ASSET_CLASS_TO_BUCKET.get(ac, "equity")

        if base_bucket == "cash":
            buckets["cash"] += mv
        elif base_bucket == "bond":
            buckets["bond"] += mv
        elif ticker in DIVIDEND_EQUITY_ETFS:
            buckets["dividend_equity"] += mv
        elif base_bucket == "equity" and div_yield >= _DIV_YIELD_THRESHOLD:
            buckets["dividend_equity"] += mv
        else:
            buckets["equity"] += mv

    # Convert to weights
    return {k: v / total for k, v in buckets.items()}


# ---------------------------------------------------------------------------
# Concentration helpers
# ---------------------------------------------------------------------------

def _ticker_weights(accounts: list[dict]) -> Dict[str, float]:
    """Return {ticker: portfolio_weight} aggregated across all accounts."""
    total = sum(r["market_value"] for r in accounts)
    if total == 0:
        return {}
    agg: Dict[str, float] = {}
    for r in accounts:
        t = r["ticker"]
        agg[t] = agg.get(t, 0) + r["market_value"]
    return {t: v / total for t, v in agg.items()}


def _top_n_pct(ticker_weights: Dict[str, float], n: int) -> float:
    """Sum of the top-n ticker weights."""
    sorted_w = sorted(ticker_weights.values(), reverse=True)
    return sum(sorted_w[:n])


def _tech_pct(accounts: list[dict]) -> float:
    """Fraction of portfolio in tech tickers (individual + tech-sector ETFs).

    For broad-market ETFs like VTI/VXUS, we do NOT attribute a tech sub-share.
    This keeps the metric conservative and auditable — only explicit tech
    holdings count.  A future enhancement could apply look-through weights.
    """
    total = sum(r["market_value"] for r in accounts)
    if total == 0:
        return 0.0
    tech_val = sum(r["market_value"] for r in accounts if r["ticker"] in TECH_TICKERS)
    return tech_val / total


# ---------------------------------------------------------------------------
# compute_aggressiveness_score
# ---------------------------------------------------------------------------

def compute_aggressiveness_score(accounts: list[dict]) -> float:
    """
    Compute a 0–100 aggressiveness score for the portfolio.

    Formula (each term linearly interpolated then clamped 0–1):
        +40 × equity tilt       : (equity_pct − 0.45) / (0.90 − 0.45)
        +15 × top-1 concentration: (top1_pct − 0.10) / (0.30 − 0.10)
        +10 × top-5 concentration: (top5_pct − 0.25) / (0.60 − 0.25)
        +20 × tech overweight   : (tech_pct − 0.20) / (0.45 − 0.20)
        −15 × defensive buffer  : (cash+bond − 0.20) / (0.60 − 0.20)

    Returns float in [0, 100].
    """
    buckets = map_holdings_to_buckets(accounts)
    tw = _ticker_weights(accounts)

    equity_pct = buckets["equity"] + buckets["dividend_equity"]
    cash_bond_pct = buckets["cash"] + buckets["bond"]
    top1_pct = _top_n_pct(tw, 1)
    top5_pct = _top_n_pct(tw, 5)
    tech = _tech_pct(accounts)

    score = 0.0
    score += 40 * _clamp((equity_pct - 0.45) / (0.90 - 0.45))
    score += 15 * _clamp((top1_pct  - 0.10) / (0.30 - 0.10))
    score += 10 * _clamp((top5_pct  - 0.25) / (0.60 - 0.25))
    score += 20 * _clamp((tech      - 0.20) / (0.45 - 0.20))
    score -= 15 * _clamp((cash_bond_pct - 0.20) / (0.60 - 0.20))

    return round(_clamp(score, 0.0, 100.0), 1)


# ---------------------------------------------------------------------------
# describe_posture
# ---------------------------------------------------------------------------

_POSTURE_BANDS = [
    (25, "Conservative",
     "Portfolio is defensively positioned with heavy bond/cash allocation.\n"
     "Low concentration risk and minimal tech exposure.\n"
     "Suitable for capital preservation with modest income generation."),
    (50, "Balanced",
     "Portfolio balances growth and safety with moderate equity exposure.\n"
     "Concentration and sector tilts are within normal ranges.\n"
     "Appropriate for a retiree seeking steady income with some upside."),
    (75, "Growth",
     "Portfolio leans toward equities with noticeable concentration.\n"
     "Tech and/or single-stock exposure is above typical retiree levels.\n"
     "Higher return potential but more volatile drawdown risk."),
    (100, "Aggressive",
     "Portfolio is heavily equity-tilted with significant concentration risk.\n"
     "Tech overweight and/or top-holding dominance amplify downside.\n"
     "Consider de-risking unless there is a specific strategic rationale."),
]


def describe_posture(score: float) -> Dict[str, str]:
    """
    Return a label and 3-line justification for the aggressiveness score.

    Returns: {"score": float, "label": str, "justification": str}
    """
    for threshold, label, justification in _POSTURE_BANDS:
        if score <= threshold:
            return {"score": score, "label": label, "justification": justification}
    # fallback (score exactly 100 already handled, but just in case)
    return {"score": score, "label": "Aggressive", "justification": _POSTURE_BANDS[-1][2]}


# ---------------------------------------------------------------------------
# risk_report — convenience wrapper
# ---------------------------------------------------------------------------

def risk_report(accounts: list[dict]) -> dict:
    """Full risk summary combining buckets, score, and posture."""
    buckets = map_holdings_to_buckets(accounts)
    tw = _ticker_weights(accounts)
    score = compute_aggressiveness_score(accounts)
    posture = describe_posture(score)

    return {
        "buckets": buckets,
        "ticker_weights_top10": dict(sorted(tw.items(), key=lambda x: -x[1])[:10]),
        "top1_pct": _top_n_pct(tw, 1),
        "top5_pct": _top_n_pct(tw, 5),
        "tech_pct": _tech_pct(accounts),
        "aggressiveness_score": score,
        "posture": posture,
    }


# ---------------------------------------------------------------------------
# Components breakdown (for UI / debugging)
# ---------------------------------------------------------------------------

def score_components(accounts: list[dict]) -> dict:
    """Return the individual additive components of the aggressiveness score."""
    buckets = map_holdings_to_buckets(accounts)
    tw = _ticker_weights(accounts)

    equity_pct = buckets["equity"] + buckets["dividend_equity"]
    cash_bond_pct = buckets["cash"] + buckets["bond"]
    top1 = _top_n_pct(tw, 1)
    top5 = _top_n_pct(tw, 5)
    tech = _tech_pct(accounts)

    return {
        "inputs": {
            "equity_pct": round(equity_pct, 4),
            "cash_bond_pct": round(cash_bond_pct, 4),
            "top1_pct": round(top1, 4),
            "top5_pct": round(top5, 4),
            "tech_pct": round(tech, 4),
        },
        "components": {
            "equity_tilt (+40 max)":    round(40 * _clamp((equity_pct - 0.45) / 0.45), 1),
            "top1_conc (+15 max)":      round(15 * _clamp((top1 - 0.10) / 0.20), 1),
            "top5_conc (+10 max)":      round(10 * _clamp((top5 - 0.25) / 0.35), 1),
            "tech_tilt (+20 max)":      round(20 * _clamp((tech - 0.20) / 0.25), 1),
            "defensive_buf (-15 max)":  round(-15 * _clamp((cash_bond_pct - 0.20) / 0.40), 1),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_report(report: dict) -> None:
    print("\n" + "=" * 60)
    print("  AGGRESSIVENESS SCORE & RISK POSTURE")
    print("=" * 60)

    print("\n── Bucket Weights ──")
    for bucket, w in report["buckets"].items():
        bar = "█" * int(w * 40)
        print(f"  {bucket:20s}  {w:6.1%}  {bar}")

    print(f"\n── Concentration ──")
    print(f"  Top-1 holding:  {report['top1_pct']:.1%}")
    print(f"  Top-5 holdings: {report['top5_pct']:.1%}")
    print(f"  Tech exposure:  {report['tech_pct']:.1%}")

    p = report["posture"]
    print(f"\n── Score: {p['score']} / 100  →  {p['label']} ──")
    for line in p["justification"].split("\n"):
        print(f"  {line}")
    print()


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from ingest import ingest_all, DEFAULT_PATHS

    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    paths = {k: str(base / v) for k, v in DEFAULT_PATHS.items()}
    ingested = ingest_all(paths)

    report = risk_report(ingested["accounts"])
    _print_report(report)

    comps = score_components(ingested["accounts"])
    print("── Score Components ──")
    for k, v in comps["inputs"].items():
        print(f"  {k:20s} = {v:.4f}")
    print()
    for k, v in comps["components"].items():
        print(f"  {k:30s} = {v:+.1f}")
    print(f"  {'TOTAL':30s} = {report['aggressiveness_score']}")
    print()
