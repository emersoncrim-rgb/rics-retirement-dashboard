# RICS — Developer Notes

## Current State (v0.1)

Working end-to-end pipeline: CSV ingest → risk scoring → deterministic 
projections → tax/IRMAA analysis → RMD scheduling → withdrawal sequencing 
→ Monte Carlo simulation → trip impact analyzer → Streamlit dashboard.

270+ tests, 3 dependencies (numpy, streamlit, matplotlib), runs fully offline.

---

## Future Features — Prioritized Roadmap

### P0 — High impact, low effort

- **Monthly time steps** in the deterministic engine.  Currently annual.
  Monthly would model within-year cash-flow timing, quarterly estimated
  tax payments, and mid-year rebalancing.  Requires changing the inner
  loop from `horizon` iterations to `horizon × 12`; growth rates become
  `(1 + r)^(1/12) − 1`.

- **Scenario comparison mode.**  Let the user define 2-3 custom scenarios
  side by side (e.g. "retire now vs. work 1 more year", "sell AAPL now
  vs. hold") with diff-table output.

- **Roth conversion optimizer.**  Given IRMAA headroom and bracket space,
  compute the optimal annual Roth conversion amount that fills the 12%
  bracket without crossing the IRMAA tier.  This is the single
  highest-value tax planning lever for this couple.

### P1 — Medium effort, high value

- **Broker CSV auto-parser.**  Map Schwab/Fidelity/Vanguard CSV export
  formats to `accounts_snapshot.csv`.  Each broker has its own column
  names and layout; write a `parsers/` subfolder with one adapter per
  broker.

- **Fat-tailed return distributions.**  Replace the multivariate normal
  in `mc_sim.py` with a Student-t copula (ν ≈ 5).  This increases left-tail
  mass and produces more realistic ruin probabilities in stressed scenarios.
  Implementation: draw from multivariate-t instead of multivariate-normal;
  keep the same correlation structure.

- **Social Security optimization.**  Model the claiming age decision
  (62 vs. 67 vs. 70) with breakeven analysis considering longevity,
  tax impact, and IRMAA.  The current model assumes SS is already
  being received.

- **Capital gain lot-level tracking across years.**  Currently the MC
  engine uses a blended-return model for speed.  For full lot-level
  tracking, maintain a per-lot data structure that carries forward
  cost basis, holding period, and wash-sale status across projection
  years.  Expensive but enables precise tax-loss harvesting simulation.

### P2 — Larger undertakings

- **PDF report generation.**  Export a 10-page PDF with charts, tables,
  and narrative summary.  Use `reportlab` or `weasyprint`.

- **Spouse-aware two-life modeling.**  Model the first-to-die and
  survivor scenarios: surviving spouse switches to Single filing,
  loses one SS benefit, and may face higher IRMAA tiers.  Requires
  a two-state Markov chain on life status.

- **Housing / reverse mortgage module.**  Model home equity as a
  non-liquid asset with options to downsize or take a HECM.

- **Long-term care cost modeling.**  Incorporate probability-weighted
  LTC events (home care, assisted living, nursing) based on age-
  adjusted incidence tables.

- **Estate planning module.**  Track beneficiary designations,
  step-up in basis at death, and projected estate tax exposure.

---

## Security Notes

### Threat Model

RICS is designed for a single user on a single machine.  The threat
model is: **protect financial data from accidental exposure**.

| Threat | Mitigation |
|--------|-----------|
| Data exfiltration via network | No network calls.  No analytics.  No update checks.  Streamlit's built-in telemetry can be disabled: set `gatherUsageStats = false` in `~/.streamlit/config.toml`. |
| Accidental git commit of data | Add `data/` to `.gitignore`.  Ship a `data_sample/` with synthetic numbers instead. |
| Other users on shared machine | Use filesystem permissions: `chmod 700 rics/data/`.  The venv and all computation are user-local. |
| Streamlit network exposure | By default Streamlit listens on `localhost:8501` only.  Do **not** use `--server.address 0.0.0.0` on a shared network. |
| Dependency supply chain | Only 3 PyPI packages (numpy, streamlit, matplotlib).  Pin exact versions in `requirements.txt` for reproducibility.  Audit with `pip-audit` periodically. |

### Recommended `.gitignore`

```gitignore
data/
.venv/
__pycache__/
*.pyc
.streamlit/
```

### Streamlit Telemetry Opt-Out

```bash
mkdir -p ~/.streamlit
cat > ~/.streamlit/config.toml << 'EOF'
[browser]
gatherUsageStats = false
EOF
```

---

## Data Privacy Guidance

### What data is sensitive

| File | Contents | Sensitivity |
|------|----------|-------------|
| `accounts_snapshot.csv` | Real account IDs, tickers, share counts, cost basis | **HIGH** — reveals net worth, holdings, tax lots |
| `trade_log.csv` | Trade history with dates, amounts, gains | **HIGH** — reveals trading activity |
| `cashflow_plan.csv` | SS income, expenses, future plans | **MEDIUM** — reveals income and lifestyle |
| `tax_profile.json` | AGI, filing status, carryforwards | **HIGH** — tax return data |
| `constraints.json` | Risk preferences, MC params | **LOW** — general parameters |
| `rmd_divisors.json` | IRS table (public data) | **NONE** |

### Best practices

1. **Never commit `data/` to version control.**  Use `.gitignore`.
2. **Keep a synthetic `data_sample/`** with fake numbers for testing
   and sharing.  The current sample data is already fictional.
3. **Encrypt at rest** if on a shared or portable machine.  Use
   full-disk encryption (FileVault, BitLocker, LUKS).
4. **Don't email data files.**  If you need to share with an advisor,
   use an encrypted ZIP or a secure file-sharing tool.
5. **Wipe temp files** after Streamlit upload mode.  Uploaded files
   go to a system temp directory; Streamlit cleans them on session end,
   but you can force it: `rm -rf /tmp/streamlit-*`.

---

## Architectural Decisions

### Why no pandas?

- The dataset is ~20 rows.  `csv.DictReader` + list-of-dicts is simpler,
  faster to start, zero-dependency, and trivially JSON-serializable.
- NumPy is used only in `mc_sim.py` where vectorized math matters
  (10 000 × 20 matrix operations).
- If the dataset grows to hundreds of lots, adding pandas is a
  one-module change in `ingest.py`.

### Why Streamlit over Flask/Dash?

- Zero frontend code.  Python only.
- Interactive widgets (sliders, file uploaders, tabs) out of the box.
- Caching (`@st.cache_data`) handles expensive recomputation.
- Single-file deployment (`app.py`).
- Tradeoff: less control over layout and no real-time updates.

### Why multivariate normal in MC?

- Industry-standard baseline for retirement Monte Carlo.
- Captures correlation structure (critical: equity crash hits
  both US and intl simultaneously).
- Tractable: `np.random.default_rng().multivariate_normal()` is
  fast and well-tested.
- **Known limitation:** understates tail risk.  Real equity returns
  have negative skew and excess kurtosis.  The fat-tail upgrade
  (Student-t copula) is on the P1 roadmap.

### Why lot-level selection in withdrawals but not in MC?

- The withdrawal module (`withdrawals.py`) runs once per manual
  query — O(lots) is fine for ~15 holdings.
- The MC engine runs the withdrawal step 10 000 × 20 = 200 000 times.
  Full lot-level tracking would require maintaining a per-sim,
  per-lot matrix, which is both slow and memory-heavy.
- The blended-return model in MC gives accurate portfolio-level
  outcomes and IRMAA probabilities — which is what MC is for.
  Lot-level precision is for the deterministic and trip modules.

---

## Performance Benchmarks

| Operation | Time | Notes |
|-----------|------|-------|
| `ingest_all()` | < 5 ms | 5 CSV/JSON files, ~20 rows |
| `risk_report()` | < 1 ms | Pure arithmetic |
| `build_projection_from_ingested(horizon=20)` | < 10 ms | 3 scenarios × 20 years |
| `simulate_year_tax_effects()` × 20 | < 5 ms | Bracket lookups |
| `run_mc_from_ingested(n_sims=10_000)` | ~150 ms | Vectorized NumPy |
| `run_mc_from_ingested(n_sims=100_000)` | ~1.5 s | Linear scaling |
| `trip_impact(run_mc=True, mc_n=1_000)` | ~100 ms | 2× MC runs + deterministic |
| Full test suite | ~2 s | 270+ tests |
| Streamlit cold start | ~3 s | First page load with caching |

---

## Contributing

This is a personal project.  If forking:

1. Keep the test suite passing (`bash run_all_tests.sh`).
2. Don't add network-dependent features to core modules.
3. Any new module should follow the pattern: pure functions,
   `list[dict]` in/out, CLI `__main__` block, dedicated test file.
4. Update this `dev_notes.md` with architectural decisions.
