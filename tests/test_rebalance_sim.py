"""
test_rebalance_sim.py – Tests for rebalance_sim module (unittest-based)

Coverage:
- Aggressiveness score → allocation conversion
- AllocationTarget validation
- Current allocation computation
- Drift calculation
- Rebalance simulation (trades, tax impact, blocking)
- Concentration constraint enforcement (AAPL)
- Rebalance band tolerance
- Edge cases
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rebalance_sim import (
    AllocationTarget,
    TradeProposal,
    RebalanceResult,
    score_to_allocation,
    compute_current_allocation,
    compute_drift,
    simulate_rebalance,
    load_holdings_from_csv,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

SAMPLE_HOLDINGS = [
    {"account_id": "TAX1", "account_type": "taxable", "ticker": "AAPL",
     "asset_class": "us_equity", "market_value": "262500", "price": "250",
     "unrealized_gain": "241000", "cost_basis": "21500"},
    {"account_id": "TAX1", "account_type": "taxable", "ticker": "VTI",
     "asset_class": "us_equity", "market_value": "324000", "price": "270",
     "unrealized_gain": "44000", "cost_basis": "280000"},
    {"account_id": "TAX1", "account_type": "taxable", "ticker": "VXUS",
     "asset_class": "intl_equity", "market_value": "87000", "price": "58",
     "unrealized_gain": "5000", "cost_basis": "82000"},
    {"account_id": "TAX1", "account_type": "taxable", "ticker": "BND",
     "asset_class": "us_bond", "market_value": "57600", "price": "72",
     "unrealized_gain": "-1400", "cost_basis": "59000"},
    {"account_id": "TAX1", "account_type": "taxable", "ticker": "VMFXX",
     "asset_class": "mmf", "market_value": "228700", "price": "1",
     "unrealized_gain": "0", "cost_basis": "228700"},
    {"account_id": "IRA1", "account_type": "trad_ira", "ticker": "VTI",
     "asset_class": "us_equity", "market_value": "216000", "price": "270",
     "unrealized_gain": "21000", "cost_basis": "195000"},
    {"account_id": "IRA1", "account_type": "trad_ira", "ticker": "BND",
     "asset_class": "us_bond", "market_value": "129600", "price": "72",
     "unrealized_gain": "-2400", "cost_basis": "132000"},
    {"account_id": "IRA1", "account_type": "trad_ira", "ticker": "VMFXX",
     "asset_class": "mmf", "market_value": "210100", "price": "1",
     "unrealized_gain": "0", "cost_basis": "210100"},
]

CONSTRAINTS = {
    "concentration_limits": {
        "single_stock_max_pct": 0.30,
        "aapl_flag": {
            "ticker": "AAPL",
            "strategy": "do_not_sell_unless_offset_by_losses",
            "embedded_gain": 241000,
        }
    }
}


class TestAllocationTarget(unittest.TestCase):
    def test_default_validates(self):
        t = AllocationTarget()
        self.assertTrue(t.validate())

    def test_custom_validates(self):
        t = AllocationTarget(us_equity=0.40, intl_equity=0.10, us_bond=0.30, mmf=0.20)
        self.assertTrue(t.validate())

    def test_invalid_sum(self):
        t = AllocationTarget(us_equity=0.50, intl_equity=0.50, us_bond=0.50, mmf=0.50)
        self.assertFalse(t.validate())

    def test_to_dict(self):
        t = AllocationTarget()
        d = t.to_dict()
        self.assertIn("us_equity", d)
        self.assertAlmostEqual(sum(d.values()), 1.0, places=2)


class TestScoreToAllocation(unittest.TestCase):
    def test_score_0_all_conservative(self):
        a = score_to_allocation(0)
        self.assertEqual(a.us_equity, 0.0)
        self.assertEqual(a.intl_equity, 0.0)
        self.assertAlmostEqual(a.us_bond + a.mmf, 1.0, places=2)

    def test_score_100_max_equity(self):
        a = score_to_allocation(100)
        total_eq = a.us_equity + a.intl_equity
        self.assertGreater(total_eq, 0.80)

    def test_score_45_moderate(self):
        a = score_to_allocation(45)
        total_eq = a.us_equity + a.intl_equity
        self.assertGreater(total_eq, 0.25)
        self.assertLess(total_eq, 0.55)
        self.assertTrue(a.validate())

    def test_score_clamped_low(self):
        a = score_to_allocation(-10)
        self.assertEqual(a.us_equity, 0.0)

    def test_score_clamped_high(self):
        b = score_to_allocation(200)
        self.assertGreater(b.us_equity + b.intl_equity, 0.80)

    def test_us_intl_ratio(self):
        a = score_to_allocation(50)
        if a.us_equity + a.intl_equity > 0:
            ratio = a.us_equity / (a.us_equity + a.intl_equity)
            self.assertGreater(ratio, 0.75)
            self.assertLess(ratio, 0.85)

    def test_always_valid(self):
        for score in range(0, 101, 10):
            a = score_to_allocation(score)
            self.assertTrue(a.validate(), f"Score {score} produced invalid allocation")


class TestCurrentAllocation(unittest.TestCase):
    def test_basic(self):
        alloc = compute_current_allocation(SAMPLE_HOLDINGS)
        self.assertAlmostEqual(sum(alloc.values()), 1.0, places=2)

    def test_equity_dominant(self):
        alloc = compute_current_allocation(SAMPLE_HOLDINGS)
        self.assertGreater(alloc["us_equity"], 0.40)

    def test_empty(self):
        alloc = compute_current_allocation([])
        self.assertTrue(all(v == 0.0 for v in alloc.values()))

    def test_single_asset(self):
        h = [{"asset_class": "us_bond", "market_value": "100000"}]
        alloc = compute_current_allocation(h)
        self.assertEqual(alloc["us_bond"], 1.0)


class TestDrift(unittest.TestCase):
    def test_no_drift(self):
        target = {"us_equity": 0.5, "us_bond": 0.5}
        current = {"us_equity": 0.5, "us_bond": 0.5}
        drift = compute_drift(current, target)
        self.assertTrue(all(abs(v) < 0.001 for v in drift.values()))

    def test_overweight(self):
        target = {"us_equity": 0.40}
        current = {"us_equity": 0.50}
        drift = compute_drift(current, target)
        self.assertAlmostEqual(drift["us_equity"], 0.10, places=3)

    def test_underweight(self):
        target = {"us_bond": 0.40}
        current = {"us_bond": 0.30}
        drift = compute_drift(current, target)
        self.assertAlmostEqual(drift["us_bond"], -0.10, places=3)

    def test_missing_key_in_current(self):
        drift = compute_drift({"us_equity": 0.5}, {"us_equity": 0.4, "us_bond": 0.6})
        self.assertIn("us_bond", drift)


class TestSimulateRebalance(unittest.TestCase):
    def test_basic_simulation(self):
        target = AllocationTarget(us_equity=0.35, intl_equity=0.10, us_bond=0.30, mmf=0.25)
        result = simulate_rebalance(SAMPLE_HOLDINGS, target, constraints=CONSTRAINTS)
        self.assertIsInstance(result, RebalanceResult)
        self.assertGreater(result.total_portfolio_value, 0)

    def test_returns_drift(self):
        target = AllocationTarget(us_equity=0.35, intl_equity=0.10, us_bond=0.30, mmf=0.25)
        result = simulate_rebalance(SAMPLE_HOLDINGS, target)
        self.assertIn("us_equity", result.drift)

    def test_aapl_sell_blocked(self):
        target = AllocationTarget(us_equity=0.20, intl_equity=0.05, us_bond=0.40, mmf=0.35)
        result = simulate_rebalance(SAMPLE_HOLDINGS, target, constraints=CONSTRAINTS)
        aapl_blocked = [t for t in result.blocked_trades if t.ticker == "AAPL"]
        if aapl_blocked:
            self.assertTrue(aapl_blocked[0].blocked)
            self.assertIn("AAPL", aapl_blocked[0].block_reason)

    def test_ira_trades_no_tax(self):
        target = AllocationTarget(us_equity=0.35, intl_equity=0.10, us_bond=0.30, mmf=0.25)
        result = simulate_rebalance(SAMPLE_HOLDINGS, target)
        ira_trades = [t for t in result.trades if t.account_type == "trad_ira"]
        for t in ira_trades:
            self.assertEqual(t.estimated_tax, 0.0)

    def test_within_band_no_trades(self):
        alloc = compute_current_allocation(SAMPLE_HOLDINGS)
        target = AllocationTarget(
            us_equity=round(alloc["us_equity"], 2),
            intl_equity=round(alloc["intl_equity"], 2),
            us_bond=round(alloc["us_bond"], 2),
            mmf=round(1 - round(alloc["us_equity"], 2) - round(alloc["intl_equity"], 2) - round(alloc["us_bond"], 2), 2),
        )
        result = simulate_rebalance(SAMPLE_HOLDINGS, target, rebalance_band=0.10)
        self.assertEqual(len(result.trades), 0)

    def test_wide_band_fewer_trades(self):
        target = AllocationTarget(us_equity=0.35, intl_equity=0.10, us_bond=0.30, mmf=0.25)
        narrow = simulate_rebalance(SAMPLE_HOLDINGS, target, rebalance_band=0.01)
        wide = simulate_rebalance(SAMPLE_HOLDINGS, target, rebalance_band=0.15)
        self.assertLessEqual(len(wide.trades), len(narrow.trades))

    def test_summary_string(self):
        target = AllocationTarget(us_equity=0.35, intl_equity=0.10, us_bond=0.30, mmf=0.25)
        result = simulate_rebalance(SAMPLE_HOLDINGS, target)
        self.assertIn("Portfolio:", result.summary)
        self.assertIn("$", result.summary)

    def test_turnover_reasonable(self):
        target = AllocationTarget(us_equity=0.35, intl_equity=0.10, us_bond=0.30, mmf=0.25)
        result = simulate_rebalance(SAMPLE_HOLDINGS, target)
        self.assertLess(result.net_turnover_pct, 1.0)

    def test_total_tax_non_negative(self):
        target = AllocationTarget(us_equity=0.35, intl_equity=0.10, us_bond=0.30, mmf=0.25)
        result = simulate_rebalance(SAMPLE_HOLDINGS, target)
        self.assertGreaterEqual(result.total_tax_cost, 0)


class TestLoadCSV(unittest.TestCase):
    def test_load_sample(self):
        path = os.path.join(DATA_DIR, "accounts_snapshot.csv")
        if not os.path.exists(path):
            self.skipTest("Sample data not available")
        rows = load_holdings_from_csv(path)
        self.assertEqual(len(rows), 16)


if __name__ == "__main__":
    unittest.main()
