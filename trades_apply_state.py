def compute_new_trades(all_trades: list, applied_count: int) -> tuple[list, int]:
    """
    Returns a slice of trades that haven't been applied yet and the
    new total count. Clamps applied_count to valid range [0, len(all_trades)].
    """
    total = len(all_trades)
    # Clamp count to ensure it doesn't exceed current list size (e.g. if log was cleared)
    safe_count = max(0, min(applied_count, total))

    new_trades = all_trades[safe_count:]
    return new_trades, total
