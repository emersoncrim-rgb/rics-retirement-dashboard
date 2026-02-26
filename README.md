# RICS – Retirement Income & Cash-flow Simulator

A comprehensive retirement planning tool for a 72/70-year-old Oregon couple (MFJ). RICS models portfolio holdings across taxable, Traditional IRA, Inherited IRA, and Roth IRA accounts, then runs tax-aware withdrawal sequencing, rebalancing, and multi-year projections.

## Quick Start

```bash
# Run all tests (157 tests across 4 modules)
./run_all_tests.sh

# Launch the Streamlit UI
pip install streamlit
streamlit run app.py

# Or run the recommendations engine standalone (no Streamlit needed)
python3 app.py
```

## Project Structure

```
rics/
├── app.py                          # Streamlit UI (7 tabs)
├── run_all_tests.sh                # Test runner script
├── broker_import.py                # Broker CSV import & normalization
├── dividend_analyzer.py            # Dividend income analysis & projections
├── rebalance_sim.py                # Rebalance simulator with tax impact
├── recommendations.py              # Rule-based recommendation engine
├── data/
│   ├── accounts_snapshot.csv       # Portfolio holdings (16 positions)
│   ├── cashflow_plan.csv           # Income, expenses, lumpy items
│   ├── trade_log.csv               # YTD realized trades
│   ├── tax_profile.json            # Federal/OR brackets, IRMAA, SS
│   ├── constraints.json            # Concentration limits, Monte Carlo params
│   └── rmd_divisors.json           # IRS Uniform Lifetime Table
└── tests/
    ├── test_broker_import.py       # 48 tests
    ├── test_dividend_analyzer.py   # 32 tests
    ├── test_rebalance_sim.py       # 29 tests
    └── test_recommendations.py     # 48 tests
```

## Modules

### broker_import.py

Parses CSV exports from Fidelity, Schwab, Vanguard, or generic formats and normalizes them into the RICS `accounts_snapshot` schema.

- Auto-detects broker format from CSV headers
- Infers account type from account name (Roth, Traditional, Inherited, Taxable)
- Maps tickers to asset classes (us_equity, intl_equity, us_bond, mmf)
- Currency parsing handles `$1,234.56`, `($500)`, `--`, `n/a`
- Merge imported holdings with existing snapshot (key: account_id + ticker)

### dividend_analyzer.py

Analyzes dividend income across all accounts with tax-aware categorization.

- Classifies income: taxable-qualified, taxable-ordinary, tax-deferred, tax-free
- Projects 5-year and 10-year income using per-asset-class growth rates
- Computes weighted average yield and income coverage ratio
- Identifies dividend upgrade opportunities (low-yield → high-yield ETF swaps)
- Respects AAPL concentration constraint and embedded gains in taxable accounts

### rebalance_sim.py

Simulates portfolio rebalancing with full tax-impact modeling.

- Converts aggressiveness score (0–100) to target allocation
- Computes allocation drift and proposes trades
- Prefers tax-advantaged accounts for selling (IRA/Roth before taxable)
- Estimates capital gains tax on taxable trades (federal LTCG + OR state)
- Enforces AAPL sell constraint (blocks sales unless offset by losses)
- Configurable rebalance band tolerance to reduce unnecessary trading

### recommendations.py

Rule-based engine that scans the full financial picture and flags actionable opportunities. Eight rules:

| Rule ID    | Category   | Description                                           |
|------------|------------|-------------------------------------------------------|
| CONC-AAPL  | Risk       | AAPL concentration approaching/exceeding 30% limit    |
| TAX-0LTCG  | Tax        | Room to harvest gains at 0% federal LTCG rate         |
| TAX-IRMAA  | Tax        | MAGI approaching IRMAA Tier 1 surcharge threshold     |
| TAX-ROTH   | Tax        | Roth conversion bracket-filling (stays in 12% bracket)|
| CASH-RSV   | Withdrawal | Cash reserve adequacy vs. 24-month spending target    |
| INC-DIVUP  | Income     | Dividend upgrade swaps in tax-advantaged accounts      |
| WITH-IIRA  | Withdrawal | Inherited IRA 10-year deadline pacing check            |
| COMP-RMD   | Compliance | RMD projection with 5-year lookahead                  |

Each recommendation includes severity (high/medium/low/info), description, suggested action, impact estimate, and supporting data for the UI. The engine is resilient — a single rule failure won't crash the others.

## Streamlit App Tabs

1. **Portfolio Overview** – Account totals, asset allocation chart, full holdings table
2. **Cash Flow Plan** – Income sources, annual expenses, lumpy/one-time items
3. **Tax Dashboard** – Federal & OR brackets, IRMAA thresholds, standard deduction
4. **Broker Import** – Upload CSV, auto-detect format, preview & merge into snapshot
5. **Dividend Analysis** – Income breakdown by tax treatment, projections, upgrade opportunities
6. **Rebalance Simulator** – Aggressiveness slider, drift table, proposed trades with tax cost
7. **Recommendations** – Priority-sorted findings with expandable detail and JSON export

## Data Model

The portfolio uses 4 account types following the standard withdrawal sequence:

1. **Taxable** (Joint Brokerage) – ~$1,030,000 across AAPL, VTI, VXUS, BND, VGSH, VMFXX
2. **Traditional IRA** (Rollover) – ~$1,000,000 across MSFT, NVDA, AVGO, VTI, BND, VGSH, VMFXX
3. **Inherited IRA** (Spouse) – ~$85,000 in BND, VGSH, VMFXX (10-year deadline: 2033)
4. **Roth IRA** – Not yet in snapshot; can be added via broker import

Total portfolio: ~$2.12M. Annual Social Security: $56,000. Annual expenses: ~$90,000.

## Key Constraints

- **AAPL position**: 25.5% of taxable account, $241k embedded gain — do not sell unless offset by losses
- **IRMAA**: Keep MAGI at least $10k below $206k to avoid Medicare surcharges (~$1,987/yr for couple)
- **Inherited IRA**: Must fully distribute by end of 2033 (10-year rule, ~$10.6k/yr even pace)
- **RMD**: Starts at age 73 (SECURE 2.0), divisor 26.5, ~$37.7k required for current year
- **Oregon**: No sales tax; SS fully exempt; state income tax 4.75–9.9%

## Testing

Tests use Python's built-in `unittest` module (no pip dependencies required). Run with:

```bash
# All tests via the runner script
./run_all_tests.sh

# Individual module
python3 -m unittest tests/test_recommendations.py -v

# All tests via unittest discover
python3 -m unittest discover -s tests -p "test_*.py" -v
```

If `pytest` is available, `run_all_tests.sh` will auto-detect and use it instead.

## Dependencies

- **Core modules**: Python 3.10+ standard library only (csv, json, dataclasses, re, io)
- **Streamlit UI**: `pip install streamlit` (optional — app.py runs in self-test mode without it)
- **Tests**: Python `unittest` (built-in) or `pytest` (optional)
