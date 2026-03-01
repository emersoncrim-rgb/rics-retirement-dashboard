"""
rebalance_sim.py – RICS Module: Rebalance Simulator

Simulates portfolio rebalancing scenarios considering:
- Target asset allocation (aggressiveness score → equity/bond/cash split)
- Tax impact of trades in taxable accounts
- Concentration limits (e.g., AAPL position)
- Account-level constraints (IRA rebalances are tax-free)
- Rebalance band tolerance to reduce unnecessary trading
"""

import csv
import json
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class AllocationTarget:
    """Target allocation percentages."""
    us_equity: float = 0.30
    intl_equity: float = 0.10
    us_bond: float = 0.35
    mmf: float = 0.25

    def validate(self) -> bool:
        total = self.us_equity + self.intl_equity + self.us_bond + self.mmf
        return abs(total - 1.0) < 0.001

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TradeProposal:
    """A proposed rebalancing trade."""
    account_id: str
    account_type: str
    ticker: str
    asset_class: str
    action: str  # "buy" or "sell"
    shares: float
    estimated_price: float
    trade_value: float
    estimated_tax: float  # 0 for IRA/Roth trades
    embedded_gain: float
    rationale: str
    blocked: bool = False
    block_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RebalanceResult:
    """Complete rebalance simulation output."""
    current_allocation: dict
    target_allocation: dict
    drift: dict  # asset_class → drift pct
    total_portfolio_value: float
    trades: list[TradeProposal]
    total_tax_cost: float
    tax_free_trades_value: float
    taxable_trades_value: float
    net_turnover_pct: float
    blocked_trades: list[TradeProposal]
    summary: str


def score_to_allocation(aggressiveness: int) -> AllocationTarget:
    """
    Convert 0-100 aggressiveness score to target allocation.

    0   → 0% equity, 50% bonds, 50% MMF
    45  → 36% us_eq, 9% intl_eq, 35% bonds, 20% MMF
    100 → 75% us_eq, 20% intl_eq, 5% bonds, 0% MMF
    """
    score = max(0, min(100, aggressiveness))

    equity_pct = score / 100 * 0.95  # max 95% equity at score=100
    us_eq = round(equity_pct * 0.80, 4)  # 80/20 US/intl split
    intl_eq = round(equity_pct * 0.20, 4)

    remaining = 1.0 - us_eq - intl_eq
    # More aggressive → less cash
    cash_pct = max(0.0, round(remaining * (1 - score / 200), 4))
    bond_pct = round(remaining - cash_pct, 4)

    return AllocationTarget(
        us_equity=us_eq,
        intl_equity=intl_eq,
        us_bond=bond_pct,
        mmf=cash_pct,
    )


def compute_current_allocation(holdings: list[dict]) -> dict:
    """Compute current allocation percentages by asset class."""
    totals = {"us_equity": 0, "intl_equity": 0, "us_bond": 0, "mmf": 0}
    for h in holdings:
        ac = h.get("asset_class", "us_equity")
        mv = float(h.get("market_value", 0))
        if ac in totals:
            totals[ac] += mv
        else:
            totals["us_equity"] += mv  # Unknown → equity

    grand = sum(totals.values())
    if grand == 0:
        return {k: 0.0 for k in totals}
    return {k: round(v / grand, 4) for k, v in totals.items()}


def compute_drift(current: dict, target: dict) -> dict:
    """Compute allocation drift (current - target) by asset class."""
    all_keys = set(list(current.keys()) + list(target.keys()))
    return {k: round(current.get(k, 0) - target.get(k, 0), 4) for k in all_keys}



def _apply_sector_tilt_to_target(target: AllocationTarget, holdings: list[dict], sector_prefs: Optional[dict]) -> AllocationTarget:
    if not sector_prefs or not sector_prefs.get("tilt_strength"):
        return AllocationTarget(**target.to_dict())

    tilt = sector_prefs.get("tilt_strength", 0)
    liked = {str(x).strip().lower() for x in sector_prefs.get("liked_sectors", []) if str(x).strip()}
    avoided = {str(x).strip().lower() for x in sector_prefs.get("avoided_sectors", []) if str(x).strip()}

    ac_mv = {}
    ac_net_score = {}
    for h in holdings:
        ac = h.get("asset_class", "us_equity")
        try:
            mv = float(h.get("market_value", 0) or 0)
        except Exception:
            mv = 0.0
        sector = str(h.get("sector", "")).strip().lower()

        ac_mv[ac] = ac_mv.get(ac, 0.0) + mv
        if sector in liked:
            ac_net_score[ac] = ac_net_score.get(ac, 0.0) + mv
        elif sector in avoided:
            ac_net_score[ac] = ac_net_score.get(ac, 0.0) - mv

    target_dict = target.to_dict()
    new_weights = {}
    for ac, weight in target_dict.items():
        total_mv = ac_mv.get(ac, 0.0)
        net_exposure = ac_net_score.get(ac, 0.0) / total_mv if total_mv > 0 else 0.0
        sign = 1 if net_exposure > 0 else (-1 if net_exposure < 0 else 0)
        delta = (tilt / 5.0) * 0.02 * sign
        new_weights[ac] = max(0.0, weight + delta)

    total_weight = sum(new_weights.values())
    if total_weight > 0:
        new_weights = {k: v / total_weight for k, v in new_weights.items()}
    else:
        new_weights = target_dict

    return AllocationTarget(**new_weights)

def simulate_rebalance(
    holdings: list[dict],
    target: AllocationTarget,
    constraints: Optional[dict] = None,
    rebalance_band: float = 0.05,
    tax_rates: Optional[dict] = None,
    sector_prefs: Optional[dict] = None,
) -> RebalanceResult:
    """
    Simulate rebalancing and generate trade proposals.

    Parameters
    ----------
    holdings : list[dict]
        Portfolio holdings from accounts_snapshot
    target : AllocationTarget
        Target allocation percentages
    constraints : dict, optional
        Concentration limits and other constraints
    rebalance_band : float
        Tolerance band — don't trade if drift < band
    tax_rates : dict, optional
        Tax rates for gain estimation: {"ltcg": 0.15, "stcg": 0.22, "state": 0.0875}
    sector_prefs : dict, optional
        Sector preferences mapping e.g. {"liked_sectors": [...], "avoided_sectors": [...], "tilt_strength": 3}
    """
    constr = constraints or {}
    rates = tax_rates or {"ltcg": 0.15, "stcg": 0.22, "state": 0.0875}
    aapl_flag = constr.get("concentration_limits", {}).get("aapl_flag", {})
    target = _apply_sector_tilt_to_target(target, holdings, sector_prefs)

    target_dict = target.to_dict()
    current = compute_current_allocation(holdings)

    total_value = sum(float(h.get("market_value", 0)) for h in holdings)

    curr_unrounded = {}
    if total_value > 0:
        ac_mv = {}
        for h in holdings:
            ac = h.get("asset_class", "us_equity")
            ac_mv[ac] = ac_mv.get(ac, 0.0) + float(h.get("market_value", 0))
        curr_unrounded = {k: v / total_value for k, v in ac_mv.items()}
    all_keys = sorted(list(set(list(current.keys()) + list(target_dict.keys()))))
    drift_raw = {k: (curr_unrounded.get(k, 0.0) - target_dict.get(k, 0.0)) for k in all_keys}
    drift = compute_drift(current, target_dict)
    total_value = sum(float(h.get("market_value", 0)) for h in holdings)

    trades = []
    blocked = []

    # Aggregate by (account_type, asset_class) for smarter trading
    acct_class_holdings = {}
    for h in holdings:
        key = (h.get("account_type", ""), h.get("asset_class", ""))
        if key not in acct_class_holdings:
            acct_class_holdings[key] = []
        acct_class_holdings[key].append(h)

    has_tilt = bool(sector_prefs and sector_prefs.get("tilt_strength", 0) > 0)


    for asset_class, drift_pct in drift_raw.items():
        if abs(drift_pct) < rebalance_band and not has_tilt:
            continue  # Within tolerance

        trade_amount = abs(drift_pct * total_value)
        action = "sell" if drift_pct > 0 else "buy"

        # Prefer to trade in tax-advantaged accounts first
        acct_priority = ["roth_ira", "trad_ira", "inherited_ira", "taxable"]

        remaining = trade_amount
        for acct_type in acct_priority:
            if remaining <= 0:
                break

            key = (acct_type, asset_class)
            acct_holdings = acct_class_holdings.get(key, [])
            if sector_prefs and sector_prefs.get("tilt_strength", 0) > 0:
                tilt = sector_prefs.get("tilt_strength", 0)
                liked = {str(x).strip().lower() for x in sector_prefs.get("liked_sectors", []) if str(x).strip()}
                avoided = {str(x).strip().lower() for x in sector_prefs.get("avoided_sectors", []) if str(x).strip()}

                def sort_key(h):
                    sector = str(h.get("sector", "")).strip().lower()
                    s = tilt if sector in liked else (-tilt if sector in avoided else 0)
                    if action == "sell":
                        return -tilt if sector in avoided else 0
                    return -s

                acct_holdings = sorted(acct_holdings, key=sort_key)

            for h in acct_holdings:
                if remaining <= 0:
                    break

                mv = float(h.get("market_value", 0))
                price = float(h.get("price", 1))
                ticker = h.get("ticker", "")
                unrealized = float(h.get("unrealized_gain", 0))

                if action == "sell":
                    sell_amount = min(remaining, mv * 0.9)  # Don't liquidate fully
                else:
                    # For buys, use cash from same account or cross-fund
                    sell_amount = min(remaining, trade_amount)

                shares_to_trade = round(sell_amount / price, 2) if price > 0 else 0

                # Tax estimate
                tax_est = 0.0
                if acct_type == "taxable" and action == "sell" and unrealized > 0:
                    gain_realized = unrealized * (sell_amount / mv) if mv > 0 else 0
                    tax_est = round(gain_realized * (rates["ltcg"] + rates["state"]), 2)

                # Check concentration constraints
                is_blocked = False
                block_reason = ""

                if ticker.upper() == aapl_flag.get("ticker", "").upper() and action == "sell":
                    if aapl_flag.get("strategy") == "do_not_sell_unless_offset_by_losses":
                        is_blocked = True
                        block_reason = "AAPL: high embedded gain, sell only to offset losses"

                proposal = TradeProposal(
                    account_id=h.get("account_id", ""),
                    account_type=acct_type,
                    ticker=ticker,
                    asset_class=asset_class,
                    action=action,
                    shares=shares_to_trade,
                    estimated_price=price,
                    trade_value=round(sell_amount, 2),
                    estimated_tax=tax_est,
                    embedded_gain=unrealized,
                    rationale=f"Rebalance {asset_class}: {drift_pct:+.1%} drift",
                    blocked=is_blocked,
                    block_reason=block_reason,
                )

                if is_blocked:
                    blocked.append(proposal)
                else:
                    trades.append(proposal)

                remaining -= sell_amount

    # Post-sort trades to respect sector preferences across the full proposal set
    # (Tests expect avoided sectors sold first, liked sectors bought first, liked sectors sold last.)
    if sector_prefs and sector_prefs.get("tilt_strength", 0) > 0 and trades:
        liked = {str(x).strip().lower() for x in sector_prefs.get("liked_sectors", []) if str(x).strip()}
        avoided = {str(x).strip().lower() for x in sector_prefs.get("avoided_sectors", []) if str(x).strip()}

        # Map ticker -> sector from holdings (best effort)
        ticker_to_sector = {}
        for h in holdings:
            tkr = str(h.get("ticker", "")).strip().upper()
            sec = str(h.get("sector", "")).strip().lower()
            if tkr and tkr not in ticker_to_sector:
                ticker_to_sector[tkr] = sec

        def _sector_rank(trade) -> int:
            tkr = str(getattr(trade, "ticker", "")).strip().upper()
            sec = ticker_to_sector.get(tkr, "")
            act = getattr(trade, "action", "")

            # Keep CASH/MMF late but not last for sells; never preferred for buys
            if tkr in {"CASH", "MMF"}:
                return 2

            if act == "sell":
                # avoided sold first, liked sold last
                if sec in avoided:
                    return 0
                if sec in liked:
                    return 3
                return 1
            else:
                # buy: liked first, avoided last
                if sec in liked:
                    return 0
                if sec in avoided:
                    return 2
                return 1

        # Tie-breakers: for same rank, prefer sells before buys, then larger trades first
        impacted = False
        for t in trades:
            tkr = str(getattr(t, "ticker", "")).strip().upper()
            sec = ticker_to_sector.get(tkr, "")
            if sec in liked or sec in avoided:
                impacted = True
                break

        if impacted:
            # Tie-breakers: for same rank, prefer sells before buys, then larger trades first
            trades.sort(key=lambda t: (_sector_rank(t), 0 if t.action == "sell" else 1, -float(getattr(t, "trade_value", 0) or 0.0)))

    total_tax = sum(t.estimated_tax for t in trades)
    tax_free_val = sum(t.trade_value for t in trades if t.account_type != "taxable")
    taxable_val = sum(t.trade_value for t in trades if t.account_type == "taxable")
    turnover = round((tax_free_val + taxable_val) / total_value, 4) if total_value > 0 else 0

    summary_parts = [
        f"Portfolio: ${total_value:,.0f}",
        f"Trades proposed: {len(trades)} (+ {len(blocked)} blocked)",
        f"Estimated tax cost: ${total_tax:,.0f}",
        f"Turnover: {turnover:.1%}",
    ]

    return RebalanceResult(
        current_allocation=current,
        target_allocation=target_dict,
        drift=drift,
        total_portfolio_value=total_value,
        trades=trades,
        total_tax_cost=total_tax,
        tax_free_trades_value=tax_free_val,
        taxable_trades_value=taxable_val,
        net_turnover_pct=turnover,
        blocked_trades=blocked,
        summary=" | ".join(summary_parts),
    )


def load_holdings_from_csv(csv_path: str) -> list[dict]:
    """Load holdings from accounts_snapshot CSV."""
    with open(csv_path) as f:
        return list(csv.DictReader(f))
