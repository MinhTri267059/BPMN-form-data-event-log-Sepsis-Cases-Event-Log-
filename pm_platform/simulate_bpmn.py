#!/usr/bin/env python3
"""
simulate_bpmn.py
Giả lập quy trình BPMN bằng phương pháp kỳ vọng ngẫu nhiên (Stochastic Expected Time).

Nhận đầu vào là `cycle_time_summary.json`.
Tính thời gian trung bình (expected duration) của cả quy trình bằng cách:
  Total Expected Time = Sum ( Thời gian trung bình 1 lần của Activity * Số lần xuất hiện trung bình của Activity trên 1 trace )

Usage:
    python simulate_bpmn.py
    python simulate_bpmn.py --set "Activity A=1.5h" --set "Activity B=90m"
"""

import argparse
import json
import os
import re
import sys
from copy import deepcopy

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
#  ARGUMENT PARSING
# ─────────────────────────────────────────────────────────────────────────────

def parse_time_override(s: str) -> tuple[str, float]:
    """Parse 'Activity Name=1.5h' or 'Activity Name=90m'."""
    m = re.match(r"^(.+?)=([0-9.]+)(h|m|d)?$", s.strip())
    if not m:
        raise argparse.ArgumentTypeError(
            f"Invalid format '{s}'. Use: \"Activity Name=1.5h\""
        )
    name, val, unit = m.group(1).strip(), float(m.group(2)), (m.group(3) or "h")
    if unit == "m": val /= 60.0
    elif unit == "d": val *= 24.0
    return name, val


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generic Process Simulation")
    parser.add_argument("--set", dest="overrides", metavar="KEY=VALUE", action="append", default=[])
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--summary-json", default="output/cycle_time_summary.json")
    parser.add_argument("--scenario-name", default="Scenario")
    return parser.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
#  SIMULATION ENGINE (Generic)
# ─────────────────────────────────────────────────────────────────────────────

def load_process_metrics(summary_json: str):
    with open(summary_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    total_traces = data["overall"]["count"]
    per_act = data.get("per_activity_wait_hours", {})
    
    # metrics[act] = {"median_h": X, "avg_occurrences": Y}
    metrics = {}
    for act, stats in per_act.items():
        occ = stats["count"] / total_traces if total_traces > 0 else 0
        metrics[act] = {
            "median_h": stats["median_h"],
            "mean_h": stats["mean_h"],
            "avg_occurrences": occ
        }
    return metrics, data["overall"]["mean_h"], data["overall"]["median_h"]


def simulate(metrics: dict, overrides: dict[str, float]) -> dict:
    """
    Compute total expected process time using sum( time * frequency ).
    Returns {steps: {act: expected_time}, total_hours: X}
    """
    total = 0.0
    steps = {}
    
    # We use median_h as the baseline representative time for typical case
    for act, stats in metrics.items():
        val = overrides.get(act, stats["median_h"])
        expected_time_contrib = val * stats["avg_occurrences"]
        steps[act] = expected_time_contrib
        total += expected_time_contrib
        
    return {
        "steps": steps,
        "total_hours": total,
        "total_days": total / 24.0
    }


def hours_to_str(h: float) -> str:
    if h < 0.017: return f"{h * 60:.1f} min"
    if h < 48: return f"{h:.2f} h"
    return f"{h / 24:.2f} days"


# ─────────────────────────────────────────────────────────────────────────────
#  CHART
# ─────────────────────────────────────────────────────────────────────────────

def draw_simulation_chart(baseline: dict, scenario: dict, name: str, out_path: str):
    b_steps = baseline["steps"]
    s_steps = scenario["steps"]
    
    # Sort by baseline highest contribution to show top 15
    sorted_keys = sorted(b_steps.keys(), key=lambda k: -b_steps[k])[:15]
    
    labels = sorted_keys
    b_vals = [b_steps[k] for k in labels]
    s_vals = [s_steps.get(k, 0.0) for k in labels]

    x = np.arange(len(labels))
    w = 0.35
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [3, 1]})
    fig.patch.set_facecolor("#F0F4F8")

    ax1.set_facecolor("#FAFCFF")
    ax1.bar(x - w/2, b_vals, w, label="Baseline (expected)", color="#3498DB", alpha=0.85)
    ax1.bar(x + w/2, s_vals, w, label=f"Scenario: {name}", color="#E67E22", alpha=0.85)
    
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
    ax1.set_ylabel("Expected Content Time (hours)", fontsize=10)
    ax1.legend(fontsize=9, framealpha=0.9)
    ax1.grid(axis="y", alpha=0.3, linestyle="--")
    
    deltas = [s - b for s, b in zip(s_vals, b_vals)]
    colors_delta = ["#E74C3C" if d > 0 else "#27AE60" for d in deltas]
    ax2.set_facecolor("#FAFCFF")
    ax2.bar(x, deltas, color=colors_delta, alpha=0.8)
    ax2.axhline(0, color="#555", linewidth=0.8)
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
    ax2.set_ylabel("Δ (hours)", fontsize=9)
    ax2.grid(axis="y", alpha=0.3, linestyle="--")

    fig.tight_layout(pad=2.0)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    args = parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    if not os.path.exists(args.summary_json):
        print(f"Error: {args.summary_json} not found.", file=sys.stderr)
        return 1
        
    metrics, actual_mean, actual_median = load_process_metrics(args.summary_json)
    
    # Real pipeline vs calculated pipeline calibration coefficient.
    # The calculated expected arithmetic total might slightly differ from exact average of sums.
    baseline_result = simulate(metrics, {})
    
    overrides = {}
    for raw in args.overrides:
        name, val = parse_time_override(raw)
        overrides[name] = val

    scenario_result = simulate(metrics, overrides)
    
    res_json = os.path.join(args.output_dir, "simulation_result.json")
    with open(res_json, "w", encoding="utf-8") as f:
        json.dump({
            "scenario_name": args.scenario_name,
            "baseline_result": baseline_result,
            "scenario_result": scenario_result,
        }, f, ensure_ascii=False, indent=2)

    chart_path = os.path.join(args.output_dir, "simulation_chart.png")
    draw_simulation_chart(baseline_result, scenario_result, args.scenario_name, chart_path)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
