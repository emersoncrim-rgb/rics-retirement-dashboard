"""live_overlay.py — Overlay live prices onto ingested accounts"""

def apply_price_overrides(accounts, price_overrides):
    if not price_overrides:
        return accounts

    for row in accounts:
        ticker = row.get("ticker")
        if ticker in price_overrides:
            live_price = float(price_overrides[ticker])
            row["price"] = live_price

            shares = float(row.get("shares", 0))
            cost_basis = float(row.get("cost_basis", 0))

            market_value = shares * live_price
            row["market_value"] = market_value
            row["unrealized_gain"] = market_value - cost_basis

            qdy = row.get("qualified_div_yield")
            if qdy is not None:
                row["annual_income_est"] = market_value * float(qdy)

    return accounts
