#!/usr/bin/env python3
"""
Minimal baseline runner that does NOT import the full app.

Purpose: Provide a sanity-check that the repo can read the snapshot and constraints
even if the main app entrypoint has optional/missing module dependencies.
"""
import csv
import json
from pathlib import Path
import sys

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

    # Try common locations based on your repo + the earlier files
    snapshot_path = find_existing_path([
        Path("accounts_snapshot.csv"),
        Path("data/accounts_snapshot.csv"),
    ])
    constraints_path = find_existing_path([
        Path("constraints.json"),
        Path("data/constraints.json"),
    ])

    if snapshot_path is None:
        print("Baseline scenario failed: could not find accounts snapshot CSV (accounts_snapshot.csv or data/accounts_snapshot.csv)")
        sys.exit(1)

    if constraints_path is None:
        print("Baseline scenario failed: could not find constraints.json (constraints.json or data/constraints.json)")
        sys.exit(1)

    holdings = load_csv_rows(snapshot_path)
    constraints = load_json(constraints_path)

    # Be resilient to different column names in the snapshot
    value_keys = ["market_value", "marketValue", "value", "current_value", "currentValue", "mv"]
    total_val = 0.0
    missing = 0

    for h in holdings:
        v = None
        for k in value_keys:
            if k in h and h[k] not in (None, ""):
                v = h[k]
                break
        if v is None:
            missing += 1
            continue
        try:
            total_val += float(str(v).replace(",", "").replace("$", ""))
        except Exception:
            missing += 1

    print(f"Loaded {len(holdings)} holdings from {snapshot_path}")
    print(f"Total Portfolio Value (best-effort): ${total_val:,.2f}")
    if missing:
        print(f"Note: {missing} rows missing a recognizable value column (checked {value_keys})")

    if isinstance(constraints, dict):
        print(f"Loaded constraints keys: {len(constraints)} from {constraints_path}")
    else:
        print(f"Loaded constraints (non-dict) from {constraints_path}")

    print("Baseline scenario executed successfully.")

if __name__ == "__main__":
    run_baseline()
