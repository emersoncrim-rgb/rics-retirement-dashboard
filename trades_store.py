import csv
from pathlib import Path
from typing import List, Dict

TRADE_LOG_PATH = Path("data/trade_log.csv")


def load_trades() -> List[Dict]:
    """Load trade history from CSV."""
    if not TRADE_LOG_PATH.exists():
        return []
    with TRADE_LOG_PATH.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def validate_trade(trade: Dict) -> bool:
    """Basic trade validation."""
    required_fields = {"account_id", "account_type", "ticker", "action", "trade_value"}
    return required_fields.issubset(set(trade.keys()))


def append_trade(trade: Dict) -> None:
    """Append a trade to the CSV log."""
    if not validate_trade(trade):
        raise ValueError("Invalid trade structure")

    file_exists = TRADE_LOG_PATH.exists()

    with TRADE_LOG_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=trade.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(trade)
