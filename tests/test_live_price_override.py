import unittest
from live_overlay import apply_price_overrides


class TestLivePriceOverride(unittest.TestCase):

    def test_override_recomputes_market_value(self):
        accounts = [{
            "ticker": "AAPL",
            "shares": 10,
            "price": 100.0,
            "market_value": 1000.0,
            "cost_basis": 800.0,
            "unrealized_gain": 200.0,
            "qualified_div_yield": 0.02,
        }]

        overrides = {"AAPL": 150.0}

        updated = apply_price_overrides(accounts, overrides)
        row = updated[0]

        assert row["price"] == 150.0
        assert row["market_value"] == 1500.0
        assert row["unrealized_gain"] == 700.0
        assert abs(row["annual_income_est"] - 30.0) < 1e-6


if __name__ == "__main__":
    unittest.main()
