# RICS — Retirement Income Control System

A **local-first, privacy-first** retirement projection tool for a married couple
(MFJ, ages 72 / 70, Oregon residents) with tax-aware withdrawal sequencing,
IRMAA headroom monitoring, RMD tracking, Monte Carlo simulation, and a
one-off expense ("Can I afford this trip?") analyzer.

> No data leaves your machine.  No broker APIs.  No cloud.  No telemetry.

---

## 1. Quick Start

```bash
cd rics

# Create + activate a virtual environment
python3 -m venv .venv && source .venv/bin/activate   # macOS / Linux
# .venv\Scripts\activate                               # Windows

# Install the three runtime dependencies
pip install -r requirements.txt   # numpy, streamlit, matplotlib

# Verify everything works (270 tests, < 3 s)
bash run_all_tests.sh

# Launch the dashboard
streamlit run app.py              # opens http://localhost:8501
```

**Requirements:** Python ≥ 3.9, ~50 MB disk (including venv).
No internet needed after the initial `pip install`.

---

## 2. Project Structure

```
rics/
├── app.py                        Streamlit dashboard (5-tab UI)
├── requirements.txt              numpy · streamlit · matplotlib
├── run_all_tests.sh              One-command test runner
├── README.md                     ← you are here
├── dev_notes.md                  Roadmap, security, privacy guidance
│
├── data/                         ── All editable inputs ──
│   ├── accounts_snapshot.csv       Holdings per account / lot
│   ├── trade_log.csv               Historical buy / sell / withdraw
│   ├── cashflow_plan.csv           Income + recurring + lumpy expenses
│   ├── tax_profile.json            Federal / OR brackets, IRMAA, SS
│   ├── constraints.json            MC params, guardrails, risk targets
│   └── rmd_divisors.json           IRS Uniform Lifetime Table (72-120)
│
├── ingest.py                     Data loading · validation · summary
├── risk.py                       Aggressiveness score (0-100) · buckets
├── deterministic.py              3-scenario projection engine
├── tax_irmaa.py                  Federal + Oregon tax · LTCG · IRMAA
├── rmd.py                        RMD computation · inherited-IRA schedules
├── withdrawals.py                Tax-aware lot selection · sequencing
├── mc_sim.py                     Vectorized Monte Carlo (NumPy)
├── trip_simulator.py             One-off expense feasibility analyzer
│
└── tests/                        ── 270+ unit / integration tests ──
    ├── test_step1_schemas.py       Schema validation (15)
    ├── test_ingest.py              Ingestion + summary (42)
    ├── test_risk.py                Risk scoring (38)
    ├── test_deterministic.py       Projection engine (32)
    ├── test_tax_irmaa.py           Tax + IRMAA (34)
    ├── test_rmd.py                 RMD + inherited IRA (37)
    ├── test_withdrawals.py         Withdrawal sequencing (29)
    ├── test_mc_sim.py              Monte Carlo engine (34)
    └── test_trip_simulator.py      Trip analysis (24)
```

---

## 3. Module Reference

| Module | Purpose | Main Entry Point |
|--------|---------|------------------|
| `ingest.py` | Load CSVs/JSON, validate, compute totals | `ingest_all(paths) → dict` |
| `risk.py` | 4-bucket allocation, 0-100 score (5-factor) | `risk_report(accounts) → dict` |
| `deterministic.py` | Year-by-year at 3% / 4.5% / 6%, with RMD + expenses | `build_projection_from_ingested(ingested, horizon)` |
| `tax_irmaa.py` | Federal brackets, LTCG stacking, OR tax (SS exempt), IRMAA tiers | `compute_taxes(…)`, `compute_irmaa_impact(magi, tp)` |
| `rmd.py` | Uniform Lifetime Table, SECURE 2.0 age 73, 3 inherited-IRA strategies | `compute_rmd_amount(year, birthdate, balance, divisors)` |
| `withdrawals.py` | Greedy lot selector, priority sourcing (cash→iIRA→taxable→IRA→Roth) | `withdraw_sequence(amount, state, lots)` |
| `mc_sim.py` | 10 000-sim correlated normal, ruin %, IRMAA %, fan chart | `run_mc_from_ingested(ingested, n_sims, horizon, seed)` ~0.15 s |
| `trip_simulator.py` | Compare 4 funding sources, deterministic + MC delta | `trip_impact(cost, year, "optimal", ingested)` |

---

## 4. Usage Examples

### CLI — one module at a time

```bash
python ingest.py .            # portfolio snapshot + validation
python risk.py .              # aggressiveness score + buckets
python deterministic.py .     # 20-yr 3-scenario table
python tax_irmaa.py .         # 20-yr tax + IRMAA + headroom
python rmd.py                 # RMD schedule + 3 inherited strategies
python mc_sim.py .            # 10 000-sim summary table
python trip_simulator.py .    # $15k + $50k trip impact
```

### Python API

```python
from ingest import ingest_all
from mc_sim import run_mc_from_ingested
from trip_simulator import trip_impact

ingested = ingest_all()                       # uses data/ folder
mc = run_mc_from_ingested(ingested, 10_000, seed=42)
print(f"P(ruin): {mc['ruin_stats']['probability_of_ruin']:.2%}")
#  → P(ruin): 0.00%

trip = trip_impact(25_000, 2026, "optimal", ingested)
print(f"Best: {trip['best_source']} at ${trip['recommendation']['best_net_cost']:,.0f}")
#  → Best: cash at $25,000
```

### Streamlit dashboard

```bash
streamlit run app.py                     # default port 8501
streamlit run app.py --server.port 8502  # alternate port
```

Five tabs:  📋 Today · ⚖ Risk · 📈 Deterministic · 🎲 Monte Carlo · ✈ Trip Simulator

---

## 5. Updating Parameters

All tunable values live in **data files**, not in code.

### Annual updates (every January)

| File | Key(s) to change | Source |
|------|-------------------|--------|
| `tax_profile.json` | `federal_brackets_mfj_2025` | IRS Rev. Proc. (Nov each year) |
| `tax_profile.json` | `qualified_div_brackets_mfj_2025` | Same IRS release |
| `tax_profile.json` | `standard_deduction_base` | Same IRS release |
| `tax_profile.json` | `irmaa_thresholds_mfj_2025` | CMS announcement (Sep for next year) |
| `tax_profile.json` | `oregon_tax.brackets_mfj_2025` | Oregon Dept of Revenue |
| `rmd_divisors.json` | `divisors` | IRS Pub 590-B (rare; last big change 2022) |

**Tip:** duplicate the existing bracket array, rename the year suffix to the
new year, and update thresholds.  The code keys off whatever name you pass
through `tax_profile`.

### When your situation changes

| File | What to edit | When |
|------|-------------|------|
| `accounts_snapshot.csv` | All holdings | After rebalancing / quarterly |
| `trade_log.csv` | New buy/sell rows | After any trade |
| `cashflow_plan.csv` | Income, expenses, lumpy items | When plans change |
| `tax_profile.json` → `agi_prior_year` | Prior-year AGI | After filing taxes |
| `tax_profile.json` → `cap_loss_carryforward` | Updated carryforward | After filing taxes |
| `constraints.json` → `aggressiveness_score.current_target` | Risk dial | When comfort level changes |
| `constraints.json` → `inherited_ira_deadline_year` | Deadline | Only if legislation changes |

---

## 6. Key Assumptions

### Returns (configurable in `constraints.json`)

| Asset Class | μ | σ | Rationale |
|-------------|-----|-----|-----------|
| US Equity | 7.0 % | 16.0 % | Long-run average, reduced for valuations |
| Intl Equity | 6.0 % | 18.0 % | Slightly lower return, higher vol |
| US Bond | 3.5 % | 5.0 % | Current intermediate yield environment |
| MMF | 4.0 % | 0.5 % | Current MMF rates (will normalize) |
| Inflation | 2.5 % | 1.0 % | Fed target ± historical variance |

### Tax

- MFJ, Oregon, standard deduction $33,100 (incl. 2× senior bonus)
- IRMAA uses 2-year lookback MAGI
- All taxable-account holdings assumed long-term
- Carryforward: ST offset first → LT → $3 k ordinary deduction

### Withdrawal sequencing

Cash → Inherited IRA → Taxable (min tax-per-dollar) → Trad IRA → Roth.
AAPL lot (`top1_pct` flag) deprioritized unless forced.

### Monte Carlo

- Multivariate normal (no fat tails — see `dev_notes.md`)
- Correlation: equity↔equity 0.85, equity↔bond −0.10, equity↔cash 0.05
- Annual steps; same seed = reproducible
- Withdrawal logic simplified to blended-return model for speed

---

## 7. Testing

```bash
bash run_all_tests.sh           # all modules, summary at end
python tests/test_risk.py       # single module
python -m pytest tests/ -v      # with pytest, if installed
```

| Module | Tests | Coverage highlights |
|--------|-------|---------------------|
| Schemas | 15 | Column presence, totals, cross-checks |
| Ingest | 42 | Type coercion, flags, account totals, summary |
| Risk | 38 | Buckets, concentration, score components, boundary |
| Deterministic | 32 | Compounding, multi-account, RMD start, inherited deadline |
| Tax / IRMAA | 34 | Brackets, SS taxation, LTCG stacking, carryforward, tiers |
| RMD | 37 | Divisor table, age boundaries, 3 strategies, exhaustion |
| Withdrawals | 29 | Lot ordering, AAPL avoidance, sequencing, apply/update |
| Monte Carlo | 34 | Shapes, stats, correlation, reproducibility, JSON serial |
| Trip | 24 | Funding comparison, IRMAA crossing, det + MC delta |
| **Total** | **~285** | |

---

## 8. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError: numpy` | Activate venv: `source .venv/bin/activate` |
| `FileNotFoundError: CSV` | Run from `rics/` dir, or pass path: `python ingest.py /path/to/rics` |
| Port 8501 busy | `streamlit run app.py --server.port 8502` |
| Tests fail after data edits | Tests validate sample data; keep a `data_backup/` or update assertions |

---

## Disclaimer

This is a personal planning tool — **not** investment advice, **not** tax advice.
Consult a qualified financial advisor and CPA for decisions involving real accounts.
