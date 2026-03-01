import copy
from typing import Dict, Any, List
from monte_carlo import run_monte_carlo

def evaluate_actions(profile: Dict[str, Any], holdings: List[Dict[str, Any]], constraints: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Evaluates different advisor actions by running Monte Carlo simulations
    and comparing the outcomes against the baseline.
    """
    # Baseline run
    baseline_result = run_monte_carlo(profile, holdings, constraints)
    baseline_prob = baseline_result["success_probability"]
    baseline_median = baseline_result["end_balance_percentiles"]["p50"]
    baseline_irmaa = baseline_result.get("avg_irmaa_warning_years", 0.0)

    actions = [
        {
            "name": "Reduce spending by 10%",
            "type": "spending_reduction",
            "value": 0.9
        },
        {
            "name": "Reduce spending by 20%",
            "type": "spending_reduction",
            "value": 0.8
        },
        {
            "name": "Delay RMD start by 2 years",
            "type": "delay_rmd",
            "value": 2
        },
        {
            "name": "Withdrawal order: taxable last",
            "type": "withdrawal_order",
            "value": ["trad_ira", "inherited_ira", "roth_ira", "taxable"]
        }
    ]

    results = []
    for action in actions:
        mod_constraints = copy.deepcopy(constraints) if isinstance(constraints, dict) else {}

        if action["type"] == "spending_reduction":
            current_spending = mod_constraints.get("placeholder_spending", 100000.0)
            mod_constraints["placeholder_spending"] = current_spending * action["value"]
        elif action["type"] == "delay_rmd":
            current_rmd = mod_constraints.get("rmd_start_age", 73)
            mod_constraints["rmd_start_age"] = current_rmd + action["value"]
        elif action["type"] == "withdrawal_order":
            mod_constraints["withdrawal_sequence_default"] = action["value"]

        mc_result = run_monte_carlo(profile, holdings, mod_constraints)
        prob = mc_result["success_probability"]
        median = mc_result["end_balance_percentiles"]["p50"]
        irmaa = mc_result.get("avg_irmaa_warning_years", 0.0)

        delta_prob = prob - baseline_prob
        delta_median = median - baseline_median
        delta_irmaa = irmaa - baseline_irmaa
        score = (delta_prob * 100.0) + (delta_median / 100000.0) - (delta_irmaa * 1.5)

        results.append({
            "name": action["name"],
            "delta_success_probability": delta_prob,
            "delta_median_end_balance": delta_median,
            "delta_avg_irmaa_warning_years": delta_irmaa,
            "score": score
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results
