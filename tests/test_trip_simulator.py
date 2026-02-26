"""
test_trip_simulator.py — Unit tests for trip_simulator.py

Run:  python -m pytest tests/test_trip_simulator.py -v
  or: python tests/test_trip_simulator.py
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from trip_simulator import (
    trip_impact,
    compare_funding_options,
    _estimate_tax_impact,
    _quick_tax,
    _deterministic_delta,
)
from ingest import ingest_all, DEFAULT_PATHS

DATA_DIR = PROJECT_ROOT / "data"
PATHS = {k: str(DATA_DIR / v.split("/")[-1]) for k, v in DEFAULT_PATHS.items()}


class BaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ingested = ingest_all(PATHS)
        cls.tp = cls.ingested["tax_profile"]


# ══════════════════════════════════════════════════════════════════════════
# Tax impact per funding source
# ══════════════════════════════════════════════════════════════════════════

class TestTaxImpact(BaseTest):

    def test_cash_no_tax(self):
        result = _estimate_tax_impact(15_000, "cash", self.tp, 104_000)
        self.assertEqual(result["tax_cost"], 0)
        self.assertEqual(result["agi_delta"], 0)
        self.assertEqual(result["net_cost"], 15_000)

    def test_inherited_ira_has_tax(self):
        result = _estimate_tax_impact(15_000, "inherited_ira", self.tp, 104_000)
        self.assertGreater(result["tax_cost"], 0)
        self.assertEqual(result["agi_delta"], 15_000)
        self.assertGreater(result["net_cost"], 15_000)

    def test_trad_ira_same_as_inherited(self):
        """Both are ordinary income distributions."""
        iira = _estimate_tax_impact(15_000, "inherited_ira", self.tp, 104_000)
        tira = _estimate_tax_impact(15_000, "trad_ira", self.tp, 104_000)
        self.assertAlmostEqual(iira["tax_cost"], tira["tax_cost"], delta=1)

    def test_taxable_less_tax_than_ira(self):
        """LTCG on 60% gain should cost less than ordinary on full amount."""
        taxable = _estimate_tax_impact(15_000, "taxable", self.tp, 104_000)
        ira = _estimate_tax_impact(15_000, "trad_ira", self.tp, 104_000)
        self.assertLess(taxable["tax_cost"], ira["tax_cost"])

    def test_cash_is_cheapest(self):
        """Cash always has the lowest net cost."""
        for amt in [5_000, 15_000, 50_000]:
            results = compare_funding_options(amt, 2025, self.ingested)
            net_costs = {s: r["net_cost"] for s, r in results.items()}
            self.assertEqual(min(net_costs, key=net_costs.get), "cash")

    def test_irmaa_not_triggered_at_104k(self):
        """$15k from cash at $104k AGI should stay in IRMAA tier 0."""
        result = _estimate_tax_impact(15_000, "cash", self.tp, 104_000)
        self.assertEqual(result["irmaa_tier_after"], 0)

    def test_irmaa_triggered_at_high_agi(self):
        """$15k IRA dist at $195k AGI → MAGI $210k → IRMAA tier 1."""
        result = _estimate_tax_impact(15_000, "trad_ira", self.tp, 195_000)
        self.assertEqual(result["irmaa_tier_after"], 1)
        self.assertGreater(result["irmaa_delta_annual"], 0)

    def test_net_cost_includes_irmaa(self):
        result = _estimate_tax_impact(15_000, "trad_ira", self.tp, 195_000)
        expected = 15_000 + result["tax_cost"] + result["irmaa_delta_annual"]
        self.assertAlmostEqual(result["net_cost"], expected, delta=1)

    def test_zero_cost_trip(self):
        result = _estimate_tax_impact(0, "cash", self.tp, 104_000)
        self.assertEqual(result["net_cost"], 0)
        self.assertEqual(result["tax_cost"], 0)


# ══════════════════════════════════════════════════════════════════════════
# Quick tax helper
# ══════════════════════════════════════════════════════════════════════════

class TestQuickTax(BaseTest):

    def test_zero_agi(self):
        self.assertEqual(_quick_tax(0, self.tp), 0)

    def test_positive_and_progressive(self):
        t50 = _quick_tax(50_000, self.tp)
        t100 = _quick_tax(100_000, self.tp)
        self.assertGreater(t50, 0)
        self.assertGreater(t100, t50)

    def test_marginal_increase(self):
        """$1 extra AGI should produce ≤ 37% extra tax (top marginal)."""
        t1 = _quick_tax(100_000, self.tp)
        t2 = _quick_tax(100_001, self.tp)
        self.assertLessEqual(t2 - t1, 0.37 + 0.10)  # fed + state max


# ══════════════════════════════════════════════════════════════════════════
# Deterministic delta tests
# ══════════════════════════════════════════════════════════════════════════

class TestDeterministicDelta(BaseTest):

    def test_trip_reduces_end_balance(self):
        """Adding a trip should reduce end balance in all scenarios."""
        delta = _deterministic_delta(self.ingested, 15_000, 2026, "cash", horizon=20)
        for s in ("conservative", "central", "growth"):
            self.assertLess(delta[s]["delta_20yr"], 0,
                            f"{s} end balance not reduced by trip")

    def test_bigger_trip_bigger_delta(self):
        d_small = _deterministic_delta(self.ingested, 10_000, 2026, "cash", horizon=20)
        d_large = _deterministic_delta(self.ingested, 50_000, 2026, "cash", horizon=20)
        self.assertLess(d_large["central"]["delta_20yr"],
                        d_small["central"]["delta_20yr"])

    def test_delta_has_yearly_detail(self):
        delta = _deterministic_delta(self.ingested, 15_000, 2026, "cash", horizon=20)
        yearly = delta["central"]["yearly_deltas"]
        self.assertGreater(len(yearly), 0)
        self.assertIn("delta", yearly[0])

    def test_three_scenarios_present(self):
        delta = _deterministic_delta(self.ingested, 15_000, 2026, "cash", horizon=20)
        self.assertEqual(set(delta.keys()), {"conservative", "central", "growth"})


# ══════════════════════════════════════════════════════════════════════════
# Full trip_impact integration tests
# ══════════════════════════════════════════════════════════════════════════

class TestTripImpact(BaseTest):

    def test_returns_expected_keys(self):
        result = trip_impact(15_000, 2026, "cash", self.ingested,
                             run_mc=False)
        expected = {"trip_cost", "trip_year", "funding_analysis",
                    "best_source", "deterministic_delta",
                    "mc_delta", "recommendation"}
        self.assertEqual(set(result.keys()), expected)

    def test_cash_is_best_source(self):
        result = trip_impact(15_000, 2026, "optimal", self.ingested,
                             run_mc=False)
        self.assertEqual(result["best_source"], "cash")

    def test_recommendation_has_verdict(self):
        result = trip_impact(15_000, 2026, "optimal", self.ingested,
                             run_mc=False)
        rec = result["recommendation"]
        self.assertIn("verdict", rec)
        self.assertIn("best_funding", rec)
        self.assertIn("reason", rec)

    def test_15k_trip_minimal_impact(self):
        """$15k trip should have < 1% impact on central 20yr balance."""
        result = trip_impact(15_000, 2026, "optimal", self.ingested,
                             run_mc=False)
        pct = result["deterministic_delta"]["central"]["pct_delta"]
        self.assertGreater(pct, -2.0)
        self.assertLess(pct, 0)

    def test_single_source_mode(self):
        """Specifying a single source should only analyze that source."""
        result = trip_impact(15_000, 2026, "inherited_ira", self.ingested,
                             run_mc=False)
        self.assertEqual(set(result["funding_analysis"].keys()), {"inherited_ira"})

    def test_with_mc(self):
        """Run with MC (small N) and verify mc_delta is populated."""
        result = trip_impact(15_000, 2026, "cash", self.ingested,
                             run_mc=True, mc_n=100, mc_seed=42)
        self.assertIsNotNone(result["mc_delta"])
        self.assertIn("ruin_delta", result["mc_delta"])
        self.assertIn("terminal_delta", result["mc_delta"])


# ══════════════════════════════════════════════════════════════════════════
# Compare funding options (quick mode)
# ══════════════════════════════════════════════════════════════════════════

class TestCompareFunding(BaseTest):

    def test_returns_four_sources(self):
        results = compare_funding_options(15_000, 2025, self.ingested)
        self.assertEqual(set(results.keys()),
                         {"cash", "inherited_ira", "taxable", "trad_ira"})

    def test_ordering_by_net_cost(self):
        """Cash ≤ taxable < IRA distributions.
        Cash and taxable can tie when LTCG falls in the 0% bracket."""
        results = compare_funding_options(15_000, 2025, self.ingested)
        self.assertLessEqual(results["cash"]["net_cost"],
                             results["taxable"]["net_cost"])
        self.assertLess(results["taxable"]["net_cost"],
                        results["trad_ira"]["net_cost"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
