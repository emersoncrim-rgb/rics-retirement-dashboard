import sys

print("--- RICS Diagnostic Test Suite ---\n")

passed = 0
failed = 0

def assert_test(name, condition, error_msg):
    global passed, failed
    if condition:
        print(f"[PASS] {name}")
        passed += 1
    else:
        print(f"[FAIL] {name}: {error_msg}")
        failed += 1

# TEST 1: The Rebalance Simulator Math
try:
    from rebalance_sim import score_to_allocation
    target = score_to_allocation(0)
    assert_test(
        "Rebalance Math (Score 0)", 
        target.mmf == 0.5 and target.us_bond == 0.5, 
        f"Expected 50% MMF and 50% Bond, got {target.mmf*100}% MMF, {target.us_bond*100}% Bond"
    )
except Exception as e:
    print(f"[ERROR] Rebalance Math failed to run: {e}")
    failed += 1

# TEST 2: The Trade Applier Engine
try:
    from trade_apply import apply_trades_to_snapshot
    dummy_snap = [{"account_id": "Acct1", "account_type": "taxable", "ticker": "AAPL", "market_value": 1000.0}]
    dummy_trades = [{"action": "buy", "account_id": "Acct1", "account_type": "taxable", "ticker": "MSFT", "trade_value": 500.0, "shares": 5}]
    
    new_snap, errs = apply_trades_to_snapshot(dummy_snap, dummy_trades)
    has_msft = any(r.get("ticker") == "MSFT" and r.get("market_value") == 500.0 for r in new_snap)
    
    assert_test(
        "Trade Applier (Buy New Asset)", 
        has_msft and not errs, 
        "The engine failed to add MSFT to the portfolio snapshot."
    )
except Exception as e:
    print(f"[ERROR] Trade Apply failed to run: {e}")
    failed += 1

# TEST 3: The Recommendation Engine Tax Brackets
try:
    from recommendations import check_zero_ltcg_harvesting
    dummy_tax = {
        "filing_status": "single",
        "qualified_div_brackets_single_2025": [{"lower": 0, "upper": 47025, "rate": 0.0}],
        "ss_combined_annual": 0,
        "effective_standard_deduction": 14600
    }
    dummy_holdings = [{"account_type": "taxable", "ticker": "SPY", "market_value": 10000, "unrealized_gain": 2000, "asset_class": "us_equity", "annual_income_est": 200}]
    
    rec = check_zero_ltcg_harvesting(dummy_holdings, dummy_tax, [])
    assert_test(
        "Recommendations (Dynamic Brackets)", 
        rec is not None and rec.rule_id == "TAX-0LTCG", 
        "Did not dynamically adjust to 'single' filer tax brackets."
    )
except Exception as e:
    print(f"[ERROR] Recommendations failed to run: {e}")
    failed += 1

# TEST 4: The Monte Carlo Simulator Unpacking
try:
    from monte_carlo import run_monte_carlo
    profile = {"age": 72}
    holdings = [{"account_type": "taxable", "market_value": 100000}]
    constraints = {"monte_carlo": {"num_simulations": 1, "projection_years": 1}}
    
    # If the tuple unpacking bug still exists, this will crash immediately
    res = run_monte_carlo(profile, holdings, constraints)
    assert_test(
        "Monte Carlo (Tuple Unpacking)", 
        "success_probability" in res, 
        "Failed to unpack step_one_year tuple."
    )
except Exception as e:
    print(f"[ERROR] Monte Carlo failed to run: {e}")
    failed += 1

print(f"\nResults: {passed} Passed, {failed} Failed")
if failed == 0:
    print("ALL TESTS PASSED: The engine is healthy and ready to run.")
