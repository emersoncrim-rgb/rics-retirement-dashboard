"""
test_rmd.py — Unit tests for rmd.py

Run:  python -m pytest tests/test_rmd.py -v
  or: python tests/test_rmd.py
"""

import sys
import unittest
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rmd import (
    load_rmd_divisors,
    compute_rmd_amount,
    project_rmd_series,
    generate_inherited_ira_schedule,
    _get_divisor,
    _generate_weights,
)

DATA_DIR = PROJECT_ROOT / "data"
DIVISORS_PATH = DATA_DIR / "rmd_divisors.json"
DIVISORS = load_rmd_divisors(str(DIVISORS_PATH))


# ══════════════════════════════════════════════════════════════════════════
# Divisor table tests
# ══════════════════════════════════════════════════════════════════════════

class TestDivisorTable(unittest.TestCase):

    def test_loads_from_json(self):
        d = load_rmd_divisors(str(DIVISORS_PATH))
        self.assertIsInstance(d, dict)
        self.assertGreater(len(d), 30)

    def test_embedded_fallback(self):
        d = load_rmd_divisors(None)
        self.assertIn(73, d)
        self.assertAlmostEqual(d[73], 26.5)

    def test_divisor_at_known_ages(self):
        self.assertAlmostEqual(DIVISORS[72], 27.4)
        self.assertAlmostEqual(DIVISORS[73], 26.5)
        self.assertAlmostEqual(DIVISORS[80], 20.2)
        self.assertAlmostEqual(DIVISORS[90], 12.2)
        self.assertAlmostEqual(DIVISORS[100], 6.4)

    def test_divisors_decrease_with_age(self):
        """Divisor must strictly decrease as age increases."""
        ages = sorted(DIVISORS.keys())
        for i in range(1, len(ages)):
            self.assertLess(DIVISORS[ages[i]], DIVISORS[ages[i - 1]],
                            f"Divisor did not decrease from age {ages[i-1]} to {ages[i]}")

    def test_all_divisors_positive(self):
        for age, div in DIVISORS.items():
            self.assertGreater(div, 0, f"Age {age} has non-positive divisor")

    def test_get_divisor_below_range(self):
        """Age below table range → 0 (no RMD)."""
        self.assertEqual(_get_divisor(50, DIVISORS), 0.0)

    def test_get_divisor_above_range(self):
        """Age above table range → uses max age divisor (floored at 1.0)."""
        result = _get_divisor(130, DIVISORS)
        self.assertGreaterEqual(result, 1.0)


# ══════════════════════════════════════════════════════════════════════════
# compute_rmd_amount tests
# ══════════════════════════════════════════════════════════════════════════

class TestComputeRMD(unittest.TestCase):

    def test_no_rmd_at_72(self):
        """Born 1953, in 2025 age=72 → no RMD (start age 73)."""
        result = compute_rmd_amount(2025, "1953-06-15", 1_000_000, DIVISORS)
        self.assertFalse(result["rmd_required"])
        self.assertEqual(result["rmd_amount"], 0.0)
        self.assertEqual(result["age"], 72)

    def test_rmd_at_73(self):
        """Born 1953, in 2026 age=73 → RMD = 1M / 26.5 ≈ $37,736."""
        result = compute_rmd_amount(2026, "1953-06-15", 1_000_000, DIVISORS)
        self.assertTrue(result["rmd_required"])
        self.assertAlmostEqual(result["rmd_amount"], 1_000_000 / 26.5, delta=1)
        self.assertEqual(result["divisor"], 26.5)
        self.assertEqual(result["age"], 73)

    def test_rmd_at_80(self):
        result = compute_rmd_amount(2033, "1953-06-15", 800_000, DIVISORS)
        self.assertAlmostEqual(result["rmd_amount"], 800_000 / 20.2, delta=1)
        self.assertAlmostEqual(result["rmd_pct"], (1 / 20.2) * 100, delta=0.1)

    def test_rmd_percentage_increases_with_age(self):
        """RMD as % of balance must increase with age."""
        r73 = compute_rmd_amount(2026, "1953-01-01", 1_000_000, DIVISORS)
        r83 = compute_rmd_amount(2036, "1953-01-01", 1_000_000, DIVISORS)
        r93 = compute_rmd_amount(2046, "1953-01-01", 1_000_000, DIVISORS)
        self.assertLess(r73["rmd_pct"], r83["rmd_pct"])
        self.assertLess(r83["rmd_pct"], r93["rmd_pct"])

    def test_zero_balance(self):
        result = compute_rmd_amount(2030, "1953-01-01", 0, DIVISORS)
        self.assertEqual(result["rmd_amount"], 0.0)

    def test_accepts_date_object(self):
        result = compute_rmd_amount(2026, date(1953, 6, 15), 500_000, DIVISORS)
        self.assertTrue(result["rmd_required"])
        self.assertGreater(result["rmd_amount"], 0)

    def test_result_has_all_keys(self):
        result = compute_rmd_amount(2026, "1953-01-01", 1_000_000, DIVISORS)
        expected_keys = {"year", "age", "balance_basis", "divisor",
                         "rmd_amount", "rmd_pct", "rmd_required", "note"}
        self.assertEqual(set(result.keys()), expected_keys)


# ══════════════════════════════════════════════════════════════════════════
# project_rmd_series tests
# ══════════════════════════════════════════════════════════════════════════

class TestProjectRMDSeries(unittest.TestCase):

    def test_series_length(self):
        series = project_rmd_series(2025, "1953-01-01", 1_000_000,
                                    horizon=20, divisors=DIVISORS)
        self.assertEqual(len(series), 20)

    def test_first_year_no_rmd(self):
        """Age 72 in 2025 → no RMD."""
        series = project_rmd_series(2025, "1953-01-01", 1_000_000,
                                    horizon=5, divisors=DIVISORS)
        self.assertEqual(series[0]["rmd_amount"], 0.0)
        self.assertFalse(series[0]["rmd_required"])

    def test_second_year_has_rmd(self):
        series = project_rmd_series(2025, "1953-01-01", 1_000_000,
                                    horizon=5, divisors=DIVISORS)
        self.assertGreater(series[1]["rmd_amount"], 0)
        self.assertTrue(series[1]["rmd_required"])

    def test_balance_decreases_with_rmd(self):
        """Even with growth, RMDs eventually draw down the balance."""
        series = project_rmd_series(2025, "1953-01-01", 1_000_000,
                                    growth_rate=0.03, horizon=30,
                                    divisors=DIVISORS)
        self.assertLess(series[-1]["end_balance"], series[0]["start_balance"])

    def test_balance_never_negative(self):
        series = project_rmd_series(2025, "1953-01-01", 1_000_000,
                                    growth_rate=0.0, horizon=30,
                                    divisors=DIVISORS)
        for r in series:
            self.assertGreaterEqual(r["end_balance"], 0)

    def test_end_balance_carries_forward(self):
        """Each year's start_balance = prior year's end_balance."""
        series = project_rmd_series(2025, "1953-01-01", 500_000,
                                    horizon=5, divisors=DIVISORS)
        for i in range(1, len(series)):
            self.assertAlmostEqual(series[i]["start_balance"],
                                   series[i - 1]["end_balance"], delta=1)

    def test_extra_withdrawal(self):
        """Extra withdrawal should reduce balance faster."""
        base = project_rmd_series(2025, "1953-01-01", 1_000_000,
                                  horizon=10, divisors=DIVISORS, extra_withdrawal=0)
        extra = project_rmd_series(2025, "1953-01-01", 1_000_000,
                                   horizon=10, divisors=DIVISORS, extra_withdrawal=20_000)
        self.assertLess(extra[-1]["end_balance"], base[-1]["end_balance"])


# ══════════════════════════════════════════════════════════════════════════
# Inherited IRA schedule tests
# ══════════════════════════════════════════════════════════════════════════

class TestInheritedIRASchedule(unittest.TestCase):

    def test_even_schedule_length(self):
        s = generate_inherited_ira_schedule(85_000, 2033, 2025, 0.035, "even")
        self.assertEqual(len(s), 9)  # 2025 through 2033 inclusive

    def test_even_schedule_exhausts_balance(self):
        """Sum of all distributions should exceed initial balance (growth)."""
        s = generate_inherited_ira_schedule(85_000, 2033, 2025, 0.035, "even")
        total = sum(s.values())
        # With 3.5% growth on 85k over 9 years, total > 85k
        self.assertGreater(total, 85_000)
        self.assertLess(total, 85_000 * (1.035 ** 9) + 100)  # bounded by max growth

    def test_even_payments_roughly_level(self):
        s = generate_inherited_ira_schedule(85_000, 2033, 2025, 0.035, "even")
        amounts = list(s.values())
        mean = sum(amounts) / len(amounts)
        for a in amounts[:-1]:  # last year may differ (residual)
            self.assertAlmostEqual(a, mean, delta=mean * 0.25)

    def test_front_load_early_years_larger(self):
        """First-half distributions should be larger than second-half."""
        s = generate_inherited_ira_schedule(85_000, 2033, 2025, 0.035, "front_load")
        amounts = list(s.values())
        mid = len(amounts) // 2
        first_half_avg = sum(amounts[:mid]) / mid
        second_half_avg = sum(amounts[mid:]) / (len(amounts) - mid)
        self.assertGreater(first_half_avg, second_half_avg)

    def test_back_load_late_years_larger(self):
        """Second-half distributions should be larger than first-half."""
        s = generate_inherited_ira_schedule(85_000, 2033, 2025, 0.035, "back_load")
        amounts = list(s.values())
        mid = len(amounts) // 2
        first_half_avg = sum(amounts[:mid]) / mid
        second_half_avg = sum(amounts[mid:]) / (len(amounts) - mid)
        self.assertGreater(second_half_avg, first_half_avg)

    def test_all_strategies_exhaust_balance(self):
        """All three strategies should distribute approximately the same total."""
        totals = {}
        for strat in ("even", "front_load", "back_load"):
            s = generate_inherited_ira_schedule(85_000, 2033, 2025, 0.035, strat)
            totals[strat] = sum(s.values())

        # All should be within 5% of each other (same growth, different timing)
        values = list(totals.values())
        for v in values:
            self.assertAlmostEqual(v, values[0], delta=values[0] * 0.05)

    def test_all_distributions_positive(self):
        for strat in ("even", "front_load", "back_load"):
            s = generate_inherited_ira_schedule(85_000, 2033, 2025, 0.035, strat)
            for year, amt in s.items():
                self.assertGreater(amt, 0, f"{strat} year {year} not positive")

    def test_zero_balance(self):
        s = generate_inherited_ira_schedule(0, 2033, 2025, 0.035, "even")
        self.assertTrue(all(v == 0 for v in s.values()))

    def test_single_year(self):
        """Distribute by 2025, current year 2025 → 1 distribution."""
        s = generate_inherited_ira_schedule(50_000, 2025, 2025, 0.035, "even")
        self.assertEqual(len(s), 1)
        # One year of growth then full distribution
        self.assertAlmostEqual(s[2025], 50_000 * 1.035, delta=1)

    def test_past_deadline(self):
        """Deadline already passed → immediate full distribution."""
        s = generate_inherited_ira_schedule(50_000, 2024, 2025, 0.035, "even")
        self.assertEqual(len(s), 1)
        self.assertAlmostEqual(s[2025], 50_000, delta=1)

    def test_zero_growth(self):
        """Without growth, even schedule should return balance / n each year."""
        s = generate_inherited_ira_schedule(90_000, 2033, 2025, 0.0, "even")
        expected_per_year = 90_000 / 9  # 9 years
        for year, amt in list(s.items())[:-1]:
            self.assertAlmostEqual(amt, expected_per_year, delta=expected_per_year * 0.1)

    def test_invalid_strategy_raises(self):
        with self.assertRaises(ValueError):
            generate_inherited_ira_schedule(85_000, 2033, 2025, 0.035, "invalid")


# ══════════════════════════════════════════════════════════════════════════
# Weight generation tests
# ══════════════════════════════════════════════════════════════════════════

class TestWeights(unittest.TestCase):

    def test_even_weights(self):
        w = _generate_weights(6, "even")
        self.assertEqual(w, [1.0] * 6)

    def test_front_load_weights(self):
        w = _generate_weights(6, "front_load")
        # First 3 = 2.0, last 3 = 1.0
        self.assertEqual(w[:3], [2.0, 2.0, 2.0])
        self.assertEqual(w[3:], [1.0, 1.0, 1.0])

    def test_back_load_weights(self):
        w = _generate_weights(6, "back_load")
        # First 3 = 1.0, last 3 = 2.0
        self.assertEqual(w[:3], [1.0, 1.0, 1.0])
        self.assertEqual(w[3:], [2.0, 2.0, 2.0])

    def test_odd_count_front_load(self):
        """5 years front_load: first 3 at 2×, last 2 at 1×."""
        w = _generate_weights(5, "front_load")
        self.assertEqual(w, [2.0, 2.0, 2.0, 1.0, 1.0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
