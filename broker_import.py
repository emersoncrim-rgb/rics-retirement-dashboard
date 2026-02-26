"""
broker_import.py – RICS Module: Broker CSV/OFX Import & Normalization

Reads exported holdings from common brokerage formats (Fidelity, Schwab, Vanguard)
and normalizes them into the RICS accounts_snapshot schema.
"""

import csv
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Optional


# ── Asset-class inference rules ───────────────────────────────────────────────
ASSET_CLASS_MAP = {
    # Money-market / cash
    "VMFXX": "mmf", "SPAXX": "mmf", "FDRXX": "mmf", "SWVXX": "mmf",
    "VMMXX": "mmf", "SPRXX": "mmf", "CASH": "mmf",
    # US bond ETFs / funds
    "BND": "us_bond", "AGG": "us_bond", "VBTLX": "us_bond", "VGSH": "us_bond",
    "BSV": "us_bond", "BIV": "us_bond", "BLV": "us_bond", "TLT": "us_bond",
    "SHY": "us_bond", "IEF": "us_bond", "SCHZ": "us_bond", "FBND": "us_bond",
    "VTIP": "us_bond", "TIP": "us_bond", "VGIT": "us_bond",
    # International equity
    "VXUS": "intl_equity", "IXUS": "intl_equity", "VEA": "intl_equity",
    "VWO": "intl_equity", "IEFA": "intl_equity", "EEM": "intl_equity",
    "VTIAX": "intl_equity", "SWISX": "intl_equity", "FTIHX": "intl_equity",
    # US equity (broad)
    "VTI": "us_equity", "VOO": "us_equity", "SPY": "us_equity",
    "VTSAX": "us_equity", "SWTSX": "us_equity", "FSKAX": "us_equity",
    "QQQ": "us_equity", "IWM": "us_equity", "VIG": "us_equity",
    "SCHD": "us_equity", "VYM": "us_equity", "DGRO": "us_equity",
}

# Patterns for inferring account types from account names/labels
ACCOUNT_TYPE_PATTERNS = [
    (re.compile(r"roth", re.I), "roth_ira"),
    (re.compile(r"inherit", re.I), "inherited_ira"),
    (re.compile(r"trad|rollover|ira(?!.*roth)", re.I), "trad_ira"),
    (re.compile(r"401|403|457", re.I), "employer_plan"),
    (re.compile(r"brok|taxable|joint|individual|trust", re.I), "taxable"),
]

BROKER_COLUMN_MAPS = {
    "fidelity": {
        "ticker_cols": ["Symbol"],
        "shares_cols": ["Quantity"],
        "price_cols": ["Last Price"],
        "value_cols": ["Current Value"],
        "cost_cols": ["Cost Basis Total"],
        "account_cols": ["Account Name/Number"],
    },
    "schwab": {
        "ticker_cols": ["Symbol"],
        "shares_cols": ["Quantity"],
        "price_cols": ["Price"],
        "value_cols": ["Market Value"],
        "cost_cols": ["Cost Basis"],
        "account_cols": ["Account"],
    },
    "vanguard": {
        "ticker_cols": ["Symbol", "Ticker"],
        "shares_cols": ["Shares", "Quantity"],
        "price_cols": ["Share Price", "Price"],
        "value_cols": ["Total Value", "Market Value"],
        "cost_cols": ["Cost Basis"],
        "account_cols": ["Account Number", "Account Name"],
    },
    "generic": {
        "ticker_cols": ["ticker", "symbol", "Symbol", "Ticker"],
        "shares_cols": ["shares", "quantity", "Shares", "Quantity"],
        "price_cols": ["price", "Price", "Last Price", "Share Price"],
        "value_cols": ["market_value", "value", "Market Value", "Current Value", "Total Value"],
        "cost_cols": ["cost_basis", "Cost Basis", "Cost Basis Total"],
        "account_cols": ["account_id", "account", "Account", "Account Name"],
    },
}


@dataclass
class HoldingRow:
    """Normalized holding row matching RICS accounts_snapshot schema."""
    snapshot_date: str
    account_id: str
    account_type: str
    account_label: str
    ticker: str
    asset_class: str
    shares: float
    price: float
    market_value: float
    cost_basis: float
    unrealized_gain: float
    qualified_div_yield: float = 0.0
    annual_income_est: float = 0.0
    top1_pct: bool = False
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def clean_currency(val: str) -> float:
    """Parse currency strings like '$1,234.56' or '(1,234.56)' to float."""
    if not val or val.strip() in ("", "--", "n/a", "N/A"):
        return 0.0
    s = str(val).strip()
    negative = s.startswith("(") or s.startswith("-")
    s = re.sub(r"[$()\s,]", "", s)
    try:
        result = float(s)
        return -result if negative and result > 0 else result
    except ValueError:
        return 0.0


def infer_asset_class(ticker: str) -> str:
    """Map ticker to asset class. Defaults to us_equity for unknown stocks."""
    t = ticker.upper().strip()
    if t in ASSET_CLASS_MAP:
        return ASSET_CLASS_MAP[t]
    return "us_equity"


def infer_account_type(label: str) -> str:
    """Infer account type from account name/label string."""
    for pattern, acct_type in ACCOUNT_TYPE_PATTERNS:
        if pattern.search(label):
            return acct_type
    return "taxable"


def detect_broker(headers: list[str]) -> str:
    """Detect broker format from CSV headers."""
    header_set = set(h.strip() for h in headers)
    if "Last Price" in header_set and "Account Name/Number" in header_set:
        return "fidelity"
    if "Price" in header_set and "Account" in header_set and "Market Value" in header_set:
        return "schwab"
    if ("Share Price" in header_set or "Shares" in header_set) and "Account Number" in header_set:
        return "vanguard"
    return "generic"


def _find_col(row: dict, candidates: list[str]) -> Optional[str]:
    """Find first matching column name in a row dict."""
    for c in candidates:
        if c in row:
            return row[c]
    return None


def parse_broker_csv(
    csv_text: str,
    broker: str = "auto",
    snapshot_date: Optional[str] = None,
    account_type_override: Optional[str] = None,
    account_label_override: Optional[str] = None,
) -> list[HoldingRow]:
    """
    Parse broker CSV text into normalized HoldingRow list.

    Parameters
    ----------
    csv_text : str
        Raw CSV content
    broker : str
        One of 'fidelity', 'schwab', 'vanguard', 'generic', 'auto'
    snapshot_date : str, optional
        Override date (YYYY-MM-DD). Defaults to today.
    account_type_override : str, optional
        Force all rows to this account type
    account_label_override : str, optional
        Force all rows to this account label

    Returns
    -------
    list[HoldingRow]
    """
    snap_date = snapshot_date or date.today().isoformat()

    # Strip BOM and blank leading lines
    lines = csv_text.strip().lstrip("\ufeff").split("\n")
    lines = [l for l in lines if l.strip()]
    if not lines:
        return []

    reader = csv.DictReader(StringIO("\n".join(lines)))
    headers = reader.fieldnames or []

    if broker == "auto":
        broker = detect_broker(headers)

    col_map = BROKER_COLUMN_MAPS.get(broker, BROKER_COLUMN_MAPS["generic"])

    results: list[HoldingRow] = []
    for row in reader:
        ticker_raw = _find_col(row, col_map["ticker_cols"])
        if not ticker_raw or not ticker_raw.strip():
            continue

        ticker = ticker_raw.strip().upper()
        # Skip summary / header rows
        if ticker in ("TOTAL", "ACCOUNT TOTAL", "CASH & CASH INVESTMENTS", ""):
            continue

        shares = clean_currency(_find_col(row, col_map["shares_cols"]) or "0")
        price = clean_currency(_find_col(row, col_map["price_cols"]) or "0")
        value = clean_currency(_find_col(row, col_map["value_cols"]) or "0")
        cost = clean_currency(_find_col(row, col_map["cost_cols"]) or "0")

        # Calculate missing values
        if value == 0 and shares > 0 and price > 0:
            value = round(shares * price, 2)
        if price == 0 and shares > 0 and value > 0:
            price = round(value / shares, 2)
        if cost == 0:
            cost = value  # Assume cost = current value if unknown

        unrealized = round(value - cost, 2)
        acct_label_raw = _find_col(row, col_map["account_cols"]) or "Unknown"
        acct_label = account_label_override or acct_label_raw.strip()
        acct_type = account_type_override or infer_account_type(acct_label)
        acct_id = re.sub(r"[^A-Za-z0-9_]", "_", acct_label)[:20].upper()

        results.append(HoldingRow(
            snapshot_date=snap_date,
            account_id=acct_id,
            account_type=acct_type,
            account_label=acct_label,
            ticker=ticker,
            asset_class=infer_asset_class(ticker),
            shares=shares,
            price=price,
            market_value=value,
            cost_basis=cost,
            unrealized_gain=unrealized,
            notes=f"imported from {broker} CSV",
        ))

    return results


def holdings_to_csv(holdings: list[HoldingRow]) -> str:
    """Convert list of HoldingRow to CSV string matching accounts_snapshot schema."""
    if not holdings:
        return ""
    fieldnames = list(HoldingRow.__dataclass_fields__.keys())
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for h in holdings:
        writer.writerow(h.to_dict())
    return output.getvalue()


def load_accounts_snapshot(csv_path: str) -> list[HoldingRow]:
    """Load an existing RICS accounts_snapshot.csv into HoldingRow list."""
    rows = []
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(HoldingRow(
                snapshot_date=row.get("snapshot_date", ""),
                account_id=row.get("account_id", ""),
                account_type=row.get("account_type", ""),
                account_label=row.get("account_label", ""),
                ticker=row.get("ticker", ""),
                asset_class=row.get("asset_class", ""),
                shares=float(row.get("shares", 0)),
                price=float(row.get("price", 0)),
                market_value=float(row.get("market_value", 0)),
                cost_basis=float(row.get("cost_basis", 0)),
                unrealized_gain=float(row.get("unrealized_gain", 0)),
                qualified_div_yield=float(row.get("qualified_div_yield", 0)),
                annual_income_est=float(row.get("annual_income_est", 0)),
                top1_pct=str(row.get("top1_pct", "false")).lower() == "true",
                notes=row.get("notes", ""),
            ))
    return rows


def merge_holdings(existing: list[HoldingRow], imported: list[HoldingRow]) -> list[HoldingRow]:
    """
    Merge imported holdings into existing, keyed by (account_id, ticker).
    Imported values overwrite existing for matching keys; new keys are appended.
    """
    merged = {}
    for h in existing:
        key = (h.account_id, h.ticker)
        merged[key] = h
    for h in imported:
        key = (h.account_id, h.ticker)
        merged[key] = h
    return list(merged.values())
