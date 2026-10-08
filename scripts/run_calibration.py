#!/usr/bin/env python
"""
Unified Calibration Experiment Runner
Runs all controlled calibration experiments.
"""
import os
import sys
import subprocess
import argparse

def run_command(cmd: list, description: str):
    """Run a command and report result."""
    print(f"\n{'='*60}")
    print(f"Running: {description}")
    print(f"Command: {' '.join(cmd)}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        print(f"FAILED: {description} (exit code {result.returncode})")
        return False
    print(f"SUCCESS: {description}")
    return True

def main():
    parser = argparse.ArgumentParser(description="Run Calibration Experiments")
    parser.add_argument("--experiment", choices=[
        "volume", "confidence", "sl_lookback", "tp_rr", 
        "ablation", "oi_modes", "all"
    ], default="all", help="Experiment to run")
    parser.add_argument("--train-only", action="store_true", help="Run on TRAIN partition only")
    parser.add_argument("--max-symbols", type=int, default=10, help="Max symbols to test")
    args = parser.parse_args()

    base_cmd = [sys.executable, "-m"]
    scripts_dir = "scripts"

    experiments = []

    if args.experiment in ["volume", "all"]:
        experiments.append({
            "script": "run_experiments.py",
            "args": ["--volume-only"] if hasattr(__import__("scripts.run_experiments"), "volume_only") else [],
            "description": "Volume Threshold Experiment"
        })

    # We'll create simpler individual scripts for each experiment
    # For now, let's define the commands

    if args.experiment in ["volume", "all"]:
        experiments.append({
            "cmd": [sys.executable, "-m", "scripts.run_volume_experiment", "--max-symbols", str(args.max_symbols)],
            "description": "Volume Threshold Experiment (TRAIN)"
        })

    if args.experiment in ["confidence", "all"]:
        experiments.append({
            "cmd": [sys.executable, "-m", "scripts.run_confidence_experiment", "--max-symbols", str(args.max_symbols)],
            "description": "Confidence Threshold Experiment (TRAIN)"
        })

    if args.experiment in ["sl_lookback", "all"]:
        experiments.append({
            "cmd": [sys.executable, "-m", "scripts.run_sl_lookback_experiment", "--max-symbols", str(args.max_symbols)],
            "description": "Structural SL Lookback Experiment (TRAIN)"
        })

    if args.experiment in ["tp_rr", "all"]:
        experiments.append({
            "cmd": [sys.executable, "-m", "scripts.run_tp_rr_experiment", "--max-symbols", str(args.max_symbols)],
            "description": "TP/RR Ladder Experiment (TRAIN)"
        })

    if args.experiment in ["ablation", "all"]:
        experiments.append({
            "cmd": [sys.executable, "-m", "scripts.run_ablation", "--max-symbols", str(args.max_symbols)],
            "description": "Ablation Study (TRAIN)"
        })

    if args.experiment in ["oi_modes", "all"]:
        experiments.append({
            "cmd": [sys.executable, "-m", "scripts.run_oi_modes_experiment", "--max-symbols", str(args.max_symbols)],
            "description": "OI Modes Comparison (TRAIN)"
        })

    if not experiments:
        print("No experiments selected")
        return

    print(f"Running {len(experiments)} experiments...")
    failed = []
    for exp in experiments:
        if not run_command(exp["cmd"], exp["description"]):
            failed.append(exp["description"])

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Total: {len(experiments)}")
    print(f"Passed: {len(experiments) - len(failed)}")
    print(f"Failed: {len(failed)}")
    if failed:
        for f in failed:
            print(f"  - {f}")
        sys.exit(1)

if __name__ == "__main__":
    main()