import random
from typing import Dict, Any, List

# Use plan_engine helpers
from plan_engine import get_initial_balances, step_one_year, _infer_horizon_years

def run_monte_carlo(profile: Dict[str, Any], holdings: List[Dict[str, Any]], constraints: Dict[str, Any]) -> Dict[str, Any]:
    mc_config = constraints.get("monte_carlo", {}) if isinstance(constraints, dict) else {}
    num_simulations = mc_config.get("num_simulations", 1000)
    projection_years = mc_config.get("projection_years", _infer_horizon_years(constraints))

    initial_balances = get_initial_balances(holdings)

    withdrawal_sequence = constraints.get("withdrawal_sequence_default", ["taxable", "trad_ira", "inherited_ira", "roth_ira"])
    rmd_start_age = constraints.get("rmd_start_age", 73)
    rmd_applies_to = constraints.get("rmd_applies_to", ["trad_ira"])

    irmaa_config = constraints.get("irmaa_guardrails", {}) if isinstance(constraints, dict) else {}
    irmaa_enabled = irmaa_config.get("enabled", False)
    irmaa_tier1 = irmaa_config.get("tier1_magi_mfj", 206000)
    irmaa_headroom = irmaa_config.get("target_headroom_below_tier1", 10000)
    irmaa_threshold = irmaa_tier1 - irmaa_headroom

    base_age = profile.get("age", 72)
    placeholder_spending = constraints.get("placeholder_spending", 100000.0) if isinstance(constraints, dict) else 100000.0

    successful_sims = 0
    end_balances = []

    for _ in range(num_simulations):
        balances = initial_balances.copy()
        current_age = base_age
        failed = False

        for _ in range(projection_years):
            # sample annual return (placeholder blended return)
            annual_return = random.gauss(0.055, 0.10)

            # step_one_year expects assumed_growth_rate parameter; pass sampled return here
            _, end_balance, _, _, _, _, _, _, _, _ = step_one_year(
                balances, current_age, placeholder_spending,
                rmd_start_age, rmd_applies_to, withdrawal_sequence,
                irmaa_enabled, irmaa_threshold, annual_return
            )

            if end_balance <= 0:
                failed = True
            current_age += 1

        if not failed:
            successful_sims += 1
        end_balances.append(sum(balances.values()))

    success_probability = successful_sims / num_simulations if num_simulations > 0 else 0.0
    end_balances.sort()

    def get_percentile(p: float) -> float:
        if not end_balances: return 0.0
        idx = int(p * len(end_balances))
        return round(end_balances[min(max(idx, 0), len(end_balances) - 1)], 2)

    return {
        "success_probability": success_probability,
        "end_balance_percentiles": {
            "p5": get_percentile(0.05),
            "p25": get_percentile(0.25),
            "p50": get_percentile(0.50),
            "p75": get_percentile(0.75),
            "p95": get_percentile(0.95)
        },
        "num_simulations": num_simulations,
        "projection_years": projection_years
    }
