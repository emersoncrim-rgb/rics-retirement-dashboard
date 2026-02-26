"""
withdrawals.py — Tax-aware withdrawal sequencing and lot selection for RICS.

Public API:
    withdraw_sequence(required, accounts_state, prefs, taxable_lots) -> list[Action]
    select_taxable_lots(lots, target, carryforward, ltcg_rate, stcg_rate) -> list[LotSale]
    apply_withdrawal_actions(accounts_state, actions) -> (updated_state, realized_gains)
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AccountState:
    """Snapshot of a single account's balances."""
    account_id: str
    account_type: str           # taxable, trad_ira, inherited_ira, roth_ira
    cash_balance: float         # MMF / cash within the account
    invested_balance: float     # non-cash holdings
    total_balance: float        # cash + invested
    rmd_remaining: float = 0.0  # unfulfilled RMD for this year
    inherited_ira_planned: float = 0.0  # planned distribution this year

    @property
    def available(self) -> float:
        return max(self.total_balance, 0.0)


@dataclass
class TaxableLot:
    """A single tax lot in a taxable account."""
    lot_id: str
    ticker: str
    shares: float
    cost_basis_per_share: float
    market_price: float
    market_value: float
    unrealized_gain: float
    term: str                   # 'long' or 'short'
    qualified_div_yield: float = 0.0
    top1_pct: bool = False      # concentration flag — avoid selling
    account_id: str = ""

    @property
    def gain_per_dollar(self) -> float:
        """Gain realized per dollar of proceeds. Higher = more tax cost."""
        if self.market_value <= 0:
            return 0.0
        return self.unrealized_gain / self.market_value

    @property
    def tax_cost_per_dollar(self) -> float:
        """Estimated tax cost per dollar sold."""
        if self.market_value <= 0:
            return 0.0
        gain_ratio = self.gain_per_dollar
        rate = 0.15 if self.term == "long" else 0.32  # approximate marginal
        return max(gain_ratio * rate, 0.0)


@dataclass
class SourcingAction:
    """One withdrawal action from a specific source."""
    source_account_id: str
    source_type: str            # cash, inherited_ira, taxable_lot, trad_ira, roth_ira
    amount: float
    lot_sales: List[dict] = field(default_factory=list)
    realized_gain: float = 0.0
    tax_character: str = ""     # ordinary, ltcg, stcg, tax_free
    note: str = ""


# ---------------------------------------------------------------------------
# Build AccountState from ingested data
# ---------------------------------------------------------------------------

def build_accounts_state(
    accounts: list[dict],
    rmd_by_account: Optional[Dict[str, float]] = None,
    inherited_planned: Optional[Dict[str, float]] = None,
) -> Dict[str, AccountState]:
    """
    Build AccountState dict from ingested accounts rows.

    Groups holdings by account_id, separates cash (MMF) from invested.
    """
    rmd_by_account = rmd_by_account or {}
    inherited_planned = inherited_planned or {}

    grouped: Dict[str, dict] = {}
    for r in accounts:
        aid = r["account_id"]
        if aid not in grouped:
            grouped[aid] = {
                "account_type": r["account_type"],
                "cash": 0.0, "invested": 0.0,
            }
        if r.get("asset_class") in ("mmf", "cash"):
            grouped[aid]["cash"] += r["market_value"]
        else:
            grouped[aid]["invested"] += r["market_value"]

    result = {}
    for aid, g in grouped.items():
        total = g["cash"] + g["invested"]
        result[aid] = AccountState(
            account_id=aid,
            account_type=g["account_type"],
            cash_balance=g["cash"],
            invested_balance=g["invested"],
            total_balance=total,
            rmd_remaining=rmd_by_account.get(aid, 0.0),
            inherited_ira_planned=inherited_planned.get(aid, 0.0),
        )
    return result


def build_taxable_lots(accounts: list[dict]) -> list[TaxableLot]:
    """Extract taxable account holdings as TaxableLot objects."""
    lots = []
    for i, r in enumerate(accounts):
        if r["account_type"] != "taxable":
            continue
        if r.get("asset_class") in ("mmf", "cash"):
            continue  # cash is handled separately

        lots.append(TaxableLot(
            lot_id=f"LOT_{i:04d}",
            ticker=r["ticker"],
            shares=r["shares"],
            cost_basis_per_share=r["cost_basis"] / r["shares"] if r["shares"] > 0 else 0,
            market_price=r["price"],
            market_value=r["market_value"],
            unrealized_gain=r["unrealized_gain"],
            term="long",  # assume long-term for snapshot holdings
            qualified_div_yield=r.get("qualified_div_yield", 0),
            top1_pct=r.get("top1_pct", False),
            account_id=r["account_id"],
        ))
    return lots


# ---------------------------------------------------------------------------
# Taxable lot selector
# ---------------------------------------------------------------------------

def select_taxable_lots(
    lots: list[TaxableLot],
    target_amount: float,
    cap_loss_carryforward: float = 0.0,
    ltcg_rate: float = 0.15,
    stcg_rate: float = 0.24,
    avoid_top1: bool = True,
) -> list[dict]:
    """
    Greedy lot selection minimizing tax cost per dollar of proceeds.

    Priority order:
      1. Lots with losses (negative gain → tax benefit)
      2. Lots with smallest gain_per_dollar
      3. Avoid top1_pct flagged lots unless no alternatives

    Carryforward offsets gains: effective tax on early lots may be $0.

    Returns list of {lot_id, ticker, shares_sold, proceeds, cost_basis,
                     realized_gain, tax_cost, note}
    """
    if target_amount <= 0:
        return []

    # Partition: loss lots, non-flagged gain lots, flagged lots
    loss_lots = [l for l in lots if l.unrealized_gain < 0]
    gain_lots = [l for l in lots if l.unrealized_gain >= 0 and not (avoid_top1 and l.top1_pct)]
    flagged_lots = [l for l in lots if l.unrealized_gain >= 0 and avoid_top1 and l.top1_pct]

    # Sort each group by tax efficiency
    loss_lots.sort(key=lambda l: l.unrealized_gain)            # most negative first (biggest tax benefit)
    gain_lots.sort(key=lambda l: l.tax_cost_per_dollar)        # cheapest tax first
    # Flagged lots as last resort
    flagged_lots.sort(key=lambda l: l.tax_cost_per_dollar)

    ordered = loss_lots + gain_lots + flagged_lots

    remaining = target_amount
    remaining_cf = cap_loss_carryforward
    selected: list[dict] = []

    for lot in ordered:
        if remaining <= 0:
            break

        # How much to sell from this lot
        sell_amount = min(remaining, lot.market_value)
        sell_fraction = sell_amount / lot.market_value if lot.market_value > 0 else 0
        shares_sold = lot.shares * sell_fraction
        cost_basis = lot.cost_basis_per_share * shares_sold
        realized_gain = sell_amount - cost_basis

        # Net gain after carryforward offset
        net_gain = realized_gain
        cf_used = 0.0
        if net_gain > 0 and remaining_cf > 0:
            cf_used = min(remaining_cf, net_gain)
            net_gain -= cf_used
            remaining_cf -= cf_used

        # Tax cost
        if net_gain > 0:
            rate = ltcg_rate if lot.term == "long" else stcg_rate
            tax_cost = net_gain * rate
        elif net_gain < 0:
            # Tax benefit from harvested loss
            tax_cost = net_gain * ltcg_rate  # negative = benefit
        else:
            tax_cost = 0.0

        note_parts = []
        if lot.top1_pct:
            note_parts.append("⚠ high-concentration lot")
        if cf_used > 0:
            note_parts.append(f"carryforward offset ${cf_used:,.0f}")
        if realized_gain < 0:
            note_parts.append("tax-loss harvest")

        selected.append({
            "lot_id": lot.lot_id,
            "ticker": lot.ticker,
            "shares_sold": round(shares_sold, 4),
            "proceeds": round(sell_amount, 2),
            "cost_basis": round(cost_basis, 2),
            "realized_gain": round(realized_gain, 2),
            "net_gain_after_cf": round(max(net_gain, 0), 2),
            "carryforward_used": round(cf_used, 2),
            "tax_cost": round(tax_cost, 2),
            "term": lot.term,
            "note": "; ".join(note_parts) if note_parts else "",
        })

        remaining -= sell_amount

    return selected


# ---------------------------------------------------------------------------
# Main withdrawal sequencing
# ---------------------------------------------------------------------------

# Default priority order
DEFAULT_SEQUENCE = [
    "cash",           # MMF / sweep across all accounts
    "inherited_ira",  # mandatory distributions (10-year rule)
    "taxable",        # sell lots (tax-aware)
    "trad_ira",       # IRA distributions (ordinary income)
    "roth_ira",       # last resort (tax-free growth)
]


def withdraw_sequence(
    required_amount: float,
    accounts_state: Dict[str, AccountState],
    taxable_lots: list[TaxableLot],
    preferences: Optional[dict] = None,
    cap_loss_carryforward: float = 0.0,
) -> list[SourcingAction]:
    """
    Determine sourcing for a required withdrawal amount.

    Follows the priority: cash → inherited IRA → taxable lots → trad IRA → Roth.
    Within each tier, uses the most tax-efficient approach.

    Parameters
    ----------
    required_amount : total dollars needed
    accounts_state : {account_id: AccountState}
    taxable_lots : list of TaxableLot for taxable accounts
    preferences : optional overrides (sequence order, avoid_top1, etc.)
    cap_loss_carryforward : available carryforward for lot selection

    Returns
    -------
    List of SourcingAction describing where each dollar comes from.
    """
    prefs = preferences or {}
    sequence = prefs.get("sequence", DEFAULT_SEQUENCE)
    avoid_top1 = prefs.get("avoid_top1", True)

    actions: list[SourcingAction] = []
    remaining = required_amount

    for source_type in sequence:
        if remaining <= 0:
            break

        if source_type == "cash":
            # Pull cash from any account, preferring taxable first
            for aid, acct in sorted(accounts_state.items(),
                                     key=lambda x: _account_type_cash_priority(x[1].account_type)):
                if remaining <= 0:
                    break
                avail = acct.cash_balance
                if avail <= 0:
                    continue
                pull = min(remaining, avail)
                actions.append(SourcingAction(
                    source_account_id=aid,
                    source_type="cash",
                    amount=pull,
                    tax_character="tax_free" if acct.account_type == "roth_ira" else "depends",
                    note=f"Cash/MMF from {acct.account_type}",
                ))
                remaining -= pull

        elif source_type == "inherited_ira":
            for aid, acct in accounts_state.items():
                if remaining <= 0:
                    break
                if acct.account_type != "inherited_ira":
                    continue
                # Use planned distribution or available balance
                planned = acct.inherited_ira_planned
                avail = acct.available
                pull = min(remaining, max(planned, 0), avail)
                if pull <= 0:
                    continue
                actions.append(SourcingAction(
                    source_account_id=aid,
                    source_type="inherited_ira",
                    amount=pull,
                    tax_character="ordinary",
                    note=f"Inherited IRA distribution (10-yr rule)",
                ))
                remaining -= pull

        elif source_type == "taxable":
            if remaining <= 0:
                continue
            lot_sales = select_taxable_lots(
                taxable_lots, remaining,
                cap_loss_carryforward=cap_loss_carryforward,
                avoid_top1=avoid_top1,
            )
            if lot_sales:
                total_proceeds = sum(s["proceeds"] for s in lot_sales)
                total_gain = sum(s["realized_gain"] for s in lot_sales)
                total_tax = sum(s["tax_cost"] for s in lot_sales)
                actions.append(SourcingAction(
                    source_account_id=lot_sales[0].get("lot_id", "TAXABLE"),
                    source_type="taxable_lot",
                    amount=total_proceeds,
                    lot_sales=lot_sales,
                    realized_gain=total_gain,
                    tax_character="ltcg" if all(s["term"] == "long" for s in lot_sales) else "mixed",
                    note=f"{len(lot_sales)} lots sold, est tax ${total_tax:,.0f}",
                ))
                remaining -= total_proceeds

        elif source_type == "trad_ira":
            for aid, acct in accounts_state.items():
                if remaining <= 0:
                    break
                if acct.account_type != "trad_ira":
                    continue
                avail = acct.available
                pull = min(remaining, avail)
                if pull <= 0:
                    continue
                actions.append(SourcingAction(
                    source_account_id=aid,
                    source_type="trad_ira",
                    amount=pull,
                    tax_character="ordinary",
                    note=f"IRA distribution (ordinary income)",
                ))
                remaining -= pull

        elif source_type == "roth_ira":
            for aid, acct in accounts_state.items():
                if remaining <= 0:
                    break
                if acct.account_type != "roth_ira":
                    continue
                avail = acct.available
                pull = min(remaining, avail)
                if pull <= 0:
                    continue
                actions.append(SourcingAction(
                    source_account_id=aid,
                    source_type="roth_ira",
                    amount=pull,
                    tax_character="tax_free",
                    note="Roth distribution (tax-free)",
                ))
                remaining -= pull

    # If still short, note the shortfall
    if remaining > 1.0:
        actions.append(SourcingAction(
            source_account_id="SHORTFALL",
            source_type="shortfall",
            amount=remaining,
            tax_character="n/a",
            note=f"⚠ Shortfall: ${remaining:,.0f} unfunded",
        ))

    return actions


def _account_type_cash_priority(account_type: str) -> int:
    """Cash withdrawal priority: taxable first (no tax on basis), then IRA, then Roth last."""
    return {"taxable": 0, "inherited_ira": 1, "trad_ira": 2, "roth_ira": 3}.get(account_type, 2)


# ---------------------------------------------------------------------------
# Apply withdrawal actions
# ---------------------------------------------------------------------------

def apply_withdrawal_actions(
    accounts_state: Dict[str, AccountState],
    actions: list[SourcingAction],
) -> Tuple[Dict[str, AccountState], dict]:
    """
    Apply sourcing actions to account state, returning updated state + tax summary.

    Returns
    -------
    (updated_accounts_state, tax_summary)
    tax_summary: {total_withdrawn, realized_gains_lt, realized_gains_st,
                  ordinary_income, tax_free, lot_sales}
    """
    state = {k: copy.deepcopy(v) for k, v in accounts_state.items()}

    tax_summary = {
        "total_withdrawn": 0.0,
        "realized_gains_lt": 0.0,
        "realized_gains_st": 0.0,
        "ordinary_income": 0.0,
        "tax_free": 0.0,
        "lot_sales": [],
    }

    for action in actions:
        if action.source_type == "shortfall":
            continue

        amt = action.amount
        aid = action.source_account_id
        tax_summary["total_withdrawn"] += amt

        # Update balances
        if action.source_type == "cash":
            if aid in state:
                state[aid].cash_balance = max(state[aid].cash_balance - amt, 0)
                state[aid].total_balance = state[aid].cash_balance + state[aid].invested_balance

        elif action.source_type == "taxable_lot":
            # Reduce invested balance across affected accounts
            for lot in action.lot_sales:
                # Find the account for this lot
                for s in state.values():
                    if s.account_type == "taxable":
                        s.invested_balance = max(s.invested_balance - lot["proceeds"], 0)
                        s.total_balance = s.cash_balance + s.invested_balance
                        break
            tax_summary["lot_sales"].extend(action.lot_sales)
            for lot in action.lot_sales:
                if lot["term"] == "long":
                    tax_summary["realized_gains_lt"] += lot["realized_gain"]
                else:
                    tax_summary["realized_gains_st"] += lot["realized_gain"]

        elif action.source_type in ("inherited_ira", "trad_ira"):
            if aid in state:
                # Withdraw proportionally from cash and invested
                total = state[aid].total_balance
                if total > 0:
                    cash_frac = state[aid].cash_balance / total
                    state[aid].cash_balance -= amt * cash_frac
                    state[aid].invested_balance -= amt * (1 - cash_frac)
                    state[aid].cash_balance = max(state[aid].cash_balance, 0)
                    state[aid].invested_balance = max(state[aid].invested_balance, 0)
                    state[aid].total_balance = state[aid].cash_balance + state[aid].invested_balance
            tax_summary["ordinary_income"] += amt

        elif action.source_type == "roth_ira":
            if aid in state:
                total = state[aid].total_balance
                if total > 0:
                    cash_frac = state[aid].cash_balance / total
                    state[aid].cash_balance -= amt * cash_frac
                    state[aid].invested_balance -= amt * (1 - cash_frac)
                    state[aid].cash_balance = max(state[aid].cash_balance, 0)
                    state[aid].invested_balance = max(state[aid].invested_balance, 0)
                    state[aid].total_balance = state[aid].cash_balance + state[aid].invested_balance
            tax_summary["tax_free"] += amt

    # Round
    for k in ("total_withdrawn", "realized_gains_lt", "realized_gains_st",
              "ordinary_income", "tax_free"):
        tax_summary[k] = round(tax_summary[k], 2)

    return state, tax_summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_actions(actions: list[SourcingAction]) -> None:
    print(f"\n{'#':>3} {'Source':>16} {'Type':>16} {'Amount':>12} {'Tax Char':>10} Note")
    print("─" * 90)
    for i, a in enumerate(actions, 1):
        print(f"{i:>3} {a.source_account_id:>16} {a.source_type:>16}"
              f" ${a.amount:>11,.2f} {a.tax_character:>10} {a.note}")
        if a.lot_sales:
            for ls in a.lot_sales:
                print(f"{'':>3} {'':>16}   └─ {ls['ticker']:6s}"
                      f" {ls['shares_sold']:>8.2f} sh"
                      f"  proceeds ${ls['proceeds']:>10,.2f}"
                      f"  gain ${ls['realized_gain']:>9,.2f}"
                      f"  tax ${ls['tax_cost']:>8,.2f}"
                      f"  {ls['note']}")


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from ingest import ingest_all, DEFAULT_PATHS
    from rmd import compute_rmd_amount, load_rmd_divisors, generate_inherited_ira_schedule

    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    paths = {k: str(base / v) for k, v in DEFAULT_PATHS.items()}
    ingested = ingest_all(paths)

    # Build state
    divisors = load_rmd_divisors(str(base / "data" / "rmd_divisors.json"))
    rmd_info = compute_rmd_amount(2026, "1953-03-15", 1_000_000, divisors)
    iira_sched = generate_inherited_ira_schedule(85_000, 2033, 2025, 0.035, "even")

    acct_state = build_accounts_state(
        ingested["accounts"],
        rmd_by_account={"RIRA_01": rmd_info["rmd_amount"]},
        inherited_planned={"IIRA_01": iira_sched.get(2025, 0)},
    )
    lots = build_taxable_lots(ingested["accounts"])

    # Scenario 1: Need $15k (inherited planned covers ~$10k, rest from lots)
    print("\n" + "=" * 90)
    print("  SCENARIO 1: Need $15,000 (inherited IRA planned ~$10k)")
    print("=" * 90)
    actions = withdraw_sequence(15_000, acct_state, lots, cap_loss_carryforward=3_000)
    _print_actions(actions)

    # Scenario 2: Need $80,000 (larger — hits IRA)
    print("\n" + "=" * 90)
    print("  SCENARIO 2: Need $80,000 (larger withdrawal)")
    print("=" * 90)
    actions2 = withdraw_sequence(80_000, acct_state, lots, cap_loss_carryforward=3_000)
    _print_actions(actions2)

    # Apply and show summary
    updated, summary = apply_withdrawal_actions(acct_state, actions2)
    print(f"\n── Tax Summary ──")
    print(f"  Total withdrawn:    ${summary['total_withdrawn']:>12,.2f}")
    print(f"  Realized gains LT:  ${summary['realized_gains_lt']:>12,.2f}")
    print(f"  Realized gains ST:  ${summary['realized_gains_st']:>12,.2f}")
    print(f"  Ordinary income:    ${summary['ordinary_income']:>12,.2f}")
    print(f"  Tax-free:           ${summary['tax_free']:>12,.2f}")
    print()
