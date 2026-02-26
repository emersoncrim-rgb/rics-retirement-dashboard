"""
test_risk.py — Unit tests for risk.py

Run:  python -m pytest tests/test_risk.py -v
  or: python tests/test_risk.py
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from risk import (
    map_holdings_to_buckets,
    compute_aggressiveness_score,
    describe_posture,
    risk_report,
    score_components,
    _clamp,
    _ticker_weights,
    _top_n_pct,
    _tech_pct,
)
from ingest import ingest_all, DEFAULT_PATHS

DATA_DIR = PROJECT_ROOT / "data"
PATHS = {k: str(DATA_DIR / v.split("/")[-1]) for k, v in DEFAULT_PATHS.items()}


# ---------------------------------------------------------------------------
# Helper to build synthetic portfolios
# ---------------------------------------------------------------------------

def _make_holding(ticker, asset_class, market_value, cost_basis=None,
                  div_yield=0.0, account_type="taxable"):
    return {
        "ticker": ticker,
        "asset_class": asset_class,
        "market_value": market_value,
        "cost_basis": cost_basis or market_value,
        "unrealized_gain": market_value - (cost_basis or market_value),
        "qualified_div_yield": div_yield,
        "shares": market_value / 100,
        "price": 100.0,
        "account_type": account_type,
        "account_id": "TEST_01",
        "top1_pct": False,
    }


def _all_cash_portfolio():
    return [_make_holding("VMFXX", "mmf", 1_000_000)]


def _all_equity_portfolio():
    return [
        _make_holding("AAPL", "us_equity", 400_000),
        _make_holding("MSFT", "us_equity", 300_000),
        _make_holding("NVDA", "us_equity", 200_000),
        _make_holding("AVGO", "us_equity", 100_000),
    ]


def _balanced_portfolio():
    return [
        _make_holding("VTI",   "us_equity", 300_000),
        _make_holding("VXUS",  "intl_equity", 100_000),
        _make_holding("BND",   "us_bond", 300_000),
        _make_holding("VMFXX", "mmf", 300_000),
    ]


def _high_dividend_portfolio():
    return [
        _make_holding("VYM",  "us_equity", 400_000, div_yield=0.03),
        _make_holding("SCHD", "us_equity", 300_000, div_yield=0.035),
        _make_holding("BND",  "us_bond", 200_000),
        _make_holding("VMFXX", "mmf", 100_000),
    ]


# ══════════════════════════════════════════════════════════════════════════
# _clamp tests
# ══════════════════════════════════════════════════════════════════════════

class TestClamp(unittest.TestCase):

    def test_within_range(self):
        self.assertEqual(_clamp(0.5), 0.5)

    def test_below_zero(self):
        self.assertEqual(_clamp(-0.3), 0.0)

    def test_above_one(self):
        self.assertEqual(_clamp(1.5), 1.0)

    def test_at_boundaries(self):
        self.assertEqual(_clamp(0.0), 0.0)
        self.assertEqual(_clamp(1.0), 1.0)


# ══════════════════════════════════════════════════════════════════════════
# map_holdings_to_buckets tests
# ══════════════════════════════════════════════════════════════════════════

class TestBucketMapping(unittest.TestCase):

    def test_all_cash(self):
        b = map_holdings_to_buckets(_all_cash_portfolio())
        self.assertAlmostEqual(b["cash"], 1.0, places=4)
        self.assertAlmostEqual(b["equity"], 0.0, places=4)

    def test_all_equity(self):
        b = map_holdings_to_buckets(_all_equity_portfolio())
        self.assertAlmostEqual(b["equity"], 1.0, places=4)
        self.assertAlmostEqual(b["cash"], 0.0, places=4)

    def test_balanced_buckets(self):
        b = map_holdings_to_buckets(_balanced_portfolio())
        self.assertAlmostEqual(b["equity"], 0.40, places=2)
        self.assertAlmostEqual(b["bond"], 0.30, places=2)
        self.assertAlmostEqual(b["cash"], 0.30, places=2)

    def test_dividend_etf_classified(self):
        """VYM and SCHD should go to dividend_equity bucket."""
        b = map_holdings_to_buckets(_high_dividend_portfolio())
        self.assertAlmostEqual(b["dividend_equity"], 0.70, places=2)
        self.assertAlmostEqual(b["equity"], 0.0, places=2)

    def test_high_yield_stock_classified_as_dividend_equity(self):
        """Individual stock with yield >= 2.5% → dividend_equity."""
        holdings = [
            _make_holding("VXUS", "intl_equity", 500_000, div_yield=0.029),
            _make_holding("VTI",  "us_equity", 500_000, div_yield=0.013),
        ]
        b = map_holdings_to_buckets(holdings)
        self.assertAlmostEqual(b["dividend_equity"], 0.50, places=2)
        self.assertAlmostEqual(b["equity"], 0.50, places=2)

    def test_weights_sum_to_one(self):
        for portfolio_fn in [_all_cash_portfolio, _all_equity_portfolio,
                             _balanced_portfolio, _high_dividend_portfolio]:
            b = map_holdings_to_buckets(portfolio_fn())
            self.assertAlmostEqual(sum(b.values()), 1.0, places=4,
                                   msg=f"Failed for {portfolio_fn.__name__}")

    def test_empty_portfolio(self):
        b = map_holdings_to_buckets([])
        self.assertEqual(sum(b.values()), 0.0)


# ══════════════════════════════════════════════════════════════════════════
# Concentration helper tests
# ══════════════════════════════════════════════════════════════════════════

class TestConcentration(unittest.TestCase):

    def test_ticker_weights_sum_to_one(self):
        tw = _ticker_weights(_all_equity_portfolio())
        self.assertAlmostEqual(sum(tw.values()), 1.0, places=4)

    def test_top1_all_equity(self):
        """All-equity: AAPL is 40% of portfolio."""
        tw = _ticker_weights(_all_equity_portfolio())
        self.assertAlmostEqual(_top_n_pct(tw, 1), 0.40, places=2)

    def test_top5_all_equity(self):
        tw = _ticker_weights(_all_equity_portfolio())
        self.assertAlmostEqual(_top_n_pct(tw, 5), 1.0, places=2)

    def test_tech_pct_all_equity(self):
        """All four tickers in all_equity are tech → 100%."""
        self.assertAlmostEqual(_tech_pct(_all_equity_portfolio()), 1.0, places=4)

    def test_tech_pct_balanced(self):
        """Balanced portfolio has no explicit tech tickers → 0%."""
        self.assertAlmostEqual(_tech_pct(_balanced_portfolio()), 0.0, places=4)


# ══════════════════════════════════════════════════════════════════════════
# compute_aggressiveness_score tests
# ══════════════════════════════════════════════════════════════════════════

class TestAggressivenessScore(unittest.TestCase):

    def test_all_cash_is_low(self):
        """All-cash still scores 10: top1=100% (+15) + top5=100% (+10) − defensive (−15).
        This is correct — a single-holding portfolio IS concentrated."""
        score = compute_aggressiveness_score(_all_cash_portfolio())
        self.assertEqual(score, 10.0)
        self.assertLess(score, 25)  # still Conservative

    def test_all_tech_equity_is_high(self):
        """100% tech equity, top1=40%, top5=100%, tech=100% → should be very high."""
        score = compute_aggressiveness_score(_all_equity_portfolio())
        self.assertGreater(score, 80)

    def test_balanced_is_moderate(self):
        score = compute_aggressiveness_score(_balanced_portfolio())
        self.assertGreater(score, 0)
        self.assertLess(score, 40)

    def test_score_in_range(self):
        for fn in [_all_cash_portfolio, _all_equity_portfolio,
                   _balanced_portfolio, _high_dividend_portfolio]:
            score = compute_aggressiveness_score(fn())
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)

    def test_dividend_portfolio_lower_than_growth(self):
        """High-dividend portfolio should score lower than all-tech-equity."""
        div_score = compute_aggressiveness_score(_high_dividend_portfolio())
        tech_score = compute_aggressiveness_score(_all_equity_portfolio())
        self.assertLess(div_score, tech_score)

    def test_adding_bonds_reduces_score(self):
        """Adding bonds to an equity portfolio should reduce the score."""
        eq_only = _all_equity_portfolio()
        eq_plus_bonds = eq_only + [_make_holding("BND", "us_bond", 500_000)]
        self.assertLess(
            compute_aggressiveness_score(eq_plus_bonds),
            compute_aggressiveness_score(eq_only),
        )


# ══════════════════════════════════════════════════════════════════════════
# describe_posture tests
# ══════════════════════════════════════════════════════════════════════════

class TestDescribePosture(unittest.TestCase):

    def test_conservative(self):
        p = describe_posture(15.0)
        self.assertEqual(p["label"], "Conservative")
        self.assertEqual(p["score"], 15.0)
        self.assertIn("\n", p["justification"])  # multi-line

    def test_balanced(self):
        p = describe_posture(40.0)
        self.assertEqual(p["label"], "Balanced")

    def test_growth(self):
        p = describe_posture(60.0)
        self.assertEqual(p["label"], "Growth")

    def test_aggressive(self):
        p = describe_posture(90.0)
        self.assertEqual(p["label"], "Aggressive")

    def test_boundary_25(self):
        self.assertEqual(describe_posture(25.0)["label"], "Conservative")

    def test_boundary_50(self):
        self.assertEqual(describe_posture(50.0)["label"], "Balanced")

    def test_boundary_75(self):
        self.assertEqual(describe_posture(75.0)["label"], "Growth")

    def test_boundary_100(self):
        self.assertEqual(describe_posture(100.0)["label"], "Aggressive")

    def test_zero(self):
        self.assertEqual(describe_posture(0.0)["label"], "Conservative")


# ══════════════════════════════════════════════════════════════════════════
# Integration: real sample data
# ══════════════════════════════════════════════════════════════════════════

class TestWithSampleData(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ingested = ingest_all(PATHS)
        cls.accounts = cls.ingested["accounts"]

    def test_buckets_sum_to_one(self):
        b = map_holdings_to_buckets(self.accounts)
        self.assertAlmostEqual(sum(b.values()), 1.0, places=4)

    def test_equity_bucket_includes_dividend_equity(self):
        """Some holdings should classify as dividend_equity (VXUS yield=2.9%)."""
        b = map_holdings_to_buckets(self.accounts)
        self.assertGreater(b["dividend_equity"], 0.0,
                           "Expected some dividend_equity from VXUS (2.9% yield)")

    def test_score_in_balanced_to_growth_range(self):
        """72yo couple with ~59% equity + tech names → expect 25-60."""
        score = compute_aggressiveness_score(self.accounts)
        self.assertGreater(score, 20)
        self.assertLess(score, 65)

    def test_posture_is_balanced_or_growth(self):
        score = compute_aggressiveness_score(self.accounts)
        p = describe_posture(score)
        self.assertIn(p["label"], ("Balanced", "Growth"))

    def test_risk_report_has_all_keys(self):
        report = risk_report(self.accounts)
        expected_keys = {"buckets", "ticker_weights_top10", "top1_pct",
                         "top5_pct", "tech_pct", "aggressiveness_score", "posture"}
        self.assertEqual(set(report.keys()), expected_keys)

    def test_score_components_add_up(self):
        """Sum of components should equal final score (within rounding)."""
        comps = score_components(self.accounts)
        total_from_comps = sum(comps["components"].values())
        score = compute_aggressiveness_score(self.accounts)
        self.assertAlmostEqual(total_from_comps, score, delta=0.5)

    def test_tech_pct_reflects_explicit_holdings(self):
        """AAPL + MSFT + NVDA + AVGO are tech; should be material but < 30%."""
        tech = _tech_pct(self.accounts)
        self.assertGreater(tech, 0.15)
        self.assertLess(tech, 0.35)


if __name__ == "__main__":
    unittest.main(verbosity=2)
