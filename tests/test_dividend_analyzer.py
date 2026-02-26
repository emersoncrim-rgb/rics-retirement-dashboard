"""
test_dividend_analyzer.py – Tests for dividend_analyzer module (unittest-based)

Coverage:
- Tax treatment classification
- Income projection math
- Portfolio income analysis (totals, categories, yields, coverage)
- Dividend upgrade identification
- Edge cases (zero values, missing fields, empty inputs)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dividend_analyzer import (
    classify_tax_treatment,
    project_income,
    analyze_holdings,
    find_upgrade_opportunities,
    load_holdings_from_csv,
    DividendSummary,
    PortfolioIncomeSummary,
    UpgradeOpportunity,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

SAMPLE_HOLDINGS = [
    {
        "account_id": "TAX1", "account_type": "taxable", "ticker": "VTI",
        "asset_class": "us_equity", "market_value": "324000", "cost_basis": "280000",
        "unrealized_gain": "44000", "qualified_div_yield": "0.013",
        "annual_income_est": "4212", "shares": "1200", "price": "270",
    },
    {
        "account_id": "TAX1", "account_type": "taxable", "ticker": "AAPL",
        "asset_class": "us_equity", "market_value": "262500", "cost_basis": "21500",
        "unrealized_gain": "241000", "qualified_div_yield": "0.0044",
        "annual_income_est": "1155", "shares": "1050", "price": "250",
    },
    {
        "account_id": "TAX1", "account_type": "taxable", "ticker": "BND",
        "asset_class": "us_bond", "market_value": "57600", "cost_basis": "59000",
        "unrealized_gain": "-1400", "qualified_div_yield": "0.033",
        "annual_income_est": "1900.80", "shares": "800", "price": "72",
    },
    {
        "account_id": "TAX1", "account_type": "taxable", "ticker": "VMFXX",
        "asset_class": "mmf", "market_value": "228700", "cost_basis": "228700",
        "unrealized_gain": "0", "qualified_div_yield": "0.05",
        "annual_income_est": "11435", "shares": "228700", "price": "1",
    },
    {
        "account_id": "IRA1", "account_type": "trad_ira", "ticker": "VTI",
        "asset_class": "us_equity", "market_value": "216000", "cost_basis": "195000",
        "unrealized_gain": "21000", "qualified_div_yield": "0.013",
        "annual_income_est": "2808", "shares": "800", "price": "270",
    },
    {
        "account_id": "IRA1", "account_type": "trad_ira", "ticker": "NVDA",
        "asset_class": "us_equity", "market_value": "81000", "cost_basis": "45000",
        "unrealized_gain": "36000", "qualified_div_yield": "0.0003",
        "annual_income_est": "24.30", "shares": "600", "price": "135",
    },
    {
        "account_id": "ROTH1", "account_type": "roth_ira", "ticker": "SCHD",
        "asset_class": "us_equity", "market_value": "50000", "cost_basis": "40000",
        "unrealized_gain": "10000", "qualified_div_yield": "0.035",
        "annual_income_est": "1750", "shares": "610", "price": "82",
    },
]

CONSTRAINTS = {
    "concentration_limits": {
        "single_stock_max_pct": 0.30,
        "aapl_flag": {
            "ticker": "AAPL",
            "strategy": "do_not_sell_unless_offset_by_losses",
        }
    }
}


class TestTaxTreatment(unittest.TestCase):
    def test_roth_is_tax_free(self):
        self.assertEqual(classify_tax_treatment("roth_ira", True), "tax-free")
        self.assertEqual(classify_tax_treatment("roth_ira", False), "tax-free")

    def test_trad_ira_is_deferred(self):
        self.assertEqual(classify_tax_treatment("trad_ira", True), "tax-deferred")

    def test_inherited_ira_is_deferred(self):
        self.assertEqual(classify_tax_treatment("inherited_ira", False), "tax-deferred")

    def test_employer_plan_is_deferred(self):
        self.assertEqual(classify_tax_treatment("employer_plan", True), "tax-deferred")

    def test_taxable_qualified(self):
        self.assertEqual(classify_tax_treatment("taxable", True), "taxable-qualified")

    def test_taxable_ordinary(self):
        self.assertEqual(classify_tax_treatment("taxable", False), "taxable-ordinary")


class TestProjectIncome(unittest.TestCase):
    def test_no_growth(self):
        self.assertEqual(project_income(1000, 0.0, 10), 1000.0)

    def test_5yr_6pct(self):
        result = project_income(1000, 0.06, 5)
        self.assertAlmostEqual(result, 1338.23, places=1)

    def test_10yr_6pct(self):
        result = project_income(1000, 0.06, 10)
        self.assertAlmostEqual(result, 1790.85, places=1)

    def test_zero_income(self):
        self.assertEqual(project_income(0, 0.06, 10), 0.0)

    def test_zero_years(self):
        self.assertEqual(project_income(1000, 0.06, 0), 1000.0)

    def test_negative_growth(self):
        result = project_income(1000, -0.05, 5)
        self.assertTrue(result < 1000)


class TestAnalyzeHoldings(unittest.TestCase):
    def test_basic_analysis(self):
        result = analyze_holdings(SAMPLE_HOLDINGS)
        self.assertIsInstance(result, PortfolioIncomeSummary)
        self.assertGreater(result.total_annual_income, 0)
        self.assertGreater(result.total_market_value, 0)
        self.assertEqual(len(result.holdings), len(SAMPLE_HOLDINGS))

    def test_income_categories(self):
        result = analyze_holdings(SAMPLE_HOLDINGS)
        self.assertGreater(result.taxable_qualified_income, 0)
        self.assertGreater(result.tax_deferred_income, 0)
        self.assertGreater(result.tax_free_income, 0)

    def test_weighted_yield(self):
        result = analyze_holdings(SAMPLE_HOLDINGS)
        self.assertGreater(result.weighted_avg_yield, 0.005)
        self.assertLess(result.weighted_avg_yield, 0.10)

    def test_coverage_ratio(self):
        result = analyze_holdings(SAMPLE_HOLDINGS, annual_expenses=90000)
        self.assertGreater(result.income_coverage_ratio, 0)
        self.assertLess(result.income_coverage_ratio, 1)

    def test_projections_grow(self):
        result = analyze_holdings(SAMPLE_HOLDINGS)
        equity_holding = [h for h in result.holdings if h.asset_class == "us_equity"][0]
        self.assertGreater(equity_holding.projected_income_5y, equity_holding.annual_income)

    def test_custom_growth_rates(self):
        custom = {"us_equity": 0.10, "us_bond": 0.02}
        result = analyze_holdings(SAMPLE_HOLDINGS, div_growth_overrides=custom)
        vti = [h for h in result.holdings if h.ticker == "VTI" and h.account_type == "taxable"][0]
        self.assertEqual(vti.growth_rate, 0.10)

    def test_empty_holdings(self):
        result = analyze_holdings([])
        self.assertEqual(result.total_annual_income, 0)
        self.assertEqual(result.weighted_avg_yield, 0)

    def test_zero_expense(self):
        result = analyze_holdings(SAMPLE_HOLDINGS, annual_expenses=0)
        self.assertEqual(result.income_coverage_ratio, 0)

    def test_yield_back_calculated(self):
        h = [{"account_id": "T", "account_type": "taxable", "ticker": "XYZ",
              "asset_class": "us_equity", "market_value": "100000",
              "qualified_div_yield": "0", "annual_income_est": "3000"}]
        result = analyze_holdings(h)
        self.assertAlmostEqual(result.holdings[0].current_yield, 0.03, places=3)

    def test_income_calculated_from_yield(self):
        h = [{"account_id": "T", "account_type": "taxable", "ticker": "XYZ",
              "asset_class": "us_equity", "market_value": "100000",
              "qualified_div_yield": "0.025", "annual_income_est": "0"}]
        result = analyze_holdings(h)
        self.assertAlmostEqual(result.holdings[0].annual_income, 2500.0, places=0)

    def test_income_sums_match(self):
        result = analyze_holdings(SAMPLE_HOLDINGS)
        category_sum = (result.taxable_qualified_income + result.taxable_ordinary_income +
                        result.tax_deferred_income + result.tax_free_income)
        self.assertAlmostEqual(result.total_annual_income, category_sum, places=1)


class TestUpgradeOpportunities(unittest.TestCase):
    def test_finds_ira_upgrades(self):
        opps = find_upgrade_opportunities(SAMPLE_HOLDINGS, CONSTRAINTS)
        nvda_opps = [o for o in opps if o.current_ticker == "NVDA"]
        self.assertGreater(len(nvda_opps), 0)

    def test_ira_swaps_are_feasible(self):
        opps = find_upgrade_opportunities(SAMPLE_HOLDINGS, CONSTRAINTS)
        ira_opps = [o for o in opps if o.account_type == "trad_ira"]
        feasible_ira = [o for o in ira_opps if o.feasible]
        self.assertGreater(len(feasible_ira), 0)

    def test_aapl_not_feasible(self):
        opps = find_upgrade_opportunities(SAMPLE_HOLDINGS, CONSTRAINTS)
        aapl_opps = [o for o in opps if o.current_ticker == "AAPL"]
        self.assertTrue(all(not o.feasible for o in aapl_opps))

    def test_taxable_with_gains_not_feasible(self):
        opps = find_upgrade_opportunities(SAMPLE_HOLDINGS, CONSTRAINTS)
        vti_tax = [o for o in opps if o.current_ticker == "VTI" and o.account_type == "taxable"]
        self.assertTrue(all(not o.feasible for o in vti_tax))

    def test_income_increase_positive(self):
        opps = find_upgrade_opportunities(SAMPLE_HOLDINGS, CONSTRAINTS)
        for o in opps:
            self.assertGreater(o.income_increase, 0)

    def test_sorted_feasible_first(self):
        opps = find_upgrade_opportunities(SAMPLE_HOLDINGS, CONSTRAINTS)
        if len(opps) > 1:
            first_infeasible = None
            for i, o in enumerate(opps):
                if not o.feasible:
                    first_infeasible = i
                    break
            if first_infeasible is not None:
                self.assertTrue(all(opps[j].feasible for j in range(first_infeasible)))

    def test_small_positions_skipped(self):
        small = [{"account_id": "T", "account_type": "trad_ira", "ticker": "TINY",
                  "asset_class": "us_equity", "market_value": "1000",
                  "qualified_div_yield": "0.001", "annual_income_est": "1",
                  "unrealized_gain": "0"}]
        opps = find_upgrade_opportunities(small, CONSTRAINTS)
        self.assertEqual(len(opps), 0)

    def test_no_constraints(self):
        opps = find_upgrade_opportunities(SAMPLE_HOLDINGS, {})
        self.assertGreater(len(opps), 0)


class TestLoadHoldings(unittest.TestCase):
    def test_load_sample(self):
        path = os.path.join(DATA_DIR, "accounts_snapshot.csv")
        if not os.path.exists(path):
            self.skipTest("Sample data not available")
        rows = load_holdings_from_csv(path)
        self.assertEqual(len(rows), 16)
        self.assertIn("ticker", rows[0])


if __name__ == "__main__":
    unittest.main()
