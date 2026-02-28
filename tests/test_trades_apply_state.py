import unittest
from trades_apply_state import compute_new_trades


class TestTradeApplyState(unittest.TestCase):
    def test_compute_new_trades_all_new(self):
        trades = [{"id": 1}, {"id": 2}]
        new_trades, count = compute_new_trades(trades, 0)
        self.assertEqual(len(new_trades), 2)
        self.assertEqual(count, 2)

    def test_compute_new_trades_partial(self):
        trades = [{"id": 1}, {"id": 2}, {"id": 3}]
        new_trades, count = compute_new_trades(trades, 1)
        self.assertEqual(len(new_trades), 2)
        self.assertEqual(new_trades[0]["id"], 2)
        self.assertEqual(count, 3)

    def test_compute_new_trades_none_new(self):
        trades = [{"id": 1}]
        new_trades, count = compute_new_trades(trades, 1)
        self.assertEqual(len(new_trades), 0)
        self.assertEqual(count, 1)

    def test_compute_new_trades_clamping(self):
        trades = [1, 2, 3, 4, 5]
        new_trades, count = compute_new_trades(trades, 10)
        self.assertEqual(len(new_trades), 0)
        self.assertEqual(count, 5)


if __name__ == "__main__":
    unittest.main()
