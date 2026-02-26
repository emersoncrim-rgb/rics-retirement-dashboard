"""
test_tax_irmaa.py — Unit tests for tax_irmaa.py

Run:  python -m pytest tests/test_tax_irmaa.py -v
  or: python tests/test_tax_irmaa.py
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tax_irmaa import (
    compute_taxes,
    compute_magi,
    compute_irmaa_impact,
    compute_ss_taxable,
    simulate_year_tax_effects,
    _tax_from_brackets,
    _net_capital_gains,
    _compute_stacked_preferential_tax,
)
from ingest import ingest_all, DEFAULT_PATHS
from deterministic import build_projection_from_ingested

DATA_DIR = PROJECT_ROOT / "data"
PATHS = {k: str(DATA_DIR / v.split("/")[-1]) for k, v in DEFAULT_PATHS.items()}


def _load_tax_profile():
    with open(DATA_DIR / "tax_profile.json") as f:
        return json.load(f)


TP = _load_tax_profile()


# ══════════════════════════════════════════════════════════════════════════
# Bracket computation tests
# ══════════════════════════════════════════════════════════════════════════

class TestBracketTax(unittest.TestCase):

    def test_zero_income(self):
        self.assertEqual(_tax_from_brackets(0, TP["federal_brackets_mfj_2025"]), 0.0)

    def test_first_bracket_only(self):
        """$20,000 income → all at 10% = $2,000."""
        tax = _tax_from_brackets(20_000, TP["federal_brackets_mfj_2025"])
        self.assertAlmostEqual(tax, 20_000 * 0.10, places=0)

    def test_two_brackets(self):
        """$50,000 income: $23,850 at 10% + $26,150 at 12%."""
        tax = _tax_from_brackets(50_000, TP["federal_brackets_mfj_2025"])
        expected = 23_850 * 0.10 + (50_000 - 23_850) * 0.12
        self.assertAlmostEqual(tax, expected, places=2)

    def test_negative_income(self):
        self.assertEqual(_tax_from_brackets(-5_000, TP["federal_brackets_mfj_2025"]), 0.0)

    def test_progressive(self):
        """Higher income → higher effective rate."""
        tax_50k = _tax_from_brackets(50_000, TP["federal_brackets_mfj_2025"])
        tax_100k = _tax_from_brackets(100_000, TP["federal_brackets_mfj_2025"])
        self.assertGreater(tax_100k / 100_000, tax_50k / 50_000)


# ══════════════════════════════════════════════════════════════════════════
# Social Security taxation tests
# ══════════════════════════════════════════════════════════════════════════

class TestSSTaxable(unittest.TestCase):

    def test_zero_other_income(self):
        """Provisional = 0 + 28k = 28k < 32k base → $0 taxable."""
        self.assertEqual(compute_ss_taxable(56_000, 0, "mfj"), 0.0)

    def test_moderate_income_hits_85pct_tier(self):
        """Other income = 20k → provisional = 20k + 28k = 48k.
        Exceeds $44k additional threshold:
          tier1 = 0.5*(44k-32k) = 6k
          tier2 = 0.85*(48k-44k) = 3.4k
          total = min(9.4k, 0.85*56k) = 9.4k."""
        result = compute_ss_taxable(56_000, 20_000, "mfj")
        self.assertAlmostEqual(result, 9_400, delta=1)

    def test_high_income_85pct_cap(self):
        """Very high income → taxable capped at 85% of SS."""
        result = compute_ss_taxable(56_000, 200_000, "mfj")
        self.assertAlmostEqual(result, 0.85 * 56_000, delta=1)

    def test_never_exceeds_85pct(self):
        for income in [0, 50_000, 100_000, 500_000]:
            result = compute_ss_taxable(56_000, income, "mfj")
            self.assertLessEqual(result, 0.85 * 56_000 + 1)


# ══════════════════════════════════════════════════════════════════════════
# Capital gain netting tests
# ══════════════════════════════════════════════════════════════════════════

class TestCapGainNetting(unittest.TestCase):

    def test_no_carryforward(self):
        cg = _net_capital_gains(10_000, 0, 0)
        self.assertEqual(cg["net_ltcg"], 10_000)
        self.assertEqual(cg["remaining_carryforward"], 0)

    def test_carryforward_offsets_ltcg(self):
        """$10k LTCG with $3k carryforward → $7k net LTCG."""
        cg = _net_capital_gains(10_000, 0, 3_000)
        self.assertEqual(cg["net_ltcg"], 7_000)
        self.assertEqual(cg["loss_used"], 3_000)
        self.assertEqual(cg["remaining_carryforward"], 0)
        self.assertEqual(cg["excess_loss_deduction"], 0)

    def test_carryforward_exceeds_gains(self):
        """$5k LTCG with $10k carryforward → $0 net, $3k deduction, $2k remaining."""
        cg = _net_capital_gains(5_000, 0, 10_000)
        self.assertEqual(cg["net_ltcg"], 0)
        self.assertEqual(cg["excess_loss_deduction"], 3_000)
        self.assertEqual(cg["remaining_carryforward"], 2_000)

    def test_stcg_offset_first(self):
        """Carryforward offsets ST gains first, then LT."""
        cg = _net_capital_gains(10_000, 5_000, 8_000)
        # 8k carryforward: first 5k offsets STCG, then 3k offsets LTCG
        self.assertEqual(cg["net_stcg"], 0)
        self.assertEqual(cg["net_ltcg"], 7_000)
        self.assertEqual(cg["remaining_carryforward"], 0)

    def test_excess_deduction_capped_at_3k(self):
        cg = _net_capital_gains(0, 0, 50_000)
        self.assertEqual(cg["excess_loss_deduction"], 3_000)
        self.assertEqual(cg["remaining_carryforward"], 47_000)


# ══════════════════════════════════════════════════════════════════════════
# compute_taxes integration tests
# ══════════════════════════════════════════════════════════════════════════

class TestComputeTaxes(unittest.TestCase):

    def test_sample_scenario_with_carryforward(self):
        """
        Ordinary income $40k (IRA dist) + $10k LTCG + $3k carryforward
        + $6,700 qualified divs + $56k SS.

        Net LTCG = 10k - 3k = 7k.  SS partly taxable.
        """
        result = compute_taxes(
            ordinary_income=40_000,
            long_term_cap_gains=10_000,
            qualified_dividends=6_700,
            cap_loss_carryforward=3_000,
            ss_annual=56_000,
            standard_deduction=33_100,
            tax_profile=TP,
        )

        # AGI should include: ordinary 40k + SS taxable + net LTCG 7k + qual divs 6.7k
        self.assertGreater(result["agi"], 50_000)
        self.assertLess(result["agi"], 120_000)

        # Net LTCG should reflect carryforward offset
        self.assertEqual(result["cap_gain_netting"]["net_ltcg"], 7_000)
        self.assertEqual(result["cap_gain_netting"]["loss_used"], 3_000)
        self.assertEqual(result["cap_gain_netting"]["remaining_carryforward"], 0)

        # Federal tax should be positive
        self.assertGreater(result["federal_total"], 0)
        self.assertGreater(result["federal_ordinary"], 0)

        # State tax should be positive (Oregon)
        self.assertGreater(result["state_tax"], 0)

        # Total tax = federal + state
        self.assertAlmostEqual(result["total_tax"],
                               result["federal_total"] + result["state_tax"], delta=1)

        # Effective rate should be reasonable for this income level
        self.assertGreater(result["effective_rate"], 0.05)
        self.assertLess(result["effective_rate"], 0.25)

    def test_zero_income(self):
        result = compute_taxes(
            ordinary_income=0, ss_annual=0,
            standard_deduction=33_100, tax_profile=TP,
        )
        self.assertEqual(result["federal_total"], 0)
        self.assertEqual(result["state_tax"], 0)

    def test_ltcg_taxed_at_preferential_rate(self):
        """Pure LTCG of $60k (no ordinary) should be taxed at 0% up to $96,700."""
        result = compute_taxes(
            ordinary_income=0,
            long_term_cap_gains=60_000,
            ss_annual=0,
            standard_deduction=33_100,
            tax_profile=TP,
        )
        # Taxable income = 60k - 33.1k = 26.9k, all preferential
        # 0% bracket goes to $96,700, so all at 0%
        self.assertAlmostEqual(result["federal_ltcg"], 0, delta=1)

    def test_ltcg_hits_15pct_bracket(self):
        """LTCG that stacks above $96,700 should hit 15% bracket."""
        result = compute_taxes(
            ordinary_income=80_000,  # fills ordinary brackets
            long_term_cap_gains=50_000,
            ss_annual=0,
            standard_deduction=33_100,
            tax_profile=TP,
        )
        # Ordinary taxable = 80k-33.1k = 46.9k
        # LTCG stacks on top: 46.9k to 96.9k → first ~49.8k at 0%, rest at 15%
        self.assertGreater(result["federal_ltcg"], 0)

    def test_state_tax_excludes_ss(self):
        """Oregon exempts SS: two identical scenarios differing only in SS
        should have the same OR state tax."""
        result_no_ss = compute_taxes(
            ordinary_income=50_000, ss_annual=0,
            standard_deduction=33_100, tax_profile=TP,
        )
        result_with_ss = compute_taxes(
            ordinary_income=50_000, ss_annual=56_000,
            standard_deduction=33_100, tax_profile=TP,
        )
        # State tax should differ only due to the ordinary income that's the same
        # SS adds to AGI (federally taxable portion) but OR exempts it
        # The state taxes won't be exactly equal because ss_taxable changes AGI
        # but the OR calculation subtracts ss_taxable back out
        # So they should be equal
        self.assertAlmostEqual(result_no_ss["state_tax"],
                               result_with_ss["state_tax"], delta=1)


# ══════════════════════════════════════════════════════════════════════════
# MAGI and IRMAA tests
# ══════════════════════════════════════════════════════════════════════════

class TestMAGI(unittest.TestCase):

    def test_no_exempt_interest(self):
        self.assertEqual(compute_magi(100_000, 0), 100_000)

    def test_with_exempt_interest(self):
        self.assertEqual(compute_magi(100_000, 5_000), 105_000)


class TestIRMAA(unittest.TestCase):

    def test_tier_0_no_surcharge(self):
        """MAGI $150k → tier 0 (below $206k), no surcharge."""
        result = compute_irmaa_impact(150_000, TP, num_people=2)
        self.assertEqual(result["tier"], 0)
        self.assertEqual(result["total_annual_cost"], 0)
        self.assertIsNotNone(result["headroom_to_next_tier"])
        self.assertEqual(result["headroom_to_next_tier"], 56_000)

    def test_tier_1_triggered(self):
        """MAGI $210k → tier 1 ($206k–$258k), surcharges apply."""
        result = compute_irmaa_impact(210_000, TP, num_people=2)
        self.assertEqual(result["tier"], 1)
        self.assertGreater(result["total_annual_cost"], 0)
        # Part B surcharge: $838.80/person × 2 = $1,677.60
        self.assertAlmostEqual(result["part_b_surcharge_annual"], 838.8 * 2, delta=1)
        # Part D surcharge: $154.80/person × 2 = $309.60
        self.assertAlmostEqual(result["part_d_surcharge_annual"], 154.8 * 2, delta=1)
        # Total: ~$1,987.20
        self.assertAlmostEqual(result["total_annual_cost"], 1987.2, delta=1)

    def test_tier_2_triggered(self):
        """MAGI $280k → tier 2 ($258k–$322k)."""
        result = compute_irmaa_impact(280_000, TP, num_people=2)
        self.assertEqual(result["tier"], 2)
        self.assertGreater(result["total_annual_cost"], 1987)  # more than tier 1

    def test_headroom_calculation(self):
        """At MAGI $196k, headroom to tier 1 should be $10k."""
        result = compute_irmaa_impact(196_000, TP, num_people=2)
        self.assertEqual(result["tier"], 0)
        self.assertEqual(result["headroom_to_next_tier"], 10_000)

    def test_headroom_matches_guardrail(self):
        """MAGI at guardrail target ($196k) should have $10k headroom."""
        from ingest import load_constraints
        constraints = load_constraints(str(DATA_DIR / "constraints.json"))
        target = (constraints["irmaa_guardrails"]["tier1_magi_mfj"]
                  - constraints["irmaa_guardrails"]["target_headroom_below_tier1"])
        result = compute_irmaa_impact(target, TP, num_people=2)
        self.assertEqual(result["tier"], 0)
        self.assertEqual(result["headroom_to_next_tier"], 10_000)

    def test_single_person(self):
        """Surcharges should halve for single beneficiary."""
        result_2 = compute_irmaa_impact(210_000, TP, num_people=2)
        result_1 = compute_irmaa_impact(210_000, TP, num_people=1)
        self.assertAlmostEqual(result_1["total_annual_cost"],
                               result_2["total_annual_cost"] / 2, delta=1)


# ══════════════════════════════════════════════════════════════════════════
# simulate_year_tax_effects integration tests
# ══════════════════════════════════════════════════════════════════════════

class TestSimulateYearTaxEffects(unittest.TestCase):

    def _make_projection_row(self):
        return {
            "year": 2026, "age": 73,
            "account_balances": {"TAXABLE_01": 1_030_000, "RIRA_01": 960_000},
            "withdrawals": {"RIRA_01": 38_000, "TAXABLE_01": 5_000},
            "total_withdrawal": 43_000,
            "rmd_amount": 38_000,
            "inherited_ira_withdrawal": 0,
            "cumulative_withdrawals": 43_000,
            "total_balance": 1_990_000,
        }

    def test_returns_expected_keys(self):
        row = self._make_projection_row()
        result = simulate_year_tax_effects(
            row, 56_000, 6_700, 3_300, 3_000, TP
        )
        expected_keys = {"year", "taxes", "irmaa",
                         "updated_cap_loss_carryforward", "total_tax_plus_irmaa"}
        self.assertEqual(set(result.keys()), expected_keys)

    def test_carryforward_consumed(self):
        row = self._make_projection_row()
        result = simulate_year_tax_effects(
            row, 56_000, 6_700, 3_300, 3_000, TP
        )
        # Carryforward $3k should be consumed (est LTCG = 1.03M * 2% = $20.6k)
        self.assertEqual(result["updated_cap_loss_carryforward"], 0)

    def test_irmaa_lookback(self):
        """Using $104k lookback MAGI → tier 0."""
        row = self._make_projection_row()
        result = simulate_year_tax_effects(
            row, 56_000, 6_700, 3_300, 3_000, TP,
            magi_lookback=104_000,
        )
        self.assertEqual(result["irmaa"]["tier"], 0)

    def test_irmaa_lookback_high(self):
        """Using $250k lookback MAGI → tier 1."""
        row = self._make_projection_row()
        result = simulate_year_tax_effects(
            row, 56_000, 6_700, 3_300, 3_000, TP,
            magi_lookback=250_000,
        )
        self.assertEqual(result["irmaa"]["tier"], 1)

    def test_total_tax_plus_irmaa(self):
        row = self._make_projection_row()
        result = simulate_year_tax_effects(
            row, 56_000, 6_700, 3_300, 3_000, TP
        )
        expected = result["taxes"]["total_tax"] + result["irmaa"]["total_annual_cost"]
        self.assertAlmostEqual(result["total_tax_plus_irmaa"], expected, delta=1)


# ══════════════════════════════════════════════════════════════════════════
# Full pipeline integration test
# ══════════════════════════════════════════════════════════════════════════

class TestFullPipelineTax(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ingested = ingest_all(PATHS)
        cls.scenarios = build_projection_from_ingested(cls.ingested, horizon=20)

    def test_20_year_tax_simulation(self):
        """Run tax effects for all 20 years of central scenario."""
        central = self.scenarios["central"]["projection"]
        tp = self.ingested["tax_profile"]
        cf = 3_000.0

        for row in central:
            effects = simulate_year_tax_effects(
                row, 56_000, 6_700, 3_300, cf, tp
            )
            cf = effects["updated_cap_loss_carryforward"]

            # Basic sanity
            self.assertGreaterEqual(effects["taxes"]["total_tax"], 0)
            self.assertGreaterEqual(effects["taxes"]["agi"], 0)
            self.assertGreaterEqual(cf, 0)

    def test_effective_rate_reasonable(self):
        """Effective rate should stay 5%–25% for this income level."""
        central = self.scenarios["central"]["projection"]
        tp = self.ingested["tax_profile"]
        cf = 3_000.0

        for row in central:
            effects = simulate_year_tax_effects(
                row, 56_000, 6_700, 3_300, cf, tp
            )
            cf = effects["updated_cap_loss_carryforward"]
            rate = effects["taxes"]["effective_rate"]
            self.assertGreaterEqual(rate, 0.04,
                                    f"Year {row['year']} rate too low: {rate}")
            self.assertLessEqual(rate, 0.25,
                                 f"Year {row['year']} rate too high: {rate}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
