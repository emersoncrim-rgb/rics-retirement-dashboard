#!/usr/bin/env bash
#
# run_all_tests.sh — Run every RICS test module and print a summary.
#
# Usage:
#   bash run_all_tests.sh           # from the rics/ directory
#   bash run_all_tests.sh --verbose # show individual test names
#
set -euo pipefail
cd "$(dirname "$0")"

VERBOSE=""
[[ "${1:-}" == "--verbose" || "${1:-}" == "-v" ]] && VERBOSE="1"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

MODULES=(
    tests/test_ingest.py
    tests/test_risk.py
    tests/test_deterministic.py
    tests/test_tax_irmaa.py
    tests/test_rmd.py
    tests/test_withdrawals.py
    tests/test_mc_sim.py
    tests/test_trip_simulator.py
)

TOTAL_PASS=0
TOTAL_FAIL=0
FAILED_MODULES=()

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  RICS Test Suite"
echo "════════════════════════════════════════════════════════════"
echo ""

for mod in "${MODULES[@]}"; do
    mod_name=$(basename "$mod" .py)

    if [[ -n "$VERBOSE" ]]; then
        output=$(python3 "$mod" 2>&1) || true
    else
        output=$(python3 "$mod" 2>&1) || true
    fi

    # Extract "Ran N tests" and OK/FAILED
    ran_line=$(echo "$output" | grep -E "^Ran [0-9]+" || echo "Ran 0 tests")
    count=$(echo "$ran_line" | grep -oE '[0-9]+' | head -1)
    count=${count:-0}

    if echo "$output" | grep -q "^OK"; then
        printf "  ${GREEN}✅  %-30s %3s passed${NC}\n" "$mod_name" "$count"
        TOTAL_PASS=$((TOTAL_PASS + count))
    else
        fail_count=$(echo "$output" | grep -oP 'failures=\K[0-9]+' || echo "0")
        fail_count=${fail_count:-0}
        err_count=$(echo "$output" | grep -oP 'errors=\K[0-9]+' || echo "0")
        err_count=${err_count:-0}
        total_bad=$((fail_count + err_count))
        printf "  ${RED}❌  %-30s %3s ran, %s failed${NC}\n" "$mod_name" "$count" "$total_bad"
        TOTAL_PASS=$((TOTAL_PASS + count - total_bad))
        TOTAL_FAIL=$((TOTAL_FAIL + total_bad))
        FAILED_MODULES+=("$mod_name")
    fi

    if [[ -n "$VERBOSE" ]]; then
        echo "$output" | grep -E "^test_|\.\.\.\ " | sed 's/^/      /'
        echo ""
    fi
done

echo ""
echo "════════════════════════════════════════════════════════════"
printf "  Total: ${GREEN}%d passed${NC}" "$TOTAL_PASS"
if [[ $TOTAL_FAIL -gt 0 ]]; then
    printf ", ${RED}%d failed${NC}" "$TOTAL_FAIL"
fi
echo ""

if [[ $TOTAL_FAIL -gt 0 ]]; then
    echo ""
    printf "  ${RED}Failed modules: ${FAILED_MODULES[*]}${NC}\n"
    echo ""
    exit 1
else
    echo "  All tests passed ✓"
    echo "════════════════════════════════════════════════════════════"
    echo ""
    exit 0
fi
