"""
test_withdrawals.py — Unit tests for withdrawals.py

Run:  python -m pytest tests/test_withdrawals.py -v
  or: python tests/test_withdrawals.py
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from withdrawals import (
    AccountState,
    TaxableLot,
    SourcingAction,
    withdraw_sequence,
    select_taxable_lots,
    apply_withdrawal_actions,
    build_accounts_state,
    build_taxable_lots,
    DEFAULT_SEQUENCE,
)
from ingest import ingest_all, DEFAULT_PATHS

DATA_DIR = PROJECT_ROOT / "data"
PATHS = {k: str(DATA_DIR / v.split("/")[-1]) for k, v in DEFAULT_PATHS.items()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lot(ticker, mv, gain, term="long", top1=False, lot_id=None):
    cb_per = (mv - gain) / 100
    return TaxableLot(
        lot_id=lot_id or f"LOT_{ticker}",
        ticker=ticker, shares=100,
        cost_basis_per_share=cb_per,
        market_price=mv / 100,
        market_value=mv, unrealized_gain=gain,
        term=term, top1_pct=top1,
    )


def _make_state():
    """Build a representative account state for testing."""
    return {
        "TAXABLE_01": AccountState("TAXABLE_01", "taxable",
                                   cash_balance=228_700, invested_balance=801_300,
                                   total_balance=1_030_000),
        "RIRA_01": AccountState("RIRA_01", "trad_ira",
                                cash_balance=210_100, invested_balance=789_900,
                                total_balance=1_000_000,
                                rmd_remaining=37_736),
        "IIRA_01": AccountState("IIRA_01", "inherited_ira",
                                cash_balance=12_550, invested_balance=72_450,
                                total_balance=85_000,
                                inherited_ira_planned=10_000),
    }


def _make_lots():
    return [
        _make_lot("BND",  57_600,  -1_400, "long"),   # loss lot
        _make_lot("VGSH", 70_200,     700, "long"),   # tiny gain
        _make_lot("VTI", 324_000,  44_000, "long"),   # moderate gain
        _make_lot("VXUS", 87_000,   5_000, "long"),   # small gain
        _make_lot("AAPL",262_500, 241_000, "long", top1=True),  # flagged
    ]


# ══════════════════════════════════════════════════════════════════════════
# TaxableLot property tests
# ══════════════════════════════════════════════════════════════════════════

class TestTaxableLot(unittest.TestCase):

    def test_gain_per_dollar(self):
        lot = _make_lot("VTI", 100_000, 20_000)
        self.assertAlmostEqual(lot.gain_per_dollar, 0.20)

    def test_tax_cost_per_dollar_long(self):
        lot = _make_lot("VTI", 100_000, 20_000, "long")
        # 20% gain ratio × 15% LTCG rate = 3%
        self.assertAlmostEqual(lot.tax_cost_per_dollar, 0.03)

    def test_loss_lot_zero_tax_cost(self):
        lot = _make_lot("BND", 50_000, -2_000)
        self.assertEqual(lot.tax_cost_per_dollar, 0.0)

    def test_zero_value_lot(self):
        lot = TaxableLot("X", "X", 0, 0, 0, 0, 0, "long")
        self.assertEqual(lot.gain_per_dollar, 0.0)


# ══════════════════════════════════════════════════════════════════════════
# select_taxable_lots tests
# ══════════════════════════════════════════════════════════════════════════

class TestSelectTaxableLots(unittest.TestCase):

    def test_loss_lots_selected_first(self):
        """Loss lots should be sold before gain lots."""
        lots = _make_lots()
        selected = select_taxable_lots(lots, 50_000)
        self.assertEqual(selected[0]["ticker"], "BND")  # loss lot first
        self.assertLess(selected[0]["realized_gain"], 0)

    def test_avoids_top1_flagged(self):
        """AAPL (top1_pct=True) should not be selected when alternatives exist."""
        lots = _make_lots()
        selected = select_taxable_lots(lots, 100_000, avoid_top1=True)
        tickers = [s["ticker"] for s in selected]
        self.assertNotIn("AAPL", tickers)

    def test_uses_top1_when_forced(self):
        """If only top1 lot has enough, it should be used."""
        lots = [_make_lot("AAPL", 300_000, 241_000, top1=True)]
        selected = select_taxable_lots(lots, 100_000, avoid_top1=True)
        # Even with avoid_top1, AAPL is the only option
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["ticker"], "AAPL")

    def test_proceeds_match_target(self):
        lots = _make_lots()
        selected = select_taxable_lots(lots, 50_000)
        total = sum(s["proceeds"] for s in selected)
        self.assertAlmostEqual(total, 50_000, delta=1)

    def test_carryforward_reduces_tax(self):
        """$3k carryforward should offset gains in selected lots."""
        lots = [_make_lot("VTI", 100_000, 20_000)]
        with_cf = select_taxable_lots(lots, 100_000, cap_loss_carryforward=3_000)
        without_cf = select_taxable_lots(lots, 100_000, cap_loss_carryforward=0)
        self.assertLess(with_cf[0]["tax_cost"], without_cf[0]["tax_cost"])
        self.assertEqual(with_cf[0]["carryforward_used"], 3_000)

    def test_partial_lot_sale(self):
        """Should sell partial lots when target < lot value."""
        lots = [_make_lot("VTI", 200_000, 40_000)]
        selected = select_taxable_lots(lots, 50_000)
        self.assertEqual(len(selected), 1)
        self.assertAlmostEqual(selected[0]["proceeds"], 50_000, delta=1)
        self.assertLess(selected[0]["shares_sold"], 100)

    def test_zero_target(self):
        selected = select_taxable_lots(_make_lots(), 0)
        self.assertEqual(selected, [])

    def test_gain_sorted_by_tax_efficiency(self):
        """After loss lots, gain lots should be sorted by tax_cost_per_dollar."""
        lots = _make_lots()
        selected = select_taxable_lots(lots, 200_000)
        gain_lots = [s for s in selected if s["realized_gain"] >= 0]
        for i in range(1, len(gain_lots)):
            # Tax cost per dollar should be non-decreasing
            tc_prev = gain_lots[i-1]["tax_cost"] / max(gain_lots[i-1]["proceeds"], 1)
            tc_curr = gain_lots[i]["tax_cost"] / max(gain_lots[i]["proceeds"], 1)
            self.assertLessEqual(tc_prev, tc_curr + 0.001)


# ══════════════════════════════════════════════════════════════════════════
# withdraw_sequence tests
# ══════════════════════════════════════════════════════════════════════════

class TestWithdrawSequence(unittest.TestCase):

    def test_small_amount_from_cash(self):
        """$5k needed → should come from cash only."""
        state = _make_state()
        lots = _make_lots()
        actions = withdraw_sequence(5_000, state, lots)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].source_type, "cash")
        self.assertAlmostEqual(actions[0].amount, 5_000, delta=1)

    def test_15k_uses_inherited_then_lots(self):
        """
        $15k needed, inherited IRA planned $10k:
        → cash may cover some, inherited covers ~$10k, taxable lots cover rest.
        """
        state = _make_state()
        lots = _make_lots()
        # Zero out cash to force the sequence into inherited + taxable
        state["TAXABLE_01"].cash_balance = 0
        state["TAXABLE_01"].total_balance = state["TAXABLE_01"].invested_balance
        state["RIRA_01"].cash_balance = 0
        state["RIRA_01"].total_balance = state["RIRA_01"].invested_balance
        state["IIRA_01"].cash_balance = 0
        state["IIRA_01"].total_balance = state["IIRA_01"].invested_balance

        actions = withdraw_sequence(15_000, state, lots)
        types = [a.source_type for a in actions]

        # Inherited IRA should appear before taxable lots
        self.assertIn("inherited_ira", types)
        iira_idx = types.index("inherited_ira")

        # Inherited should provide $10k (planned amount)
        iira_action = actions[iira_idx]
        self.assertAlmostEqual(iira_action.amount, 10_000, delta=1)

        # Rest should come from taxable lots
        if "taxable_lot" in types:
            lot_action = actions[types.index("taxable_lot")]
            self.assertAlmostEqual(lot_action.amount, 5_000, delta=1)

        # Total should equal required
        total = sum(a.amount for a in actions)
        self.assertAlmostEqual(total, 15_000, delta=1)

    def test_large_amount_hits_ira(self):
        """$1.5M+ should eventually source from traditional IRA after exhausting
        cash ($451k), inherited ($10k), and taxable lots ($801k)."""
        state = _make_state()
        lots = _make_lots()
        actions = withdraw_sequence(1_500_000, state, lots)
        types = [a.source_type for a in actions]
        self.assertIn("trad_ira", types)

    def test_total_sourced_matches_required(self):
        state = _make_state()
        lots = _make_lots()
        for amount in [5_000, 15_000, 50_000, 200_000]:
            actions = withdraw_sequence(amount, state, lots)
            total = sum(a.amount for a in actions if a.source_type != "shortfall")
            self.assertAlmostEqual(total, amount, delta=1,
                                   msg=f"Failed for ${amount:,}")

    def test_shortfall_flagged(self):
        """If portfolios can't cover, a shortfall action should appear."""
        state = {"SMALL": AccountState("SMALL", "taxable", 1_000, 0, 1_000)}
        actions = withdraw_sequence(50_000, state, [])
        types = [a.source_type for a in actions]
        self.assertIn("shortfall", types)

    def test_sequence_order_respected(self):
        """Cash should be used before inherited IRA, before taxable, before IRA."""
        state = _make_state()
        lots = _make_lots()
        # Need enough to touch all layers
        actions = withdraw_sequence(300_000, state, lots)
        types = [a.source_type for a in actions if a.source_type != "shortfall"]

        # Verify ordering
        seen = set()
        priority = {"cash": 0, "inherited_ira": 1, "taxable_lot": 2, "trad_ira": 3}
        last_priority = -1
        for t in types:
            p = priority.get(t, 99)
            if t not in seen:
                self.assertGreaterEqual(p, last_priority,
                                        f"{t} appeared after higher-priority source")
                last_priority = p
                seen.add(t)

    def test_custom_sequence(self):
        """Custom preference should reorder sources."""
        state = _make_state()
        lots = _make_lots()
        prefs = {"sequence": ["trad_ira", "cash", "taxable"]}
        actions = withdraw_sequence(50_000, state, lots, preferences=prefs)
        # First action should be from IRA (custom order)
        self.assertEqual(actions[0].source_type, "trad_ira")


# ══════════════════════════════════════════════════════════════════════════
# apply_withdrawal_actions tests
# ══════════════════════════════════════════════════════════════════════════

class TestApplyWithdrawalActions(unittest.TestCase):

    def test_cash_withdrawal_reduces_balance(self):
        state = _make_state()
        actions = [SourcingAction("TAXABLE_01", "cash", 10_000)]
        updated, summary = apply_withdrawal_actions(state, actions)
        self.assertAlmostEqual(updated["TAXABLE_01"].cash_balance,
                               228_700 - 10_000, delta=1)
        self.assertEqual(summary["total_withdrawn"], 10_000)

    def test_ira_withdrawal_is_ordinary_income(self):
        state = _make_state()
        actions = [SourcingAction("RIRA_01", "trad_ira", 40_000,
                                  tax_character="ordinary")]
        updated, summary = apply_withdrawal_actions(state, actions)
        self.assertAlmostEqual(updated["RIRA_01"].total_balance,
                               1_000_000 - 40_000, delta=1)
        self.assertEqual(summary["ordinary_income"], 40_000)

    def test_lot_sale_tracks_gains(self):
        state = _make_state()
        lot_sales = [{"ticker": "VTI", "proceeds": 50_000,
                       "realized_gain": 8_000, "term": "long",
                       "cost_basis": 42_000, "shares_sold": 50,
                       "tax_cost": 1_200, "net_gain_after_cf": 8_000,
                       "carryforward_used": 0, "lot_id": "L1", "note": ""}]
        actions = [SourcingAction("LOT_VTI", "taxable_lot", 50_000,
                                  lot_sales=lot_sales, realized_gain=8_000)]
        updated, summary = apply_withdrawal_actions(state, actions)
        self.assertEqual(summary["realized_gains_lt"], 8_000)
        self.assertEqual(summary["total_withdrawn"], 50_000)

    def test_original_state_unchanged(self):
        """apply should deep-copy, not mutate original."""
        state = _make_state()
        original_bal = state["RIRA_01"].total_balance
        actions = [SourcingAction("RIRA_01", "trad_ira", 20_000)]
        updated, _ = apply_withdrawal_actions(state, actions)
        # Original should be unchanged
        self.assertEqual(state["RIRA_01"].total_balance, original_bal)
        # Updated should be reduced
        self.assertAlmostEqual(updated["RIRA_01"].total_balance,
                               original_bal - 20_000, delta=1)

    def test_shortfall_ignored(self):
        state = _make_state()
        actions = [SourcingAction("SHORTFALL", "shortfall", 99_999)]
        updated, summary = apply_withdrawal_actions(state, actions)
        self.assertEqual(summary["total_withdrawn"], 0)


# ══════════════════════════════════════════════════════════════════════════
# Integration with sample data
# ══════════════════════════════════════════════════════════════════════════

class TestWithSampleData(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ingested = ingest_all(PATHS)
        cls.acct_state = build_accounts_state(
            cls.ingested["accounts"],
            inherited_planned={"IIRA_01": 10_000},
        )
        cls.lots = build_taxable_lots(cls.ingested["accounts"])

    def test_build_accounts_state(self):
        self.assertEqual(len(self.acct_state), 3)
        self.assertAlmostEqual(self.acct_state["TAXABLE_01"].total_balance,
                               1_030_000, delta=100)

    def test_build_taxable_lots(self):
        """Should have 5 non-cash lots from taxable account."""
        self.assertEqual(len(self.lots), 5)
        tickers = {l.ticker for l in self.lots}
        self.assertIn("AAPL", tickers)
        self.assertIn("VTI", tickers)

    def test_aapl_flagged(self):
        aapl = [l for l in self.lots if l.ticker == "AAPL"][0]
        self.assertTrue(aapl.top1_pct)
        self.assertAlmostEqual(aapl.unrealized_gain, 241_000, delta=100)

    def test_full_sequence_50k(self):
        actions = withdraw_sequence(50_000, self.acct_state, self.lots,
                                    cap_loss_carryforward=3_000)
        total = sum(a.amount for a in actions if a.source_type != "shortfall")
        self.assertAlmostEqual(total, 50_000, delta=1)

        # Verify AAPL not sold (should be avoided)
        for a in actions:
            if a.lot_sales:
                for ls in a.lot_sales:
                    self.assertNotEqual(ls["ticker"], "AAPL")

    def test_apply_and_verify_balances(self):
        actions = withdraw_sequence(50_000, self.acct_state, self.lots)
        updated, summary = apply_withdrawal_actions(self.acct_state, actions)
        # Total portfolio should decrease by ~$50k
        original_total = sum(a.total_balance for a in self.acct_state.values())
        new_total = sum(a.total_balance for a in updated.values())
        self.assertAlmostEqual(original_total - new_total, 50_000, delta=100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
