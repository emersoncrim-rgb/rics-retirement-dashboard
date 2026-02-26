"""
ingest.py — CSV/JSON ingestion, validation, and portfolio summary for RICS.

Public API:
    ingest_all(paths: dict) -> dict
    compute_today_summary(ingested: dict) -> dict
"""

from __future__ import annotations

import csv
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_csv(path: str | Path) -> list[dict]:
    """Read a CSV into a list of row-dicts, stripping whitespace from headers."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [h.strip() for h in reader.fieldnames]
        return list(reader)


def _read_json(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"JSON not found: {path}")
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Type coercion helpers
# ---------------------------------------------------------------------------

_ACCOUNTS_NUMERIC = {
    "shares": float, "price": float, "market_value": float,
    "cost_basis": float, "unrealized_gain": float,
    "qualified_div_yield": float, "annual_income_est": float,
}

_TRADES_NUMERIC = {
    "shares": float, "price": float, "total_amount": float,
    "cost_basis_per_share": float, "realized_gain": float,
}

_CASHFLOW_NUMERIC = {"amount": float}


def _coerce_row(row: dict, schema: dict) -> dict:
    """Return a new dict with values cast per schema; non-schema keys pass through."""
    out = dict(row)
    for col, typ in schema.items():
        if col in out and out[col] not in (None, ""):
            try:
                out[col] = typ(out[col])
            except (ValueError, TypeError):
                out[col] = None
    return out


def _coerce_bool(val: str) -> bool:
    return str(val).strip().lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Individual loaders
# ---------------------------------------------------------------------------

def load_accounts(csv_path: str | Path, price_overrides: dict[str, float] | None = None) -> list[dict]:
    """Load accounts_snapshot.csv → list of typed row dicts.

    If price_overrides is provided (mapping ticker -> live price), rows whose
    tickers are present will have price/market_value/unrealized_gain (and
    annual_income_est when possible) recomputed.
    """
    rows = _read_csv(csv_path)
    out: list[dict] = []
    for r in rows:
        r = _coerce_row(r, _ACCOUNTS_NUMERIC)
        r["top1_pct"] = _coerce_bool(r.get("top1_pct", "false"))

        if price_overrides:
            t = r.get("ticker")
            if t in price_overrides and price_overrides[t] is not None:
                live_price = float(price_overrides[t])
                r["price"] = live_price

                shares = float(r.get("shares") or 0.0)
                cost_basis = float(r.get("cost_basis") or 0.0)

                mv = shares * live_price
                r["market_value"] = mv
                r["unrealized_gain"] = mv - cost_basis

                qdy = r.get("qualified_div_yield")
                if qdy not in (None, ""):
                    try:
                        r["annual_income_est"] = mv * float(qdy)
                    except (ValueError, TypeError):
                        pass

        out.append(r)
    return out


def load_trade_log(csv_path: str | Path) -> list[dict]:
    """Load trade_log.csv → list of typed row dicts."""
    rows = _read_csv(csv_path)
    return [_coerce_row(r, _TRADES_NUMERIC) for r in rows]


def load_cashflow(csv_path: str | Path) -> list[dict]:
    """Load cashflow_plan.csv → list of typed row dicts."""
    rows = _read_csv(csv_path)
    out = []
    for r in rows:
        r = _coerce_row(r, _CASHFLOW_NUMERIC)
        r["year"] = int(r["year"])
        r["inflation_adj"] = _coerce_bool(r.get("inflation_adj", "false"))
        out.append(r)
    return out


def load_tax_profile(json_path: str | Path) -> dict:
    return _read_json(json_path)


def load_constraints(json_path: str | Path) -> dict:
    return _read_json(json_path)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_REQUIRED_ACCOUNT_COLS = {
    "snapshot_date", "account_id", "account_type", "ticker",
    "asset_class", "shares", "price", "market_value",
    "cost_basis", "unrealized_gain", "top1_pct",
}

_VALID_ACCOUNT_TYPES = {"taxable", "trad_ira", "inherited_ira", "roth_ira", "roth_401k"}
_VALID_ASSET_CLASSES = {"us_equity", "intl_equity", "us_bond", "intl_bond", "mmf", "reit", "cash", "other"}
_VALID_ACTIONS = {"buy", "sell", "withdraw", "dividend", "interest", "transfer"}


def validate_accounts(rows: list[dict]) -> list[str]:
    flags: list[str] = []
    if not rows:
        flags.append("ACCT_EMPTY: accounts snapshot is empty")
        return flags

    # Column check
    present = set(rows[0].keys())
    missing = _REQUIRED_ACCOUNT_COLS - present
    if missing:
        flags.append(f"ACCT_COLS_MISSING: {sorted(missing)}")

    # Type check
    for i, r in enumerate(rows):
        if r.get("account_type") not in _VALID_ACCOUNT_TYPES:
            flags.append(f"ACCT_TYPE_INVALID row {i}: {r.get('account_type')}")
        if r.get("asset_class") not in _VALID_ASSET_CLASSES:
            flags.append(f"ACCT_ASSET_CLASS_INVALID row {i}: {r.get('asset_class')}")

    # market_value ≈ shares × price
    for i, r in enumerate(rows):
        expected = r["shares"] * r["price"]
        if abs(r["market_value"] - expected) > 1.0:
            flags.append(f"ACCT_MV_MISMATCH row {i}: {r['ticker']} mv={r['market_value']} vs shares*price={expected}")

    # unrealized_gain ≈ market_value − cost_basis
    for i, r in enumerate(rows):
        expected_gain = r["market_value"] - r["cost_basis"]
        if abs(r["unrealized_gain"] - expected_gain) > 1.0:
            flags.append(f"ACCT_GAIN_MISMATCH row {i}: {r['ticker']} gain={r['unrealized_gain']} vs mv-cb={expected_gain}")

    return flags


def validate_trades(rows: list[dict]) -> list[str]:
    flags: list[str] = []
    for i, r in enumerate(rows):
        if r.get("action") not in _VALID_ACTIONS:
            flags.append(f"TRADE_ACTION_INVALID row {i}: {r.get('action')}")
        if r.get("action") == "sell" and r.get("total_amount") is not None:
            expected = r["shares"] * r["price"]
            if abs(r["total_amount"] - expected) > 1.0:
                flags.append(f"TRADE_AMOUNT_MISMATCH row {i}: total={r['total_amount']} vs shares*price={expected}")
    return flags


def validate_cashflow(rows: list[dict]) -> list[str]:
    flags: list[str] = []
    if not rows:
        flags.append("CF_EMPTY: cashflow plan is empty")
        return flags
    categories = {r["category"] for r in rows}
    if "income" not in categories:
        flags.append("CF_NO_INCOME: no income rows found")
    if "expense" not in categories:
        flags.append("CF_NO_EXPENSE: no expense rows found")
    # Check SS present
    ss = [r for r in rows if r.get("subcategory") == "social_security"]
    if not ss:
        flags.append("CF_NO_SS: no social_security income row found")
    return flags


def validate_tax_profile(tp: dict) -> list[str]:
    flags: list[str] = []
    required_keys = {"filing_status", "state", "ages", "agi_prior_year",
                     "standard_deduction_base", "effective_standard_deduction",
                     "federal_brackets_mfj_2025"}
    missing = required_keys - set(tp.keys())
    if missing:
        flags.append(f"TAX_KEYS_MISSING: {sorted(missing)}")
    # Validate standard deduction arithmetic
    if all(k in tp for k in ("standard_deduction_base", "standard_deduction_senior_bonus_each", "effective_standard_deduction")):
        expected = tp["standard_deduction_base"] + len(tp.get("ages", [])) * tp["standard_deduction_senior_bonus_each"]
        if tp["effective_standard_deduction"] != expected:
            flags.append(f"TAX_STD_DED_MISMATCH: effective={tp['effective_standard_deduction']} vs computed={expected}")
    return flags


def validate_constraints(c: dict) -> list[str]:
    flags: list[str] = []
    if "inherited_ira_deadline_year" not in c:
        flags.append("CONST_NO_INHERITED_DEADLINE")
    if "irmaa_guardrails" not in c:
        flags.append("CONST_NO_IRMAA_GUARDRAILS")
    mc = c.get("monte_carlo", {})
    if mc.get("num_simulations", 0) < 100:
        flags.append("CONST_MC_LOW_SIMS: num_simulations < 100")
    return flags


# ---------------------------------------------------------------------------
# Account-level totals helper
# ---------------------------------------------------------------------------

def _compute_totals(accounts: list[dict]) -> dict:
    """Aggregate market values by account_id and account_type."""
    by_id: dict[str, float] = {}
    by_type: dict[str, float] = {}
    for r in accounts:
        aid = r["account_id"]
        atype = r["account_type"]
        mv = r["market_value"]
        by_id[aid] = by_id.get(aid, 0) + mv
        by_type[atype] = by_type.get(atype, 0) + mv
    return {
        "by_account_id": by_id,
        "by_account_type": by_type,
        "total_portfolio": sum(by_id.values()),
    }


# ---------------------------------------------------------------------------
# ingest_all — main entry point
# ---------------------------------------------------------------------------

DEFAULT_PATHS = {
    "accounts": "data/accounts_snapshot.csv",
    "trades": "data/trade_log.csv",
    "cashflow": "data/cashflow_plan.csv",
    "tax_profile": "data/tax_profile.json",
    "constraints": "data/constraints.json",
}


def ingest_all(paths: dict | None = None, price_overrides: dict[str, float] | None = None) -> dict:
    """
    Load all data files, run validation, compute totals.

    Parameters
    ----------
    paths : dict mapping keys ('accounts','trades','cashflow','tax_profile','constraints')
            to file paths.  Missing keys fall back to DEFAULT_PATHS.

    Returns
    -------
    dict with keys: accounts, trades, cashflow, tax_profile, constraints, totals, flags
    """
    p = {**DEFAULT_PATHS, **(paths or {})}

    accounts = load_accounts(p["accounts"], price_overrides=price_overrides)
    trades = load_trade_log(p["trades"])
    cashflow = load_cashflow(p["cashflow"])
    tax_profile = load_tax_profile(p["tax_profile"])
    constraints = load_constraints(p["constraints"])

    # Validate
    flags: list[str] = []
    flags.extend(validate_accounts(accounts))
    flags.extend(validate_trades(trades))
    flags.extend(validate_cashflow(cashflow))
    flags.extend(validate_tax_profile(tax_profile))
    flags.extend(validate_constraints(constraints))

    totals = _compute_totals(accounts)

    return {
        "accounts": accounts,
        "trades": trades,
        "cashflow": cashflow,
        "tax_profile": tax_profile,
        "constraints": constraints,
        "totals": totals,
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# compute_today_summary
# ---------------------------------------------------------------------------

_EQUITY_CLASSES = {"us_equity", "intl_equity"}
_BOND_CLASSES = {"us_bond", "intl_bond"}
_CASH_CLASSES = {"mmf", "cash"}


def compute_today_summary(ingested: dict) -> dict:
    """
    Produce a human-readable snapshot of the portfolio as of today.

    Returns dict with:
      account_totals, total_portfolio,
      equity_pct, bond_pct, cash_pct,
      top1_holding, top5_holdings,
      income_summary, cash_reserve_months
    """
    accounts = ingested["accounts"]
    cashflow = ingested["cashflow"]
    total = ingested["totals"]["total_portfolio"]

    # ── Asset-class breakdown ──
    class_totals: dict[str, float] = {}
    for r in accounts:
        ac = r["asset_class"]
        class_totals[ac] = class_totals.get(ac, 0) + r["market_value"]

    equity_val = sum(class_totals.get(c, 0) for c in _EQUITY_CLASSES)
    bond_val = sum(class_totals.get(c, 0) for c in _BOND_CLASSES)
    cash_val = sum(class_totals.get(c, 0) for c in _CASH_CLASSES)

    # ── Concentration: top holdings across whole portfolio ──
    ticker_totals: dict[str, float] = {}
    for r in accounts:
        t = r["ticker"]
        ticker_totals[t] = ticker_totals.get(t, 0) + r["market_value"]
    sorted_tickers = sorted(ticker_totals.items(), key=lambda x: -x[1])

    top1_ticker, top1_val = sorted_tickers[0] if sorted_tickers else ("N/A", 0)
    top5 = sorted_tickers[:5]

    # ── Income aggregation ──
    ss_income = sum(r["amount"] for r in cashflow
                    if r["subcategory"] == "social_security")
    qual_div = sum(r["amount"] for r in cashflow
                   if r["subcategory"] == "qualified_dividends")
    ord_div = sum(r["amount"] for r in cashflow
                  if r["subcategory"] == "ordinary_dividends")
    total_portfolio_income = qual_div + ord_div  # from taxable account
    total_income = ss_income + total_portfolio_income

    # ── Baseline expense and cash reserve ──
    annual_expenses = sum(r["amount"] for r in cashflow
                          if r["category"] == "expense" and r["frequency"] == "annual")
    monthly_expense = annual_expenses / 12.0
    cash_reserve_months = cash_val / monthly_expense if monthly_expense > 0 else float("inf")

    # ── Account-level totals ──
    account_totals = ingested["totals"]["by_account_id"]

    return {
        "account_totals": account_totals,
        "total_portfolio": total,
        "asset_class_totals": class_totals,
        "equity_pct": equity_val / total if total else 0,
        "bond_pct": bond_val / total if total else 0,
        "cash_pct": cash_val / total if total else 0,
        "top1_holding": {"ticker": top1_ticker, "value": top1_val, "pct": top1_val / total if total else 0},
        "top5_holdings": [
            {"ticker": t, "value": v, "pct": v / total} for t, v in top5
        ],
        "income_summary": {
            "social_security": ss_income,
            "qualified_dividends": qual_div,
            "ordinary_dividends": ord_div,
            "total_portfolio_income": total_portfolio_income,
            "total_income": total_income,
        },
        "annual_expenses": annual_expenses,
        "monthly_expense": monthly_expense,
        "cash_val": cash_val,
        "cash_reserve_months": round(cash_reserve_months, 1),
    }


# ---------------------------------------------------------------------------
# CLI convenience
# ---------------------------------------------------------------------------

def print_summary(summary: dict) -> None:
    """Pretty-print the today summary to stdout."""
    print("\n" + "=" * 60)
    print("  RETIREMENT INCOME CONTROL SYSTEM — TODAY'S SNAPSHOT")
    print("=" * 60)

    print("\n── Account Totals ──")
    for aid, val in summary["account_totals"].items():
        print(f"  {aid:20s}  ${val:>14,.0f}")
    print(f"  {'TOTAL':20s}  ${summary['total_portfolio']:>14,.0f}")

    print("\n── Asset Allocation ──")
    print(f"  Equity:  {summary['equity_pct']:6.1%}   Bond: {summary['bond_pct']:6.1%}   Cash: {summary['cash_pct']:6.1%}")

    print("\n── Top 5 Holdings ──")
    for h in summary["top5_holdings"]:
        print(f"  {h['ticker']:8s}  ${h['value']:>12,.0f}  ({h['pct']:.1%})")

    inc = summary["income_summary"]
    print("\n── Annual Income ──")
    print(f"  Social Security:       ${inc['social_security']:>10,.0f}")
    print(f"  Qualified Dividends:   ${inc['qualified_dividends']:>10,.0f}")
    print(f"  Ordinary Dividends:    ${inc['ordinary_dividends']:>10,.0f}")
    print(f"  Total Income:          ${inc['total_income']:>10,.0f}")

    print(f"\n── Cash Reserve: {summary['cash_reserve_months']} months of expenses ──")
    print(f"   (${summary['cash_val']:,.0f} cash / ${summary['monthly_expense']:,.0f}/mo expenses)")
    print()


if __name__ == "__main__":
    import sys
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    paths = {k: str(base / v) for k, v in DEFAULT_PATHS.items()}
    result = ingest_all(paths)

    if result["flags"]:
        print("⚠ Validation flags:")
        for f in result["flags"]:
            print(f"  • {f}")
    else:
        print("✓ All validations passed.")

    summary = compute_today_summary(result)
    print_summary(summary)
