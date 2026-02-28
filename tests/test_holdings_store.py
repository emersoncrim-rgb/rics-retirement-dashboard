import os
import tempfile
import unittest

from holdings_store import load_holdings, validate_holdings, save_holdings


class TestHoldingsStore(unittest.TestCase):
    def setUp(self):
        fd, self.temp_csv = tempfile.mkstemp(suffix=".csv")
        os.close(fd)

        with open(self.temp_csv, "w", newline="", encoding="utf-8") as f:
            f.write("account_id,ticker,shares,unrelated_col\n")
            f.write("acct1,AAPL,100,keep_me\n")
            f.write("acct2,MSFT,50.5,keep_me_too\n")

    def tearDown(self):
        if os.path.exists(self.temp_csv):
            os.remove(self.temp_csv)

    def test_load_holdings(self):
        rows, fieldnames = load_holdings(self.temp_csv)
        self.assertEqual(fieldnames, ["account_id", "ticker", "shares", "unrelated_col"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["ticker"], "AAPL")
        self.assertEqual(rows[0]["unrelated_col"], "keep_me")

    def test_validate_holdings_valid(self):
        rows, _ = load_holdings(self.temp_csv)
        errors = validate_holdings(rows)
        self.assertEqual(len(errors), 0)

    def test_validate_holdings_negative_shares(self):
        rows = [{"ticker": "AAPL", "shares": "-10"}]
        errors = validate_holdings(rows)
        self.assertEqual(len(errors), 1)
        self.assertIn("negative", errors[0])

    def test_validate_holdings_invalid_ticker(self):
        rows = [{"ticker": "AA PL", "shares": "10"}]
        errors = validate_holdings(rows)
        self.assertEqual(len(errors), 1)
        self.assertIn("spaces", errors[0])

    def test_save_holdings_atomic_preserves(self):
        rows, fieldnames = load_holdings(self.temp_csv)

        rows[0]["shares"] = "150"

        ok, errors = save_holdings(self.temp_csv, rows, fieldnames)
        self.assertTrue(ok)
        self.assertEqual(len(errors), 0)

        new_rows, new_fieldnames = load_holdings(self.temp_csv)
        self.assertEqual(new_fieldnames, fieldnames)
        self.assertEqual(new_rows[0]["shares"], "150")
        self.assertEqual(new_rows[0]["unrelated_col"], "keep_me")


if __name__ == "__main__":
    unittest.main()
