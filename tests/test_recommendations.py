"""
test_recommendations.py – Tests for recommendations engine (unittest-based)

Coverage:
- SS taxable helper
- MAGI estimation helper
- All 8 recommendation rules individually
- Full engine run with real data
- Sorting by severity
- Error resilience
"""

import os
import sys
import json
import csv
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from recommendations import (
    Recommendation,
    check_aapl_concentration,
    check_zero_ltcg_harvesting,
    check_irmaa_headroom,
    check_roth_conversion_opportunity,
    check_cash_reserve_adequacy,
    check_dividend_upgrades,
    check_inherited_ira_pacing,
    check_rmd_projection,
    generate_all_recommendations,
    _compute_ss_taxable,
    _estimate_magi,
    _federal_tax_on_ordinary,
    _find_bracket_room,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _load_json(name):
    with open(os.path.join(DATA_DIR, name)) as f:
        return json.load(f)

def _load_csv(name):
    with open(os.path.join(DATA_DIR, name)) as f:
        return list(csv.DictReader(f))


# ── Fixtures loaded once ─────────────────────────────────────────────────────

def _get_fixtures():
    return {
        "holdings": _load_csv("accounts_snapshot.csv"),
        "tax_profile": _load_json("tax_profile.json"),
        "constraints": _load_json("constraints.json"),
        "cashflow": _load_csv("cashflow_plan.csv"),
        "rmd_divisors": _load_json("rmd_divisors.json"),
    }


# ── Helper function tests ───────────────────────────────────────────────────

class TestSSHelper(unittest.TestCase):
    def test_low_income_zero_ss_tax(self):
        self.assertEqual(_compute_ss_taxable(56000, 0), 0.0)

    def test_moderate_income(self):
        result = _compute_ss_taxable(56000, 20000)
        self.assertGreater(result, 0)
        self.assertLessEqual(result, 0.85 * 56000)

    def test_high_income_85pct_cap(self):
        result = _compute_ss_taxable(56000, 200000)
        self.assertAlmostEqual(result, 0.85 * 56000, delta=100)

    def test_zero_ss(self):
        self.assertEqual(_compute_ss_taxable(0, 50000), 0.0)

    def test_below_base_amount(self):
        # Combined income (other + 50% SS) below $32k → 0 taxable
        result = _compute_ss_taxable(20000, 10000)  # 10000 + 10000 = 20000 < 32000
        self.assertEqual(result, 0.0)


class TestMAGIEstimate(unittest.TestCase):
    def test_base_case(self):
        magi = _estimate_magi(56000, 0, 6700, 3300)
        self.assertGreater(magi, 0)

    def test_with_roth_conversion(self):
        base = _estimate_magi(56000, 0, 6700, 3300, 0)
        with_conv = _estimate_magi(56000, 0, 6700, 3300, 50000)
        self.assertGreater(with_conv, base)


class TestFederalTax(unittest.TestCase):
    def test_zero_income(self):
        brackets = [{"lower": 0, "upper": 23850, "rate": 0.10}]
        self.assertEqual(_federal_tax_on_ordinary(0, brackets), 0.0)

    def test_first_bracket(self):
        brackets = [{"lower": 0, "upper": 23850, "rate": 0.10},
                     {"lower": 23850, "upper": 96950, "rate": 0.12}]
        tax = _federal_tax_on_ordinary(20000, brackets)
        self.assertAlmostEqual(tax, 2000.0, places=0)

    def test_multi_bracket(self):
        brackets = [{"lower": 0, "upper": 23850, "rate": 0.10},
                     {"lower": 23850, "upper": 96950, "rate": 0.12}]
        tax = _federal_tax_on_ordinary(50000, brackets)
        expected = 23850 * 0.10 + (50000 - 23850) * 0.12
        self.assertAlmostEqual(tax, expected, places=0)


class TestBracketRoom(unittest.TestCase):
    def test_room_below_22pct(self):
        brackets = [{"lower": 0, "upper": 23850, "rate": 0.10},
                     {"lower": 23850, "upper": 96950, "rate": 0.12},
                     {"lower": 96950, "upper": 206700, "rate": 0.22}]
        room = _find_bracket_room(50000, brackets, 0.22)
        self.assertAlmostEqual(room, 96950 - 50000, places=0)

    def test_already_in_bracket(self):
        brackets = [{"lower": 0, "upper": 23850, "rate": 0.10},
                     {"lower": 23850, "upper": 96950, "rate": 0.12},
                     {"lower": 96950, "upper": 206700, "rate": 0.22}]
        room = _find_bracket_room(100000, brackets, 0.22)
        self.assertEqual(room, 0)


# ── Rule 1: AAPL Concentration ───────────────────────────────────────────────

class TestAAPLConcentration(unittest.TestCase):
    def test_flags_aapl(self):
        f = _get_fixtures()
        rec = check_aapl_concentration(f["holdings"], f["constraints"])
        self.assertIsNotNone(rec)
        self.assertEqual(rec.rule_id, "CONC-AAPL")
        self.assertEqual(rec.category, "risk")
        self.assertGreater(rec.data["aapl_pct"], 0.20)

    def test_no_flag_if_below_threshold(self):
        f = _get_fixtures()
        holdings = [
            {"account_type": "taxable", "ticker": "AAPL", "market_value": "10000", "unrealized_gain": "5000"},
            {"account_type": "taxable", "ticker": "VTI", "market_value": "990000", "unrealized_gain": "0"},
        ]
        rec = check_aapl_concentration(holdings, f["constraints"])
        self.assertIsNone(rec)

    def test_no_taxable_holdings(self):
        f = _get_fixtures()
        holdings = [
            {"account_type": "trad_ira", "ticker": "AAPL", "market_value": "100000", "unrealized_gain": "0"},
        ]
        rec = check_aapl_concentration(holdings, f["constraints"])
        self.assertIsNone(rec)

    def test_embedded_gain_in_data(self):
        f = _get_fixtures()
        rec = check_aapl_concentration(f["holdings"], f["constraints"])
        if rec:
            self.assertGreater(rec.data["embedded_gain"], 200000)


# ── Rule 2: 0% LTCG Harvesting ──────────────────────────────────────────────

class TestZeroLTCG(unittest.TestCase):
    def test_finds_opportunity(self):
        f = _get_fixtures()
        rec = check_zero_ltcg_harvesting(f["holdings"], f["tax_profile"], f["cashflow"])
        self.assertIsNotNone(rec)
        self.assertEqual(rec.rule_id, "TAX-0LTCG")
        self.assertGreater(rec.data["room_in_bracket"], 0)

    def test_no_brackets_returns_none(self):
        f = _get_fixtures()
        profile = {"qualified_div_brackets_mfj_2025": []}
        rec = check_zero_ltcg_harvesting(f["holdings"], profile, f["cashflow"])
        self.assertIsNone(rec)

    def test_excludes_aapl(self):
        f = _get_fixtures()
        rec = check_zero_ltcg_harvesting(f["holdings"], f["tax_profile"], f["cashflow"])
        if rec:
            candidates = rec.data.get("candidates", [])
            self.assertFalse(any(c["ticker"] == "AAPL" for c in candidates))

    def test_harvest_amount_bounded(self):
        f = _get_fixtures()
        rec = check_zero_ltcg_harvesting(f["holdings"], f["tax_profile"], f["cashflow"])
        if rec:
            self.assertLessEqual(rec.data["suggested_harvest"], rec.data["room_in_bracket"])


# ── Rule 3: IRMAA Headroom ──────────────────────────────────────────────────

class TestIRMAA(unittest.TestCase):
    def test_base_case_ample_headroom(self):
        f = _get_fixtures()
        rec = check_irmaa_headroom(f["tax_profile"], f["cashflow"])
        # With moderate income, likely None or info
        if rec:
            self.assertEqual(rec.rule_id, "TAX-IRMAA")
            self.assertGreater(rec.data["headroom"], 0)

    def test_roth_conversion_narrows_headroom(self):
        f = _get_fixtures()
        rec = check_irmaa_headroom(f["tax_profile"], f["cashflow"], roth_conversion_amount=100000)
        self.assertIsNotNone(rec)
        self.assertLess(rec.data["headroom"], 206000)

    def test_massive_conversion_high_severity(self):
        f = _get_fixtures()
        rec = check_irmaa_headroom(f["tax_profile"], f["cashflow"], roth_conversion_amount=200000)
        self.assertIsNotNone(rec)
        self.assertIn(rec.severity, ("high", "medium"))

    def test_surcharge_for_couple(self):
        f = _get_fixtures()
        rec = check_irmaa_headroom(f["tax_profile"], f["cashflow"], roth_conversion_amount=200000)
        if rec:
            self.assertGreater(rec.data["surcharge_annual_couple"], 1000)


# ── Rule 4: Roth Conversion ─────────────────────────────────────────────────

class TestRothConversion(unittest.TestCase):
    def test_finds_opportunity(self):
        f = _get_fixtures()
        rec = check_roth_conversion_opportunity(f["holdings"], f["tax_profile"], f["cashflow"])
        self.assertIsNotNone(rec)
        self.assertEqual(rec.rule_id, "TAX-ROTH")
        self.assertGreater(rec.data["suggested_conversion"], 0)
        self.assertGreater(rec.data["total_tax"], 0)

    def test_no_trad_ira_no_conversion(self):
        f = _get_fixtures()
        holdings = [{"account_type": "taxable", "ticker": "VTI", "market_value": "500000"}]
        rec = check_roth_conversion_opportunity(holdings, f["tax_profile"], f["cashflow"])
        self.assertIsNone(rec)

    def test_respects_irmaa_ceiling(self):
        f = _get_fixtures()
        rec = check_roth_conversion_opportunity(f["holdings"], f["tax_profile"], f["cashflow"])
        if rec:
            self.assertLessEqual(rec.data["suggested_conversion"], rec.data["irmaa_safe_room"])

    def test_includes_state_tax(self):
        f = _get_fixtures()
        rec = check_roth_conversion_opportunity(f["holdings"], f["tax_profile"], f["cashflow"])
        if rec:
            self.assertGreater(rec.data["state_tax"], 0)
            self.assertEqual(rec.data["total_tax"], rec.data["federal_tax"] + rec.data["state_tax"])


# ── Rule 5: Cash Reserve ────────────────────────────────────────────────────

class TestCashReserve(unittest.TestCase):
    def test_with_real_data(self):
        f = _get_fixtures()
        rec = check_cash_reserve_adequacy(f["holdings"], f["cashflow"])
        self.assertIsNotNone(rec)
        self.assertEqual(rec.rule_id, "CASH-RSV")
        self.assertGreater(rec.data["months_covered"], 0)

    def test_adequate_reserves(self):
        f = _get_fixtures()
        holdings = [{"account_type": "taxable", "asset_class": "mmf", "market_value": "1000000"}]
        rec = check_cash_reserve_adequacy(holdings, f["cashflow"])
        self.assertEqual(rec.severity, "info")

    def test_low_reserves(self):
        f = _get_fixtures()
        holdings = [{"account_type": "taxable", "asset_class": "mmf", "market_value": "5000"}]
        rec = check_cash_reserve_adequacy(holdings, f["cashflow"])
        self.assertIn(rec.severity, ("high", "medium"))

    def test_months_target_customizable(self):
        f = _get_fixtures()
        rec12 = check_cash_reserve_adequacy(f["holdings"], f["cashflow"], months_target=12)
        rec36 = check_cash_reserve_adequacy(f["holdings"], f["cashflow"], months_target=36)
        self.assertEqual(rec12.data["months_target"], 12)
        self.assertEqual(rec36.data["months_target"], 36)


# ── Rule 6: Dividend Upgrades ───────────────────────────────────────────────

class TestDividendUpgrades(unittest.TestCase):
    def test_finds_upgrades(self):
        f = _get_fixtures()
        rec = check_dividend_upgrades(f["holdings"], f["constraints"])
        # NVDA has very low yield in IRA → should find upgrades
        if rec:
            self.assertEqual(rec.rule_id, "INC-DIVUP")
            self.assertGreater(rec.data["total_income_increase"], 0)

    def test_has_top_swaps(self):
        f = _get_fixtures()
        rec = check_dividend_upgrades(f["holdings"], f["constraints"])
        if rec:
            self.assertGreater(len(rec.data["top_swaps"]), 0)


# ── Rule 7: Inherited IRA Pacing ────────────────────────────────────────────

class TestInheritedIRAPacing(unittest.TestCase):
    def test_with_real_data(self):
        f = _get_fixtures()
        rec = check_inherited_ira_pacing(f["holdings"], f["constraints"], current_year=2025)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.rule_id, "WITH-IIRA")
        self.assertEqual(rec.data["years_remaining"], 8)

    def test_future_year(self):
        f = _get_fixtures()
        rec = check_inherited_ira_pacing(f["holdings"], f["constraints"], current_year=2032)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.data["years_remaining"], 1)

    def test_no_inherited_ira(self):
        f = _get_fixtures()
        holdings = [{"account_type": "taxable", "market_value": "100000"}]
        rec = check_inherited_ira_pacing(holdings, f["constraints"])
        self.assertIsNone(rec)

    def test_annual_target_reasonable(self):
        f = _get_fixtures()
        rec = check_inherited_ira_pacing(f["holdings"], f["constraints"], current_year=2025)
        if rec:
            self.assertGreater(rec.data["annual_target"], 0)
            self.assertLess(rec.data["annual_target"], rec.data["current_balance"])


# ── Rule 8: RMD Projection ──────────────────────────────────────────────────

class TestRMDProjection(unittest.TestCase):
    def test_with_real_data(self):
        f = _get_fixtures()
        rec = check_rmd_projection(f["holdings"], f["tax_profile"], f["rmd_divisors"])
        self.assertIsNotNone(rec)
        self.assertEqual(rec.rule_id, "COMP-RMD")
        self.assertGreater(rec.data["rmd_amount"], 0)
        self.assertEqual(len(rec.data["five_year_projection"]), 5)

    def test_no_trad_ira(self):
        f = _get_fixtures()
        holdings = [{"account_type": "roth_ira", "market_value": "100000"}]
        rec = check_rmd_projection(holdings, f["tax_profile"], f["rmd_divisors"])
        self.assertIsNone(rec)

    def test_young_person_no_rmd(self):
        f = _get_fixtures()
        profile = {"ages": [55, 52]}
        rec = check_rmd_projection(f["holdings"], profile, f["rmd_divisors"])
        self.assertIsNone(rec)

    def test_five_year_projections_decrease_divisor(self):
        f = _get_fixtures()
        rec = check_rmd_projection(f["holdings"], f["tax_profile"], f["rmd_divisors"])
        if rec:
            proj = rec.data["five_year_projection"]
            divisors = [p["divisor"] for p in proj]
            # Each year's divisor should be <= previous
            for i in range(1, len(divisors)):
                self.assertLessEqual(divisors[i], divisors[i - 1])


# ── Full Engine ──────────────────────────────────────────────────────────────

class TestFullEngine(unittest.TestCase):
    def test_generates_recommendations(self):
        f = _get_fixtures()
        recs = generate_all_recommendations(
            f["holdings"], f["tax_profile"], f["constraints"],
            f["cashflow"], f["rmd_divisors"], current_year=2025
        )
        self.assertGreaterEqual(len(recs), 3)

    def test_sorted_by_severity(self):
        f = _get_fixtures()
        recs = generate_all_recommendations(
            f["holdings"], f["tax_profile"], f["constraints"],
            f["cashflow"], f["rmd_divisors"], current_year=2025
        )
        severity_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
        for i in range(1, len(recs)):
            prev = severity_order.get(recs[i - 1].severity, 9)
            curr = severity_order.get(recs[i].severity, 9)
            self.assertLessEqual(prev, curr,
                f"Rec {recs[i-1].rule_id} ({recs[i-1].severity}) should come before "
                f"{recs[i].rule_id} ({recs[i].severity})")

    def test_all_recs_have_required_fields(self):
        f = _get_fixtures()
        recs = generate_all_recommendations(
            f["holdings"], f["tax_profile"], f["constraints"],
            f["cashflow"], f["rmd_divisors"], current_year=2025
        )
        for rec in recs:
            self.assertTrue(rec.rule_id)
            self.assertIn(rec.category, ("tax", "risk", "income", "withdrawal", "compliance", "info"))
            self.assertIn(rec.severity, ("high", "medium", "low", "info"))
            self.assertTrue(rec.title)
            self.assertTrue(rec.description)
            self.assertTrue(rec.action)
            self.assertIsInstance(rec.data, dict)

    def test_to_dict_serializable(self):
        f = _get_fixtures()
        recs = generate_all_recommendations(
            f["holdings"], f["tax_profile"], f["constraints"],
            f["cashflow"], f["rmd_divisors"], current_year=2025
        )
        for rec in recs:
            d = rec.to_dict()
            # Should be JSON-serializable
            json_str = json.dumps(d)
            self.assertIsInstance(json_str, str)

    def test_resilient_to_bad_data(self):
        """Engine should not crash even with incomplete data."""
        recs = generate_all_recommendations(
            holdings=[{"account_type": "taxable", "ticker": "VTI", "market_value": "100000"}],
            tax_profile={"ages": [72, 70], "ss_combined_annual": 56000,
                          "effective_standard_deduction": 33100},
            constraints={},
            cashflow=[],
            rmd_divisors={"divisors": {}},
            current_year=2025,
        )
        # Should complete without exception — some rules return None, some may error
        self.assertIsInstance(recs, list)

    def test_unique_rule_ids(self):
        f = _get_fixtures()
        recs = generate_all_recommendations(
            f["holdings"], f["tax_profile"], f["constraints"],
            f["cashflow"], f["rmd_divisors"], current_year=2025
        )
        rule_ids = [r.rule_id for r in recs if r.rule_id != "ERR"]
        self.assertEqual(len(rule_ids), len(set(rule_ids)), "Duplicate rule IDs found")


if __name__ == "__main__":
    unittest.main()
