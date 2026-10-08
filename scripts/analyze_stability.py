#!/usr/bin/env python
"""
Stability Analysis Utilities - Identifies stable parameter plateaus vs isolated peaks.
"""
import json
from typing import List, Dict, Any

def analyze_stability(results: List[Dict], param_name: str, metric: str = "expected_r") -> Dict[str, Any]:
    """
    Analyze whether results show a stable plateau or isolated peaks.
    
    Args:
        results: List of experiment results with param_value and metric
        param_name: Name of the parameter being analyzed
        metric: Metric to analyze (default: expected_r)
    
    Returns:
        Dict with stability analysis
    """
    if not results:
        return {"error": "No results"}
    
    # Sort by parameter value
    sorted_results = sorted(results, key=lambda x: x.get("param_value", 0))
    
    values = [r["param_value"] for r in sorted_results]
    metrics = [r.get(metric, 0) for r in sorted_results]
    
    if len(values) < 3:
        return {"error": "Need at least 3 points for stability analysis"}
    
    # Find local maxima
    local_maxima = []
    for i in range(1, len(metrics) - 1):
        if metrics[i] > metrics[i-1] and metrics[i] > metrics[i+1]:
            local_maxima.append({
                "param_value": values[i],
                "metric": metrics[i],
                "index": i
            })
    
    # Find plateau regions (consecutive values within 5% of max)
    max_metric = max(metrics)
    plateau_threshold = max_metric * 0.95
    plateau_regions = []
    
    in_plateau = False
    plateau_start = None
    
    for i, (v, m) in enumerate(zip(values, metrics)):
        if m >= plateau_threshold:
            if not in_plateau:
                in_plateau = True
                plateau_start = i
        else:
            if in_plateau:
                plateau_regions.append({
                    "start_value": values[plateau_start],
                    "end_value": values[i-1],
                    "start_index": plateau_start,
                    "end_index": i-1,
                    "min_metric": min(metrics[plateau_start:i]),
                    "max_metric": max(metrics[plateau_start:i]),
                    "width": i - plateau_start
                })
                in_plateau = False
    
    if in_plateau:
        plateau_regions.append({
            "start_value": values[plateau_start],
            "end_value": values[-1],
            "start_index": plateau_start,
            "end_index": len(values)-1,
            "min_metric": min(metrics[plateau_start:]),
            "max_metric": max(metrics[plateau_start:]),
            "width": len(values) - plateau_start
        })
    
    # Determine stability
    is_stable = len(plateau_regions) > 0 and any(r["width"] >= 2 for r in plateau_regions)
    
    # Check for isolated sharp peak
    has_sharp_peak = False
    if local_maxima:
        best_peak = max(local_maxima, key=lambda x: x["metric"])
        # Check if neighbors are significantly worse
        idx = best_peak["index"]
        if idx > 0 and idx < len(metrics) - 1:
            left_drop = (best_peak["metric"] - metrics[idx-1]) / abs(best_peak["metric"]) if best_peak["metric"] != 0 else 0
            right_drop = (best_peak["metric"] - metrics[idx+1]) / abs(best_peak["metric"]) if best_peak["metric"] != 0 else 0
            if left_drop > 0.2 and right_drop > 0.2:  # >20% drop on both sides
                has_sharp_peak = True
    
    return {
        "parameter": param_name,
        "metric": metric,
        "values": values,
        "metrics": metrics,
        "local_maxima": local_maxima,
        "plateau_regions": plateau_regions,
        "is_stable": is_stable,
        "has_sharp_peak": has_sharp_peak,
        "best_param": values[metrics.index(max(metrics))],
        "best_metric": max(metrics),
        "worst_param": values[metrics.index(min(metrics))],
        "worst_metric": min(metrics),
        "range_metric": max(metrics) - min(metrics),
        "recommendation": "stable" if is_stable else ("sharp_peak" if has_sharp_peak else "monotonic")
    }

def load_experiment_results(json_path: str) -> List[Dict]:
    """Load experiment results from JSON file."""
    with open(json_path, "r") as f:
        return json.load(f)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Analyze parameter stability")
    parser.add_argument("json_file", help="Path to experiment results JSON")
    parser.add_argument("--param", default="param_value", help="Parameter field name")
    parser.add_argument("--metric", default="expected_r", help="Metric to analyze")
    parser.add_argument("--output", help="Output file for analysis")
    args = parser.parse_args()
    
    results = load_experiment_results(args.json_file)
    analysis = analyze_stability(results, args.param, args.metric)
    
    print(json.dumps(analysis, indent=2))
    
    if args.output:
        with open(args.output, "w") as f:
            json.dump(analysis, f, indent=2)
        print(f"Analysis saved to {args.output}")

if __name__ == "__main__":
    main()