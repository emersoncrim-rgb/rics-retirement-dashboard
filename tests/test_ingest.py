"""
test_ingest.py — Unit tests for ingest.py

Run:  python -m pytest tests/test_ingest.py -v
  or: python tests/test_ingest.py          (unittest fallback)
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ingest import (
    ingest_all,
    compute_today_summary,
    load_accounts,
    load_trade_log,
    load_cashflow,
    load_tax_profile,
    load_constraints,
    validate_accounts,
    validate_trades,
    validate_cashflow,
    validate_tax_profile,
    validate_constraints,
)

DATA_DIR = PROJECT_ROOT / "data"

PATHS = {
    "accounts": str(DATA_DIR / "accounts_snapshot.csv"),
    "trades": str(DATA_DIR / "trade_log.csv"),
    "cashflow": str(DATA_DIR / "cashflow_plan.csv"),
    "tax_profile": str(DATA_DIR / "tax_profile.json"),
    "constraints": str(DATA_DIR / "constraints.json"),
}


# ══════════════════════════════════════════════════════════════════════════
# Individual loader tests
# ══════════════════════════════════════════════════════════════════════════

class TestLoadAccounts(unittest.TestCase):

    def test_returns_list_of_dicts(self):
        rows = load_accounts(PATHS["accounts"])
        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 0)
        self.assertIsInstance(rows[0], dict)

    def test_numeric_coercion(self):
        rows = load_accounts(PATHS["accounts"])
        for r in rows:
            self.assertIsInstance(r["market_value"], float)
            self.assertIsInstance(r["shares"], float)
            self.assertIsInstance(r["price"], float)

    def test_price_override_recomputes_fields(self):
        # Use a tiny synthetic row written to a temp CSV
        import csv as _csv
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "accounts_snapshot.csv"
            fieldnames = ["snapshot_date","account_id","account_type","ticker","asset_class","shares","price","market_value","cost_basis","unrealized_gain","top1_pct","qualified_div_yield","annual_income_est"]
            row = {
                "snapshot_date":"2026-01-01",
                "account_id":"TAX",
                "account_type":"taxable",
                "ticker":"AAPL",
                "asset_class":"us_equity",
                "shares":"10",
                "price":"100",
                "market_value":"1000",
                "cost_basis":"800",
                "unrealized_gain":"200",
                "top1_pct":"true",
                "qualified_div_yield":"0.02",
                "annual_income_est":"20",
            }
            with open(p, "w", newline="") as f:
                w = _csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerow(row)

            rows = load_accounts(str(p), price_overrides={"AAPL": 150.0})
            self.assertEqual(rows[0]["price"], 150.0)
            self.assertEqual(rows[0]["market_value"], 1500.0)
            self.assertEqual(rows[0]["unrealized_gain"], 700.0)
            self.assertAlmostEqual(rows[0]["annual_income_est"], 30.0, places=6)

    def test_top1_pct_is_bool(self):
        rows = load_accounts(PATHS["accounts"])
        aapl = [r for r in rows if r["ticker"] == "AAPL"][0]
        self.assertIs(aapl["top1_pct"], True)
        vti = [r for r in rows if r["ticker"] == "VTI"][0]
        self.assertIs(vti["top1_pct"], False)


class TestLoadTradeLog(unittest.TestCase):

    def test_returns_list(self):
        rows = load_trade_log(PATHS["trades"])
        self.assertGreater(len(rows), 0)

    def test_numeric_coercion(self):
        rows = load_trade_log(PATHS["trades"])
        for r in rows:
            self.assertIsInstance(r["shares"], float)
            self.assertIsInstance(r["total_amount"], float)


class TestLoadCashflow(unittest.TestCase):

    def test_year_is_int(self):
        rows = load_cashflow(PATHS["cashflow"])
        for r in rows:
            self.assertIsInstance(r["year"], int)

    def test_inflation_adj_is_bool(self):
        rows = load_cashflow(PATHS["cashflow"])
        ss = [r for r in rows if r["subcategory"] == "social_security"][0]
        self.assertIs(ss["inflation_adj"], True)


# ══════════════════════════════════════════════════════════════════════════
# Validation tests
# ══════════════════════════════════════════════════════════════════════════

class TestValidation(unittest.TestCase):

    def test_accounts_pass_clean(self):
        rows = load_accounts(PATHS["accounts"])
        flags = validate_accounts(rows)
        self.assertEqual(flags, [], f"Unexpected flags: {flags}")

    def test_trades_pass_clean(self):
        rows = load_trade_log(PATHS["trades"])
        flags = validate_trades(rows)
        self.assertEqual(flags, [], f"Unexpected flags: {flags}")

    def test_cashflow_pass_clean(self):
        rows = load_cashflow(PATHS["cashflow"])
        flags = validate_cashflow(rows)
        self.assertEqual(flags, [], f"Unexpected flags: {flags}")

    def test_tax_profile_pass_clean(self):
        tp = load_tax_profile(PATHS["tax_profile"])
        flags = validate_tax_profile(tp)
        self.assertEqual(flags, [], f"Unexpected flags: {flags}")

    def test_constraints_pass_clean(self):
        c = load_constraints(PATHS["constraints"])
        flags = validate_constraints(c)
        self.assertEqual(flags, [], f"Unexpected flags: {flags}")

    def test_bad_account_type_flagged(self):
        """Inject a bad account_type and verify it's caught."""
        rows = load_accounts(PATHS["accounts"])
        rows[0]["account_type"] = "checking"  # invalid
        flags = validate_accounts(rows)
        self.assertTrue(any("ACCT_TYPE_INVALID" in f for f in flags))

    def test_mv_mismatch_flagged(self):
        """Inject a market_value mismatch and verify it's caught."""
        rows = load_accounts(PATHS["accounts"])
        rows[0]["market_value"] = 999999.0  # way off
        flags = validate_accounts(rows)
        self.assertTrue(any("ACCT_MV_MISMATCH" in f for f in flags))

    def test_bad_trade_action_flagged(self):
        rows = load_trade_log(PATHS["trades"])
        rows[0]["action"] = "short_sell"
        flags = validate_trades(rows)
        self.assertTrue(any("TRADE_ACTION_INVALID" in f for f in flags))

    def test_empty_cashflow_flagged(self):
        flags = validate_cashflow([])
        self.assertTrue(any("CF_EMPTY" in f for f in flags))

    def test_missing_tax_keys_flagged(self):
        flags = validate_tax_profile({"filing_status": "mfj"})
        self.assertTrue(any("TAX_KEYS_MISSING" in f for f in flags))


# ══════════════════════════════════════════════════════════════════════════
# ingest_all integration tests
# ══════════════════════════════════════════════════════════════════════════

class TestIngestAll(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.result = ingest_all(PATHS)

    def test_returns_expected_keys(self):
        expected = {"accounts", "trades", "cashflow", "tax_profile",
                    "constraints", "totals", "flags"}
        self.assertEqual(set(self.result.keys()), expected)

    def test_no_flags_on_clean_data(self):
        self.assertEqual(self.result["flags"], [],
                         f"Unexpected flags: {self.result['flags']}")

    def test_totals_taxable(self):
        by_type = self.result["totals"]["by_account_type"]
        self.assertAlmostEqual(by_type["taxable"], 1_030_000, delta=100)

    def test_totals_trad_ira(self):
        by_type = self.result["totals"]["by_account_type"]
        self.assertAlmostEqual(by_type["trad_ira"], 1_000_000, delta=100)

    def test_totals_inherited_ira(self):
        by_type = self.result["totals"]["by_account_type"]
        self.assertAlmostEqual(by_type["inherited_ira"], 85_000, delta=100)

    def test_total_portfolio(self):
        self.assertAlmostEqual(self.result["totals"]["total_portfolio"],
                               2_115_000, delta=500)

    def test_accounts_row_count(self):
        self.assertEqual(len(self.result["accounts"]), 16)

    def test_trades_row_count(self):
        self.assertGreaterEqual(len(self.result["trades"]), 5)

    def test_file_not_found_raises(self):
        bad_paths = {**PATHS, "accounts": "/nonexistent/file.csv"}
        with self.assertRaises(FileNotFoundError):
            ingest_all(bad_paths)


# ══════════════════════════════════════════════════════════════════════════
# compute_today_summary tests
# ══════════════════════════════════════════════════════════════════════════

class TestComputeTodaySummary(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ingested = ingest_all(PATHS)
        cls.summary = compute_today_summary(cls.ingested)

    def test_total_portfolio_matches(self):
        self.assertAlmostEqual(self.summary["total_portfolio"], 2_115_000, delta=500)

    def test_allocation_sums_to_100(self):
        total_pct = (self.summary["equity_pct"]
                     + self.summary["bond_pct"]
                     + self.summary["cash_pct"])
        self.assertAlmostEqual(total_pct, 1.0, places=2)

    def test_equity_pct_reasonable(self):
        """72yo couple: equity should be 40-65% of portfolio."""
        self.assertGreater(self.summary["equity_pct"], 0.40)
        self.assertLess(self.summary["equity_pct"], 0.65)

    def test_bond_pct_reasonable(self):
        self.assertGreater(self.summary["bond_pct"], 0.10)
        self.assertLess(self.summary["bond_pct"], 0.30)

    def test_cash_pct_reasonable(self):
        self.assertGreater(self.summary["cash_pct"], 0.15)
        self.assertLess(self.summary["cash_pct"], 0.30)

    def test_top1_is_vti(self):
        """VTI is held in both taxable and IRA, so should be largest by value."""
        self.assertEqual(self.summary["top1_holding"]["ticker"], "VTI")

    def test_top5_has_five_entries(self):
        self.assertEqual(len(self.summary["top5_holdings"]), 5)

    def test_top1_pct_below_30(self):
        """No single holding should exceed 30% of total portfolio."""
        self.assertLess(self.summary["top1_holding"]["pct"], 0.30)

    def test_income_ss(self):
        inc = self.summary["income_summary"]
        self.assertEqual(inc["social_security"], 56_000)

    def test_income_qualified_divs(self):
        inc = self.summary["income_summary"]
        self.assertEqual(inc["qualified_dividends"], 6_700)

    def test_income_total_portfolio_income(self):
        """Portfolio income = qualified + ordinary divs = 6700 + 3300 = 10000."""
        inc = self.summary["income_summary"]
        self.assertEqual(inc["total_portfolio_income"], 10_000)

    def test_income_total(self):
        """Total income = SS 56k + portfolio 10k = 66k."""
        inc = self.summary["income_summary"]
        self.assertEqual(inc["total_income"], 66_000)

    def test_annual_expenses(self):
        """Annual recurring expenses = 50k + 15k + 10k + 8k + 7k = 90k."""
        self.assertEqual(self.summary["annual_expenses"], 90_000)

    def test_cash_reserve_months_positive(self):
        """Cash reserve should cover at least 6 months of expenses."""
        self.assertGreater(self.summary["cash_reserve_months"], 6)

    def test_cash_reserve_months_value(self):
        """~$451k cash / $7,500/mo = ~60 months."""
        self.assertGreater(self.summary["cash_reserve_months"], 50)
        self.assertLess(self.summary["cash_reserve_months"], 70)

    def test_account_totals_has_three_accounts(self):
        self.assertEqual(len(self.summary["account_totals"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
