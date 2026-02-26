"""
test_deterministic.py — Unit tests for deterministic.py

Run:  python -m pytest tests/test_deterministic.py -v
  or: python tests/test_deterministic.py
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from deterministic import (
    project_deterministic,
    project_three_scenarios,
    compute_agi_projection,
    compute_rmd,
    compute_inherited_ira_schedule,
    build_projection_from_ingested,
    UNIFORM_LIFETIME_TABLE,
)
from ingest import ingest_all, DEFAULT_PATHS

DATA_DIR = PROJECT_ROOT / "data"
PATHS = {k: str(DATA_DIR / v.split("/")[-1]) for k, v in DEFAULT_PATHS.items()}


# ══════════════════════════════════════════════════════════════════════════
# RMD tests
# ══════════════════════════════════════════════════════════════════════════

class TestRMD(unittest.TestCase):

    def test_no_rmd_before_73(self):
        self.assertEqual(compute_rmd(1_000_000, 72), 0.0)
        self.assertEqual(compute_rmd(1_000_000, 70), 0.0)

    def test_rmd_at_73(self):
        """At age 73, divisor = 26.5 → RMD = 1M / 26.5 ≈ $37,736."""
        rmd = compute_rmd(1_000_000, 73)
        self.assertAlmostEqual(rmd, 1_000_000 / 26.5, places=0)

    def test_rmd_at_80(self):
        rmd = compute_rmd(500_000, 80)
        expected = 500_000 / 20.2
        self.assertAlmostEqual(rmd, expected, places=0)

    def test_rmd_increases_with_age(self):
        """RMD as percentage of balance increases with age."""
        bal = 1_000_000
        rmd_75 = compute_rmd(bal, 75) / bal
        rmd_85 = compute_rmd(bal, 85) / bal
        rmd_95 = compute_rmd(bal, 95) / bal
        self.assertLess(rmd_75, rmd_85)
        self.assertLess(rmd_85, rmd_95)

    def test_rmd_zero_balance(self):
        self.assertEqual(compute_rmd(0, 80), 0.0)

    def test_uniform_table_complete(self):
        """Table covers ages 72–105."""
        for age in range(72, 106):
            self.assertIn(age, UNIFORM_LIFETIME_TABLE)


# ══════════════════════════════════════════════════════════════════════════
# Inherited IRA schedule tests
# ══════════════════════════════════════════════════════════════════════════

class TestInheritedIRASchedule(unittest.TestCase):

    def test_exhausts_balance(self):
        """Schedule should approximately exhaust the balance."""
        schedule = compute_inherited_ira_schedule(85_000, 8, 0.035)
        self.assertEqual(len(schedule), 8)
        # Total withdrawn should exceed initial balance (due to growth)
        self.assertGreater(sum(schedule), 85_000)

    def test_level_payments(self):
        """Payments should be roughly level (within 10% of mean)."""
        schedule = compute_inherited_ira_schedule(85_000, 8, 0.035)
        mean_w = sum(schedule) / len(schedule)
        for w in schedule:
            self.assertAlmostEqual(w, mean_w, delta=mean_w * 0.15)

    def test_single_year(self):
        schedule = compute_inherited_ira_schedule(50_000, 1, 0.04)
        self.assertEqual(len(schedule), 1)
        self.assertAlmostEqual(schedule[0], 52_000, delta=1)

    def test_zero_growth(self):
        schedule = compute_inherited_ira_schedule(80_000, 4, 0.0)
        self.assertEqual(len(schedule), 4)
        for w in schedule:
            self.assertAlmostEqual(w, 20_000, delta=1)

    def test_zero_years(self):
        schedule = compute_inherited_ira_schedule(50_000, 0, 0.04)
        self.assertEqual(schedule, [50_000])


# ══════════════════════════════════════════════════════════════════════════
# Core projection tests
# ══════════════════════════════════════════════════════════════════════════

class TestProjectDeterministic(unittest.TestCase):

    def test_no_withdrawal_grows(self):
        """With no withdrawals, balance should grow at the given rate."""
        result = project_deterministic(
            initial_balances={"A": 1_000_000},
            annual_withdrawals={},
            growth_rates={"A": 0.05},
            horizon=5,
        )
        self.assertEqual(len(result), 5)
        # After 5 years at 5%: 1M * 1.05^5 = 1,276,282
        expected = 1_000_000 * (1.05 ** 5)
        self.assertAlmostEqual(result[-1]["total_balance"], expected, delta=1)

    def test_withdrawal_reduces_balance(self):
        """Balance should be less with withdrawals than without."""
        no_w = project_deterministic(
            {"A": 1_000_000}, {}, {"A": 0.04}, 10
        )
        with_w = project_deterministic(
            {"A": 1_000_000},
            {2025 + y: {"A": 50_000} for y in range(10)},
            {"A": 0.04}, 10
        )
        self.assertGreater(no_w[-1]["total_balance"],
                           with_w[-1]["total_balance"])

    def test_balance_never_negative(self):
        """Even with huge withdrawals, balance floors at 0."""
        result = project_deterministic(
            {"A": 100_000},
            {2025 + y: {"A": 200_000} for y in range(5)},
            {"A": 0.02}, 5
        )
        for row in result:
            self.assertGreaterEqual(row["total_balance"], 0.0)

    def test_cumulative_withdrawals_increase(self):
        result = project_deterministic(
            {"A": 1_000_000},
            {2025 + y: {"A": 30_000} for y in range(5)},
            {"A": 0.04}, 5
        )
        for i in range(1, len(result)):
            self.assertGreaterEqual(result[i]["cumulative_withdrawals"],
                                    result[i - 1]["cumulative_withdrawals"])

    def test_multi_account(self):
        """Two accounts should project independently."""
        result = project_deterministic(
            {"A": 500_000, "B": 500_000},
            {2025: {"A": 20_000, "B": 10_000}},
            {"A": 0.04, "B": 0.06},
            horizon=1,
        )
        self.assertEqual(len(result), 1)
        row = result[0]
        # A: 500k * 1.04 - 20k = 500k
        self.assertAlmostEqual(row["account_balances"]["A"],
                               500_000 * 1.04 - 20_000, delta=1)
        # B: 500k * 1.06 - 10k = 520k
        self.assertAlmostEqual(row["account_balances"]["B"],
                               500_000 * 1.06 - 10_000, delta=1)

    def test_year_and_age_tracking(self):
        result = project_deterministic(
            {"A": 100_000}, {}, {"A": 0.03},
            horizon=3, start_year=2025, owner_start_age=72
        )
        self.assertEqual(result[0]["year"], 2025)
        self.assertEqual(result[0]["age"], 72)
        self.assertEqual(result[2]["year"], 2027)
        self.assertEqual(result[2]["age"], 74)

    def test_zero_rate_preserves_balance(self):
        """0% growth with no withdrawals → unchanged balance."""
        result = project_deterministic(
            {"A": 1_000_000}, {}, {"A": 0.0}, 5
        )
        self.assertAlmostEqual(result[-1]["total_balance"], 1_000_000, delta=1)


# ══════════════════════════════════════════════════════════════════════════
# AGI projection tests
# ══════════════════════════════════════════════════════════════════════════

class TestAGIProjection(unittest.TestCase):

    def _simple_projection(self):
        return [{
            "year": 2025, "age": 72,
            "account_balances": {"TAXABLE_01": 1_000_000, "RIRA_01": 500_000},
            "withdrawals": {"RIRA_01": 40_000, "TAXABLE_01": 10_000},
            "total_withdrawal": 50_000,
            "rmd_amount": 40_000,
            "inherited_ira_withdrawal": 0,
            "cumulative_withdrawals": 50_000,
            "total_balance": 1_500_000,
        }]

    def _tax_profile(self):
        return {
            "ss_taxation": {"base_amount_mfj": 32000, "additional_amount_mfj": 44000},
            "effective_standard_deduction": 33100,
        }

    def test_agi_includes_ira_withdrawals(self):
        proj = self._simple_projection()
        agi_proj = compute_agi_projection(proj, 56000, 6700, 3300, self._tax_profile())
        self.assertEqual(len(agi_proj), 1)
        # IRA withdrawal should be in AGI
        self.assertGreater(agi_proj[0]["agi"], 40_000)

    def test_ss_taxable_up_to_85_pct(self):
        proj = self._simple_projection()
        agi_proj = compute_agi_projection(proj, 56000, 6700, 3300, self._tax_profile())
        self.assertLessEqual(agi_proj[0]["ss_taxable"], 0.85 * 56000)
        self.assertGreater(agi_proj[0]["ss_taxable"], 0)

    def test_taxable_income_after_deduction(self):
        proj = self._simple_projection()
        agi_proj = compute_agi_projection(proj, 56000, 6700, 3300, self._tax_profile())
        row = agi_proj[0]
        self.assertAlmostEqual(row["taxable_income"],
                               max(row["agi"] - 33100, 0), delta=1)

    def test_zero_ss_if_low_income(self):
        """If provisional income < base, SS is not taxable."""
        proj = [{
            "year": 2025, "age": 72,
            "account_balances": {"TAXABLE_01": 100_000},
            "withdrawals": {},
            "total_withdrawal": 0, "rmd_amount": 0,
            "inherited_ira_withdrawal": 0, "cumulative_withdrawals": 0,
            "total_balance": 100_000,
        }]
        tp = self._tax_profile()
        agi_proj = compute_agi_projection(proj, 10_000, 0, 0, tp)
        # Provisional = 0 + 5000 = 5000 < 32000
        self.assertEqual(agi_proj[0]["ss_taxable"], 0)


# ══════════════════════════════════════════════════════════════════════════
# Three-scenario integration tests
# ══════════════════════════════════════════════════════════════════════════

class TestThreeScenarios(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ingested = ingest_all(PATHS)
        cls.scenarios = build_projection_from_ingested(cls.ingested, horizon=20)

    def test_returns_three_scenarios(self):
        self.assertEqual(set(self.scenarios.keys()),
                         {"conservative", "central", "growth"})

    def test_each_scenario_has_20_years(self):
        for name, s in self.scenarios.items():
            self.assertEqual(len(s["projection"]), 20, f"{name} wrong length")
            self.assertEqual(len(s["agi_projection"]), 20, f"{name} AGI wrong length")

    def test_growth_beats_conservative_end_balance(self):
        g_end = self.scenarios["growth"]["summary"]["end_balance"]
        c_end = self.scenarios["conservative"]["summary"]["end_balance"]
        self.assertGreater(g_end, c_end)

    def test_balances_always_non_negative(self):
        for name, s in self.scenarios.items():
            for row in s["projection"]:
                self.assertGreaterEqual(row["total_balance"], 0,
                                        f"{name} year {row['year']} negative balance")

    def test_cumulative_withdrawals_monotonic(self):
        for name, s in self.scenarios.items():
            proj = s["projection"]
            for i in range(1, len(proj)):
                self.assertGreaterEqual(
                    proj[i]["cumulative_withdrawals"],
                    proj[i - 1]["cumulative_withdrawals"],
                    f"{name} year {proj[i]['year']} cumulative decreased"
                )

    def test_rmd_starts_at_age_73(self):
        """Year 2 of projection (age 73) should have non-zero RMD."""
        central = self.scenarios["central"]["projection"]
        # Year 0 = age 72: no RMD
        self.assertEqual(central[0]["age"], 72)
        # Year 1 = age 73: RMD should be > 0
        self.assertEqual(central[1]["age"], 73)
        self.assertGreater(central[1]["rmd_amount"], 0)

    def test_agi_below_irmaa_in_central(self):
        """In central scenario, AGI should stay below IRMAA tier 1 ($206k) most years."""
        central_agi = self.scenarios["central"]["agi_projection"]
        over_irmaa = sum(1 for row in central_agi if row["agi"] > 206_000)
        self.assertLessEqual(over_irmaa, 3,
                             "AGI exceeds IRMAA threshold in too many years")

    def test_summary_has_required_keys(self):
        for name, s in self.scenarios.items():
            required = {"end_balance", "total_withdrawals", "final_year", "final_age"}
            self.assertEqual(set(s["summary"].keys()), required,
                             f"{name} missing summary keys")

    def test_final_age_is_91(self):
        """20 years from age 72 → final age 91."""
        for name, s in self.scenarios.items():
            self.assertEqual(s["summary"]["final_age"], 91)

    def test_inherited_ira_distributed_by_2033(self):
        """Inherited IRA withdrawals should occur and taper off by 2033."""
        central = self.scenarios["central"]["projection"]
        iira_after_2033 = sum(
            row["inherited_ira_withdrawal"]
            for row in central if row["year"] > 2033
        )
        self.assertAlmostEqual(iira_after_2033, 0, delta=100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
