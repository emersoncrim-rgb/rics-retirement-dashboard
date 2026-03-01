#!/usr/bin/env python3
"""
Minimal baseline runner that does NOT import the full app.

Slice 0: Sanity-check that snapshot + constraints load.
Slice 1: Call plan_engine.run_plan(...) and print plan_summary.
"""
import csv
import json
from pathlib import Path
import sys

from plan_engine import run_plan
from monte_carlo import run_monte_carlo


def load_csv_rows(path: Path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_existing_path(candidates):
    for p in candidates:
        if p.exists():
            return p
    return None


def run_baseline():
    print("--- RICS Baseline Scenario ---")

    snapshot_path = find_existing_path([
        Path("accounts_snapshot.csv"),
        Path("data/accounts_snapshot.csv"),
    ])
    constraints_path = find_existing_path([
        Path("constraints.json"),
        Path("data/constraints.json"),
    ])
    tax_profile_path = find_existing_path([
        Path("tax_profile.json"),
        Path("data/tax_profile.json"),
    ])

    if snapshot_path is None:
        print("Baseline scenario failed: could not find accounts snapshot CSV (accounts_snapshot.csv or data/accounts_snapshot.csv)")
        sys.exit(1)

    if constraints_path is None:
        print("Baseline scenario failed: could not find constraints.json (constraints.json or data/constraints.json)")
        sys.exit(1)

    holdings = load_csv_rows(snapshot_path)
    constraints = load_json(constraints_path)

    profile = {}
    if tax_profile_path is not None:
        try:
            profile = load_json(tax_profile_path)
        except Exception as e:
            print(f"Warning: failed to load tax profile from {tax_profile_path}: {e}")
            profile = {}

    print(f"Loaded {len(holdings)} holdings from {snapshot_path}")
    if isinstance(constraints, dict):
        print(f"Loaded constraints keys: {len(constraints)} from {constraints_path}")
    else:
        print(f"Loaded constraints (non-dict) from {constraints_path}")

    if tax_profile_path is not None:
        print(f"Loaded tax profile from {tax_profile_path}")
    else:
        print("Tax profile not found (tax_profile.json or data/tax_profile.json) — continuing with empty profile.")

    plan_result = run_plan(profile, holdings, constraints)

    print("\n--- Plan Summary ---")
    for k, v in plan_result.get("plan_summary", {}).items():
        print(f"  {k}: {v}")

    
    print("\n--- Monte Carlo Summary ---")
    try:
        mc_result = run_monte_carlo(profile, holdings, constraints)
        print(f"  Success Probability: {mc_result['success_probability']:.2%}")
        print(f"  Simulations: {mc_result['num_simulations']} (over {mc_result['projection_years']} years)")
        print("  End Balance Percentiles:")
        for p, val in mc_result["end_balance_percentiles"].items():
            print(f"    {p}: ${val:,.2f}")
    except Exception as e:
        print("Monte Carlo run failed:", e)

    print("\nBaseline scenario executed successfully.")


if __name__ == "__main__":
    run_baseline()
