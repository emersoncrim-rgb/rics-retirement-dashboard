from typing import List, Dict, Tuple, Any

def apply_trades_to_snapshot(snap_rows: List[Dict[str, Any]], new_trades: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    errors: List[str] = []
    if not isinstance(snap_rows, list):
        return [], ["Snapshot rows must be a list of dicts."]
    if not isinstance(new_trades, list):
        return snap_rows, ["New trades must be a list of dicts."]

    # Create a copy so we don't accidentally mutate original data if it fails halfway
    updated_snap = [dict(row) for row in snap_rows]

    for idx, t in enumerate(new_trades):
        if not isinstance(t, dict):
            errors.append(f"Trade #{idx+1} is not a dict.")
            continue

        action = str(t.get("action", "")).strip().lower()
        ticker = str(t.get("ticker", "")).strip().upper()
        account_id = str(t.get("account_id", "")).strip()
        account_type = str(t.get("account_type", "taxable")).strip() # fallback if app.py forgets it
        
        # Dynamically catch "trade_value" (from engine) or "total_amount" (from app.py form)
        trade_val_raw = t.get("trade_value", t.get("total_amount", 0))
        shares_raw = t.get("shares", 0)
        
        try:
            trade_value = float(trade_val_raw) if trade_val_raw else 0.0
            shares = float(shares_raw) if shares_raw else 0.0
        except ValueError:
            errors.append(f"Trade #{idx+1} for {ticker} has invalid numeric values.")
            continue

        if action not in {"buy", "sell"}:
            errors.append(f"Trade #{idx+1} has invalid action '{action}'. Expected 'buy' or 'sell'.")
            continue

        matched = False
        for row in updated_snap:
            if (str(row.get("ticker", "")).strip().upper() == ticker and 
                str(row.get("account_id", "")).strip() == account_id):
                matched = True
                current_mv = float(row.get("market_value", 0) or 0)
                
                if action == "buy":
                    row["market_value"] = current_mv + trade_value
                elif action == "sell":
                    row["market_value"] = max(0.0, current_mv - trade_value)
                break

        if not matched and action == "buy":
            # Establish brand new position in the account
            new_row = {
                "account_id": account_id,
                "account_type": account_type,
                "account_label": account_id,
                "ticker": ticker,
                "asset_class": t.get("asset_class", "unknown"),
                "shares": shares,
                "price": t.get("price", trade_value / shares if shares > 0 else 0),
                "market_value": trade_value,
                "unrealized_gain": 0
            }
            updated_snap.append(new_row)
        elif not matched and action == "sell":
            errors.append(f"Trade #{idx+1}: Cannot sell {ticker} because it isn't in account {account_id}.")

    # Prune positions you've fully sold out of
    updated_snap = [row for row in updated_snap if float(row.get("market_value", 0) or 0) > 0.01]

    return updated_snap, errors
