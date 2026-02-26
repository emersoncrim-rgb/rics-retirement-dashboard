"""
test_broker_import.py – Tests for broker_import module (unittest-based)

Coverage:
- Currency parsing edge cases
- Asset class inference
- Account type inference
- Broker detection from headers
- CSV parsing (Fidelity, Schwab, Vanguard, generic, auto)
- Holdings merge logic
- Round-trip CSV export/load
- Edge cases: empty input, totals rows, missing values
"""

import os
import sys
import unittest
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from broker_import import (
    clean_currency,
    infer_asset_class,
    infer_account_type,
    detect_broker,
    parse_broker_csv,
    holdings_to_csv,
    load_accounts_snapshot,
    merge_holdings,
    HoldingRow,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# ── Sample broker CSVs ───────────────────────────────────────────────────────

FIDELITY_CSV = """Symbol,Quantity,Last Price,Current Value,Cost Basis Total,Account Name/Number
AAPL,100,$250.00,"$25,000.00","$5,000.00",Individual Brokerage
VTI,200,$270.00,"$54,000.00","$48,000.00",Individual Brokerage
VMFXX,10000,$1.00,"$10,000.00","$10,000.00",Rollover IRA
BND,300,$72.00,"$21,600.00","$22,000.00",Rollover IRA
"""

SCHWAB_CSV = """Symbol,Quantity,Price,Market Value,Cost Basis,Account
SCHD,500,$82.00,"$41,000.00","$35,000.00",Roth IRA
VXUS,300,$58.00,"$17,400.00","$16,000.00",Joint Brokerage
"""

GENERIC_CSV = """ticker,shares,price,market_value,cost_basis,account_id
VTI,100,270,27000,25000,TAXABLE
BND,200,72,14400,14000,TRAD_IRA
"""


class TestCleanCurrency(unittest.TestCase):
    def test_basic_number(self):
        self.assertEqual(clean_currency("1234.56"), 1234.56)

    def test_dollar_sign(self):
        self.assertEqual(clean_currency("$1,234.56"), 1234.56)

    def test_negative_parens(self):
        self.assertEqual(clean_currency("($500.00)"), -500.0)

    def test_negative_dash(self):
        self.assertEqual(clean_currency("-$1,000"), -1000.0)

    def test_empty(self):
        self.assertEqual(clean_currency(""), 0.0)

    def test_na(self):
        self.assertEqual(clean_currency("n/a"), 0.0)

    def test_dashes(self):
        self.assertEqual(clean_currency("--"), 0.0)

    def test_whitespace(self):
        self.assertEqual(clean_currency("  $  1,500.00  "), 1500.0)

    def test_none_like(self):
        self.assertEqual(clean_currency("N/A"), 0.0)


class TestInferAssetClass(unittest.TestCase):
    def test_known_us_equity(self):
        self.assertEqual(infer_asset_class("VTI"), "us_equity")

    def test_known_intl(self):
        self.assertEqual(infer_asset_class("VXUS"), "intl_equity")

    def test_known_bond(self):
        self.assertEqual(infer_asset_class("BND"), "us_bond")

    def test_known_mmf(self):
        self.assertEqual(infer_asset_class("VMFXX"), "mmf")

    def test_unknown_defaults_equity(self):
        self.assertEqual(infer_asset_class("TSLA"), "us_equity")

    def test_case_insensitive(self):
        self.assertEqual(infer_asset_class("vti"), "us_equity")

    def test_short_term_treasury(self):
        self.assertEqual(infer_asset_class("VGSH"), "us_bond")


class TestInferAccountType(unittest.TestCase):
    def test_roth(self):
        self.assertEqual(infer_account_type("My Roth IRA"), "roth_ira")

    def test_traditional(self):
        self.assertEqual(infer_account_type("Traditional IRA"), "trad_ira")

    def test_rollover(self):
        self.assertEqual(infer_account_type("Rollover IRA"), "trad_ira")

    def test_inherited(self):
        self.assertEqual(infer_account_type("Inherited IRA"), "inherited_ira")

    def test_brokerage(self):
        self.assertEqual(infer_account_type("Joint Brokerage"), "taxable")

    def test_401k(self):
        self.assertEqual(infer_account_type("401k Plan"), "employer_plan")

    def test_unknown(self):
        self.assertEqual(infer_account_type("Mystery Account"), "taxable")

    def test_individual(self):
        self.assertEqual(infer_account_type("Individual Brokerage"), "taxable")


class TestDetectBroker(unittest.TestCase):
    def test_fidelity(self):
        headers = ["Symbol", "Quantity", "Last Price", "Current Value", "Account Name/Number"]
        self.assertEqual(detect_broker(headers), "fidelity")

    def test_schwab(self):
        headers = ["Symbol", "Quantity", "Price", "Market Value", "Account"]
        self.assertEqual(detect_broker(headers), "schwab")

    def test_vanguard(self):
        headers = ["Symbol", "Shares", "Share Price", "Total Value", "Account Number"]
        self.assertEqual(detect_broker(headers), "vanguard")

    def test_generic(self):
        headers = ["ticker", "shares", "price"]
        self.assertEqual(detect_broker(headers), "generic")


class TestParseBrokerCSV(unittest.TestCase):
    def test_fidelity_parse(self):
        rows = parse_broker_csv(FIDELITY_CSV, broker="fidelity", snapshot_date="2025-06-01")
        self.assertEqual(len(rows), 4)
        aapl = [r for r in rows if r.ticker == "AAPL"][0]
        self.assertEqual(aapl.shares, 100)
        self.assertEqual(aapl.price, 250.0)
        self.assertEqual(aapl.market_value, 25000.0)
        self.assertEqual(aapl.cost_basis, 5000.0)
        self.assertEqual(aapl.unrealized_gain, 20000.0)
        self.assertEqual(aapl.asset_class, "us_equity")

    def test_fidelity_account_type_inference(self):
        rows = parse_broker_csv(FIDELITY_CSV, broker="fidelity")
        brokerage = [r for r in rows if r.ticker == "AAPL"][0]
        ira = [r for r in rows if r.ticker == "VMFXX"][0]
        self.assertEqual(brokerage.account_type, "taxable")
        self.assertEqual(ira.account_type, "trad_ira")

    def test_schwab_parse(self):
        rows = parse_broker_csv(SCHWAB_CSV, broker="schwab", snapshot_date="2025-06-01")
        self.assertEqual(len(rows), 2)
        schd = [r for r in rows if r.ticker == "SCHD"][0]
        self.assertEqual(schd.market_value, 41000.0)
        self.assertEqual(schd.account_type, "roth_ira")

    def test_generic_parse(self):
        rows = parse_broker_csv(GENERIC_CSV, broker="generic", snapshot_date="2025-06-01")
        self.assertEqual(len(rows), 2)

    def test_auto_detect(self):
        rows = parse_broker_csv(FIDELITY_CSV, broker="auto")
        self.assertEqual(len(rows), 4)

    def test_account_type_override(self):
        rows = parse_broker_csv(GENERIC_CSV, account_type_override="roth_ira")
        self.assertTrue(all(r.account_type == "roth_ira" for r in rows))

    def test_empty_csv(self):
        rows = parse_broker_csv("")
        self.assertEqual(rows, [])

    def test_skips_totals(self):
        csv_with_total = FIDELITY_CSV + 'TOTAL,,,,"$110,600.00",\n'
        rows = parse_broker_csv(csv_with_total, broker="fidelity")
        self.assertFalse(any(r.ticker == "TOTAL" for r in rows))

    def test_calculates_missing_value(self):
        csv_no_value = """ticker,shares,price,cost_basis,account_id
VTI,100,270,25000,TAXABLE"""
        rows = parse_broker_csv(csv_no_value, broker="generic")
        self.assertEqual(rows[0].market_value, 27000.0)

    def test_notes_include_broker(self):
        rows = parse_broker_csv(FIDELITY_CSV, broker="fidelity")
        self.assertIn("fidelity", rows[0].notes)

    def test_snapshot_date_default(self):
        rows = parse_broker_csv(FIDELITY_CSV, broker="fidelity")
        self.assertTrue(len(rows[0].snapshot_date) == 10)  # YYYY-MM-DD format


class TestHoldingsCSV(unittest.TestCase):
    def test_roundtrip(self):
        rows = parse_broker_csv(FIDELITY_CSV, broker="fidelity", snapshot_date="2025-06-01")
        csv_out = holdings_to_csv(rows)
        self.assertIn("AAPL", csv_out)
        self.assertIn("snapshot_date", csv_out)

    def test_empty_holdings(self):
        self.assertEqual(holdings_to_csv([]), "")

    def test_roundtrip_to_file(self):
        rows = parse_broker_csv(FIDELITY_CSV, broker="fidelity", snapshot_date="2025-06-01")
        csv_out = holdings_to_csv(rows)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_out)
            f.flush()
            loaded = load_accounts_snapshot(f.name)
        os.unlink(f.name)
        self.assertEqual(len(loaded), len(rows))
        self.assertEqual(loaded[0].ticker, rows[0].ticker)


class TestLoadSnapshot(unittest.TestCase):
    def test_load_sample_data(self):
        path = os.path.join(DATA_DIR, "accounts_snapshot.csv")
        if not os.path.exists(path):
            self.skipTest("Sample data not available")
        rows = load_accounts_snapshot(path)
        self.assertEqual(len(rows), 16)
        self.assertEqual(rows[0].ticker, "AAPL")
        self.assertEqual(rows[0].shares, 1050)

    def test_load_preserves_types(self):
        path = os.path.join(DATA_DIR, "accounts_snapshot.csv")
        if not os.path.exists(path):
            self.skipTest("Sample data not available")
        rows = load_accounts_snapshot(path)
        self.assertIsInstance(rows[0].shares, float)
        self.assertIsInstance(rows[0].top1_pct, bool)


class TestMergeHoldings(unittest.TestCase):
    def test_merge_overwrites(self):
        existing = [HoldingRow("2025-01-01", "A1", "taxable", "Acct", "VTI", "us_equity",
                               100, 270, 27000, 25000, 2000)]
        imported = [HoldingRow("2025-06-01", "A1", "taxable", "Acct", "VTI", "us_equity",
                               120, 275, 33000, 25000, 8000)]
        merged = merge_holdings(existing, imported)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].shares, 120)
        self.assertEqual(merged[0].snapshot_date, "2025-06-01")

    def test_merge_appends_new(self):
        existing = [HoldingRow("2025-01-01", "A1", "taxable", "Acct", "VTI", "us_equity",
                               100, 270, 27000, 25000, 2000)]
        imported = [HoldingRow("2025-06-01", "A1", "taxable", "Acct", "BND", "us_bond",
                               200, 72, 14400, 14000, 400)]
        merged = merge_holdings(existing, imported)
        self.assertEqual(len(merged), 2)

    def test_merge_empty_existing(self):
        imported = [HoldingRow("2025-06-01", "A1", "taxable", "Acct", "VTI", "us_equity",
                               100, 270, 27000, 25000, 2000)]
        merged = merge_holdings([], imported)
        self.assertEqual(len(merged), 1)

    def test_merge_empty_imported(self):
        existing = [HoldingRow("2025-01-01", "A1", "taxable", "Acct", "VTI", "us_equity",
                               100, 270, 27000, 25000, 2000)]
        merged = merge_holdings(existing, [])
        self.assertEqual(len(merged), 1)


if __name__ == "__main__":
    unittest.main()
