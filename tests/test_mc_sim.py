"""
test_mc_sim.py — Unit tests for mc_sim.py

Run:  python -m pytest tests/test_mc_sim.py -v
  or: python tests/test_mc_sim.py
"""

import sys
import unittest
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mc_sim import (
    simulate_monte_carlo,
    summarize_mc_results,
    run_mc_from_ingested,
    generate_correlated_returns,
    generate_inflation_series,
    MCResults,
    _build_default_correlation,
)
from ingest import ingest_all, DEFAULT_PATHS

DATA_DIR = PROJECT_ROOT / "data"
PATHS = {k: str(DATA_DIR / v.split("/")[-1]) for k, v in DEFAULT_PATHS.items()}

# Small N for fast tests
SMALL_N = 200
HORIZON = 20


# ══════════════════════════════════════════════════════════════════════════
# Return generation tests
# ══════════════════════════════════════════════════════════════════════════

class TestReturnGeneration(unittest.TestCase):

    def test_shape(self):
        mu = np.array([0.07, 0.035, 0.04])
        sigma = np.array([0.16, 0.05, 0.005])
        corr = np.eye(3)
        r = generate_correlated_returns(mu, sigma, corr, 100, 10, seed=42)
        self.assertEqual(r.shape, (100, 10, 3))

    def test_mean_close_to_mu(self):
        """With enough samples, mean should be close to mu."""
        mu = np.array([0.07, 0.04])
        sigma = np.array([0.16, 0.005])
        corr = np.eye(2)
        r = generate_correlated_returns(mu, sigma, corr, 50_000, 1, seed=42)
        sample_means = r[:, 0, :].mean(axis=0)
        np.testing.assert_allclose(sample_means, mu, atol=0.005)

    def test_std_close_to_sigma(self):
        mu = np.array([0.07, 0.04])
        sigma = np.array([0.16, 0.005])
        corr = np.eye(2)
        r = generate_correlated_returns(mu, sigma, corr, 50_000, 1, seed=42)
        sample_stds = r[:, 0, :].std(axis=0)
        np.testing.assert_allclose(sample_stds, sigma, atol=0.005)

    def test_correlation_structure(self):
        """Correlated assets should show correlation in draws."""
        mu = np.array([0.07, 0.06])
        sigma = np.array([0.16, 0.18])
        corr = np.array([[1.0, 0.85], [0.85, 1.0]])
        r = generate_correlated_returns(mu, sigma, corr, 50_000, 1, seed=42)
        sample_corr = np.corrcoef(r[:, 0, 0], r[:, 0, 1])[0, 1]
        self.assertAlmostEqual(sample_corr, 0.85, delta=0.03)

    def test_reproducible_with_seed(self):
        mu = np.array([0.05])
        sigma = np.array([0.10])
        corr = np.eye(1)
        r1 = generate_correlated_returns(mu, sigma, corr, 100, 5, seed=99)
        r2 = generate_correlated_returns(mu, sigma, corr, 100, 5, seed=99)
        np.testing.assert_array_equal(r1, r2)


class TestInflationGeneration(unittest.TestCase):

    def test_shape(self):
        infl = generate_inflation_series(0.025, 0.01, 100, 10, seed=42)
        self.assertEqual(infl.shape, (100, 10))

    def test_clamped(self):
        """Inflation should be clamped to [0, 0.15]."""
        infl = generate_inflation_series(0.025, 0.05, 10_000, 1, seed=42)
        self.assertTrue((infl >= 0).all())
        self.assertTrue((infl <= 0.15).all())

    def test_mean_close(self):
        infl = generate_inflation_series(0.025, 0.01, 50_000, 1, seed=42)
        self.assertAlmostEqual(infl.mean(), 0.025, delta=0.002)


# ══════════════════════════════════════════════════════════════════════════
# Correlation matrix tests
# ══════════════════════════════════════════════════════════════════════════

class TestCorrelationMatrix(unittest.TestCase):

    def test_symmetric(self):
        corr = _build_default_correlation(["us_equity", "intl_equity", "us_bond", "mmf"])
        np.testing.assert_array_almost_equal(corr, corr.T)

    def test_diagonal_ones(self):
        corr = _build_default_correlation(["us_equity", "us_bond", "mmf"])
        np.testing.assert_array_equal(np.diag(corr), np.ones(3))

    def test_positive_definite(self):
        corr = _build_default_correlation(["us_equity", "intl_equity", "us_bond", "mmf"])
        eigenvalues = np.linalg.eigvalsh(corr)
        self.assertTrue((eigenvalues > 0).all(), "Correlation matrix not positive definite")

    def test_equity_bond_negative(self):
        classes = ["us_equity", "us_bond"]
        corr = _build_default_correlation(classes)
        self.assertLess(corr[0, 1], 0)


# ══════════════════════════════════════════════════════════════════════════
# simulate_monte_carlo tests
# ══════════════════════════════════════════════════════════════════════════

class TestSimulateMC(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ingested = ingest_all(PATHS)
        mc_cfg = cls.ingested["constraints"]["monte_carlo"]
        ra = mc_cfg["return_assumptions"]

        accounts = cls.ingested["accounts"]
        ac_totals = {}
        total_mv = 0
        for r in accounts:
            ac = r["asset_class"]
            mv = r["market_value"]
            ac_totals[ac] = ac_totals.get(ac, 0) + mv
            total_mv += mv
        alloc = {ac: v / total_mv for ac, v in ac_totals.items() if ac in ra}
        w_sum = sum(alloc.values())
        alloc = {k: v / w_sum for k, v in alloc.items()}

        cls.results = simulate_monte_carlo(
            initial_balances=cls.ingested["totals"]["by_account_id"],
            allocation_weights=alloc,
            return_assumptions=ra,
            cashflow=cls.ingested["cashflow"],
            tax_profile=cls.ingested["tax_profile"],
            constraints=cls.ingested["constraints"],
            n_sims=SMALL_N,
            horizon=HORIZON,
            seed=42,
        )

    def test_result_type(self):
        self.assertIsInstance(self.results, MCResults)

    def test_portfolio_paths_shape(self):
        self.assertEqual(self.results.portfolio_paths.shape, (SMALL_N, HORIZON))

    def test_withdrawal_paths_shape(self):
        self.assertEqual(self.results.withdrawal_paths.shape, (SMALL_N, HORIZON))

    def test_agi_paths_shape(self):
        self.assertEqual(self.results.agi_paths.shape, (SMALL_N, HORIZON))

    def test_terminal_values_shape(self):
        self.assertEqual(self.results.terminal_values.shape, (SMALL_N,))

    def test_ruin_flags_shape(self):
        self.assertEqual(self.results.ruin_flags.shape, (SMALL_N,))

    def test_no_nan_in_paths(self):
        self.assertFalse(np.isnan(self.results.portfolio_paths).any())
        self.assertFalse(np.isnan(self.results.withdrawal_paths).any())
        self.assertFalse(np.isnan(self.results.agi_paths).any())

    def test_balances_non_negative(self):
        self.assertTrue((self.results.portfolio_paths >= 0).all())

    def test_withdrawals_non_negative(self):
        self.assertTrue((self.results.withdrawal_paths >= 0).all())

    def test_terminal_median_reasonable(self):
        """Median terminal value should be positive and > $500k for this portfolio."""
        median_tv = np.median(self.results.terminal_values)
        self.assertGreater(median_tv, 500_000)

    def test_ruin_probability_low(self):
        """$2.1M portfolio with $90k expenses → ruin should be very rare."""
        ruin_pct = self.results.ruin_flags.mean()
        self.assertLess(ruin_pct, 0.10)  # < 10%

    def test_elapsed_time_recorded(self):
        self.assertGreater(self.results.elapsed_seconds, 0)

    def test_reproducible(self):
        """Same seed → same results."""
        mc_cfg = self.ingested["constraints"]["monte_carlo"]
        ra = mc_cfg["return_assumptions"]
        accounts = self.ingested["accounts"]
        ac_totals = {}
        total_mv = 0
        for r in accounts:
            ac = r["asset_class"]
            mv = r["market_value"]
            ac_totals[ac] = ac_totals.get(ac, 0) + mv
            total_mv += mv
        alloc = {ac: v / total_mv for ac, v in ac_totals.items() if ac in ra}
        w_sum = sum(alloc.values())
        alloc = {k: v / w_sum for k, v in alloc.items()}

        r2 = simulate_monte_carlo(
            initial_balances=self.ingested["totals"]["by_account_id"],
            allocation_weights=alloc,
            return_assumptions=ra,
            cashflow=self.ingested["cashflow"],
            tax_profile=self.ingested["tax_profile"],
            constraints=self.ingested["constraints"],
            n_sims=SMALL_N,
            horizon=HORIZON,
            seed=42,
        )
        np.testing.assert_array_almost_equal(
            self.results.portfolio_paths, r2.portfolio_paths
        )


# ══════════════════════════════════════════════════════════════════════════
# summarize_mc_results tests
# ══════════════════════════════════════════════════════════════════════════

class TestSummarize(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        ingested = ingest_all(PATHS)
        cls.summary = run_mc_from_ingested(ingested, n_sims=SMALL_N, horizon=HORIZON, seed=42)

    def test_has_expected_keys(self):
        expected = {"metadata", "terminal_stats", "ruin_stats",
                    "irmaa_stats", "cumulative_withdrawal_stats", "year_by_year"}
        self.assertEqual(set(self.summary.keys()), expected)

    def test_year_table_length(self):
        self.assertEqual(len(self.summary["year_by_year"]), HORIZON)

    def test_year_row_has_percentiles(self):
        row = self.summary["year_by_year"][0]
        for p in [5, 10, 25, 50, 75, 90, 95]:
            self.assertIn(f"balance_p{p}", row)

    def test_percentiles_ordered(self):
        """P5 < P25 < P50 < P75 < P95 for each year."""
        for row in self.summary["year_by_year"]:
            self.assertLessEqual(row["balance_p5"], row["balance_p25"])
            self.assertLessEqual(row["balance_p25"], row["balance_p50"])
            self.assertLessEqual(row["balance_p50"], row["balance_p75"])
            self.assertLessEqual(row["balance_p75"], row["balance_p95"])

    def test_terminal_stats_consistent(self):
        ts = self.summary["terminal_stats"]
        self.assertLessEqual(ts["p5"], ts["median"])
        self.assertLessEqual(ts["median"], ts["p95"])

    def test_ruin_probability_is_fraction(self):
        rp = self.summary["ruin_stats"]["probability_of_ruin"]
        self.assertGreaterEqual(rp, 0)
        self.assertLessEqual(rp, 1)

    def test_irmaa_probability_is_fraction(self):
        ip = self.summary["irmaa_stats"]["prob_ever_triggered"]
        self.assertGreaterEqual(ip, 0)
        self.assertLessEqual(ip, 1)

    def test_metadata_n_sims(self):
        self.assertEqual(self.summary["metadata"]["n_sims"], SMALL_N)

    def test_json_serializable(self):
        """Summary should be JSON-serializable (no numpy types)."""
        import json
        try:
            json.dumps(self.summary)
        except TypeError as e:
            self.fail(f"Summary not JSON-serializable: {e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
