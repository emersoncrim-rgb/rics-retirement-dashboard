"""
Step 1 Tests: Validate CSV and JSON data templates.
Run with: pytest tests/test_step1_schemas.py -v
"""
import csv
import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


# ── accounts_snapshot.csv ────────────────────────────────────────────────

class TestAccountsSnapshot:
    def _load(self):
        with open(DATA_DIR / "accounts_snapshot.csv") as f:
            return list(csv.DictReader(f))

    def test_account_totals_match(self):
        """Taxable ~$1,030,000; Trad IRA ~$1,000,000; Inherited IRA ~$85,000."""
        rows = self._load()
        totals = {}
        for r in rows:
            t = r["account_type"]
            totals[t] = totals.get(t, 0) + float(r["market_value"])
        assert abs(totals["taxable"] - 1_030_000) < 100
        assert abs(totals["trad_ira"] - 1_000_000) < 100
        assert abs(totals["inherited_ira"] - 85_000) < 100

    def test_aapl_concentration_and_gain(self):
        """AAPL should be ~25.5% of taxable with $241k embedded gain."""
        rows = self._load()
        taxable_total = sum(float(r["market_value"]) for r in rows if r["account_type"] == "taxable")
        aapl = [r for r in rows if r["ticker"] == "AAPL" and r["account_type"] == "taxable"][0]
        aapl_pct = float(aapl["market_value"]) / taxable_total
        assert abs(aapl_pct - 0.255) < 0.005
        assert float(aapl["unrealized_gain"]) == 241_000
        assert aapl["top1_pct"] == "true"

    def test_ira_mmf_approximately_20_pct(self):
        """Rollover IRA should have ~20% in money market."""
        rows = self._load()
        ira_rows = [r for r in rows if r["account_id"] == "RIRA_01"]
        ira_total = sum(float(r["market_value"]) for r in ira_rows)
        mmf_val = sum(float(r["market_value"]) for r in ira_rows if r["ticker"] == "VMFXX")
        assert abs(mmf_val / ira_total - 0.20) < 0.02

    def test_required_columns_present(self):
        """All required columns exist in the header."""
        with open(DATA_DIR / "accounts_snapshot.csv") as f:
            reader = csv.DictReader(f)
            required = {"snapshot_date", "account_id", "account_type", "ticker",
                        "asset_class", "shares", "price", "market_value",
                        "cost_basis", "unrealized_gain", "top1_pct"}
            assert required.issubset(set(reader.fieldnames))


# ── trade_log.csv ────────────────────────────────────────────────────────

class TestTradeLog:
    def _load(self):
        with open(DATA_DIR / "trade_log.csv") as f:
            return list(csv.DictReader(f))

    def test_all_actions_valid(self):
        """Actions must be buy, sell, or withdraw."""
        rows = self._load()
        valid = {"buy", "sell", "withdraw"}
        for r in rows:
            assert r["action"] in valid, f"Invalid action: {r['action']}"

    def test_realized_gains_positive_for_sells(self):
        """Sell transactions in taxable account should have non-negative realized gain."""
        rows = self._load()
        taxable_sells = [r for r in rows if r["action"] == "sell" and r["account_id"] == "TAXABLE_01"]
        assert len(taxable_sells) >= 1
        for r in taxable_sells:
            assert float(r["realized_gain"]) >= 0


# ── cashflow_plan.csv ────────────────────────────────────────────────────

class TestCashflowPlan:
    def _load(self):
        with open(DATA_DIR / "cashflow_plan.csv") as f:
            return list(csv.DictReader(f))

    def test_ss_income_matches(self):
        """Social Security should be $56,000."""
        rows = self._load()
        ss = [r for r in rows if r["subcategory"] == "social_security"]
        assert len(ss) == 1
        assert float(ss[0]["amount"]) == 56_000

    def test_baseline_expenses_and_lumpy_budget(self):
        """Recurring annual expenses ~$90k; lumpy one-time items exist."""
        rows = self._load()
        annual_exp = sum(float(r["amount"]) for r in rows
                         if r["category"] == "expense" and r["frequency"] == "annual")
        assert annual_exp == 90_000  # 50k + 15k + 10k + 8k + 7k
        lumpy = [r for r in rows if "one_time" in r["frequency"]]
        assert len(lumpy) >= 3


# ── tax_profile.json ─────────────────────────────────────────────────────

class TestTaxProfile:
    def _load(self):
        with open(DATA_DIR / "tax_profile.json") as f:
            return json.load(f)

    def test_filing_status_and_state(self):
        """MFJ filing in Oregon."""
        tp = self._load()
        assert tp["filing_status"] == "mfj"
        assert tp["state"] == "OR"
        assert tp["agi_prior_year"] == 104_000
        assert tp["cap_loss_carryforward"] == 3_000

    def test_standard_deduction_with_senior_bonus(self):
        """Standard deduction = base + 2 × senior bonus."""
        tp = self._load()
        expected = tp["standard_deduction_base"] + 2 * tp["standard_deduction_senior_bonus_each"]
        assert tp["effective_standard_deduction"] == expected


# ── constraints.json ─────────────────────────────────────────────────────

class TestConstraints:
    def _load(self):
        with open(DATA_DIR / "constraints.json") as f:
            return json.load(f)

    def test_inherited_ira_deadline(self):
        """Inherited IRA must be fully distributed by 2033."""
        c = self._load()
        assert c["inherited_ira_deadline_year"] == 2033
        assert c["inherited_ira_balance_start"] == 85_000

    def test_irmaa_guardrail_below_tier1(self):
        """IRMAA headroom target is $10k below $206k threshold."""
        c = self._load()
        g = c["irmaa_guardrails"]
        assert g["enabled"] is True
        assert g["tier1_magi_mfj"] - g["target_headroom_below_tier1"] == 196_000


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
