#!/usr/bin/env bash
# run_all_tests.sh – RICS Test Runner
# Runs all unit tests and reports results.
#
# Usage:
#   ./run_all_tests.sh          # Run all tests
#   ./run_all_tests.sh -v       # Verbose output
#   ./run_all_tests.sh -q       # Quiet (summary only)

set -euo pipefail
cd "$(dirname "$0")"

VERBOSE="${1:--v}"
PASS=0
FAIL=0
ERRORS=0
TOTAL=0

echo "═══════════════════════════════════════════════════════════"
echo "  RICS Test Suite"
echo "═══════════════════════════════════════════════════════════"
echo ""

# Collect all test files
TEST_FILES=(
    tests/test_broker_import.py
    tests/test_dividend_analyzer.py
    tests/test_rebalance_sim.py
    tests/test_recommendations.py
)

# Check for pytest availability; fall back to unittest
if python3 -c "import pytest" 2>/dev/null; then
    RUNNER="pytest"
    echo "Runner: pytest"
    echo ""
    python3 -m pytest tests/ $VERBOSE --tb=short 2>&1
    exit $?
else
    RUNNER="unittest"
    echo "Runner: unittest (pytest not available)"
    echo ""
fi

ALL_PASSED=true

for test_file in "${TEST_FILES[@]}"; do
    if [ ! -f "$test_file" ]; then
        echo "⚠️  MISSING: $test_file"
        ERRORS=$((ERRORS + 1))
        ALL_PASSED=false
        continue
    fi

    echo "───────────────────────────────────────────────────────"
    echo "  Running: $test_file"
    echo "───────────────────────────────────────────────────────"

    OUTPUT=$(python3 -m unittest "$test_file" $VERBOSE 2>&1) || true
    echo "$OUTPUT"

    # Parse results from last line like "Ran 48 tests in 0.006s"
    RAN=$(echo "$OUTPUT" | grep -oP "Ran \K\d+" | tail -1)
    if [ -n "$RAN" ]; then
        TOTAL=$((TOTAL + RAN))
    fi

    if echo "$OUTPUT" | grep -q "^OK$"; then
        PASS=$((PASS + ${RAN:-0}))
    elif echo "$OUTPUT" | grep -q "FAILED"; then
        FAILURES=$(echo "$OUTPUT" | grep -oP "failures=\K\d+" || echo "0")
        ERR=$(echo "$OUTPUT" | grep -oP "errors=\K\d+" || echo "0")
        FAIL=$((FAIL + ${FAILURES:-0}))
        ERRORS=$((ERRORS + ${ERR:-0}))
        ALL_PASSED=false
    fi
    echo ""
done

# Also run app.py self-test
echo "───────────────────────────────────────────────────────"
echo "  Running: app.py self-test"
echo "───────────────────────────────────────────────────────"
if python3 app.py 2>&1 | grep -q "findings:"; then
    echo "✅ app.py self-test passed"
else
    echo "❌ app.py self-test failed"
    ALL_PASSED=false
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  SUMMARY"
echo "═══════════════════════════════════════════════════════════"
echo "  Total tests:  $TOTAL"
echo "  Passed:       $PASS"
echo "  Failed:       $FAIL"
echo "  Errors:       $ERRORS"
echo ""

if $ALL_PASSED; then
    echo "  ✅ ALL TESTS PASSED"
    exit 0
else
    echo "  ❌ SOME TESTS FAILED"
    exit 1
fi
